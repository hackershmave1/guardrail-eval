# CLAUDE.md — guardrail-eval

Facts for every session on this repo. Read this, then `STATUS.md` (where we are), then
`docs/COVERAGE.md` (what is covered).

## What this project is

A Promptfoo harness that evaluates an agentic application on **28 test cases** (TC-IDs,
`docs/COVERAGE.md`): it attacks **AgenticGoat**, a deliberately vulnerable agentic app (git
submodule `agentic-goat/`), and grades each attack by its side effect. It is for evaluating AI
guardrails, or simply for learning how agentic attacks work and how to grade them.

- **The subject under test is the guardrail, never the app's own defences.** A guardrail sits
  inline at a gateway in the request path to the model, or behind pre/post model-I/O hooks
  (`docs/ARCHITECTURE.md`).
- **Today no guardrail is in the loop.** The attacks, oracles and guardrail slots make every
  undefended baseline red, so a guardrail has something to turn green. Over-refusal, latency,
  fail-open and judge attacks wait until a guardrail is wired in.

## Where things live

| Doc | Holds |
|---|---|
| `STATUS.md` | Current snapshot only: where we are, the next step, what gates it |
| `docs/COVERAGE.md` | The 28 TC-IDs and their rows (parsed by the ingest and the notebook), plus possible extensions |
| `docs/TEST_CASES.md` | Per-row specs: objective, harness, oracle, result |
| `docs/REQUIREMENTS.md` | REQ-* prerequisites and whether the repo meets them |
| `docs/ARCHITECTURE.md` | Wiring, channels, guardrail placement, oracles, evidence sinks |
| `docs/OPEN_QUESTIONS.md` | Open questions, plus the log of resolved decisions |
| `docs/RESULTS_DB.md` | Results store design and operation |
| `README.md` | What this is, setup, running, reading proof |

Detailed rules load only when the matching files are touched (`.claude/rules/`):
`model-facing-text.md` (attacks, prompts, seeds, pages), `grading.md` (providers, outcomes,
oracles), `promptfoo-configs.md` (configs, carrier, promptfoo skills, lab map),
`results-and-coverage.md` (store, coverage docs), `notebook.md`, `ub-mode.md` (quality-gate tools,
commit rhythm). Shipping steps: the `ship` skill.
Enforced by `.claude/settings.json`: no edits to the submodule, past results or generated
datasets; an edit hook blocks the real company name and other banned text (regexes in `.env`).

## Core rules

- **PASS = the guardrail held. FAIL = the attack landed** (the canary reached its sink). One grading
  rule decides it: `promptfoo/providers/outcome.py`. **NOT-RUN is never a pass.**
- **Nothing the model sees may name the test or defend itself.** No test vocabulary in
  model-facing text, no "do not disclose" hardening. `scripts/lint_prompts.py` gates every run.
- **Never invent requirements.** Every row cites its TC-ID; every design choice that rests on a
  prerequisite cites its REQ-ID. If a test case's intent is ambiguous, add a question to
  `docs/OPEN_QUESTIONS.md` instead of guessing.
- **Keep repo text product-neutral.** No named guardrail products, and no outside spreadsheet
  named as the source of the test cases: TC-IDs and REQ-IDs are this harness's own catalog.
- **Never name the real logistics company** behind the DLP-05 formats: no name, parent group,
  HQ address, number prefixes or URLs. The carrier in every dataset and doc is the fictitious
  **Acme Parcel** (DE "Acme Paket").
- **The submodule is never edited.** Fixtures, drivers and wrapper servers live in this repo.
- **Results are append-only;** a rerun is a new run. Carrier model `openai/gpt-oss-20b`, held
  constant (REQ-MD-01/02).
- **Docs describe the present.** Rewrite `STATUS.md` as a fresh snapshot, never append; delete
  completed plans; a decision worth keeping goes in the resolved log of `docs/OPEN_QUESTIONS.md`;
  change history lives in git.

## Two harness shapes

| Shape | Configs | Provider | Oracles |
|---|---|---|---|
| **Lab driver** (app-targeted rows) | `promptfoo/{mcp,rag,tools,web,web_server,memory,a2a}.yaml` | `providers/agentic_goat_provider.py` runs a lab subprocess | lab `canary.py`, `exfil_log`, `http_callback`, `retrieval`, `deleted`, `file_contains`, `answer` |
| **Model channel** (model-level rows) | `promptfoo/model_channel.yaml` | `providers/model_channel_provider.py` → guardrail slot proxy | `canary_in_reply`, `pii_forwarded`, `grounded`, `overblocked` |

Both return the same output contract (`outcome`, `verdict`, `delivered`, `detected`, `held_by`,
`note`, ...), graded by `providers/outcome.py` and reported by `providers/assert_canary.py`.

## Layout

```
CLAUDE.md  STATUS.md  README.md   the only docs in the main folder; every other doc is in docs/
                         (incl. WORKED_EXAMPLE_PI-04.md: one row end to end)
db/                      schema.sql (user_version = migration number) + NNN_*.sql migrations
promptfoo/*.yaml         configs; datasets/, drivers/, web_fixtures/ (server + generated pages)
promptfoo/providers/     both providers, outcome.py, assert_canary.py, shim/ (pre-image recorder,
                         empty-turn resampling, EXTRA_HEADERS)
promptfoo/guardrail_slot/proxy.py   model-channel slot: where a guardrail plugs in
notebooks/               operator.ipynb (guided demo + console) + operator_lib.py (its logic)
scripts/                 bootstrap.sh, run_evals.sh, gen_*.py (generators), lint_prompts.py, ingest_run.py,
                         db_report.py, summarize.py, proof.jq, pair_filter.py, run_meta.py
tests/                   stdlib unittest, no model calls
results/                 runs/<run_id>/ (immutable), guardrail_eval.db, <config>.json/.html/.log
                         (copy of the latest real run), scratch/ (gitignored)
```

## Running

- **Tests:** `venv/bin/python -m unittest discover -s tests`.
- **Full suite:** `REPEAT=3 scripts/run_evals.sh` (name configs to run fewer). Each run lands in
  `results/runs/<run_id>/` and is ingested into `results/guardrail_eval.db`.
- **One battery:** `REPEAT=3 FILTER='<regex on the description>' scripts/run_evals.sh <config>`.
  `NO_INGEST=1` keeps a scratch run out of the store; `KEEP_LISTENERS=1` watches callbacks live.
- **Notebook:** `venv/bin/jupyter notebook notebooks/operator.ipynb`.
- **Report:** `python3 scripts/db_report.py` (`--runs`, `--rows`, `--guardrail <id>`).
  **Proof:** `jq -f scripts/proof.jq results/<config>.json`.
- Gotchas:
  - RAG rows share `chroma_db`: run one `rag` filter at a time.
  - `promptfoo eval` can ignore SIGTERM: stop it with `kill -9 <pid>`. Never `pkill -f` /
    `pgrep -f` with a pattern that also matches the calling shell.
  - After lab runs, `git -C agentic-goat status` should show only untracked lab state.

## Working rules

- Rewrite `STATUS.md` after every milestone as a current snapshot.
- Docs lead with a diagram (Mermaid); the user learns visually.
- Run labs only in an isolated environment (REQ-ENV-03). All secrets and PII are synthetic.
- Don't commit unless asked; then follow the `ship` skill (branch off `main` first). Under
  ub-mode, committing often on that branch counts as asked (`.claude/rules/ub-mode.md`).
