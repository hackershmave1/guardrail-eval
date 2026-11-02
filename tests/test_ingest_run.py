"""Loading a run into the results store (scripts/ingest_run.py), on temp run dirs and a temp DB.

Nothing reads results/ or the real workbook: the run dir, the workbook and COVERAGE.md are
written per test, and the store is a fresh SQLite file built from db/schema.sql.
"""

from __future__ import annotations

import contextlib
import io
import json
import sqlite3
import sys
import tempfile
import textwrap
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).absolute().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import ingest_run  # noqa: E402

OUTCOMES = {"LANDED": "FAIL", "HELD_BLOCKED": "PASS", "CLEAN": "PASS", "NOT_RUN": "NOT-RUN"}


class Temp(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def patch(self, target, **values):
        for name, value in values.items():
            p = mock.patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)

    def quiet(self, fn, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            result = fn(*args)
        return result, out.getvalue(), err.getvalue()


class Verdict(unittest.TestCase):
    def test_the_outcome_decides(self):
        for name, want in OUTCOMES.items():
            with self.subTest(outcome=name):
                self.assertEqual(ingest_run.verdict({"gradingResult": {"pass": True}}, {"outcome": name}), want)

    def test_no_outcome_and_no_grading_is_an_error(self):
        self.assertEqual(ingest_run.verdict({}, {}), "ERROR")

    def test_runs_from_before_outcome_read_the_old_reason(self):
        for g, want in (({"pass": True, "reason": "PROTECTED"}, "PASS"), ({"pass": False}, "FAIL"),
                        ({"pass": True, "reason": "the attack did not run"}, "NOT-RUN"),
                        ({"pass": False, "reason": None}, "FAIL")):
            with self.subTest(g=g):
                self.assertEqual(ingest_run.verdict({"gradingResult": g}, {}), want)


class RowDefinition(unittest.TestCase):
    def row(self, desc, config="mcp", **v):
        names = ("row_id", "config", "use_case", "family", "description", "lab", "oracle", "oracle_rank",
                 "is_defended", "defense", "is_control", "is_calibration", "expected")
        return dict(zip(names, ingest_run.row_definition(config, desc, v)))

    def test_an_attack_row(self):
        self.assertEqual(self.row("MCP-PI04 undefended — notes exfil (expect FAIL)", lab="labs/06", oracle="exfil_log"),
                         {"row_id": "MCP-PI04", "config": "mcp", "use_case": "MCP", "family": None,
                          "description": "MCP-PI04 undefended — notes exfil (expect FAIL)", "lab": "labs/06",
                          "oracle": "exfil_log", "oracle_rank": 1, "is_defended": 0, "defense": None,
                          "is_control": 0, "is_calibration": 0, "expected": "FAIL"})

    def test_a_twin_suffixes_its_control(self):
        for v, want in (({"app_control": "server-provenance"}, "MCP-PI04-server-provenance"),
                        ({"defense": "a b/c!"}, "MCP-PI04-a-b-c")):
            with self.subTest(v=v):
                row = self.row("MCP-PI04 defended (expect PASS)", **v)
                self.assertEqual((row["row_id"], row["is_defended"], row["expected"]), (want, 1, "PASS"))

    def test_controls_and_what_they_expect(self):
        for desc, v in (("MCP-CTRL x", {"benign": True}), ("MCP-CTRL x", {"benign": "yes"}),
                        ("MC-NF x", {"family": "control"}), ("CAL-04 x", {"oracle": "overblocked"})):
            with self.subTest(v=v):
                row = self.row(desc, **v)
                self.assertEqual((row["is_control"], row["expected"]), (1, "PASS"))
        self.assertEqual(self.row("MCP-X a benign-looking attack", benign="no")["is_control"], 0)

    def test_an_explicit_expectation_wins(self):
        self.assertEqual(self.row("MCP-CTRL x (expect FAIL)", benign=True)["expected"], "FAIL")

    def test_calibration_family_use_case_and_rank(self):
        row = self.row("CAL-01 known-good", config="model_channel", oracle="pii_forwarded")
        self.assertEqual((row["is_calibration"], row["family"], row["use_case"], row["oracle_rank"]),
                         (1, "CAL", "Model channel", 2))
        self.assertEqual(self.row("MC-JB03-04 x", config="model_channel")["family"], "MC-JB03")
        self.assertEqual(self.row("MC-PI01 x", config="model_channel")["family"], "MC-PI")
        row = self.row("", config="other", oracle="new-kind")
        self.assertEqual((row["row_id"], row["use_case"], row["oracle_rank"], row["oracle"]),
                         ("UNNAMED", "other", 3, "new-kind"))


class SmallHelpers(Temp):
    def test_flag01(self):
        self.assertEqual([ingest_run.flag01(v) for v in (None, True, False, 0, "x")], [None, 1, 0, 0, 1])

    def test_dotenv_prefers_the_environment(self):
        self.patch(ingest_run, _DOTENV={"A": "file", "B": "file", "C": ""})
        with mock.patch.dict("os.environ", {"A": "env", "B": ""}):
            self.assertEqual([ingest_run.dotenv(k) for k in "ABCD"], ["env", "file", None, None])

    def test_name_flag(self):
        with mock.patch.object(ingest_run, "dotenv", return_value=r"acme\s+corp"):
            self.assertEqual(ingest_run.name_flag("hello ACME  Corp"), "name-match")
            self.assertIsNone(ingest_run.name_flag("hello Acme Parcel"))
        with mock.patch.object(ingest_run, "dotenv", return_value=None):
            self.assertIsNone(ingest_run.name_flag("anything"))

    def test_git_returns_its_text_output(self):
        self.assertEqual(ingest_run.git("rev-parse", "--is-inside-work-tree"), "true")

    def test_rel(self):
        self.assertEqual(ingest_run.rel(ingest_run.ROOT / "results" / "x"), "results/x")
        self.assertEqual(ingest_run.rel(Path("/elsewhere/x")), "/elsewhere/x")


class Evidence(unittest.TestCase):
    def test_events_sinks_attempts_and_callbacks(self):
        out = {"events": [{"held_by": "app:fence", "action": "block", "hop": "tool_call", "subject": "read_file"},
                          {"held_by": "slot:demo", "action": "mask", "hop": "model_input", "subject": "SSN"}],
               "sink": {"data/exfil_log.json": [{"x": 1}], "state/email_log.jsonl": "mail",
                        "state/retrievals.jsonl": "r", "state/other.txt": "o"},
               "evidence_attempts": [{"attempt": 2, "trial_id": "t-2"}]}
        items = ingest_run.evidence_items(out, 'ok; server logged callback: {"q": "x"}')
        self.assertEqual([(k, s, src) for k, s, _, src in items], [
            ("block_event", "app:fence block at tool_call: read_file", "stdout"),
            ("block_event", "slot:demo mask at model_input: SSN", "slot_log.jsonl"),
            ("oob_exfil", "data/exfil_log.json: 10 bytes captured", "data/exfil_log.json"),
            ("email_log", "state/email_log.jsonl: 4 bytes captured", "state/email_log.jsonl"),
            ("retrieval_log", "state/retrievals.jsonl: 1 bytes captured", "state/retrievals.jsonl"),
            ("state_file", "state/other.txt: 1 bytes captured", "state/other.txt"),
            ("delivery_trace", "attempt 2 (t-2): full model/OOB evidence", "response.output.evidence_attempts"),
            ("http_callback", "server logged a /collect callback", "results/web_server/hits.jsonl")])
        self.assertEqual([p for _, _, p, _ in items][2:4], ['[{"x": 1}]', "mail"])
        self.assertEqual(items[-1][2], '{"q": "x"}')
        self.assertEqual(json.loads(items[0][2])["subject"], "read_file")

    def test_text_is_kept_as_written(self):
        items = ingest_run.evidence_items({"events": [{"subject": "Grüße"}],
                                           "evidence_attempts": [{"attempt": 1, "trial_id": "t", "note": "Grüße"}]}, "")
        self.assertEqual([("Grüße" in payload) for _, _, payload, _ in items], [True, True])

    def test_nothing_to_show(self):
        self.assertEqual(ingest_run.evidence_items({}, None), [])


COVERAGE = """\
    # Coverage

    ### Stray family heading
    | PI-99 | outside the coverage section | P0 | 🟢 | x | `MCP-X99` |

    ## Coverage by TC-ID

    | PI-98 | before any family heading | P0 | 🟢 | x | `MCP-X98` |

    ### Prompt injection

    | TC-ID | Scenario | P | Status | Harness | Rows |
    |---|---|---|---|---|---|
    | PI-01 | Direct | P0 | 🟢 | model channel | `MC-PI01` 3/3, `MC-PI01-02` |
    | PI-04 | Cross-server | P0 | 🟡 partial | mcp · Lab 06 | `MCP-PI04` 3/3 |
    | PI-05 | Five columns | P2 | 🔴 | only notes |
    | PI-06 | Four columns | P3 | 🔴 |
    | PI-04 | Second band | P0 | 🟢 | mcp | `MCP-PI04b` |

    ### Data loss

    | DLP-01 | PII | P0 | ⚪ unknown | slot | `MC-DLP01` |

    ## Something else

    | JB-01 | after the coverage section | P0 | 🟢 | x | `MC-JB01` |
    """


class Coverage(Temp):
    def setUp(self):
        super().setUp()
        (self.tmp / "docs").mkdir()
        (self.tmp / "docs" / "COVERAGE.md").write_text(textwrap.dedent(COVERAGE), encoding="utf-8")
        self.patch(ingest_run, ROOT=self.tmp)

    def test_rows_status_section_and_row_tokens(self):
        self.assertEqual(ingest_run.coverage_rows(), [
            ("PI-01", "Covered", "Prompt injection", "model channel", "`MC-PI01` 3/3, `MC-PI01-02`",
             ["MC-PI01", "MC-PI01-02"]),
            ("PI-04", "Partial", "Prompt injection", "mcp", "`MCP-PI04` 3/3 | `MCP-PI04b`",
             ["MCP-PI04", "MCP-PI04b"]),
            ("PI-05", "Not covered", "Prompt injection", "", "only notes", []),
            ("PI-06", "Not covered", "Prompt injection", "", "", []),
            ("DLP-01", "Not covered", "Data loss", "slot", "`MC-DLP01`", ["MC-DLP01"])])

    def test_a_snapshot_is_taken_only_when_a_status_changed(self):
        con = sqlite3.connect(":memory:")
        con.executescript(ingest_run.SCHEMA.read_text(encoding="utf-8"))
        cov = ingest_run.coverage_rows()
        with mock.patch.object(ingest_run, "git", side_effect=lambda *a: "abc123" if a[0] == "rev-parse" else " M"):
            ingest_run.snapshot_coverage(con, cov)
            ingest_run.snapshot_coverage(con, cov)
            self.assertEqual(con.execute("SELECT snapshot_id, git_commit, git_dirty FROM coverage_snapshot").fetchall(),
                             [(1, "abc123", 1)])
            changed = [(tc, "Covered" if tc == "PI-05" else st, *rest) for tc, st, *rest in cov]
            ingest_run.snapshot_coverage(con, changed)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM coverage_snapshot").fetchone()[0], 2)
        self.assertEqual(con.execute("SELECT tc_id, status, section, target, notes FROM coverage_status"
                                     " WHERE snapshot_id = 1 AND tc_id = 'PI-05'").fetchone(),
                         ("PI-05", "Not covered", "Prompt injection", "", "only notes"))
        with mock.patch.object(ingest_run, "git", return_value=""):
            ingest_run.snapshot_coverage(con, cov)
        self.assertEqual(con.execute("SELECT git_commit, git_dirty FROM coverage_snapshot WHERE snapshot_id = 3")
                         .fetchone(), (None, 0))


def cell(ref, *runs):
    text = "".join(f"<r><t>{r}</t></r>" for r in runs)
    return f'<c r="{ref}" t="inlineStr"><is>{text}</is></c>'


def write_workbook(path: Path, rows: list[str]) -> None:
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    rns = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/workbook.xml", f'<workbook {ns} {rns}><sheets>'
                   '<sheet name="Requirements" r:id="rId1"/><sheet name="Use Cases" r:id="rId2"/>'
                   "</sheets></workbook>")
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/>'
                   '<Relationship Id="rId2" Target="/xl/worksheets/sheet2.xml"/></Relationships>')
        z.writestr("xl/worksheets/sheet1.xml", f"<worksheet {ns}><sheetData/></worksheet>")
        z.writestr("xl/worksheets/sheet2.xml",
                   f"<worksheet {ns}><sheetData>{''.join(f'<row>{r}</row>' for r in rows)}</sheetData></worksheet>")


