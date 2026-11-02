"""
Guardrail slot: an OpenAI-compatible recording proxy on the MODEL channel.

    caller (promptfoo / a lab)  ->  THIS SLOT  ->  upstream model (LLM_BASE_URL)

It is the place a guardrail plugs in (as an AI gateway plugin or an in-code SDK
call), and it is the DLP-01 oracle's evidence:
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

REPO_ROOT = Path(__file__).absolute().parents[2]
_LOG_LOCK = threading.Lock()
sys.path.insert(0, str(REPO_ROOT / "promptfoo" / "providers" / "shim"))
import extra_headers  # noqa: E402  (one per-endpoint header parser, shared with the shim)


def _dotenv_value(val: str) -> str:
    """A quoted value up to its closing quote; else the value before an inline ` # comment`."""
    if val[:1] in ("\"", "'") and val.count(val[0]) >= 2:
        return val[1:val.index(val[0], 1)]
    return re.split(r"\s+#", val)[0].strip()


def load_dotenv(env: dict) -> None:
    """Fill missing keys from the repo's .env (the .env carries `MODEL=x   # note`)."""
    try:
        lines = (REPO_ROOT / ".env").read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if key and key not in env:
            env[key] = _dotenv_value(val.strip())


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
    doubled = (int(c) * 2 if i % 2 else int(c) for i, c in enumerate(reversed(digits)))
    return sum(d - 9 if d > 9 else d for d in doubled) % 10 == 0


def _iban_ok(iban: str) -> bool:
    s = iban.replace(" ", "")
    s = s[4:] + s[:4]
    return int("".join(str(int(c, 36)) for c in s)) % 97 == 1


def _mask_text(text: str) -> str:
    text = _SSN.sub("[SSN]", text)
    text = _CARD.sub(lambda m: "[CARD]" if _luhn_ok(re.sub(r"\D", "", m.group(0))) else m.group(0), text)
    text = _IBAN.sub(lambda m: "[IBAN]" if _iban_ok(m.group(0)) else m.group(0), text)
    return _EMAIL.sub("[EMAIL]", text)


def _user_texts(body: dict) -> list[dict]:
    """The user messages whose content is a plain string: all the demo guardrail reads."""
    return [m for m in body.get("messages", [])
            if isinstance(m, dict) and m.get("role") == "user" and isinstance(m.get("content"), str)]


def scan_input(body: dict, mode: str) -> tuple[str, dict, str]:
    """Return (action, body_to_forward, reason). action: allow | mask | block.

    VENDOR HOOK (SDK placement): call the vendor's prompt-scan API here with the
    messages; map its verdict to block / mask (use its redacted text) / allow.
    """
    if mode != "demo":
        return "allow", body, ""
    if _INJECTION.search(" ".join(m["content"] for m in _user_texts(body))):
        return "block", body, "demo: injection keywords"
    out = json.loads(json.dumps(body))
    changed = False
    for m in _user_texts(out):
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


def _elapsed_ms(started: float) -> int:
    return round((time.time() - started) * 1000)


def _call_upstream(body: dict, authorization: str | None) -> tuple[int, bytes]:
    """POST the body to the model; (status, raw reply). Raises when the upstream is unreachable."""
    url = UPSTREAM + "/chat/completions"
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": authorization or f"Bearer {API_KEY}",
                 **extra_headers.for_url(EXTRA_HEADERS, url)})
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _parse_reply(raw: bytes) -> dict:
    try:
        return json.loads(raw)
    except ValueError:
        return {"raw": raw.decode(errors="replace")[:2000]}


def _model_turn(forwarded: dict, mode: str, authorization: str | None) -> tuple[int, dict, object]:
    """Forward an allowed request: (status, reply to send, reply to log)."""
    try:
        status, raw = _call_upstream(forwarded, authorization)
    except Exception as e:  # noqa: BLE001 - upstream unreachable
        return 502, {"error": {"type": "upstream_unreachable", "message": str(e)}}, str(e)
    reply = _parse_reply(raw)
    if status == 200:
        _, reply = scan_output(reply, mode)
    return status, reply, reply


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
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        except ValueError:
            return self._send(400, {"error": "body is not JSON"})

        mode = self.headers.get("X-Guardrail-Mode") or DEFAULT_MODE
        action, forwarded, reason = scan_input(body, mode)
        if action == "block":
            forwarded, status, logged = None, 403, None
            reply = {"error": {"type": "guardrail_blocked", "message": reason}}
        else:
            status, reply, logged = _model_turn(forwarded, mode, self.headers.get("Authorization"))
        _log({"ts": started, "trial_id": self.headers.get("X-Trial-Id"), "mode": mode, "action": action,
              "reason": reason, "received": body, "forwarded": forwarded, "status": status,
              "reply": logged, "latency_ms": _elapsed_ms(started)})
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
