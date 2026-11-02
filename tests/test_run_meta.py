"""run.json as run_evals.sh writes it (scripts/run_meta.py): start, finish and the EXTRA_HEADERS gate.

In process, with git and promptfoo faked, a fixed clock and an empty .env view: no subprocess, no
model call, nothing written outside a temp dir.
"""

from __future__ import annotations

import itertools
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).absolute().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import run_meta  # noqa: E402

BASE = "http://gateway.example/v1"
START, END = "2026-11-02T09:00:00Z", "2026-11-02T09:01:00Z"


def outputs(dirty="", pf="npm warn one\nnpm warn two\n0.118.4"):
    return {("git", "rev-parse", "HEAD"): "abc123",
            ("git", "status", "--porcelain", "--untracked-files=no"): dirty,
            ("promptfoo", "--version"): pf}


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.run_dir = Path(self.tmp.name) / "runs" / "rid"
        self.clock = (f"2026-11-02T09:{m:02d}:00Z" for m in itertools.count())
        self.env(), self.dotenv()

    def env(self, **kw):
        p = mock.patch.dict(os.environ, kw, clear=True)
        p.start()
        self.addCleanup(p.stop)

    def dotenv(self, **kw):
        p = mock.patch.dict(run_meta._DOTENV, kw, clear=True)
        p.start()
        self.addCleanup(p.stop)

    def cli(self, *argv, sh=None):
        sh = outputs() if sh is None else sh
        with mock.patch.object(sys, "argv", ["run_meta.py", *argv]), \
                mock.patch.object(run_meta, "sh", side_effect=lambda *c: sh[c]), \
                mock.patch.object(run_meta, "now", side_effect=lambda: next(self.clock)):
            return run_meta.main()

    def meta(self):
        return json.loads((self.run_dir / "run.json").read_text(encoding="utf-8"))


class Dotenv(Case):
    def test_the_environment_wins_over_dotenv(self):
        self.dotenv(MODEL="from-file")
        self.assertEqual(run_meta.dotenv("MODEL"), "from-file")
        self.env(MODEL="from-env")
        self.assertEqual(run_meta.dotenv("MODEL"), "from-env")

    def test_an_empty_environment_value_falls_back_and_missing_is_empty(self):
        self.dotenv(MODEL="from-file")
        self.env(MODEL="")
        self.assertEqual((run_meta.dotenv("MODEL"), run_meta.dotenv("NOPE")), ("from-file", ""))


class Sh(unittest.TestCase):
    def test_runs_from_the_repo_root_and_returns_trimmed_text(self):
        cwd = run_meta.sh(sys.executable, "-c", "import os; print(' ' + os.getcwd() + ' ')")
        self.assertEqual(cwd, str(run_meta.ROOT))


class HeaderNames(Case):
    def test_no_table_is_no_headers(self):
        self.assertEqual(run_meta.header_names(), {})

    def test_names_per_endpoint_never_values(self):
        self.dotenv(EXTRA_HEADERS=json.dumps({BASE: {"x-model": "m-1", "x-project-id": "${PROJ}"}}), PROJ="p-9")
        self.assertEqual(run_meta.header_names(), {BASE: ["x-model", "x-project-id"]})

    def test_a_referenced_variable_may_come_from_the_environment(self):
        self.dotenv(EXTRA_HEADERS=json.dumps({BASE: {"x-project-id": "${PROJ}"}}))
        self.env(PROJ="p-9")
        self.assertEqual(run_meta.header_names(), {BASE: ["x-project-id"]})

    def test_an_unset_variable_is_an_error_naming_it(self):
        self.dotenv(EXTRA_HEADERS=json.dumps({BASE: {"a": "${ONE}", "b": "${TWO}"}}))
        self.assertEqual(run_meta.header_names(), {"error": "unset variable(s): ONE, TWO"})

    def test_a_bad_table_is_an_error(self):
        self.env(EXTRA_HEADERS="{bad")
        self.assertIn("error", run_meta.header_names())


