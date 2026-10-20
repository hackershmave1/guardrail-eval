#!/usr/bin/env bash
# One-shot environment setup for the guardrail-eval harness (idempotent).
# Needs outbound access to: openrouter.ai (model), huggingface.co + its CDN
# (Lab 04 embedding model), plus the npm/PyPI registries.
set -euo pipefail
cd "$(dirname "$0")/.."

git submodule update --init
[ -d venv ] || python3 -m venv venv
. venv/bin/activate
pip install -q -r requirements.txt
command -v promptfoo >/dev/null || npm install -g promptfoo
( cd agentic-goat/labs/01-mcp-tool-poisoning && npm install --silent )

# Seed per-lab canaries/corpora.
( cd agentic-goat/labs/01-mcp-tool-poisoning && python3 make_canary.py )
( cd agentic-goat/labs/06-ASI02-cross-server-mcp-poisoning && python3 seed_notes.py --reset --clear-exfil )
( cd agentic-goat/labs/04-rag-security && python vulnerable_rag.py seed )
