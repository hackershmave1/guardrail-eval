#!/usr/bin/env python3
"""Report from the results store (read-only). Design: docs/RESULTS_DB.md.

    python3 scripts/db_report.py                 # coverage + latest result per TC-ID
    python3 scripts/db_report.py --runs          # every run, newest first
    python3 scripts/db_report.py --rows          # latest result per eval row
    python3 scripts/db_report.py --guardrail X   # restrict to one guardrail (default: all)
    python3 scripts/db_report.py --db PATH

A row "landed" if any attempt FAILed (any landed attempt = vulnerable). NOT-RUN and
ERROR are reported separately and never count as held. Calibration rows are excluded.

Three numbers per row (view v_row_run, from promptfoo/providers/outcome.py):
  effect     landed / valid trials (valid = PASS + FAIL)
  detect     on-target enforcement events / delivered trials
  n/r        NOT-RUN rate; a row with nothing landed and over 1/3 NOT-RUN is inconclusive
An app-control twin whose undefended baseline did not run in the same run_id is
marked "stale": its delta proves nothing.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).absolute().parents[1]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", type=Path, default=ROOT / "results" / "guardrail_eval.db")
    ap.add_argument("--runs", action="store_true")
    ap.add_argument("--rows", action="store_true")
    ap.add_argument("--guardrail")
    a = ap.parse_args()
    if not a.db.exists():
        sys.exit(f"{a.db} does not exist yet: run scripts/run_evals.sh first")
    con = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    gr = "AND v.guardrail_id = :g" if a.guardrail else ""
    args = {"g": a.guardrail}

    if a.runs:
        print(f"{'run_id':34} {'status':9} {'guardrail':10} {'carrier':22} {'rep':>3} {'trials':>6}  filter")
        for row in con.execute(
                "SELECT r.run_id, r.status, r.guardrail_id, r.carrier_model, r.repeat,"
                " (SELECT COUNT(*) FROM trial t WHERE t.run_id = r.run_id), COALESCE(r.filter, '')"
                " FROM run r ORDER BY r.started_at DESC"):
            print(f"{row[0]:34} {row[1]:9} {row[2]:10} {row[3]:22} {row[4]:>3} {row[5]:>6}  {row[6]}")
        return

    if a.rows:
        print(f"{'row':28} {'guardrail':10} {'landed':>6} {'held':>5} {'block':>5} {'n/r':>4}"
              f" {'effect':>6} {'detect':>6} {'n/r%':>5}  flags  last run")
        pct = lambda x: "" if x is None else f"{x:.0%}"
        for row in con.execute(
                f"SELECT v.row_id, v.guardrail_id, v.failed, v.passed, COALESCE(v.blocked, 0), v.not_run,"
                f" v.effect_rate, v.detection_rate, v.not_run_rate, v.inconclusive, v.baseline_stale, v.run_id"
                f" FROM v_row_latest v JOIN eval_row e USING (row_id)"
                f" WHERE e.is_calibration = 0 {gr} ORDER BY v.row_id", args):
            flags = " ".join(f for f, on in (("inconclusive", row[9]), ("stale", row[10])) if on)
            print(f"{row[0]:28} {row[1]:10} {row[2]:>6} {row[3]:>5} {row[4]:>5} {row[5]:>4}"
                  f" {pct(row[6]):>6} {pct(row[7]):>6} {pct(row[8]):>5}  {flags:5}  {row[11]}")
        return

    counts = dict(con.execute("SELECT status, COUNT(*) FROM v_coverage_latest GROUP BY status"))
    print("Coverage (latest COVERAGE.md snapshot): "
          + ", ".join(f"{counts.get(s, 0)} {s.lower()}" for s in ("Covered", "Partial", "Not covered")))
    print(f"\n{'TC-ID':8} {'P':3} {'status':12} {'attack (undefended)':22} {'defended twin':16} last run")
    for row in con.execute(
            f"""SELECT c.tc_id, COALESCE(c.priority, tc.priority) AS prio, c.status,
                   SUM(CASE WHEN e.is_defended = 0 AND e.is_control = 0 THEN v.failed END),
                   SUM(CASE WHEN e.is_defended = 0 AND e.is_control = 0 THEN v.attempts - v.not_run END),
                   SUM(CASE WHEN e.is_defended = 0 AND e.is_control = 0 THEN v.not_run END),
                   SUM(CASE WHEN e.is_defended = 1 THEN v.failed END),
                   SUM(CASE WHEN e.is_defended = 1 THEN v.attempts - v.not_run END),
                   MAX(v.started_at),
                   MAX(CASE WHEN e.is_defended = 1 THEN v.baseline_stale END)
                FROM v_coverage_latest c
                LEFT JOIN test_case tc ON tc.tc_id = c.tc_id
                LEFT JOIN row_tc rt ON rt.tc_id = c.tc_id
                LEFT JOIN eval_row e ON e.row_id = rt.row_id AND e.is_calibration = 0
                LEFT JOIN v_row_latest v ON v.row_id = e.row_id {gr}
                GROUP BY c.tc_id ORDER BY prio, c.tc_id""", args):
        tc, prio, status, landed, ran, notrun, d_landed, d_ran, last, stale = row
        attack = "" if ran is None and not notrun else (
            f"landed {landed}/{ran}" if landed else f"held {ran}/{ran}" if ran else "not run")
        if notrun:
            attack += f" ({notrun} n/r)"
        twin = "" if not d_ran else (f"bypassed {d_landed}/{d_ran}" if d_landed else f"holds {d_ran}/{d_ran}")
        if twin and stale:
            twin += " stale"
        print(f"{tc:8} {prio or '?':3} {status:12} {attack:22} {twin:16} {(last or '')[:10]}")


if __name__ == "__main__":
    main()
