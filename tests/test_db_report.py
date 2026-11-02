"""The results-store report (scripts/db_report.py), on a temp store built by the ingest.

A fresh store has no legacy catalog rows: everything the report shows per TC-ID must come from
the COVERAGE.md snapshot the ingest takes.
"""

from __future__ import annotations

import contextlib
import io
import re
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).absolute().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import db_report  # noqa: E402
import ingest_run  # noqa: E402

COVERAGE = """\
    ## Coverage by TC-ID

    ### Prompt injection

    | TC-ID | Scenario | P | Status | Harness | Rows |
    |---|---|---|---|---|---|
    | PI-08 | Stateful | P3 | 🟢 | memory | `PI-08` 3/3 |
    | PI-04 | Cross-server | P0 | 🟢 | mcp | `MCP-PI04` 3/3 |
    """


class TcReport(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "docs").mkdir()
        (self.root / "docs" / "COVERAGE.md").write_text(textwrap.dedent(COVERAGE), encoding="utf-8")
        self.db = self.root / "store.db"
        with mock.patch.object(ingest_run, "ROOT", self.root), mock.patch.object(ingest_run, "git", return_value=""):
            con = ingest_run.connect(self.db)
            with con:
                ingest_run.snapshot_coverage(con, ingest_run.coverage_rows())
            con.close()

    def report(self, *argv):
        out = io.StringIO()
        with mock.patch.object(sys, "argv", ["db_report.py", "--db", str(self.db), *argv]), \
                contextlib.redirect_stdout(out):
            db_report.main()
        return out.getvalue()

    def test_a_fresh_store_shows_each_tc_ids_priority_from_coverage(self):
        out = self.report()
        lines = [ln for ln in out.splitlines() if re.match(r"^(PI-04|PI-08) ", ln)]
        self.assertEqual([ln.split()[:3] for ln in lines], [["PI-04", "P0", "Covered"], ["PI-08", "P3", "Covered"]])


if __name__ == "__main__":
    unittest.main()
