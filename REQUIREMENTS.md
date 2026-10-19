# Requirements — Phase 1 (Agentic Goat + Promptfoo)

Extracted from `AI_Guardrails_POC_4_2.xlsx` → **Requirements** tab (REQ-*), scoped to the
three in-scope use cases (MCP / RAG / tools). A rate is only as trustworthy as the oracle
and the prerequisites under it; these are the prerequisites that must be TRUE before any
number from this repo is believable.

| Req-ID | Requirement | Why it matters | Status in this repo |
|--------|-------------|----------------|---------------------|
| **REQ-OR-01** | Define the success oracle per scenario *before* running | A weak oracle silently miscounts and corrupts every % downstream | ✅ Each test case in `TEST_CASES.md` names its oracle |
| **REQ-OR-02** | OOB callback server stood up & reachable | A callback = data actually left; a model *describing* exfil scores zero | ✅ Agentic Goat ships `exfil_server.py` per lab (binds localhost:9999) |
| **REQ-OR-03** | Canary tokens seeded in docs, secrets, tool manifests | Binary ground truth for leakage | ✅ Agentic Goat plants `AGENTICGOAT{...}` canaries per lab |
| **REQ-OR-04** | LLM-judge only as last resort | The judge is the weakest oracle and is prompt-injectable | ✅ We use canary/OOB/tool-log for every in-scope row; no judge needed |
| **REQ-OR-05** | Minimal trial counts N>1 (≈20–30 payloads × 3 repeats, deterministic oracle) | Catch nondeterminism without research-grade cost | ⚙️ `repeat`/payload count set per config; tune before a real run |
| **REQ-TL-04** | **Promptfoo as the config-driven backbone**; wire the OOB/canary oracle as a custom JS/Python assertion | One tool runs the attacks and the vendors side-by-side; the oracle is the only glue you build | ✅ `promptfoo/*.yaml` + `promptfoo/providers/*` + `assert_canary.py` |
| **REQ-MD-01** | Base model held **constant** across vendors | A block must be attributable to the guardrail, not model drift | ⚙️ Set `MODEL` once; keep it identical across provider runs |
| **REQ-MD-02** | Use a **permissive** model that does not self-refuse the base attacks | A hard-aligned model refuses for free and the guardrail looks perfect | ⚙️ Pick a permissive carrier (Agentic Goat notes `gpt-oss-20b` triggers; small 7B often won't) |
| **REQ-MD-04** | Record model role placement (system vs user) | #1 false-positive cause is system text in the user role | ✅ Agentic Goat agents keep system text in the system role |
| **REQ-DS-06** | Test MCP servers + poisoned corpora (docs, pages, manifests) | Agentic/indirect tests need controllable untrusted content and tools you own | ✅ Agentic Goat **is** this: poisoned MCP servers, poisoned RAG docs, DocuAssist tools |
| **REQ-ENV-03** | Isolated / sandboxed network | Harmful payloads and exfil must stay contained | ✅ Agentic Goat exfil binds to localhost; run in a disposable VM/container |
| **REQ-ENV-04** | A test app per shape: chatbot, RAG, agent-MCP | One shape can't exercise tool/memory/exfil rows | ✅ Agentic Goat Lab 04 = RAG, Labs 01/06 = agent-MCP, Lab 03 = tool-agent |
| **REQ-ENV-05** | Evidence logging — harness-first (JSONL) | The harness + OOB logs are the primary evidence | ✅ Agentic Goat writes `state/*.jsonl`, `email_log.txt`, `exfil_log.json` |
| **REQ-ENV-06** | Env vars / secrets management | Keys + endpoints injectable; never commit keys | ✅ `.env.example` + `.gitignore`; LLM endpoint via `LLM_BASE_URL`/`MODEL`/`API_KEY` |
| **REQ-MX-03** | Attribute each result to the guardrail layer, not the bundle | Vendors bundle identity/egress/gateway with the content engine | ⚙️ Each run records placement (SDK vs MCP-gateway) per `TEST_CASES.md` |

Legend: ✅ satisfied by this repo / Agentic Goat · ⚙️ a knob to set before a real run.

## Base model (REQ-MD-01/02)

The labs read their endpoint from env vars, so point them at any OpenAI-compatible local
model without editing code:

```bash
export LLM_BASE_URL=http://localhost:11434/v1   # Ollama default; LM Studio = :1234/v1
export MODEL=qwen2.5-7b-instruct                # a permissive carrier that does not self-refuse
export API_KEY=lm-studio
```

Document any scenario where the model refused on its own, so it is not credited to the
guardrail. For the model-dependent labs, prefer a stronger carrier (Agentic Goat notes
`gpt-oss-20b` reproduces the attacks; small 7B models often do not).

**No local model?** Use any OpenAI-compatible hosted endpoint — e.g. OpenRouter:
`LLM_BASE_URL=https://openrouter.ai/api/v1`, `API_KEY=<your OpenRouter key>`,
`MODEL=<an OpenRouter model id>`. Keep the key in `.env` (gitignored), never in git.
Note (REQ-ENV-03): with a hosted model the model inference leaves the box, but the labs'
exfil listeners and canaries stay on `localhost`, so the attack side effects remain
contained; still run in a disposable VM/container.

## When this grows into the full POC

This phase uses Agentic Goat as the single target and no vendor guardrail in front — it
establishes the attacks, the oracles, and the Promptfoo wiring. The workbook's full
architecture (see `../.notes/inputs.md`) then slots a **guardrail vendor scanner** between
the app and the model/MCP channels and runs the same configs across all four vendors
side-by-side. The REQ-ENV-01/02 (vendor tenants + policy-config privileges) and the
Platform & Ops (PT-*) track come in at that point.