class Catalog(Temp):
    HEADER = (cell("A3", "TC-ID") + cell("B3", "Category") + cell("C3", "Scenario (Use Case)")
              + cell("D3", "Priority") + cell("E3", " Guardrail Scope ") + cell("F3", "Attack Target")
              + cell("G3", "Measurable Success Criteria"))

    def setUp(self):
        super().setUp()
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(ingest_run.SCHEMA.read_text(encoding="utf-8"))
        self.book = self.tmp / "book.xlsx"
        self.patch(ingest_run, WORKBOOK=self.book, now=lambda: "2026-11-02T00:00:00Z")

    def test_the_use_cases_tab_is_loaded_once(self):
        write_workbook(self.book, [
            cell("A1", "AI Guardrails POC"),
            cell("A2", "PI-00") + cell("B2", "before the header"),
            self.HEADER,
            cell("A4", "PI-01") + cell("B4", "Prompt ", "injection") + cell("C4", "Direct")
            + cell("D4", "P0") + cell("E4", "In scope") + cell("F4", "Model-level") + cell("G4", " Blocks 95% "),
            cell("A5", "Notes") + cell("B5", "not a TC-ID"),
            cell("A6", "DLP-05") + cell("B6", "Data loss") + cell("D6", "P1") + cell("F6", "Both")])
        ingest_run.load_catalog(self.con)
        ingest_run.load_catalog(self.con)
        self.assertEqual(self.con.execute("SELECT * FROM catalog").fetchall(),
                         [("4.2", "book.xlsx", "2026-11-02T00:00:00Z")])
        self.assertEqual(self.con.execute("SELECT * FROM test_case ORDER BY tc_id").fetchall(), [
            ("DLP-05", "4.2", "Data loss", "", "P1", "", "Both", None),
            ("PI-01", "4.2", "Prompt injection", "Direct", "P0", "In scope", "Model-level", "Blocks 95%")])

    def test_a_missing_workbook_is_noted(self):
        _, _, err = self.quiet(ingest_run.load_catalog, self.con)
        self.assertEqual(err, "note: book.xlsx not found; catalog not loaded\n")
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM catalog").fetchone()[0], 0)


