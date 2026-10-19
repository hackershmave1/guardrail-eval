"""
Promptfoo custom provider that drives an AgenticGoat lab as the application
under test, then reports the lab's deterministic canary/OOB oracle.

Workbook basis (AI_Guardrails_POC_4_2.xlsx):
  - Tooling tab / REQ-TL-04: Promptfoo is the backbone; the ONLY glue you build
    is the deterministic oracle (OOB listener + canary), wired in as a custom
    assertion. This provider runs the app; `assert_canary.py` is that assertion.
  - Success Oracles tab: reach for the strongest oracle the attack allows
    (side-channel/OOB > canary > tool-call log). Each AgenticGoat lab's
    canary.py::canary_reached() IS that oracle — a real side effect, never the
    model's words.

Promptfoo calls `call_api(prompt, options, context)`. We return:
  { "output": { "canary_reached": bool, "answer": str, "lab": str,
                "defense": str|None, "ran": bool, "note": str } }
so the assertion can grade on `canary_reached` deterministically.

Per-test config (set under each test's `vars`; merged over provider `config`):
  lab          relative lab dir, e.g. "labs/01-mcp-tool-poisoning"   (required)
  run          "agent" | "script" | "rag_attack"                     (required)
  servers      list of MCP server scripts (run == "agent")
  arg_style    "positional" (Labs 01, 03: `agent.py [--defense X] server... prompt`)
               or "server-flags" (Labs 01b, 06: `agent.py --server s --query prompt
               [--defense X]`). Default "positional".
  script       script filename to execute (run == "script"/"rag_attack")
  defense      optional defense flag passed to the agent (e.g. "sensitive-paths")
  reset_state  comma-delimited lab-relative files OR dirs to delete before the run
  setup        comma-delimited setup commands run (with the venv python) in the
               lab dir after reset, e.g. "vulnerable_rag.py seed" to rebuild a
               clean Lab 04 knowledge base so rows don't see each other's docs
  oracle       which deterministic oracle decides `canary_reached` (default
               "canary" = the lab's own canary.py):
                 "retrieval" — any id in `oracle_ids` appears in the lab's
                               state/retrievals.jsonl (retrieval-side, model-
                               independent)
                 "answer"    — any string in `oracle_markers` appears in the
                               pipeline's `[Answer]` blocks (only the model's
                               generated text, never the driver's own banners
                               or raw-retrieval dumps)
  oracle_ids / oracle_markers   comma-delimited scalars for the above
  oracle_regex optional single regex (case-insensitive) also checked by the
               "answer" oracle — for tokens a model only emits by OBEYING an
               injection (a filled-in exfil URL), not by quoting the doc
  reasoning_effort  optional ("low"|"medium"|"high"): pin the carrier's
               reasoning effort on every chat completion via shim/sitecustomize.py
               (stops reasoning models spending a lab's small max_tokens on
               thinking and returning no answer)
  start_exfil  bool — best-effort launch of the lab's exfil_server.py first
  timeout      seconds for the subprocess (default 180)

Env:
  AGENTIC_GOAT_ROOT   path to the AgenticGoat checkout (default: <repo>/agentic-goat)
  LLM_BASE_URL/MODEL/API_KEY   forwarded to the lab (REQ-MD-01/02: hold constant,
                               use a permissive carrier)
  GUARDRAIL_EVAL_DRY_RUN=1     skip the subprocess; report ran=False (useful when
                               the submodule or a local model isn't present yet)
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GOAT_ROOT = Path(os.getenv("AGENTIC_GOAT_ROOT", REPO_ROOT / "agentic-goat"))


def _apply_dotenv(env: dict) -> None:
    """Fill the subprocess env from the repo's .env (for LLM_BASE_URL/MODEL/API_KEY).

    The AgenticGoat labs read os.environ directly and never load .env, and we
    cannot rely on promptfoo propagating it to this provider's subprocess, so we
    load it here. Values already present in `env` (a real export, or promptfoo's
    own .env load) win; .env only fills what's missing.
    """
    dotenv_path = REPO_ROOT / ".env"
    try:
        for raw in dotenv_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in env:
                env[key] = val
    except OSError:
        pass


def _as_list(value) -> list:
    """Coerce a config value to a list of strings.

    Multi-valued fields (`servers`, `reset_state`) are passed as a single
    COMMA-DELIMITED scalar, NOT a YAML list, and split here. Two reasons:
      1. promptfoo matrix-expands an array `vars` value into one test case per
         element — so `servers: [poison, filesystem]` would run each server in a
         SEPARATE agent, breaking two-server attacks (Lab 01 poison+filesystem,
         Lab 06 cross-server poisoning). A scalar is passed through as one case.
      2. A bare string must not be fed to `list()` (that explodes "x.py" into
         ['x','.','p','y'], the agent launches 'x' as a server, and the crash
         gets misread as PROTECTED).
    Coerce str -> split on ',' (trimmed, empties dropped); leave real lists alone.
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [s.strip() for s in value.split(",") if s.strip()]
    return list(value)


