#!/usr/bin/env bash
# Run the eval configs and print one line per row:
#   <config> <PASS|FAIL|INCONCLUSIVE|NOT-RUN> [k/n landed, ...] <description> :: <outcome: reason>
# Each run is kept in results/runs/<run_id>/ (run.json context + promptfoo JSON/HTML/log)
# and ingested into results/guardrail_eval.db (docs/RESULTS_DB.md); results/<config>.*
# is a copy of the latest real run for per-row traces (not refreshed when RUNS_DIR is set).
# Usage: [REPEAT=n] [FILTER=regex] [KEEP_LISTENERS=1] scripts/run_evals.sh [mcp rag tools web web_server model_channel]
# FILTER runs only rows whose description matches (promptfoo --filter-pattern),
# widened to every condition of those rows (scripts/pair_filter.py) so a twin and
# its undefended baseline always share a run_id.
# JOBS sets model_channel concurrency (default 4). GUARDRAIL_ID names the guardrail under
# test in the results DB (default none). NO_INGEST=1 skips the DB ingest; RUNS_DIR and
# RESULTS_DB override where runs and the DB go (tests, notebook scratch runs). With
# RUNS_DIR set, results/<config>.* are not refreshed.
# Agent rows are stochastic (same model can follow or ignore an injection), so
# REPEAT>1 runs each row n times and the line reports how many attempts landed.
# model_channel needs the guardrail slot (promptfoo/guardrail_slot/proxy.py) on :8787;
# web_server needs the PI-03 web server (promptfoo/web_fixtures/server.py) on :8088.
# This script starts/stops each for its config. A server already on :8088 is reused only if it
# logs to results/web_server/. Exits 1 if any config was refused or wrote no results (`!!` lines).
set -uo pipefail
cd "$(dirname "$0")/.."
. venv/bin/activate
export PROMPTFOO_PYTHON="$PWD/venv/bin/python" PROMPTFOO_DISABLE_TELEMETRY=1
mkdir -p results
# Every lab's exfil listener binds :9999; never let a stale one (from a manual
# run or an earlier config) catch another lab's exfil. The provider starts the
# right one per row. KEEP_LISTENERS=1 leaves a listener you started yourself (to
# watch callbacks live) alone; the provider still replaces one from another lab.
stop_listeners() {
  [ "${KEEP_LISTENERS:-0}" = 1 ] && return 0
  pkill -f 'labs/.*/exfil_server.py' 2>/dev/null; pkill -f '^[^ ]*python[0-9.]* exfil_server.py' 2>/dev/null; true
}
SLOT_PID=""
stop_slot() { [ -n "$SLOT_PID" ] && kill "$SLOT_PID" 2>/dev/null; SLOT_PID=""; true; }
start_slot() {
  python promptfoo/guardrail_slot/proxy.py >results/slot.out 2>&1 &
  SLOT_PID=$!
  for _ in $(seq 40); do curl -sf localhost:8787/health >/dev/null && return 0; sleep 0.25; done
  # Kill the half-started process now so `|| continue` doesn't leave :8787 bound
  # for the rest of the run (the EXIT trap is only a backstop).
  echo "guardrail slot did not come up (see results/slot.out)" >&2; stop_slot; return 1
}
WEBSRV_PID=""
stop_websrv() { [ -n "$WEBSRV_PID" ] && kill "$WEBSRV_PID" 2>/dev/null; WEBSRV_PID=""; true; }
start_websrv() {
  # Already up (you started it in a terminal to watch hits live): use it, but only if it
  # logs where the http_callback oracle reads. One that logs elsewhere would swallow a
  # landed callback (false PASS), so a unique probe must show up in our hits.jsonl.
  if curl -sf localhost:8088/health >/dev/null; then
    probe="probe-$(date +%s%N)"
    curl -sf "localhost:8088/health?$probe" >/dev/null
    grep -qs "$probe" results/web_server/hits.jsonl && { echo "web server already on :8088, reusing it" >&2; return 0; }
    echo "a server on :8088 does not log to results/web_server/; stop it or start it with WEB_FIXTURES_LOG_DIR=$PWD/results/web_server" >&2
    return 1
  fi
  WEB_FIXTURES_LOG_DIR="$PWD/results/web_server" python promptfoo/web_fixtures/server.py >results/web_server.out 2>&1 &
  WEBSRV_PID=$!
  for _ in $(seq 40); do curl -sf localhost:8088/health >/dev/null && return 0; sleep 0.25; done
  # Kill the half-started process before `|| continue`.
  echo "web fixtures server did not come up (see results/web_server.out)" >&2; stop_websrv; return 1
}
stop_listeners; trap 'stop_listeners; stop_slot; stop_websrv' EXIT
# Exit 1 if any config was refused or produced no results (the `!!` lines say which).
rc=0
configs=("${@:-mcp rag tools web memory a2a web_server model_channel}")
for c in ${configs[@]}; do
  # Nothing the model sees may name the test (.claude/rules/model-facing-text.md): refuse to run
  # a config whose prompts, seeds or canaries carry a test ID, eval vocabulary or a report label,
  # or whose vars hold a YAML list (promptfoo would split the row).
  python scripts/lint_prompts.py "$c" >/dev/null || { python scripts/lint_prompts.py "$c" | grep ERROR >&2; echo "!! $c: lint_prompts refused the config; fix it (scripts/lint_prompts.py)" >&2; rc=1; continue; }
  # A config promptfoo can't parse would fail mid-run; check it first.
  promptfoo validate config -c "promptfoo/$c.yaml" >/dev/null 2>&1 || { promptfoo validate config -c "promptfoo/$c.yaml" >&2; echo "!! $c: promptfoo config is invalid; fix it" >&2; rc=1; continue; }
  # Headers by endpoint (EXTRA_HEADERS) must resolve, or lab rows would run without them.
  python3 scripts/run_meta.py check-headers || { echo "!! $c: EXTRA_HEADERS can't apply; fix it in .env" >&2; rc=1; continue; }
  if [ "$c" = model_channel ]; then
    : >results/slot_log.jsonl
    start_slot || { echo "!! $c: guardrail slot did not start" >&2; rc=1; continue; }
  fi
  if [ "$c" = web_server ]; then
    venv/bin/python scripts/gen_web_pages.py >/dev/null   # keep pages+rows fresh
    start_websrv || { echo "!! $c: web fixtures server unusable" >&2; rc=1; continue; }
  fi
  # Lab rows share lab state and the :9999 listener, so one at a time; model-
  # channel rows are stateless (unique trial ids), so they can run in parallel.
  jobs=1; [ "$c" = model_channel ] && jobs="${JOBS:-4}"
  # A filtered run still runs every condition of each matched row (undefended
  # baseline + twins) in the same run_id, so a twin's delta is never stale.
  filter=(); [ -n "${FILTER:-}" ] && filter=(--filter-pattern "$(python scripts/pair_filter.py "$c" "$FILTER")")
  # Every run gets its own immutable dir (docs/RESULTS_DB.md); results/<c>.* stay as
  # a copy of the latest real run so summarize.py / proof.jq work unchanged.
  run_id="$(date -u +%Y%m%dT%H%M%SZ)-$c"; run_dir="${RUNS_DIR:-results/runs}/$run_id"
  REPEAT="${REPEAT:-1}" python3 scripts/run_meta.py start "$run_dir" "$run_id" "$c" "$jobs"
  promptfoo eval -c "promptfoo/$c.yaml" --no-cache --no-share --no-progress-bar \
    -j "$jobs" --repeat "${REPEAT:-1}" "${filter[@]}" -o "$run_dir/$c.json" "$run_dir/$c.html" >"$run_dir/$c.log" 2>&1
  code=$?
  [ "$c" = model_channel ] && stop_slot && cp results/slot_log.jsonl "$run_dir/"
  [ "$c" = web_server ] && stop_websrv
  python3 scripts/run_meta.py finish "$run_dir" "$code"
  # Only refresh results/<c>.* and summarise when THIS run actually produced
  # output. If promptfoo crashed before writing the JSON (exit $code), copying
  # and summarising would silently reprint a PRIOR run's PASS/FAIL.
  # A scratch run (RUNS_DIR overridden, e.g. from notebooks/operator.ipynb) leaves
  # results/<c>.* as the last real run, so they keep agreeing with the store.
  if [ -f "$run_dir/$c.json" ]; then
    [ -n "${RUNS_DIR:-}" ] || cp "$run_dir/$c".* results/ 2>/dev/null
    [ "${NO_INGEST:-0}" = 1 ] || python3 scripts/ingest_run.py "$run_dir" --db "${RESULTS_DB:-results/guardrail_eval.db}"
    python3 scripts/summarize.py "$c" "$run_dir/$c.json"
  else
    echo "!! $c: promptfoo wrote no results (exit $code) — see $run_dir/$c.log; results/$c.* and summary left unchanged" >&2
    rc=1
  fi
done
exit "$rc"
