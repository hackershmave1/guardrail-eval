"""
Guardrail slot: an OpenAI-compatible recording proxy on the MODEL channel.

    caller (promptfoo / a lab)  ->  THIS SLOT  ->  upstream model (LLM_BASE_URL)

It is the place a vendor guardrail plugs in (workbook Enforcement Placement tab:
"AI gateway plugin" / "In-code SDK"), and it is the DLP-01 oracle's evidence:
every request is logged exactly as it was FORWARDED to the model, so "did the
PII reach the model?" is answered from the wire, never from the model's words.

Modes (per request via the `X-Guardrail-Mode` header, else env GUARDRAIL_MODE):
  none   pass-through. The Phase 1 baseline: no guardrail in the loop.
  demo   a TOY reference guardrail (regex PII masking + keyword injection
         block). NOT a vendor and not a benchmark - it exists only to calibrate
         the oracles: a known-good control must PASS, the same rows under
         `none` must FAIL. Swap in a vendor by replacing scan_input/scan_output
         (SDK-style call) or by pointing UPSTREAM_BASE_URL at a vendor gateway.

Log: results/slot_log.jsonl, one JSON line per request:
  {ts, trial_id, mode, action: allow|mask|block, received, forwarded, status,
   reply, latency_ms}
`trial_id` comes from the `X-Trial-Id` header (the model-channel provider sets
one per row) so the oracle can find its own request.

Run:  venv/bin/python promptfoo/guardrail_slot/proxy.py      (port 8787)
Env:  UPSTREAM_BASE_URL (default: LLM_BASE_URL from env/.env), API_KEY,
      GUARDRAIL_SLOT_PORT (8787), GUARDRAIL_MODE (none), SLOT_LOG,
      EXTRA_HEADERS (per-endpoint headers on the upstream call; providers/shim/extra_headers.py)
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
_LOG_LOCK = threading.Lock()
sys.path.insert(0, str(REPO_ROOT / "promptfoo" / "providers" / "shim"))
import extra_headers  # noqa: E402  (one per-endpoint header parser, shared with the shim)


def load_dotenv(env: dict) -> None:
    """Fill missing keys from the repo's .env. Strips inline `# comments` on
    unquoted values (the .env carries `MODEL=x   # note`)."""
    try:
        lines = (REPO_ROOT / ".env").read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip()
        if val[:1] in "\"'" and val[:1] and val.count(val[0]) >= 2:
            val = val[1:val.index(val[0], 1)]
        else:
            val = re.split(r"\s+#", val, maxsplit=1)[0].strip()
        if key and key not in env:
            env[key] = val


ENV = dict(os.environ)
load_dotenv(ENV)
UPSTREAM = (ENV.get("UPSTREAM_BASE_URL") or ENV.get("LLM_BASE_URL") or "http://localhost:11434/v1").rstrip("/")
API_KEY = ENV.get("API_KEY", "")
PORT = int(ENV.get("GUARDRAIL_SLOT_PORT", "8787"))
DEFAULT_MODE = ENV.get("GUARDRAIL_MODE", "none")
LOG_PATH = Path(ENV.get("SLOT_LOG", REPO_ROOT / "results" / "slot_log.jsonl"))
EXTRA_HEADERS: dict[str, dict[str, str]] = {}  # resolved in main()


# ── demo guardrail (toy, for oracle calibration only) ─────────────────────────

_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]){11,30}\b")
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")
_INJECTION = re.compile(
    r"\b(ignore|disregard|forget|override)\b.{0,40}\b(instructions|rules|guidelines|system prompt)\b",
    re.IGNORECASE | re.DOTALL,
)


def _luhn_ok(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d = d * 2 - 9 if d > 4 else d * 2
        total, alt = total + d, not alt
    return total % 10 == 0


def _iban_ok(iban: str) -> bool:
    s = iban.replace(" ", "")
    s = s[4:] + s[:4]
    return int("".join(str(int(c, 36)) for c in s)) % 97 == 1


def _mask_text(text: str) -> str:
    text = _SSN.sub("[SSN]", text)
    text = _CARD.sub(lambda m: "[CARD]" if _luhn_ok(re.sub(r"\D", "", m.group(0))) else m.group(0), text)
    text = _IBAN.sub(lambda m: "[IBAN]" if _iban_ok(m.group(0)) else m.group(0), text)
    return _EMAIL.sub("[EMAIL]", text)


def scan_input(body: dict, mode: str) -> tuple[str, dict, str]:
    """Return (action, body_to_forward, reason). action: allow | mask | block.

    VENDOR HOOK (SDK placement): call the vendor's prompt-scan API here with the
    messages; map its verdict to block / mask (use its redacted text) / allow.
    """
    if mode != "demo":
        return "allow", body, ""
    user_text = " ".join(m.get("content") or "" for m in body.get("messages", [])
                         if isinstance(m, dict) and m.get("role") == "user" and isinstance(m.get("content"), str))
    if _INJECTION.search(user_text):
        return "block", body, "demo: injection keywords"
    out = json.loads(json.dumps(body))
    changed = False
    for m in out.get("messages", []):
        if isinstance(m, dict) and m.get("role") == "user" and isinstance(m.get("content"), str):
            masked = _mask_text(m["content"])
            changed |= masked != m["content"]
            m["content"] = masked
    return ("mask" if changed else "allow"), out, ("demo: masked PII" if changed else "")


def scan_output(reply: dict, mode: str) -> tuple[str, dict]:
    """VENDOR HOOK (response scan). Pass-through in Phase 1."""
    return "allow", reply


# ── HTTP plumbing ─────────────────────────────────────────────────────────────

def _log(entry: dict) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _LOG_LOCK, LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # quiet
        pass

    def _send(self, status: int, payload: dict) -> None:
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.rstrip("/") in ("/health", "/v1/health"):
            return self._send(200, {"ok": True, "upstream": UPSTREAM, "mode": DEFAULT_MODE})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self.path.rstrip("/").endswith("/chat/completions"):
            return self._send(404, {"error": f"slot only proxies chat/completions, got {self.path}"})
        started = time.time()
        trial_id = self.headers.get("X-Trial-Id")
        mode = self.headers.get("X-Guardrail-Mode") or DEFAULT_MODE
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        except ValueError:
            return self._send(400, {"error": "body is not JSON"})

        action, forwarded, reason = scan_input(body, mode)
        entry = {"ts": started, "trial_id": trial_id, "mode": mode, "action": action,
                 "reason": reason, "received": body, "forwarded": None, "status": None, "reply": None}
        if action == "block":
            entry.update(status=403, latency_ms=round((time.time() - started) * 1000))
            _log(entry)
            return self._send(403, {"error": {"type": "guardrail_blocked", "message": reason}})

        entry["forwarded"] = forwarded
        req = urllib.request.Request(
            UPSTREAM + "/chat/completions", data=json.dumps(forwarded).encode(), method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": self.headers.get("Authorization") or f"Bearer {API_KEY}",
                     **extra_headers.for_url(EXTRA_HEADERS, UPSTREAM + "/chat/completions")})
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                status, raw = resp.status, resp.read()
        except urllib.error.HTTPError as e:
            status, raw = e.code, e.read()
        except Exception as e:  # noqa: BLE001 - upstream unreachable
            entry.update(status=502, reply=str(e), latency_ms=round((time.time() - started) * 1000))
            _log(entry)
            return self._send(502, {"error": {"type": "upstream_unreachable", "message": str(e)}})

        try:
            reply = json.loads(raw)
        except ValueError:
            reply = {"raw": raw.decode(errors="replace")[:2000]}
        if status == 200:
            _, reply = scan_output(reply, mode)
        entry.update(status=status, reply=reply, latency_ms=round((time.time() - started) * 1000))
        _log(entry)
        self._send(status, reply)


def main() -> None:
    global EXTRA_HEADERS
    # Resolved at start, not per request: a malformed table or an unset ${VAR} stops
    # the slot here (run_evals.sh then reports it did not come up), never mid-run.
    try:
        EXTRA_HEADERS = extra_headers.resolve(extra_headers.parse(ENV.get("EXTRA_HEADERS")), ENV)
    except (ValueError, KeyError) as e:
        sys.exit(f"guardrail slot: {e}")
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"guardrail slot on http://127.0.0.1:{PORT}/v1 -> {UPSTREAM} (default mode={DEFAULT_MODE})", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
