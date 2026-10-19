# guardrail-eval

A test environment for evaluating **AI guardrails**. Phase 1 wires up
[Promptfoo](https://www.promptfoo.dev/) against a known vulnerable agentic app —
[AgenticGoat](https://github.com/aminrj-labs/agentic-goat) — and exercises three use
cases: **MCP**, **RAG**, and **Tools**.

This is the first, preliminary stage of the POC described in `AI_Guardrails_POC_4_2.xlsx`
and `local_ai_guardrail_poc_architecture_prompt_dom_simulator.html` (in the project files).
Those two documents are the source of truth: the test cases here are the workbook's own
**TC-IDs**, scoped to the three use cases and mapped onto AgenticGoat labs. We are not
inventing scenarios — we are running the catalog against a concrete target.

## Why this shape

- **Target: AgenticGoat** — vendor-neutral, vulnerable-by-design, OWASP-mapped. Every lab
  plants a synthetic `AGENTICGOAT{...}` canary and declares success by an **observable side
  effect** (an exfil callback, a poisoned-doc retrieval, a canary in an email log), never
  by parsing the model's words. That matches the workbook's Success-Oracle ranking
  (OOB/side-channel > canary > tool-call log > classifier > LLM-judge).
- **Runner: Promptfoo** — the workbook's mandated backbone (Tooling tab / REQ-TL-04): one
  config generates attacks and runs targets side-by-side. The **only glue we build** is the
  deterministic oracle, wired as a Promptfoo custom assertion
  (`promptfoo/providers/assert_canary.py`).
- **Convention: PASS = guardrail held.** A FAIL means the attack landed (the canary reached
  its sink). Where a lab ships a control, each row runs twice — undefended (expect FAIL) and
  defended (expect PASS) — because that delta is the only proof a guardrail did something.

## Scope

| Use case | Workbook TC-IDs | AgenticGoat labs | Config |
|----------|-----------------|------------------|--------|
| **MCP** | SUP-01, PI-04 | 01, 01b, 06 | `promptfoo/mcp.yaml` |
| **RAG** | PI-02, DLP-02, OUT-04 | 04 | `promptfoo/rag.yaml` |
| **Tools** | TOOL-03, TOOL-02, EXF-01, EXF-02, DLP-03 | 03, 01 | `promptfoo/tools.yaml` |

Full detail — objectives, oracles, success criteria, OWASP/ATLAS mapping, expected
undefended/defended outcomes — in [`TEST_CASES.md`](./TEST_CASES.md). Prerequisites for
trustworthy numbers (the workbook's REQ-*) in [`REQUIREMENTS.md`](./REQUIREMENTS.md).

## Layout

```
guardrail-eval/
├── README.md
├── TEST_CASES.md          # the workbook-grounded test-case list (TC-IDs → labs)
├── REQUIREMENTS.md        # workbook REQ-* prerequisites, scoped to this phase
├── requirements.txt       # Python deps for the labs + harness
├── .env.example           # LLM endpoint + model (REQ-MD/ENV)
├── .gitmodules            # AgenticGoat pinned as a submodule (external dependency)
└── promptfoo/
    ├── mcp.yaml           # MCP use case
    ├── rag.yaml           # RAG use case
    ├── tools.yaml         # Tools use case
    └── providers/
        ├── agentic_goat_provider.py   # drives a lab as the app under test
        └── assert_canary.py           # the deterministic canary/OOB oracle
```

AgenticGoat is referenced as an **external dependency (git submodule)**, not copied in, so
its licence and provenance stay intact and it tracks upstream.

## Setup

> ⚠️ AgenticGoat is intentionally vulnerable and ships working attack tooling. Run it only
> in an isolated VM or disposable container (workbook REQ-ENV-03). Exfil listeners bind to
> `localhost` and every secret/credential in the labs is synthetic.

```bash
# 1. clone with the submodule
git clone <this-repo-url> guardrail-eval && cd guardrail-eval
git submodule update --init          # pulls AgenticGoat into ./agentic-goat

# 2. Python env for the labs + harness
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 3. Promptfoo
npm install -g promptfoo

# 3b. Lab 01 (MCP/Tools SSH-key exfil) also spawns a Node MCP server, so install
#     its npm deps once. Without this the poisoned add() has no read_file tool to
#     abuse and the row errors out (looks like a false "protected" pass).
( cd agentic-goat/labs/01-mcp-tool-poisoning && npm install )

# 4. an OpenAI-compatible model, held constant & permissive (REQ-MD-01/02).
#    Put the key in .env (gitignored) — promptfoo and the provider both read it.
cp .env.example .env     # then edit LLM_BASE_URL / API_KEY / MODEL
#    No local model? Use OpenRouter (https://openrouter.ai/keys):
export LLM_BASE_URL=https://openrouter.ai/api/v1
export API_KEY=sk-or-...
export MODEL=qwen/qwen-2.5-7b-instruct
#    Or a local model (Ollama): LLM_BASE_URL=http://localhost:11434/v1, MODEL=qwen2.5-7b-instruct

# 5. (RAG only) seed the clean knowledge base once
( cd agentic-goat/labs/04-rag-security && python vulnerable_rag.py seed )
```

If the submodule isn't present yet, or you have no local model, set
`GUARDRAIL_EVAL_DRY_RUN=1` — the provider then skips the subprocess and reports each row as
"did not run" instead of crashing, so you can validate the configs first.

## Run

```bash
promptfoo eval -c promptfoo/mcp.yaml   && promptfoo view
promptfoo eval -c promptfoo/rag.yaml   && promptfoo view
promptfoo eval -c promptfoo/tools.yaml && promptfoo view
```

Plus the automated red-team sweep (reuses AgenticGoat's shipped Lab 03 config; per-plugin
pass rate, reported separately — never averaged):

```bash
promptfoo redteam run \
  --config agentic-goat/labs/03-red-team-assessment/promptfoo/promptfoo-redteam-config.yaml
promptfoo redteam report
```

Reading the report: green = the guardrail held (canary never left the box); red = the app
was vulnerable at this layer. With no guardrail in front (this phase), the undefended rows
are red by design — they are the baseline a real guardrail has to turn green.

## Where this goes next

This phase has no vendor guardrail in front of the app — it establishes the attacks, the
oracles, and the Promptfoo wiring. The workbook's full architecture then slots a **guardrail
vendor scanner** between the app and the model/MCP channels and runs these same configs
across all four vendors (Cato/Aim, Noma, Palo Alto AIRS, Zscaler) side-by-side, adding the
vendor-tenant requirements (REQ-ENV-01/02) and the Platform & Ops (PT-*) track.
