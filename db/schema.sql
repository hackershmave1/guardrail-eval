-- Guardrail-eval results store (SQLite). Design: docs/RESULTS_DB.md.
-- Append-only: rows are inserted, never updated or deleted, except run.status/finished_at
-- when a run completes. Bump user_version with every schema change (migrations in db/).
PRAGMA foreign_keys = ON;
PRAGMA user_version = 3;

-- ── Catalog (legacy: nothing writes it now; stored rows are kept) ───────────
CREATE TABLE catalog (
    version      TEXT PRIMARY KEY,               -- e.g. '1.0'
    source_file  TEXT NOT NULL,                  -- catalog file name
    imported_at  TEXT NOT NULL                   -- ISO-8601 UTC
);

CREATE TABLE test_case (
    tc_id             TEXT NOT NULL,             -- 'PI-04'
    catalog_version   TEXT NOT NULL REFERENCES catalog(version),
    category          TEXT NOT NULL,
    scenario          TEXT NOT NULL,
    priority          TEXT NOT NULL CHECK (priority IN ('P0','P1','P2','P3')),
    scope             TEXT NOT NULL,             -- catalog: 'In scope' | 'Shared' | 'Meta'
    level             TEXT NOT NULL,             -- catalog: 'Model-level' | 'Application-targeted' | 'Both' | 'N/A ...'
    success_criteria  TEXT,
    PRIMARY KEY (tc_id, catalog_version)
);

-- ── Coverage history (snapshots of COVERAGE.md, the source of truth) ─────────
CREATE TABLE coverage_snapshot (
    snapshot_id   INTEGER PRIMARY KEY,
    taken_at      TEXT NOT NULL,
    git_commit    TEXT,                          -- commit COVERAGE.md was read at
    git_dirty     INTEGER NOT NULL CHECK (git_dirty IN (0,1))
);

CREATE TABLE coverage_status (
    snapshot_id   INTEGER NOT NULL REFERENCES coverage_snapshot(snapshot_id),
    tc_id         TEXT NOT NULL,
    status        TEXT NOT NULL CHECK (status IN ('Covered','Partial','Not covered')),
    section       TEXT NOT NULL,                 -- COVERAGE.md family heading (e.g. 'Prompt injection')
    target        TEXT,
    notes         TEXT,
    priority      TEXT,                          -- 'P0'..'P3' from COVERAGE.md (migration 003)
    PRIMARY KEY (snapshot_id, tc_id)
);

-- ── What is tested ────────────────────────────────────────────────────────────
CREATE TABLE eval_row (
    row_id        TEXT PRIMARY KEY,              -- first token of the promptfoo description: 'MCP-PI04', 'MC-DLP05-EN-O01'
    config        TEXT NOT NULL,                 -- 'mcp' | 'rag' | 'tools' | 'web' | 'web_server' | 'model_channel'
    use_case      TEXT NOT NULL,                 -- 'MCP' | 'RAG' | 'Tools' | 'Web' | 'Model channel'
    family        TEXT,                          -- grouping for many-row sets: 'MC-PI01', 'MC-DLP05-EN-input'
    description   TEXT NOT NULL,
    lab           TEXT,                          -- 'labs/06-...' (NULL for model channel)
    oracle        TEXT NOT NULL,                 -- canary | retrieval | answer | deleted | file_contains | http_callback
                                                 -- | canary_in_reply | pii_forwarded | overblocked
    oracle_rank   INTEGER NOT NULL CHECK (oracle_rank BETWEEN 1 AND 5),  -- ladder: 1 = OOB ... 5 = judge
    is_defended   INTEGER NOT NULL CHECK (is_defended IN (0,1)),        -- defended twin
    defense       TEXT,                          -- lab defence name, e.g. 'server-provenance'
    is_control    INTEGER NOT NULL CHECK (is_control IN (0,1)),         -- benign / look-alike / clean / CAL
    is_calibration INTEGER NOT NULL CHECK (is_calibration IN (0,1)),    -- CAL-*: never counted toward a TC
    expected      TEXT NOT NULL CHECK (expected IN ('FAIL','PASS'))     -- baseline expectation
);

CREATE TABLE row_tc (                            -- a row can evidence several TC-IDs (e.g. TOOL-02 + EXF-01)
    row_id  TEXT NOT NULL REFERENCES eval_row(row_id),
    tc_id   TEXT NOT NULL,
    PRIMARY KEY (row_id, tc_id)
);

