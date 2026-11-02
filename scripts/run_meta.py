#!/usr/bin/env python3
"""Record a run's context in <run_dir>/run.json (read by scripts/ingest_run.py).

    run_meta.py start  <run_dir> <run_id> <config> <concurrency>
    run_meta.py finish <run_dir> <promptfoo exit code>
    run_meta.py check-headers     # exit 1 if EXTRA_HEADERS can't apply (run_evals.sh gate)

Context comes from git, `promptfoo --version`, .env (MODEL, OPENROUTER_PROVIDER_ONLY,
EXTRA_HEADERS: header names per endpoint, never values) and the run_evals.sh
environment (REPEAT, FILTER, GUARDRAIL_ID). promptfoo exits 0
when every assertion passed and 100 when some failed; both are a complete run.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).absolute().parents[1]

# Reuse the ONE canonical .env parser (proxy.load_dotenv), so the recorded
# carrier_model / provider_pin match what the slot and the labs actually read.
sys.path.insert(0, str(ROOT / "promptfoo" / "guardrail_slot"))
from proxy import load_dotenv  # noqa: E402
import extra_headers  # noqa: E402  (proxy put providers/shim on sys.path)

_DOTENV: dict[str, str] = {}
load_dotenv(_DOTENV)


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def dotenv(key: str) -> str:
    return os.environ.get(key) or _DOTENV.get(key, "")


def header_names() -> dict:
    """{base URL: [header names]} from EXTRA_HEADERS, or {"error": ...} if it can't apply."""
    try:
        table = extra_headers.parse(dotenv("EXTRA_HEADERS"))
        missing = extra_headers.missing_vars(table, {**_DOTENV, **os.environ})
    except ValueError as e:
        return {"error": str(e)}
    return {"error": f"unset variable(s): {', '.join(missing)}"} if missing else extra_headers.names(table)


def sh(*cmd: str) -> str:
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True).stdout.strip()


def _check_headers() -> None:
    # A lab row's shim only warns on a bad table, so the run would go out without the
    # header (possibly on the wrong model): refuse the config here instead.
    err = header_names().get("error")
    if err:
        sys.exit(f"EXTRA_HEADERS: {err}")


def _promptfoo_version() -> str:
    out = sh("promptfoo", "--version")
    return out.splitlines()[-1] if out else "unknown"


def _start(run_dir: Path, run_id: str, config: str, concurrency: str) -> dict:
    jobs = int(concurrency)
    run_dir.mkdir(parents=True, exist_ok=True)
    return {
        "run_id": run_id, "config": config, "started_at": now(), "finished_at": None,
        "status": "running",
        "git_commit": sh("git", "rev-parse", "HEAD"),
        "git_dirty": bool(sh("git", "status", "--porcelain", "--untracked-files=no")),
        "promptfoo_version": _promptfoo_version(),
        "carrier_model": dotenv("MODEL"),
        "provider_pin": dotenv("OPENROUTER_PROVIDER_ONLY") or None,
        "extra_headers": header_names(),
        "guardrail_id": os.environ.get("GUARDRAIL_ID", "none"),
        "repeat": int(os.environ.get("REPEAT", "1")),
        "filter": os.environ.get("FILTER") or None,
        "concurrency": jobs,
    }


def _finish(run_dir: Path, code: str) -> dict:
    meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    meta["finished_at"] = now()
    meta["exit_code"] = int(code)
    complete = meta["exit_code"] in (0, 100) and (run_dir / f"{meta['config']}.json").exists()
    meta["status"] = "complete" if complete else "aborted"
    return meta


def main() -> None:
    action, *args = sys.argv[1:]
    if action == "check-headers":
        return _check_headers()
    run_dir = Path(args[0])
    meta = _start(run_dir, *args[1:]) if action == "start" else _finish(run_dir, *args[1:])
    (run_dir / "run.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

if __name__ == "__main__":
    main()