class Connect(Temp):
    def schema(self, version, migrations=()):
        d = self.tmp / "db"
        d.mkdir(exist_ok=True)
        (d / "schema.sql").write_text(f"PRAGMA user_version = {version};\nCREATE TABLE t (a);\n")
        for name, sql in migrations:
            (d / name).write_text(sql)
        self.patch(ingest_run, SCHEMA=d / "schema.sql")

    def version(self, con):
        return con.execute("PRAGMA user_version").fetchone()[0]

    def test_a_new_store_is_built_from_the_schema(self):
        self.schema(2, [("002_x.sql", "CREATE TABLE never (a); PRAGMA user_version = 2;")])
        con, out, _ = self.quiet(ingest_run.connect, self.tmp / "a" / "b" / "new.db")
        self.assertEqual((self.version(con), out), (2, ""))
        self.assertEqual(con.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.assertEqual([r[0] for r in con.execute("SELECT name FROM sqlite_master")], ["t"])

    def test_an_older_store_runs_only_the_missing_migrations(self):
        db = self.tmp / "old.db"
        old = sqlite3.connect(db)
        old.executescript("CREATE TABLE t (a); PRAGMA user_version = 1;")
        old.close()
        self.schema(3, [("001_a.sql", "CREATE TABLE one (a); PRAGMA user_version = 1;"),
                        ("002_b.sql", "CREATE TABLE two (a); PRAGMA user_version = 2;"),
                        ("003_c.sql", "CREATE TABLE three (a); PRAGMA user_version = 3;"),
                        ("004_d.sql", "CREATE TABLE four (a); PRAGMA user_version = 4;")])
        con, out, _ = self.quiet(ingest_run.connect, db)
        self.assertEqual(self.version(con), 3)
        self.assertEqual(sorted(r[0] for r in con.execute("SELECT name FROM sqlite_master")), ["t", "three", "two"])
        self.assertEqual(out.splitlines(), [f"{db}: migrated to schema 002 (002_b.sql)",
                                            f"{db}: migrated to schema 003 (003_c.sql)"])

    def test_a_store_with_no_migration_path_stops(self):
        db = self.tmp / "odd.db"
        sqlite3.connect(db).executescript("PRAGMA user_version = 7;")
        self.schema(2)
        with self.assertRaises(SystemExit) as cm:
            ingest_run.connect(db)
        self.assertEqual(str(cm.exception), f"{db}: schema version 7, db/schema.sql is 2; no migration path")


META = {"run_id": "20261102T000000Z-mcp", "config": "mcp", "started_at": "2026-11-02T00:00:00Z",
        "finished_at": "2026-11-02T00:05:00Z", "git_commit": "abc", "git_dirty": False,
        "promptfoo_version": "0.1", "carrier_model": "openai/gpt-oss-20b", "provider_pin": "deepinfra",
        "reasoning_effort": "low", "repeat": 3, "filter": "MCP-PI04", "concurrency": 1}
COV = [("PI-04", "Covered", "Prompt injection", "mcp", "", ["MCP-PI04"]),
       ("CAL-01", "Covered", "Calibration", "", "", ["CAL-01"])]


def result(desc, output, vars=None, grading=None, **extra):
    return {"testCase": {"description": desc, "vars": vars or {}}, "response": {"output": output},
            "gradingResult": grading if grading is not None else {"pass": False, "reason": "LANDED: hit"}, **extra}


class Ingest(Temp):
    def setUp(self):
        super().setUp()
        self.db = self.tmp / "store.db"
        self.run_dir = self.tmp / "runs" / "r1"
        self.run_dir.mkdir(parents=True)
        self.patch(ingest_run, WORKBOOK=self.tmp / "missing.xlsx", coverage_rows=lambda: COV,
                   git=lambda *a: "", name_flag=lambda text: "name-match" if "Globex" in text else None)

    def write(self, meta=None, results=None, slot_lines=None):
        (self.run_dir / "run.json").write_text(json.dumps({**META, **(meta or {})}))
        config = (meta or {}).get("config", META["config"])
        if results is not None:
            (self.run_dir / f"{config}.json").write_text(json.dumps(
                {"evalId": "eval-1", "results": {"stats": {"successes": 1}, "results": results}}))
        if slot_lines is not None:
            (self.run_dir / "slot_log.jsonl").write_text("\n".join(slot_lines) + "\n")

    def ingest(self):
        _, out, _ = self.quiet(ingest_run.ingest, self.run_dir, self.db)
        return out

    def rows(self, sql):
        con = sqlite3.connect(self.db)
        try:
            return con.execute(sql).fetchall()
        finally:
            con.close()

    def test_a_lab_run(self):
        lab_out = {"outcome": "LANDED", "canary_reached": True, "ran": True, "delivered": True, "detected": False,
                   "held_by": None, "hop": None, "resamples": 1, "answer": "tail " * 3,
                   "transcript": {"conversations": [], "served_by": ["DeepInfra"]},
                   "sink": {"data/exfil_log.json": "{}"}}
        self.write(results=[
            result("MCP-PI04 undefended (expect FAIL)", lab_out, {"lab": "labs/06", "oracle": "exfil_log"},
                   latencyMs=1200, cost=0.01, tokenUsage={"prompt": 10, "completion": 0}),
            result("MCP-PI04 undefended (expect FAIL)", {**lab_out, "outcome": "NOT_RUN", "ran": False}),
            result("MCP-PI04 defended (expect PASS)", {"outcome": "HELD_BLOCKED", "events": [
                {"held_by": "app:server-provenance", "action": "block", "hop": "tool_call", "subject": "sync"}]},
                {"app_control": "server-provenance"}, {"pass": True, "reason": "PROTECTED"}),
            result("MCP-GLOBEX x", "a provider error string", grading={}, error="boom"),
        ])
        out = self.ingest()
        self.assertEqual(out, f"{META['run_id']}: 4 trials ingested into {self.db}\n")
        self.assertEqual(self.rows("SELECT run_id, config, status, git_dirty, promptfoo_eval_id, provider_pin,"
                                   " guardrail_id, repeat, filter, concurrency, raw_dir, stats_json FROM run"),
                         [(META["run_id"], "mcp", "complete", 0, "eval-1", "deepinfra", "none", 3, "MCP-PI04", 1,
                           ingest_run.rel(self.run_dir), '{"successes": 1}')])
        self.assertEqual(self.rows("SELECT * FROM guardrail"), [("none", "none", None, None, "none", None, None)])
        self.assertEqual(self.rows("SELECT row_id, expected, is_defended FROM eval_row ORDER BY row_id"),
                         [("MCP-GLOBEX", "FAIL", 0), ("MCP-PI04", "FAIL", 0),
                          ("MCP-PI04-server-provenance", "PASS", 1)])
        self.assertEqual(self.rows("SELECT * FROM row_tc ORDER BY row_id"),
                         [("MCP-PI04", "PI-04"), ("MCP-PI04-server-provenance", "PI-04")])
        self.assertEqual(self.rows(
            "SELECT row_id, attempt, verdict, canary_reached, ran, reason, latency_ms, cost_usd, tokens_prompt,"
            " tokens_completion, outcome, delivered, detected, held_by, resamples FROM trial ORDER BY trial_id"), [
            ("MCP-PI04", 1, "FAIL", 1, 1, "LANDED: hit", 1200, 0.01, 10, None, "LANDED", 1, 0, None, 1),
            ("MCP-PI04", 2, "NOT-RUN", 1, 0, "LANDED: hit", None, None, None, None, "NOT_RUN", 1, 0, None, 1),
            ("MCP-PI04-server-provenance", 1, "PASS", None, None, "PROTECTED", None, None, None, None,
             "HELD_BLOCKED", None, None, None, None),
            ("MCP-GLOBEX", 1, "ERROR", None, None, "boom", None, None, None, None, None, None, None, None, None)])
        [(fmt, conversation, forwarded, final, tail, digest, size, flag)] = self.rows(
            "SELECT format, conversation, forwarded_body, final_answer, stdout_tail, sha256, bytes, flags"
            " FROM transcript WHERE trial_id = 1")
        self.assertEqual((fmt, json.loads(conversation)["served_by"], forwarded, final, tail, flag),
                         ("lab-compact-v1", ["DeepInfra"], None, None, "tail " * 3, None))
        self.assertEqual((digest, size), (ingest_run.sha(conversation + "tail " * 3), len(conversation) + 15))
        self.assertEqual(self.rows("SELECT trial_id, kind, source_path FROM evidence ORDER BY evidence_id"),
                         [(1, "oob_exfil", "data/exfil_log.json"), (2, "oob_exfil", "data/exfil_log.json"),
                          (3, "block_event", "stdout")])

    def test_a_model_channel_run_joins_the_slot_log(self):
        wire = {"trial_id": "t-1", "mode": "demo", "action": "mask", "reason": "demo: masked PII",
                "received": {"messages": ["raw"]}, "forwarded": {"messages": ["masked"]}, "reply": {"id": "r"}}
        self.write(meta={"config": "model_channel", "run_id": "r-mc", "guardrail_id": "demo", "provider_pin": "",
                         "filter": "", "status": "complete"},
                   results=[result("CAL-01 x", {"outcome": "HELD_BLOCKED", "trial_id": "t-1", "slot_action": "mask",
                                                "answer": "Globex says hi"},
                                   {"oracle": "pii_forwarded"}, prompt={"raw": "my ssn"}),
                            result("MC-PI01-02 y", {"outcome": "LANDED", "trial_id": "t-unknown", "answer": "ok"})],
                   slot_lines=[json.dumps({**wire, "action": "allow"}), "", json.dumps(wire)])
        out = self.ingest()
        self.assertEqual(out, f"r-mc: 2 trials ingested into {self.db}; 1 transcripts flagged name-match\n")
        self.assertEqual(self.rows("SELECT provider_pin, filter, guardrail_id FROM run"), [(None, None, "demo")])
        self.assertEqual(self.rows("SELECT guardrail_id, vendor, placement FROM guardrail"), [("demo", "demo", "none")])
        self.assertEqual(self.rows("SELECT row_id, family, is_calibration FROM eval_row ORDER BY row_id"),
                         [("CAL-01", "CAL", 1), ("MC-PI01-02", "MC-PI01", 0)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM row_tc"), [(0,)])
        self.assertEqual(self.rows("SELECT guardrail_action FROM trial ORDER BY trial_id"), [("mask",), (None,)])
        [(fmt, conversation, forwarded, final, tail, flag), second] = self.rows(
            "SELECT format, conversation, forwarded_body, final_answer, stdout_tail, flags FROM transcript"
            " ORDER BY trial_id")
        self.assertEqual((fmt, json.loads(conversation), json.loads(forwarded), final, tail, flag),
                         ("model-channel-v1", {"prompt": "my ssn", "request": {"messages": ["raw"]},
                                               "reply": {"id": "r"}},
                          {"messages": ["masked"]}, "Globex says hi", None, "name-match"))
        self.assertEqual((json.loads(second[1]), second[2]), ({"prompt": None, "request": None, "reply": "ok"}, None))
        self.assertEqual(self.rows("SELECT trial_id, kind, summary, payload, source_path FROM evidence"),
                         [(1, "slot_wire", "slot demo: mask (demo: masked PII)", forwarded, "slot_log.jsonl")])

    def test_text_is_stored_as_written_and_vars_hash_ignores_key_order(self):
        self.write(meta={"config": "model_channel"}, results=[
            result("MC-X1 x", {"outcome": "LANDED", "trial_id": "t-1", "answer": "a"}, {"a": 1, "b": "ü"}),
            result("MC-X1 x", {"outcome": "LANDED", "answer": "a", "transcript": {"note": "München"}},
                   {"b": "ü", "a": 1})],
            slot_lines=[json.dumps({"trial_id": "t-1", "mode": "none", "action": "allow",
                                    "received": "Grüße", "forwarded": "Grüße", "reply": "Grüße"})])
        self.ingest()
        [(h1,), (h2,)] = self.rows("SELECT row_vars_sha256 FROM trial ORDER BY trial_id")
        self.assertEqual(h1, h2)
        [(c1, f1), (c2, _)] = self.rows("SELECT conversation, forwarded_body FROM transcript ORDER BY trial_id")
        self.assertEqual(("Grüße" in c1, "Grüße" in f1, "München" in c2), (True, True, True))

    def test_a_vendor_guardrail_is_registered_by_its_id(self):
        self.write(meta={"guardrail_id": "paloalto-airs-1-ai_gateway", "placement": "ai_gateway"}, results=[])
        self.ingest()
        self.assertEqual(self.rows("SELECT guardrail_id, vendor, placement FROM guardrail"),
                         [("paloalto-airs-1-ai_gateway", "paloalto", "ai_gateway")])

    def test_a_slot_action_without_a_reason(self):
        self.write(meta={"config": "model_channel"},
                   results=[result("MC-X1 x", {"outcome": "LANDED", "trial_id": "t-1"})],
                   slot_lines=[json.dumps({"trial_id": "t-1", "mode": "none", "action": "allow"})])
        self.ingest()
        self.assertEqual(self.rows("SELECT summary, payload FROM evidence"), [("slot none: allow", "null")])

    def test_a_run_without_promptfoo_output_is_recorded_as_aborted(self):
        self.write()
        self.assertEqual(self.ingest(), f"{META['run_id']}: no promptfoo output; recorded as aborted\n")
        self.assertEqual(self.rows("SELECT status, promptfoo_eval_id, stats_json FROM run"), [("aborted", None, None)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM trial"), [(0,)])

    def test_a_run_is_ingested_once(self):
        self.write(results=[result("MCP-PI04 x", {"outcome": "LANDED"})])
        self.ingest()
        self.assertEqual(self.ingest(), f"{META['run_id']}: already ingested, skipped\n")
        self.assertEqual(self.rows("SELECT COUNT(*) FROM trial"), [(1,)])

    def test_main_reads_the_run_dir_and_the_db(self):
        with mock.patch.object(ingest_run, "ingest") as ingest, \
                mock.patch.object(sys, "argv", ["ingest_run.py", "runs/r1", "--db", "x.db"]):
            ingest_run.main()
        ingest.assert_called_once_with(Path("runs/r1").resolve(), Path("x.db"))
        with mock.patch.object(ingest_run, "ingest") as ingest, mock.patch.object(sys, "argv", ["i", "r"]):
            ingest_run.main()
        self.assertEqual(ingest.call_args.args[1], ingest_run.DEFAULT_DB)

    def test_help_names_the_job(self):
        out = io.StringIO()
        with mock.patch.object(sys, "argv", ["ingest_run.py", "--help"]), contextlib.redirect_stdout(out), \
                self.assertRaises(SystemExit):
            ingest_run.main()
        self.assertIn("Load one eval run into the results store", out.getvalue())


if __name__ == "__main__":
    unittest.main()
