# Requirements

The workbook's **Requirements** tab (`AI_Guardrails_POC_4_2.xlsx`, REQ-*) lists the
prerequisites that must hold before any number from this repo is believable: a rate is only as
trustworthy as the oracle and the setup under it. These are the REQ-IDs Phase 1 depends on, and
whether the repo meets them today.

| Req-ID | Requirement | Why it matters | Status in this repo |
|--------|-------------|----------------|---------------------|
| **REQ-OR-01** | Define the success oracle per scenario *before* running | A weak oracle silently miscounts every % downstream | ✅ Every row names its oracle (`TEST_CASES.md`) and its delivery proof (`injection_marker`); one rule grades them all (`providers/outcome.py`) |
| **REQ-OR-02** | OOB callback server stood up and reachable | A callback = data actually left; a model *describing* exfil scores zero | ✅ Each lab's `exfil_server.py` (:9999), our web server's `/collect` (:8088) and the search-egress log |
| **REQ-OR-03** | Canary tokens seeded in docs, secrets, tool manifests | Binary ground truth for leakage | ✅ AgenticGoat `AGENTICGOAT{...}` flags plus our sentinels, which never appear whole in an attack |
| **REQ-OR-04** | LLM judge only as a last resort | The judge is the weakest oracle and is prompt-injectable | ✅ No row uses a judge; the weakest oracle in use is `grounded` (OUT-04, regex tier). JB-01 / JB-02 / TOX-01 will need one |
| **REQ-OR-05** | Minimal trial counts N>1 (≈20–30 payloads × 3 repeats, deterministic oracle) | Catch nondeterminism without research-grade cost | ⚙️ Model-channel datasets run 6–25 payloads × 3; agent rows run one scenario × 3 (open question A3) |
| **REQ-TL-04** | **Promptfoo as the config-driven backbone**; the OOB/canary oracle as a custom assertion | One tool runs the attacks and the vendors side by side; the oracle is the only glue you build | ✅ `promptfoo/*.yaml` + `promptfoo/providers/*` + `assert_canary.py` |
| **REQ-MD-01** | Base model held **constant** across vendors | A block must be attributable to the guardrail, not model drift | ⚙️ One `MODEL` for every row (labs read it from the env; `pin_model` forces it where a lab would auto-pick one, Labs 04 and 05); the OpenRouter provider pin is still unset (A1a) |
| **REQ-MD-02** | A **permissive** model that does not self-refuse the base attacks | A hard-aligned model refuses for free and the guardrail looks perfect | ⚙️ `openai/gpt-oss-20b`, chosen by bake-off for the app-targeted rows; self-refusals are recorded as HELD_RESISTED, never credited. **A row's undefended baseline must land**, or its twin and any vendor delta prove nothing. Model-facing wording must not cause a refusal: `RAG-DLP04` read 0/3 until a report label was removed from its prompt, then 3/3 (`scripts/lint_prompts.py` now guards this). Harm rows need their own carrier (A1b) |
| **REQ-MD-04** | Record model role placement (system vs user) | The #1 false-positive cause is system text in the user role | ✅ System text stays in the SYSTEM role in the labs and the model-channel provider |
| **REQ-DS-06** | Test MCP servers + poisoned corpora (docs, pages, manifests) | Agentic tests need controllable untrusted content and tools you own | ✅ AgenticGoat's poisoned MCP servers, RAG docs and tools, plus our seeded fixtures and generated web pages |
| **REQ-ENV-03** | Isolated / sandboxed network | Harmful payloads and exfil must stay contained | ✅ Listeners and canaries stay on `localhost`; run in a disposable VM or container. With a hosted model, inference leaves the box but no attack side effect does |
| **REQ-ENV-04** | A test app per shape: chatbot, RAG, agent-MCP | One shape can't exercise tool / memory / exfil rows | ✅ Lab 04 = RAG, Labs 01 / 06 = agent-MCP, Lab 03 = tool agent, Lab 05 = memory + browsing agent, Lab 07 = multi-agent; the model channel = chatbot |
| **REQ-ENV-05** | Evidence logging, harness-first (JSONL) | The harness and OOB logs are the primary evidence | ✅ Lab `state/*` logs and the slot's `slot_log.jsonl`, kept per trial with full transcripts in the results store |
| **REQ-ENV-06** | Env vars / secrets management | Keys and endpoints injectable; never commit keys | ✅ `.env.example` + `.gitignore`; endpoint via `LLM_BASE_URL` / `MODEL` / `API_KEY` |
| **REQ-MX-03** | Attribute each result to the guardrail layer, not the bundle | Vendors bundle identity / egress / gateway with the content engine | ⚙️ The results store records the guardrail and placement per run; real attribution starts with vendor integration (A4) |

Legend: ✅ met · ⚙️ a knob still to set before a vendor comparison run.

**Phase 2 adds** the vendor-side requirements: REQ-ENV-01/02 (vendor tenants and
policy-configuration privileges) and the Platform & Ops (PT-*) track.
