#!/usr/bin/env bash
# Run the mcp/rag/tools configs and print one line per row:
#   <config> <PASS|FAIL|NOT-RUN> <description> :: <oracle reason>
# Raw promptfoo JSON lands in results/<config>.json for per-row traces.
# Usage: [REPEAT=n] scripts/run_evals.sh [mcp rag tools]
# Agent rows are stochastic (same model can follow or ignore an injection), so
# REPEAT>1 runs each row n times and the line reports how many attempts landed.
set -uo pipefail
cd "$(dirname "$0")/.."
. venv/bin/activate
export PROMPTFOO_PYTHON="$PWD/venv/bin/python" PROMPTFOO_DISABLE_TELEMETRY=1
mkdir -p results
# Every lab's exfil listener binds :9999; never let a stale one (from a manual
# run or an earlier config) catch another lab's exfil. The provider starts the
# right one per row.
stop_listeners() { pkill -f 'labs/.*/exfil_server.py' 2>/dev/null; pkill -f '^[^ ]*python[0-9.]* exfil_server.py' 2>/dev/null; true; }
stop_listeners; trap stop_listeners EXIT
configs=("${@:-mcp rag tools}")
for c in ${configs[@]}; do
  promptfoo eval -c "promptfoo/$c.yaml" --no-cache --no-progress-bar \
    -j 1 --repeat "${REPEAT:-1}" -o "results/$c.json" "results/$c.html" >"results/$c.log" 2>&1
  python3 - "$c" <<'PY'
import json, sys
from collections import OrderedDict
c = sys.argv[1]
rows = OrderedDict()
for r in json.load(open(f"results/{c}.json"))["results"]["results"]:
    g = r.get("gradingResult") or {}
    reason = g.get("reason", r.get("error", ""))
    state = "NOT-RUN" if "did not run" in reason else ("PASS" if g.get("pass") else "FAIL")
    rows.setdefault(r["testCase"].get("description", ""), []).append((state, reason))
for desc, runs in rows.items():
    states = [s for s, _ in runs]
    ran = [s for s in states if s != "NOT-RUN"]
    # Any landed attempt = vulnerable (a guardrail must hold every time).
    state = "FAIL" if "FAIL" in ran else ("PASS" if ran else "NOT-RUN")
    tally = f"{states.count('FAIL')}/{len(ran)} landed" + (f", {len(states)-len(ran)} not-run" if len(ran) < len(states) else "")
    reason = next((rs for s, rs in runs if s == state), runs[0][1])
    print(f"{c:5} {state:7} [{tally}] {desc[:70]} :: {reason[:120]}")
PY
done