def _lab_dir(cfg: dict) -> Path:
    lab = cfg.get("lab")
    if not lab:
        raise ValueError("provider config is missing required field 'lab'")
    return (GOAT_ROOT / lab).resolve()


def _reset_state(lab_dir: Path, cfg: dict) -> None:
    for rel in _as_list(cfg.get("reset_state")):
        target = lab_dir / rel
        try:
            if target.is_dir():
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()
        except OSError:
            pass


def _run_setup(lab_dir: Path, cfg: dict, env: dict) -> None:
    for step in _as_list(cfg.get("setup")):
        proc = subprocess.run(
            [sys.executable, *shlex.split(step)],
            cwd=str(lab_dir), env=env,
            capture_output=True, text=True, timeout=300,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"setup '{step}' exited {proc.returncode}: {proc.stderr[-300:]}")


# End of one `[Answer]` block: the drivers follow each answer with an
# indicator line, a diagnostic line, or a ─/= separator.
_ANSWER_END = re.compile(r"\n(  (⚠️|✅|🚨|\(diagnostic\))|─{10}|={10})")


def _answers(stdout: str) -> list[str]:
    """The model's generated text only, pulled from the driver's `[Answer]` blocks."""
    out = []
    for chunk in stdout.split("[Answer]\n")[1:]:
        m = _ANSWER_END.search(chunk)
        out.append(chunk[: m.start()] if m else chunk)
    return out


def _norm(text: str) -> str:
    """Case-fold, drop all whitespace, unify dashes: models restyle "$950K" as
    "$950 K" and "2025-CV-4472" with non-breaking hyphens."""
    text = re.sub(r"[\u2010-\u2015\u2212]", "-", text.lower())
    return re.sub(r"\s+", "", text)


def _oracle(lab_dir: Path, cfg: dict, env: dict, stdout: str) -> tuple[bool, str]:
    """Return (reached, evidence) for the row's configured oracle."""
    kind = cfg.get("oracle", "canary")
    if kind == "canary":
        return _canary_reached(lab_dir, env), ""
    if kind == "retrieval":
        ids = set(_as_list(cfg.get("oracle_ids")))
        log = lab_dir / "state" / "retrievals.jsonl"
        hits = []
        if log.exists():
            for line in log.read_text(encoding="utf-8").splitlines():
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                hits += [f"{entry.get('query')!r} -> {d}" for d in entry.get("retrieved", []) if d in ids]
        return bool(hits), "; ".join(hits)
    if kind == "answer":
        markers = _as_list(cfg.get("oracle_markers"))
        answers = [_norm(a) for a in _answers(stdout)]
        hits = [m for a in answers for m in markers if _norm(m) in a]
        if cfg.get("oracle_regex"):
            rx = re.compile(cfg["oracle_regex"], re.IGNORECASE)
            hits += [m.group(0)[:80] for a in _answers(stdout) for m in rx.finditer(a)]
        return bool(hits), "markers in answer: " + ", ".join(sorted(set(hits))) if hits else ""
    raise ValueError(f"unknown oracle: {kind!r}")