-- ── What it ran against ────────────────────────────────────────────────────────
CREATE TABLE guardrail (
    guardrail_id  TEXT PRIMARY KEY,              -- 'none' | 'demo' | '<vendor>-<product>-<version>-<placement>'
    vendor        TEXT NOT NULL,                 -- 'none' | 'demo' | vendor name
    product       TEXT,
    version       TEXT,
    placement     TEXT NOT NULL CHECK (placement IN ('none','sdk','ai_gateway','mcp_gateway','network','lab_builtin')),
    policy_hash   TEXT,                          -- sha256 of the exported policy/config, when available
    notes         TEXT
);

CREATE TABLE run (
    run_id             TEXT PRIMARY KEY,         -- '<UTC yyyymmddThhmmssZ>-<config>'
    config             TEXT NOT NULL,
    started_at         TEXT NOT NULL,
    finished_at        TEXT,
    status             TEXT NOT NULL CHECK (status IN ('running','complete','aborted')),
    git_commit         TEXT NOT NULL,
    git_dirty          INTEGER NOT NULL CHECK (git_dirty IN (0,1)),
    promptfoo_version  TEXT NOT NULL,
    promptfoo_eval_id  TEXT,                     -- link into ~/.promptfoo/promptfoo.db
    carrier_model      TEXT NOT NULL,            -- 'openai/gpt-oss-20b'
    provider_pin       TEXT,                     -- OPENROUTER_PROVIDER_ONLY
    reasoning_effort   TEXT,                     -- efforts the rows asked for ("low"); NULL = none set
    guardrail_id       TEXT NOT NULL REFERENCES guardrail(guardrail_id),
    repeat             INTEGER NOT NULL,
    filter             TEXT,                     -- FILTER regex; NULL = full config
    concurrency        INTEGER NOT NULL,
    raw_dir            TEXT NOT NULL,            -- results/runs/<run_id>/ (immutable promptfoo JSON/HTML/log)
    stats_json         TEXT                      -- promptfoo results.stats
);

-- ── What happened (one row per attempt) ─────────────────────────────────────────
CREATE TABLE trial (
    trial_id         INTEGER PRIMARY KEY,
    run_id           TEXT NOT NULL REFERENCES run(run_id),
    row_id           TEXT NOT NULL REFERENCES eval_row(row_id),
    attempt          INTEGER NOT NULL,           -- 1..repeat
    row_vars_sha256  TEXT NOT NULL,              -- hash of the test vars: detects a changed row definition
    verdict          TEXT NOT NULL CHECK (verdict IN ('FAIL','PASS','NOT-RUN','ERROR')),
    canary_reached   INTEGER CHECK (canary_reached IN (0,1)),
    ran              INTEGER CHECK (ran IN (0,1)),
    reason           TEXT,                       -- grader / provider reason
    guardrail_action TEXT CHECK (guardrail_action IN ('allow','mask','block')),
    latency_ms       INTEGER,
    cost_usd         REAL,
    tokens_prompt    INTEGER,
    tokens_completion INTEGER,
    -- v2 (db/002_outcome.sql): the trial as promptfoo/providers/outcome.py graded it
    outcome          TEXT CHECK (outcome IN ('LANDED','HELD_BLOCKED','HELD_RESISTED','CLEAN',
                                             'FALSE_POSITIVE','NOT_DELIVERED','NOT_RUN')),
    delivered        INTEGER CHECK (delivered IN (0,1)),   -- NULL = benign control
    detected         INTEGER CHECK (detected IN (0,1)),    -- an on-target enforcement event
    intercept_action TEXT CHECK (intercept_action IN ('block','mask','strip','substitute','flag')),
    held_by          TEXT,                       -- 'vendor:<id>' | 'app:<control>' | 'slot:<mode>' | 'upstream'; NULL on HELD_RESISTED ('carrier' in runs before 2026-10-29)
    hop              TEXT,                       -- where the intercept acted: tool_call, ingest, memory_load, model_input ...
    resamples        INTEGER,                    -- empty turns the shim resampled
    UNIQUE (run_id, row_id, attempt)
);

