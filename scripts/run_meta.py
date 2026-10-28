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

ROOT = Path(__file__).resolve().parents[1]

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


def main() -> None:
    if sys.argv[1] == "check-headers":
        # A lab row's shim only warns on a bad table, so the run would go out without the
        # header (possibly on the wrong model): refuse the config here instead.
        err = header_names().get("error")
        if err:
            sys.exit(f"EXTRA_HEADERS: {err}")
        return
    action, run_dir = sys.argv[1], Path(sys.argv[2])
    meta_path = run_dir / "run.json"
    if action == "start":
        run_id, config, concurrency = sys.argv[3], sys.argv[4], int(sys.argv[5])
        run_dir.mkdir(parents=True, exist_ok=True)
        pf_ver = sh("promptfoo", "--version")
        meta = {
            "run_id": run_id, "config": config, "started_at": now(), "finished_at": None,
            "status": "running",
            "git_commit": sh("git", "rev-parse", "HEAD"),
            "git_dirty": bool(sh("git", "status", "--porcelain", "--untracked-files=no")),
            "promptfoo_version": pf_ver.splitlines()[-1] if pf_ver else "unknown",
            "carrier_model": dotenv("MODEL"),
            "provider_pin": dotenv("OPENROUTER_PROVIDER_ONLY") or None,
            "extra_headers": header_names(),
            "guardrail_id": os.environ.get("GUARDRAIL_ID", "none"),
            "repeat": int(os.environ.get("REPEAT", "1")),
            "filter": os.environ.get("FILTER") or None,
            "concurrency": concurrency,
        }
    else:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        code = int(sys.argv[3])
        meta["finished_at"] = now()
        meta["exit_code"] = code
        meta["status"] = "complete" if code in (0, 100) and (run_dir / f"{meta['config']}.json").exists() else "aborted"
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
