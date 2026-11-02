# STATUS

As of 2026-11-02 · branch `ingest-run-tests` (off `main` at `561fca2`, where PR #21 merged the lint
work): ub-mode target 6 is done, so targets 1-6 are done. **PR #22 is open, waiting on the owner's
go** (https://github.com/hackershmave1/guardrail-eval/pull/22). Proposed next, waiting on the
owner's OK: targets 7 (`_delivered`) and 8 (`run_meta.main`), before the baseline reruns. Rules
live in `CLAUDE.md` and `.claude/rules/`; shipping steps in the `ship` skill.

```mermaid
flowchart LR
    A["Phase 1: 28 / 42 TC-IDs ✅"] --> W["PI-04 worked example ✅<br/>+ A1 (a) logged ✅"]
    W --> C["Owner: visual check<br/>+ small decisions"]
    C --> D["Phase B<br/>gateway + scan adapters<br/>un-gate vendor targets"]
    D --> E["Phase 2<br/>vendors in the loop"]
    U["ub-mode targets 1-5 ✅<br/>PRs #12-#21"] -.-> I["target 6: ingest ✅<br/>PR #22"]
    I -.-> T["proposed: targets 7-8<br/>_delivered, run_meta.main<br/>(owner's OK)"]
    T -.-> B["pinned<br/>baseline reruns"]
    P["provider pinned ✅<br/>deepinfra bf16<br/>served_by recorded ✅"] -.-> B
```

## Waiting on the owner: PR #22, `ingest-run-tests` (ub-mode target 6, `scripts/ingest_run.py`)

- ✅ **`147032e`, the pins:**
  - `tests/test_ingest_run.py`, 32 tests. Temp run dirs, a synthetic workbook and a synthetic
    `COVERAGE.md` go into a fresh store built from `db/schema.sql`.
  - They pin `verdict`, `row_definition`, `coverage_rows`, `snapshot_coverage`, `load_catalog`,
    `connect` (new store, only the missing migrations, no migration path) and `evidence_items`.
  - They also pin `ingest` end to end: lab and model-channel runs, the slot-log join, `row_tc`, name
    flags, vendor ids, aborted runs, re-ingest skipped, text stored as written, and the vars hash
    ignoring key order. Plus `main`.
  - No code change. mutator 108 / 108.
- ✅ **`7bdf867`, the split:**
  - `ingest` → `_insert_run` (+ `_register_guardrail`) and `_insert_trials` over `_slot_log`. Per
    result: `_result_parts`, `_insert_row`, `_insert_trial`, the pure `_transcript`,
    `_insert_transcript` and `_slot_evidence`.
  - `load_catalog` → `_use_cases_sheet` + `_use_cases`; `coverage_rows` → `_coverage_line`;
    `evidence_items` → one builder per item kind; `row_definition` → `_is_control`.
  - Public names are unchanged (`operator_lib` and the tests use them). `ROOT` uses `.absolute()`.
- **Proof:**
  - All 77 real run dirs (`results/runs` and `results/scratch/runs`) were ingested into two fresh
    stores, one with the old code and one with the new. Every table is identical: 76 runs, 3,087
    trials and transcripts, 3,511 evidence items, 323 rows, 317 TC links (the two import timestamps
    left out). The stdout per run is identical too. One old scratch dir with no `run.json`
    (`20261026T131657Z-mcp`) fails the same way in both.
  - CRAP: every `ingest_run` function is under 10 (crapper exits 0 on the file). `ingest` 70 → 5,
    `load_catalog` 117 → 3.
  - mutator `--mutate-all` 105 / 105 (weak test class 2 / 105). 292 tests OK; dryer: no new pairs.

## Next steps, in order

1. **Owner:** say "merge" for PR #22. Claude then runs `gh pr merge 22 --merge
   --match-head-commit <sha>` (the sha from `gh pr view 22 --json headRefOid`), then
   `git checkout main && git pull --ff-only`, and reruns the tests (292).
2. **Owner decides on targets 7-8** (proposed 2026-11-02; see "Still over CRAP 10" below):
   - 7: `agentic_goat_provider._delivered` (decides delivery; 69% covered);
   - 8: `run_meta.main` (writes `run.json`, the reruns' REQ-MD-01 evidence; 0% covered);
   - optionally 9: the edit hook `.claude/hooks/guard_edit.py` (the banned-text gate; 0% covered);
   - and defers group A plus `pair_filter.main` and `_exfil_pids`.

   Each target is a short job in the usual pattern: pin → mutator → split → gates → PR.
3. **Then the pinned baseline reruns** (the owner's order: once every ub-mode target is completed or
   deferred):
   - `REPEAT=3 scripts/run_evals.sh` (every config) into the real store. Each run is pinned to
     `deepinfra` and records `served_by` per call.
   - Compare each row with run #1 and the 10-29 reruns. Then update `docs/COVERAGE.md` (source of
     truth first; e.g. SUP01's twin, now 2/3 landed under the pin) and `docs/TEST_CASES.md`.
   - This also covers the `rag`, `memory` and `a2a` twins, the forged-block-line rule live, and the
     non-canary oracle kinds end to end.

## ub-mode targets

**Owner (2026-11-01):** rerun the baselines only once every ub-mode target is completed or deferred.

| # | Target | Was → now | PR |
|---|---|---|---|
| 1 | `assert_canary.get_assert` | 72 → 8 | #16 |
| 2 | `agentic_goat_provider._maybe_start_exfil` | 160 → 7 | #16 |
| 3 | `guardrail_slot/proxy.py` `scan_input` / `do_POST` | 148 / 122 → 6 / 6 | #18 |
| 4 | `shim/sitecustomize.py` `_recorded` | 110 → 6 | #19 |
| 5 | `lint_prompts.py` `rows` / `check` / `main` | 316 / 270 / 61 → 6 / 7 / 8 | #21 |
| 6 | `ingest_run.py` `ingest` / `load_catalog` | 70 / 117 → 5 / 3 | #22 (open) |

**Still over CRAP 10.** Ranked by the same test as targets 1-6: could a bug silently misgrade a
trial or corrupt the evidence, while little tests it? A high score alone is not risk: it can be
complexity in well-tested code, or untested code that can't touch a verdict.

- **B, fit that test: proposed as targets 7-9 (owner's OK pending):**
  - `agentic_goat_provider._delivered` 13 (69% covered). It decides whether the attack reached the
    model. A bug could count an undelivered attack as delivered, and the row would read "held": a
    false PASS. **Target 7.**
  - `run_meta.main` 90 and `header_names` 12 (0%). They write `run.json` (carrier, `provider_pin`,
    git commit), the reruns' REQ-MD-01 evidence. A bug would record the wrong pin silently.
    **Target 8.**
  - `.claude/hooks/guard_edit.py` `main` 90 and `new_text` 20 (0%). The hook that blocks the real
    company name and submodule edits; failing open would be silent. A compliance gate, not grading.
    **Optional target 9.**
  - `pair_filter.main` 21 (14%). It pairs a twin with its baseline in a filtered run; the
    full-suite reruns use no filter. Defer.
  - `_exfil_pids` 24 (8%). PR #16's checks already turn a failure into NOT-RUN. Defer.
- **A, can't silently change a verdict or the evidence: defer:**
  - Reporting only: `db_report.main` 462, `summarize.tally` 42. The store stays right.
  - Generators: `gen_dlp05` / `gen_dlp01` / `gen_web_pages` / `gen_jb07` (12-462). They write
    protected datasets once, `lint_prompts` checks the output, and a refactor risks changing a
    dataset.
  - Lab drivers' `main` (12-156, 0%). A break crashes (NOT-RUN) or fails the delivery check
    (NOT_DELIVERED), both loud. `web_fixtures/server.Handler.do_GET` 17 is the same.
  - Already well tested; the score is complexity: `call_api` 41 (86%), `_collect_evidence` 20
    (88%), `outcome.decide` 17 (93%, kept whole as the rule table), `_model_text` 13 (100%),
    `_tool_called` 12 (88%), `_compact_transcript` 12 (100%), `_build_cmd` 36 (31%; a bad command
    crashes the lab).
- **C, skipped by the owner:** `operator_lib`'s 42.

## Where things stand

- ✅ **Phase 1:** 28 of 42 TC-IDs covered (all 10 P0s). Each has a landing undefended baseline on
  `openai/gpt-oss-20b` (`docs/COVERAGE.md`). The store holds run #1 (2026-10-25/26, 9 runs, 981
  trials) plus three real reruns from 2026-10-29: `20261029T144843Z-mcp`,
  `20261029T145209Z-tools` and `20261029T145414Z-model_channel` (a 6-row filtered rerun). Those were
  all unpinned. Runs from 2026-11-01 are pinned to `deepinfra` (A1 (a) in
  `docs/OPEN_QUESTIONS.md`). 260 unit tests on `main`, 292 on `ingest-run-tests`; lint shows
  0 errors.
- ✅ **PR #11 merged** (`3df20d3`): grading review fixes, the operator notebook's Part B, and an
  operator-first README. After a code update, restart the notebook kernel (else
  `AttributeError: ... setup_ui`).
- ✅ **PR #12 merged** (`df10e17`): ub-mode with honest quality-gate tools. `tests/__init__.py`,
  `.absolute()` paths; upstream `SKILL.md` unedited, repo specifics in `.claude/rules/ub-mode.md`;
  commit often on a topic branch, push = `ship` PR, merge on the owner's go. Tools in
  `~/tools/{crapper,dryer,mutator}` (venv `~/tools/ub-venv`, on PATH via `~/.local/bin`).
- ✅ **PR #13 merged** (`d6f9168`): `tests/test_outcome.py`; mutator on `outcome.py` 100%.
- ✅ **PR #14 merged** (`645be56`): `_oracle` split, CRAP 560 → 7; `tests/test_oracle.py`; grading
  fix: `http_callback` with no `oracle_markers` raises.
- ✅ **PR #15 merged** (`17cae8e`): model-channel `call_api` split, CRAP 495 → 9;
  `tests/test_model_channel.py` (43). Scratch run `20261101T110910Z-model_channel` (807 trials) vs
  run #1: 238 / 269 identical, controls and blocks unchanged.
- ✅ **PR #16 merged** (`851ecdd`): `get_assert` tests; `_maybe_start_exfil` split. Grading fix:
  when no listener of ours owns :9999, the trial is NOT-RUN, not a silent false PASS.
- ✅ **PR #17 merged** (`a31062f`, docs): A1 (a) resolved (`OPENROUTER_PROVIDER_ONLY=deepinfra`,
  also the `.env.example` default). `docs/WORKED_EXAMPLE_PI-04.md` is the source → sink trace.
- ✅ **PR #18 merged** (`af39591`): guardrail slot pinned (30 tests) and split. The ub-mode rule now
  says how to prove a mutator run (`--mutate-all` plus a weak test).
- ✅ **PR #19 merged** (`dd94a6d`): lab-side shim pinned (28 tests) and split into one factory per
  layer, stacked by `install()`.
- ✅ **PR #20 merged** (`b35317f`): the upstream that served each lab call is recorded as
  `served_by` (shim → `_compact_transcript` → `mcp.json`, the store, `proof.jq`). First recorded
  run: `20261102T065619Z-mcp`, every call `DeepInfra`. Model-channel rows have it in the slot log's
  reply.
- ✅ **PR #21 merged** (`561fca2`): `lint_prompts.py` pinned (31 tests) and split into a rules table
  and one loader. Rule fix: "leak" is now flagged as test vocabulary, as
  `.claude/rules/model-facing-text.md` says (no row in the repo had it). Byte-identical report on the
  real configs.

## ub-mode baselines (2026-11-02)

- **mutator 100%:** `outcome.py`, `_oracle_*`, `get_assert`, the listener functions,
  `guardrail_slot/proxy.py`, `shim/sitecustomize.py`, `_compact_transcript`, `lint_prompts.py` and
  `ingest_run.py` (on the branch). `model_channel_provider.py` is at 94 / 99 (5 equivalent).
- **crapper:** 86 of 320 functions over CRAP 10 (on `ingest-run-tests`; 92 on `main`, 110 at the
  start). 42 of the 86 are `operator_lib`, skipped.
- **dryer @ 0.8:** one pair at 0.85, `lab04_aggregation_leak.py:73-99` ≈
  `lab04_proprietary_leak.py:92-120`.
- **Fast mutator runs and the proof that workers ran:** in `.claude/rules/ub-mode.md`.
- **The job pattern:** pin behaviour with tests → mutator proves the pins → fix any rule gap
  test-first → restructure → same tests + mutator + crapper + dryer → an end-to-end proof → PR.
  - For a grading-path change, the proof is one scratch run, compared row by row with the last run
    (and with `main` as a control when they differ).
  - For a tool off the grading path, it is the old and new code on the real inputs: the lint report
    on the real configs, or the store built from the real runs.

## Findings worth keeping

- **Slot gap for Phase B:** `do_POST` discards `scan_output`'s action (PR #18). A vendor's
  response-side block would be relayed as a 200 and not logged as a block. Fix it with the scan
  adapter.
- **Lab 01's `sensitive-paths` fence misses `read_text_file`.** It blocks `read_file` only. Under
  the `deepinfra` pin the carrier picks `read_text_file` 2 of 3 times and the demo key reaches the
  sink, so LANDED is correct. Like Lab 06's control, it is a reference twin, not a benchmark.
  `docs/COVERAGE.md`'s "twin HELD_BLOCKED 3/3" for SUP01 is the unpinned 10-29 run; the pinned
  baseline reruns will refresh it.
- **Lab 06's `server-provenance` control is a verbatim-substring check** (more than 20 chars, whole
  result). Under the `deepinfra` pin, compact JSON re-serialisation evades it ×3. The data leaks,
  so LANDED is correct.
- **The workbook is not in this checkout** (`AI_Guardrails_POC_4_2.xlsx`), so ingest skips the
  catalog with a note. The parser is covered by the synthetic workbook tests.
- **mutator reuses its last snapshot.** A site it killed before, in an unchanged function, is
  carried over and not rerun. A "weak test" proof without `--mutate-all` just repeats the last
  result.
- **No vendor slot is on the lab path today.** Lab rows call the model at `LLM_BASE_URL` and the MCP
  servers over direct stdio; `guardrail_slot/proxy.py` serves model-channel rows only. PI-04 credit
  needs an MCP-gateway response-side tap or a tool-output hook (hop H10).
- **PI-04 false holds:** a lossy retype of the secret into `notes_dump` (data leaks, oracle reads
  clean) is still open. The dead-listener case is closed (PR #16).
- **`pii_forwarded` is verbatim by design** (pinned in `test_model_channel.py`). Changing it is an
  owner grading decision.
- **Provider drift is real:** unpinned OpenRouter routing broke Lab 06 mid-run on 2026-11-01. Lab
  transcripts name the upstream of every call (`served_by`) from `20261102T065619Z-mcp` on. A
  check that flags a call served by an upstream other than the pin is not built (offered).
- **The 10-29 `model_channel` rerun is a 6-row filtered run;** compare a full `model_channel` run
  with `20261025T225653Z-model_channel`.

## Other next steps

1. **Optional one-line fix:** the `_reset_state` comment (`agentic_goat_provider.py:193-195`) says
   Lab 06's listener crashes on an empty `exfil_log.json`, but `exfil_server.py:13-19` reads it as
   `[]`.
2. **Owner: visual check** of the notebook. Restart the kernel, Run All with `RUN_LIVE = False`,
   click through B1 → B2 → B3 in light and dark, and clear outputs before saving.
3. **Owner: remove** `agentic-goat/labs/05-agentic-memory-attacks/sandbox/audit_log.txt` (run #1
   leftover; Claude's `rm` was denied). The next memory run also deletes it.
4. **Decide before the reruns:** align reasoning effort for `tools`, `a2a` and `web_server`
   (changing it now costs nothing extra, since their baselines are about to be rerun).
5. **Phase B:**
   - Build the gateway adapter and the scan adapter, and un-gate the B1 targets.
   - Pair every vendor run with a fresh undefended baseline, and add a `v_vendor_credit` view.
   - Add an MCP tap, so a vendor can be credited for PI-04 at H10.
   - Add HMAC-signed events, a hook-order self-test and a fixed-refusal hook policy.
   - Redraw `docs/ARCHITECTURE.md`, and get vendor answers to C8–C10.

## Known open items

- **ub-mode tool runs are slow with the full suite:** crapper ≈ 1 min; mutator on `ingest_run.py`
  ≈ 3.5 min. Prefer a focused `--test-command`. Always name paths; the tools do not skip
  `agentic-goat/`. A full `model_channel` scratch run takes several minutes (807 trials).
- **Config load check** is partly open: the owner runs `/hooks` and `/permissions`.
- **Git identity** is unset on this machine. Commits pass `-c user.name=hackershmave1
  -c user.email=109069139+hackershmave1@users.noreply.github.com`.
- **Rows are stochastic** (MEM-02 went 3/3, then 1/3): REPEAT=3 is the minimum.
- **Lint warning on `MC-JB03-04`:** the attack text contains "test". Confirm it is the attacker's
  own pretext.
- **`EXTRA_HEADERS` endpoints match literally** (`localhost` ≠ `127.0.0.1`). A token in a base URL
  would land in `run.json`.
- **PR #8's git history** still has an old styling reference that PR #9 removed (history not
  rewritten).
- **Leftovers:**
  - 22 scratch runs in `results/scratch/runs/` (one, `20261026T131657Z-mcp`, has no `run.json`).
  - `agentic-goat/labs/03-red-team-assessment/state/` (untracked lab state).
  - Local branches the owner may prune: all 16 besides `main` and `ingest-run-tests` are merged
    into `main` (`git branch --merged main`, checked 2026-11-02).
  - Backup at `~/Projects/GuardRails/guardrail-eval-backup-20261028.tar.gz`.

## On hold

- NF-02 and all benign / over-refusal measurement: until a vendor is in the loop.
- Notebook UI builders (`operator_lib`): skipped by the owner for now (2026-11-01).
- Rows needing owner input: harm carrier (A1b → JB-01, JB-02, TOX-01); other targets (A6 → OUT-03,
  C2 → PI-06, A5 → MM-01/02).
- Coverage dashboard (artifact NzLwm4t7…): not to be refreshed (owner, 2026-10-26).
- Baseline reruns: until targets 7-8 (and 9 if picked) are done and the rest is deferred (owner's
  order, 2026-11-01).
