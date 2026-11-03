#!/usr/bin/env bash
# Container entrypoint (Dockerfile). Every command but `help`, `test` and `shell` seeds the labs
# first, so each container starts from fresh lab state.
#   run [configs...]   scripts/run_evals.sh (REPEAT, FILTER, NO_INGEST, ... pass through as env)
#   notebook           the operator notebook on :8888
#   test               the unit tests (no model calls)
#   report [args...]   scripts/db_report.py
#   shell | <cmd...>   a shell, or any command
set -euo pipefail
cd /app

need_env() {
  [ -f .env ] || [ -n "${LLM_BASE_URL:-}" ] && return 0
  echo "no model endpoint: mount your .env at /app/.env (compose.yaml does) or set LLM_BASE_URL" >&2
  exit 2
}
need_results() {
  mkdir -p results 2>/dev/null && [ -w results ] && return 0
  echo "results/ is not writable by uid $(id -u): create it on the host first (mkdir -p results)" >&2
  echo "or rebuild with your ids: UID=\$(id -u) GID=\$(id -g) docker compose build" >&2
  exit 2
}
seed() {
  scripts/seed_labs.sh >results/seed.log 2>&1 && return 0
  echo "lab seeding failed (results/seed.log):" >&2; tail -20 results/seed.log >&2; exit 1
}

cmd="${1:-help}"; [ $# -gt 0 ] && shift
case "$cmd" in
  run)      need_env; need_results; seed; exec scripts/run_evals.sh "$@" ;;
  notebook) need_env; need_results; seed
            exec jupyter notebook notebooks/operator.ipynb --ip=0.0.0.0 --port=8888 --no-browser "$@" ;;
  test)     exec python -m unittest discover -s tests "$@" ;;
  report)   exec python scripts/db_report.py "$@" ;;
  shell)    exec bash "$@" ;;
  help|-h|--help) sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//' ;;
  *)        exec "$cmd" "$@" ;;
esac