class CheckHeaders(Case):
    def test_a_good_table_passes_and_writes_nothing(self):
        self.assertIsNone(self.cli("check-headers"))
        self.assertFalse(self.run_dir.exists())

    def test_a_table_that_cannot_apply_stops_the_run(self):
        self.dotenv(EXTRA_HEADERS=json.dumps({BASE: {"a": "${NOPE_UNSET}"}}))
        with self.assertRaises(SystemExit) as stop:
            self.cli("check-headers")
        self.assertEqual(str(stop.exception.code), "EXTRA_HEADERS: unset variable(s): NOPE_UNSET")


class Start(Case):
    def test_records_the_run_context(self):
        self.dotenv(MODEL="openai/gpt-oss-20b", OPENROUTER_PROVIDER_ONLY="deepinfra",
                    EXTRA_HEADERS=json.dumps({BASE: {"x-model": "m"}}))
        self.env(GUARDRAIL_ID="vendor-a", REPEAT="3", FILTER="^PI-04")
        self.cli("start", str(self.run_dir), "rid", "tools", "4")
        self.assertEqual(self.meta(), {
            "run_id": "rid", "config": "tools", "started_at": START, "finished_at": None,
            "status": "running", "git_commit": "abc123", "git_dirty": False,
            "promptfoo_version": "0.118.4", "carrier_model": "openai/gpt-oss-20b",
            "provider_pin": "deepinfra", "extra_headers": {BASE: ["x-model"]},
            "guardrail_id": "vendor-a", "repeat": 3, "filter": "^PI-04", "concurrency": 4})

    def test_defaults_when_nothing_is_set(self):
        self.cli("start", str(self.run_dir), "rid", "tools", "1", sh=outputs(dirty=" M x.py", pf=""))
        meta = self.meta()
        self.assertEqual({k: meta[k] for k in ("git_dirty", "promptfoo_version", "carrier_model", "provider_pin",
                                                "extra_headers", "guardrail_id", "repeat", "filter")},
                         {"git_dirty": True, "promptfoo_version": "unknown", "carrier_model": "",
                          "provider_pin": None, "extra_headers": {}, "guardrail_id": "none", "repeat": 1,
                          "filter": None})

    def test_a_header_table_that_cannot_apply_is_recorded_not_raised(self):
        self.dotenv(EXTRA_HEADERS="{bad")
        self.cli("start", str(self.run_dir), "rid", "tools", "1")
        self.assertIn("error", self.meta()["extra_headers"])

    def test_the_file_ends_with_a_newline(self):
        self.cli("start", str(self.run_dir), "rid", "tools", "1")
        self.assertTrue((self.run_dir / "run.json").read_text(encoding="utf-8").endswith("}\n"))


class Finish(Case):
    def finish(self, code, results=True):
        self.cli("start", str(self.run_dir), "rid", "tools", "1")
        if results:
            (self.run_dir / "tools.json").write_text("{}")
        self.cli("finish", str(self.run_dir), str(code))
        return self.meta()

    def test_promptfoo_0_and_100_with_results_are_complete(self):
        for code in (0, 100):
            with self.subTest(code=code):
                meta = self.finish(code)
                self.assertEqual((meta["status"], meta["exit_code"]), ("complete", code))

    def test_any_other_exit_is_aborted(self):
        for code in (1, 2, 101, -9):
            with self.subTest(code=code):
                self.assertEqual(self.finish(code)["status"], "aborted")

    def test_no_results_file_is_aborted(self):
        self.assertEqual(self.finish(0, results=False)["status"], "aborted")

    def test_the_start_context_is_kept_and_the_end_time_added(self):
        meta = self.finish(0)
        self.assertEqual((meta["started_at"], meta["finished_at"], meta["run_id"], meta["git_commit"]),
                         (START, END, "rid", "abc123"))


if __name__ == "__main__":
    unittest.main()
