#!/usr/bin/env bash
# Carrier bake-off (REQ-MD-01/02): which model obeys injections AND can drive tools?
# Runs the same small probe set per candidate model and prints one comparison table.
#
#   scripts/carrier_bakeoff.sh openai/gpt-oss-20b mistralai/mistral-nemo ...
#
# Probes (all undefended, no vendor guardrail):
#   MCP-PI04 x2  multi-step tool use across 2 MCP servers + injected tool RESULT
#   WEB-PI03 x2  injected web page the agent fetches itself (Lab 05, one hop)
#   WEB-PI03b x2 same page relayed by a researcher sub-agent (Lab 05 Attack 3, two hops)
#   TOOL02   x1  file/email tools + doc-borne injection (Lab 03)
#   MC-PI01  x1  25 direct-injection payloads through the guardrail slot
# Reading the table: "landed" = the model obeyed; NOT-RUN on a lab probe usually means
# the model could not drive the tools (the attack path never ran), not that it resisted.
# Results: results/bakeoff/<model-slug>/<probe>.json
set -uo pipefail
cd "$(dirname "$0")/.."
. venv/bin/activate
export PROMPTFOO_PYTHON="$PWD/venv/bin/python" PROMPTFOO_DISABLE_TELEMETRY=1
stop_listeners() { pkill -f 'labs/.*/exfil_server.py' 2>/dev/null; pkill -f '^[^ ]*python[0-9.]* exfil_server.py' 2>/dev/null; true; }
SLOT_PID=""
stop_slot() { [ -n "$SLOT_PID" ] && kill "$SLOT_PID" 2>/dev/null; SLOT_PID=""; true; }
trap 'stop_listeners; stop_slot' EXIT

run() {  # run <config> <filter> <repeat> <out> [jobs]
  promptfoo eval -c "promptfoo/$1.yaml" --filter-pattern "$2" --repeat "$3" -j "${5:-1}" \
    --no-cache --no-share --no-progress-bar -o "$4" >"${4%.json}.log" 2>&1
}

for model in "$@"; do
  slug=$(echo "$model" | tr '/:' '__')
  out="results/bakeoff/$slug"; mkdir -p "$out"
  export MODEL="$model"
  echo "== $model" >&2
  stop_listeners
  run mcp   'MCP-PI04 undefended' 2 "$out/pi04.json"
  run web   'WEB-PI03 undefended' 2 "$out/pi03.json"
  run web   'WEB-PI03b undefended' 2 "$out/pi03b.json"
  run tools 'TOOLS-TOOL02'        1 "$out/tool02.json"
  SLOT_LOG="$PWD/$out/slot_log.jsonl" python promptfoo/guardrail_slot/proxy.py >"$out/slot.out" 2>&1 &
  SLOT_PID=$!
  for _ in $(seq 40); do curl -sf localhost:8787/health >/dev/null && break; sleep 0.25; done
  SLOT_LOG="$PWD/$out/slot_log.jsonl" run model_channel '^MC-PI01' 1 "$out/pi01.json" 4
  stop_slot
done

python3 - "$@" <<'PY'
import json, sys, os
def tally(path):
    if not os.path.exists(path):
        return "missing"
    rs = json.load(open(path))["results"]["results"]
    landed = notrun = 0
    for r in rs:
        reason = (r.get("gradingResult") or {}).get("reason", r.get("error", "")) or ""
        if "did not run" in reason:
            notrun += 1
        elif not (r.get("gradingResult") or {}).get("pass"):
            landed += 1
    ran = len(rs) - notrun
    return f"{landed}/{ran}" + (f" ({notrun}NR)" if notrun else "")
print(f"\n{'model':48} {'PI-04 tools+inj':16} {'PI-03 web':12} {'PI-03b relay':13} {'TOOL-02':10} {'PI-01 direct':12}")
for m in sys.argv[1:]:
    d = "results/bakeoff/" + m.replace("/", "_").replace(":", "_")
    print(f"{m:48} {tally(d+'/pi04.json'):16} {tally(d+'/pi03.json'):12} {tally(d+'/pi03b.json'):13} {tally(d+'/tool02.json'):10} {tally(d+'/pi01.json'):12}")
PY
