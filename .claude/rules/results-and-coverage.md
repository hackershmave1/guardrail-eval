---
paths:
  - "db/**"
  - "results/**"
  - "scripts/ingest_run.py"
  - "scripts/db_report.py"
  - "scripts/run_meta.py"
  - "docs/COVERAGE.md"
  - "docs/TEST_CASES.md"
  - "docs/RESULTS_DB.md"
---

# Results store and coverage docs

- **Results are append-only:** never edit or delete a run dir or DB rows; a rerun is a new run
  (Claude Code denies edits under `results/runs/` and to `results/guardrail_eval.db`).
- Schema changes bump `PRAGMA user_version` in `db/schema.sql` and ship a `db/NNN_*.sql` migration.
- **`docs/COVERAGE.md` is the source of truth for coverage.** Update it first; counts, reports and
  the notebook derive from it. Keep its table layout and the backticked row IDs: `ingest_run.py`
  and `notebooks/operator_lib.py` parse them to link eval rows to TC-IDs, and
  `tests/test_operator_lib.py` fails on format drift.
- **Coverage grows by workbook TC-IDs.** A new variant of a covered TC-ID adds depth, not coverage.
- A result goes in `docs/TEST_CASES.md` / `docs/COVERAGE.md`; a decision worth keeping goes in the
  resolved log of `docs/OPEN_QUESTIONS.md`.
- The coverage dashboard (claude.ai artifact NzLwm4t7E9vEjEE9JrM1TQ) is on hold: do not refresh it
  unless the owner asks. If refreshed, derive `testcases` from `docs/COVERAGE.md` and `rows` /
  `history` from the store.
