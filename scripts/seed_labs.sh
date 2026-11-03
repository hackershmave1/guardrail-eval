#!/usr/bin/env bash
# Seed the per-lab canaries and corpora (idempotent). Run by scripts/bootstrap.sh on a host and by
# docker/entrypoint.sh at every container start, so each container begins from fresh lab state.
# Lab 04 only ingests here; a placeholder MODEL keeps its import from probing a model endpoint.
set -euo pipefail
cd "$(dirname "$0")/../agentic-goat/labs"
( cd 01-mcp-tool-poisoning && python3 make_canary.py )
( cd 06-ASI02-cross-server-mcp-poisoning && python3 seed_notes.py --reset --clear-exfil )
( cd 04-rag-security && MODEL="${MODEL:-seed-only}" python vulnerable_rag.py seed )
