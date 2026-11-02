-- Migration 2 -> 3: coverage_status carries each TC-ID's priority from COVERAGE.md, so a store
-- with no catalog rows (every fresh clone) still reports it. Applied by scripts/ingest_run.py
-- when the store is at user_version 2. Additive: older snapshots read NULL.
ALTER TABLE coverage_status ADD COLUMN priority TEXT;   -- 'P0'..'P3'

PRAGMA user_version = 3;
