#!/usr/bin/env bash
# Run the mcp/rag/tools configs and print one line per row:
#   <config> <PASS|FAIL|NOT-RUN> <description> :: <oracle reason>
# Raw promptfoo JSON lands in results/<config>.json for per-row traces.
# Usage: scripts/run_evals.sh [mcp rag tools]
set -uo pipefail
cd "$(dirname "$0")/.."
. venv/bin/activate
export PROMPTFOO_PYTHON="$PWD/venv/bin/python" PROMPTFOO_DISABLE_TELEMETRY=1
mkdir -p results
configs=("${@:-mcp rag tools}")
for c in ${configs[@]}; do
  promptfoo eval -c "promptfoo/$c.yaml" --no-cache --no-progress-bar \
    -j 1 -o "results/$c.json" >"results/$c.log" 2>&1
  python3 - "$c" <<'PY'
import json, sys
c = sys.argv[1]
for r in json.load(open(f"results/{c}.json"))["results"]["results"]:
    g = r.get("gradingResult") or {}
    reason = g.get("reason", r.get("error", ""))
    state = "NOT-RUN" if "did not run" in reason else ("PASS" if g.get("pass") else "FAIL")
    print(f"{c:5} {state:7} {r['testCase'].get('description','')[:70]} :: {reason[:120]}")
PY
done
