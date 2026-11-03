#!/usr/bin/env bash
# One-shot environment setup for the guardrail-eval harness (idempotent).
# Needs outbound access to: the model endpoint (LLM_BASE_URL), huggingface.co + its CDN
# (Lab 04 embedding model), plus the npm/PyPI registries.
set -euo pipefail
cd "$(dirname "$0")/.."

git submodule update --init
[ -d venv ] || python3 -m venv venv
. venv/bin/activate
pip install -q -r requirements.txt
# promptfoo is pinned (promptfoo/VERSION): one version grades every run that is compared.
command -v promptfoo >/dev/null || npm install -g "promptfoo@$(cat promptfoo/VERSION)"
( cd agentic-goat/labs/01-mcp-tool-poisoning && npm install --silent )

scripts/seed_labs.sh          # per-lab canaries and corpora
