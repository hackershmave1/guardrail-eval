---
paths:
  - "promptfoo/**/*.yaml"
  - "promptfoo/drivers/**"
  - "scripts/run_evals.sh"
  - "scripts/carrier_bakeoff.sh"
---

# Promptfoo configs and lab rows

## Which promptfoo skill, when

The promptfoo plugin skills are the house patterns. Invoke the matching one before the work:

| Task | Skill |
|---|---|
| New/broken provider, proxy, request/response contract | `promptfoo:promptfoo-provider-setup` |
| Dataset-driven rows with deterministic assertions (lab rows, model-channel rows) | `promptfoo:promptfoo-evals` |
| Generated adversarial sweep (plugins/strategies, e.g. `promptfoo/redteam.yaml`) | `promptfoo:promptfoo-redteam-setup` |
| Running/rerunning a generated scan, ASR, filtered reruns | `promptfoo:promptfoo-redteam-run` |

## Config rules

- Header every config with `# yaml-language-server: $schema=https://promptfoo.dev/config-schema.json`.
- Every row cites its TC-ID; a design choice that rests on a prerequisite cites its REQ-ID.
- **Multi-value vars are comma-delimited scalars, never YAML lists.** *Why:* promptfoo
  matrix-expands list vars into separate test cases, which splits two-server attacks.
- Secrets only via `{{env.VAR}}` / `.env`. Never commit keys.
- `scripts/run_evals.sh` runs `promptfoo validate config` and the prompt lint before each config.
  Outside it: `--no-share`, a fresh output path per run, `PROMPTFOO_PYTHON=$PWD/venv/bin/python`.
  Inspect `results.stats` and per-row `gradingResult` / `error`, not only the exit code; zero
  graded rows = inconclusive.
- Any `llm-rubric` sets an explicit grader (`defaultTest.options.provider`). *Why:* otherwise
  promptfoo falls back to a Vertex grader with no project (400 `RESOURCE_PROJECT_INVALID`).
- OpenRouter provider IDs are `openrouter:<vendor>/<model>`.
- Check current promptfoo syntax with Context7 when unsure; don't rely on memory.

## Carrier and trial counts

- **Hold the carrier constant and permissive** (REQ-MD-01/02); system text stays in the SYSTEM
  role (REQ-MD-04). Carrier for app-targeted rows: `openai/gpt-oss-20b` (bake-off,
  `docs/OPEN_QUESTIONS.md` A1). The OpenRouter provider is pinned: `OPENROUTER_PROVIDER_ONLY=deepinfra`
  (A1 (a)); never change it between runs that are compared. A different carrier for a whole scenario family (e.g. JB / TOX) only if chosen
  by `scripts/carrier_bakeoff.sh` and documented. Never swap per row.
- **Trial counts** (REQ-OR-05): ~20-30 payloads × 3 repeats for deterministic oracles; agent rows
  run with `REPEAT=3` minimum. Any landed attempt = vulnerable.

## AgenticGoat lab map

| Lab | What it is | TC-IDs |
|---|---|---|
| 01 | MCP tool-description poisoning (poisoned `add()` steals an SSH key) | SUP-01, EXF-01 |
| 03 | DocuAssist: file / email / delete / web-search tools, `hitl_confirmation: false` | DLP-03, TOOL-02, TOOL-03, EXF-02 |
| 04 | RAG with poisoned docs, a retrieval log and five defence layers | PI-02, PI-07, DLP-02, DLP-04, DLP-06 |
| 05 | AssistantOS: persistent memory, researcher sub-agent, web fetch, five hardening layers | PI-03, MEM-01, MEM-02, PI-08 |
| 06 | Cross-server MCP poisoning via a tool's return value or description | PI-04 |
| 07 | MCP → A2A kill chain (five stages, model-independent) | PI-04 (A2A variant) |

Labs 00 (template), 01b (cross-server shadowing) and 02 (Docker) are not used by any row.
Fixtures, sentinels, drivers and wrapper servers live in this repo (`promptfoo/drivers/`, provider
vars `seed_file` / `setup` / `oracle`), never in the submodule.