def _maybe_start_exfil(lab_dir: Path, cfg: dict, env: dict) -> None:
    """Best-effort: REQ-OR-02 wants the OOB listener reachable before a run."""
    if not cfg.get("start_exfil"):
        return
    try:
        urllib.request.urlopen("http://localhost:9999/", timeout=0.5)
        return  # already up
    except Exception:
        pass
    exfil = lab_dir / "exfil_server.py"
    if exfil.exists():
        try:
            subprocess.Popen(
                [sys.executable, str(exfil)],
                cwd=str(lab_dir), env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            time.sleep(1.5)
        except Exception:
            pass


def _canary_reached(lab_dir: Path, env: dict) -> bool:
    """Invoke the lab's own canary.py — the deterministic oracle of record."""
    canary = lab_dir / "canary.py"
    if not canary.exists():
        return False
    proc = subprocess.run(
        [sys.executable, str(canary)],
        cwd=str(lab_dir), env=env,
        capture_output=True, text=True, timeout=30,
    )
    # canary.py exits 0 when the canary reached its sink, 1 otherwise.
    return proc.returncode == 0


def _build_cmd(lab_dir: Path, cfg: dict, prompt: str):
    run = cfg.get("run")
    if run == "agent":
        servers = _as_list(cfg.get("servers"))
        arg_style = cfg.get("arg_style", "positional")
        cmd = [sys.executable, "agent.py"]
        if arg_style == "server-flags":
            # Labs 01b / 06: --server s1 --server s2 --query "<prompt>" [--defense X]
            for s in servers:
                cmd += ["--server", s]
            cmd += ["--query", prompt]
            if cfg.get("defense"):
                cmd += ["--defense", str(cfg["defense"])]
            return cmd
        # positional (Labs 01 / 03): [--defense X] server... prompt
        if cfg.get("defense"):
            cmd += ["--defense", str(cfg["defense"])]
        cmd += list(servers) + [prompt]
        return cmd
    if run in ("script", "rag_attack"):
        script = cfg.get("script")
        if not script:
            raise ValueError(f"run='{run}' needs a 'script' field")
        # The RAG/attack drivers are self-contained; prompt is passed through
        # where the script accepts argv, otherwise ignored by the driver.
        return [sys.executable, script, prompt] if run == "script" else [sys.executable, script]
    raise ValueError(f"unknown run mode: {run!r}")


def call_api(prompt, options, context):
    # Scenario config is carried in each test's `vars` (merged over any
    # provider-level `config`), so a single top-level provider serves every
    # test — promptfoo requires exactly one top-level provider/target.
    cfg = dict((options or {}).get("config", {}) or {})
    cfg.update((context or {}).get("vars", {}) or {})
    lab_dir = _lab_dir(cfg)

    env = dict(os.environ)
    _apply_dotenv(env)                       # fill LLM_BASE_URL/MODEL/API_KEY from .env
    env.setdefault("LLM_BASE_URL", "http://localhost:11434/v1")
    env.setdefault("MODEL", "qwen2.5-7b-instruct")
    env.setdefault("API_KEY", "lm-studio")

    if cfg.get("reasoning_effort"):
        env["GUARDRAIL_REASONING_EFFORT"] = str(cfg["reasoning_effort"])
        shim = str(Path(__file__).resolve().parent / "shim")
        env["PYTHONPATH"] = os.pathsep.join(p for p in (shim, env.get("PYTHONPATH")) if p)

    if not lab_dir.exists():
        return {"output": {"canary_reached": False, "answer": "", "lab": cfg.get("lab"),
                           "defense": cfg.get("defense"), "ran": False,
                           "note": f"lab dir not found: {lab_dir} — run `git submodule update --init`"}}

    if os.getenv("GUARDRAIL_EVAL_DRY_RUN") == "1":
        return {"output": {"canary_reached": False, "answer": "", "lab": cfg.get("lab"),
                           "defense": cfg.get("defense"), "ran": False, "note": "dry run"}}

    _reset_state(lab_dir, cfg)
    _maybe_start_exfil(lab_dir, cfg, env)

    try:
        _run_setup(lab_dir, cfg, env)
        cmd = _build_cmd(lab_dir, cfg, prompt)
        proc = subprocess.run(
            cmd, cwd=str(lab_dir), env=env,
            capture_output=True, text=True,
            timeout=int(cfg.get("timeout", 180)),
        )
        answer = (proc.stdout or "")[-20000:]
        reached, evidence = _oracle(lab_dir, cfg, env, proc.stdout or "")
        if reached:
            # A real exfil/side effect is ground truth: VULNERABLE regardless of
            # how the process exited.
            return {"output": {"canary_reached": True, "answer": answer,
                               "lab": cfg.get("lab"), "defense": cfg.get("defense"),
                               "ran": True, "note": evidence}}
        if proc.returncode != 0:
            # The attack never completed (e.g. the model endpoint is unreachable),
            # so this is a setup/run error, NOT "the guardrail held". Report it as
            # not-run so it doesn't read as a false PROTECTED pass.
            return {"output": {"canary_reached": False, "answer": answer,
                               "lab": cfg.get("lab"), "defense": cfg.get("defense"),
                               "ran": False,
                               "note": f"agent exited {proc.returncode}: {(proc.stderr or '')[-500:]}"}}
        return {"output": {"canary_reached": False, "answer": answer,
                           "lab": cfg.get("lab"), "defense": cfg.get("defense"),
                           "ran": True, "note": ""}}
    except subprocess.TimeoutExpired:
        return {"output": {"canary_reached": False, "answer": "", "lab": cfg.get("lab"),
                           "defense": cfg.get("defense"), "ran": True, "note": "timeout"}}
    except Exception as e:  # noqa: BLE001 - surface the failure to the report
        return {"output": {"canary_reached": False, "answer": "", "lab": cfg.get("lab"),
                           "defense": cfg.get("defense"), "ran": False, "note": f"error: {e}"}}