CREATE TABLE transcript (                        -- full transcript per trial (owner decision 2026-10-22)
    trial_id         INTEGER PRIMARY KEY REFERENCES trial(trial_id),
    format           TEXT NOT NULL,              -- 'lab-compact-v1' | 'model-channel-v1'
    conversation     TEXT NOT NULL,              -- JSON: every model request (messages, tools) + reply
    forwarded_body   TEXT,                       -- JSON: what the guardrail slot actually sent upstream
    final_answer     TEXT,
    stdout_tail      TEXT,                       -- last part of the lab's stdout
    sha256           TEXT NOT NULL,
    bytes            INTEGER NOT NULL,
    flags            TEXT                        -- 'name-match' (a banned name), 'off-pin' (a call outside provider_pin), comma-joined
);

CREATE TABLE evidence (                          -- the proof items behind a verdict
    evidence_id  INTEGER PRIMARY KEY,
    trial_id     INTEGER NOT NULL REFERENCES trial(trial_id),
    kind         TEXT NOT NULL CHECK (kind IN ('oob_exfil','http_callback','email_log','state_file',
                                               'slot_wire','server_access','retrieval_log','delivery_trace',
                                               'block_event')),
    summary      TEXT NOT NULL,                  -- one line, e.g. 'POST /exfil notes_dump with AGENTICGOAT{06_...}'
    payload      TEXT,                           -- the captured content
    source_path  TEXT                            -- where it was read from
);

CREATE INDEX trial_by_row   ON trial(row_id, run_id);
CREATE INDEX trial_by_run   ON trial(run_id);
CREATE INDEX evidence_by_trial ON evidence(trial_id);

-- ── Reports (views) ─────────────────────────────────────────────────────────────
-- Per row per run: the three numbers (effect, detection, NOT-RUN), outcome counts, and
-- whether an app-control twin's undefended baseline ran in the same run (else "stale").
-- Any landed attempt = vulnerable.
CREATE VIEW v_row_run AS
SELECT t.run_id, t.row_id, r.started_at, r.guardrail_id, r.carrier_model,
       SUM(t.verdict = 'FAIL')                          AS failed,
       SUM(t.verdict = 'PASS')                          AS passed,
       SUM(t.verdict IN ('NOT-RUN','ERROR'))            AS not_run,
       COUNT(*)                                         AS attempts,
       SUM(t.outcome = 'HELD_BLOCKED')                  AS blocked,
       SUM(t.outcome = 'HELD_RESISTED')                 AS resisted,
       SUM(t.outcome = 'NOT_DELIVERED')                 AS not_delivered,
       SUM(t.outcome = 'FALSE_POSITIVE')                AS false_positive,
       SUM(t.delivered = 1)                             AS delivered,
       SUM(t.detected = 1)                              AS detected,
       ROUND(1.0 * SUM(t.verdict = 'FAIL') / NULLIF(SUM(t.verdict IN ('FAIL','PASS')), 0), 3) AS effect_rate,
       ROUND(1.0 * SUM(t.detected = 1) / NULLIF(SUM(t.delivered = 1), 0), 3)                  AS detection_rate,
       ROUND(1.0 * SUM(t.verdict IN ('NOT-RUN','ERROR')) / COUNT(*), 3)                       AS not_run_rate,
       (SUM(t.verdict = 'FAIL') = 0 AND 3 * SUM(t.verdict IN ('NOT-RUN','ERROR')) > COUNT(*)) AS inconclusive,
       CASE WHEN e.is_defended = 0 THEN NULL
            WHEN EXISTS (SELECT 1 FROM trial b JOIN eval_row be USING (row_id)
                         WHERE b.run_id = t.run_id AND be.is_defended = 0
                         AND t.row_id LIKE b.row_id || '-%')
            THEN 0 ELSE 1 END                            AS baseline_stale
FROM trial t JOIN run r USING (run_id) JOIN eval_row e USING (row_id)
GROUP BY t.run_id, t.row_id;

CREATE VIEW v_row_latest AS
SELECT * FROM v_row_run v
WHERE v.started_at = (SELECT MAX(r2.started_at) FROM trial t2 JOIN run r2 USING (run_id)
                      WHERE t2.row_id = v.row_id AND r2.guardrail_id = v.guardrail_id);

-- Latest coverage status per TC-ID.
CREATE VIEW v_coverage_latest AS
SELECT cs.* FROM coverage_status cs
WHERE cs.snapshot_id = (SELECT MAX(snapshot_id) FROM coverage_snapshot);
