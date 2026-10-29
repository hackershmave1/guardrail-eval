"""notebooks/operator_lib.py: the doc parsers against the real COVERAGE.md / TEST_CASES.md (so a
format change in the docs fails here, not silently in the notebook), escaping, and the FILTER
built for a pick. Read-only: no runs, no model calls.

    venv/bin/python -m unittest discover -s tests
"""

from __future__ import annotations

import ast
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "notebooks"))
import operator_lib as op  # noqa: E402


class Docs(unittest.TestCase):
    """The notebook's cards come from the docs; these break when the docs drift."""

    @classmethod
    def setUpClass(cls):
        cls.cov = op.coverage()
        cls.entries = [en for s in op.test_cases() for en in s["entries"]]

    def test_coverage_parses_every_tc(self):
        self.assertEqual(len(self.cov), 42)
        for tc, c in self.cov.items():
            self.assertIn(c["status"], op.STATUS_STYLE, tc)
            self.assertRegex(c["priority"], r"^P[0-3]$", tc)
            self.assertTrue(c["scenario"], tc)

    def test_every_covered_tc_has_spec_and_rows(self):
        for tc, c in self.cov.items():
            if c["status"] == "Not covered":
                continue
            with self.subTest(tc=tc):
                self.assertTrue([en for en in self.entries if tc in en["tc_ids"]], "no TEST_CASES.md entry")
                ex = op.expand(tc=tc)
                self.assertTrue(ex["rows"], "COVERAGE.md tokens match no row in the configs")
                self.assertTrue(ex["filter"].startswith("^("))

    def test_entries_have_fields(self):
        for en in self.entries:
            with self.subTest(heading=en["heading"]):
                # `- **Field:**` bullets, or prose / plain bullets / a battery table (shown as notes)
                self.assertTrue(en["fields"] or any(n.strip() for n in en["notes"]), "entry parsed empty")
                self.assertIn(en["config"], op.CONFIGS)

    def test_every_card_renders(self):
        for tc in self.cov:
            self.assertIn(tc, op.card_html(tc))


class Parsing(unittest.TestCase):
    def test_heading_variants(self):
        h = op._heading("RAG-PI02 / RAG-PI02b — Indirect injection via a poisoned RAG doc (PI-02, P0)")
        self.assertEqual((h["rows"], h["tc_ids"], h["priority"]), (["RAG-PI02", "RAG-PI02b"], ["PI-02"], "P0"))
        h = op._heading("A2A-KC — MCP → A2A kill chain (PI-04, A2A variant, P0)")
        self.assertEqual((h["tc_ids"], h["priority"]), (["PI-04"], "P0"))
        h = op._heading("MC-OUT01 / MC-OUT02 — Malicious content in the output (OUT-01, OUT-02, P1)")
        self.assertEqual(h["tc_ids"], ["OUT-01", "OUT-02"])
        h = op._heading("CAL-01..06 — Oracle calibration (no TC-ID)")
        self.assertEqual((h["tc_ids"], h["priority"]), ([], None))
        h = op._heading("PI-08 — Stateful cross-turn injection (PI-08, P3)")
        self.assertEqual((h["rows"], h["tc_ids"]), (["PI-08"], ["PI-08"]))
        h = op._heading(r"WEB-SRV-\<carrier\> — The same hidden instruction in 11 page carriers (PI-03, P0)")
        self.assertEqual(h["rows"], ["WEB-SRV-<carrier>"])

    def test_md_escapes_before_markup(self):
        out = op.md_html(["- **Attack:** `<img src=x onerror=alert(1)>` and <script>x</script>"])
        self.assertNotIn("<script", out)
        self.assertNotIn("<img", out)
        self.assertIn("<code>&lt;img", out)

    def test_trial_html_escapes_payloads(self):
        t = {"row_id": "R", "attempt": 1, "outcome": "LANDED", "delivered": True, "detected": False,
             "held_by": None, "hop": None, "intercept_action": None, "events": [], "note": "<b>zq</b>",
             "resamples": 0, "marker": "x", "injection_note": None, "attack": "<script>a</script>",
             "delivery": [("user message", "<svg onload=x>")], "tool_calls": ["f <x>"], "answer": "<script>",
             "forwarded_user_text": None, "slot_action": None, "sink": {"f": "<iframe>"}, "benign": False}
        out = op.trial_html(t)
        for tag in ("<script", "<svg", "<iframe", "<b>zq"):
            self.assertNotIn(tag, out)

    def test_row_pick_filter_is_exact_word(self):
        ex = op.expand(word="TOOLS-CTRL-delete")
        self.assertEqual(ex["configs"], ["tools"])
        self.assertTrue(re.search(ex["filter"], ex["rows"][0]["description"]))
        self.assertFalse(re.search(ex["filter"], "TOOLS-CTRL-deleteX something"))

    def test_row_state_matches_summarize_rule(self):
        base = {"failed": 0, "passed": 0, "inconclusive": 0}
        self.assertEqual(op.row_state({**base, "failed": 1}), "FAIL")
        self.assertEqual(op.row_state(base), "NOT-RUN")
        self.assertEqual(op.row_state({**base, "passed": 1, "inconclusive": 1}), "INCONCLUSIVE")
        self.assertEqual(op.row_state({**base, "passed": 3}), "PASS")


class Host(unittest.TestCase):
    """What the editor hands the kernel, and what the editor's theme does to our HTML."""

    def test_vscode_env_misparse_is_undone(self):
        from unittest import mock
        dot = {"MODEL": "openai/gpt-oss-20b", "API_KEY": "k1", "OTHER": "x"}
        shell = {"MODEL": "openai/gpt-oss-20b   # any OpenRouter model id", "API_KEY": '"k1"',
                 "OTHER": "deliberately-different"}
        with mock.patch.object(op, "dotenv_values", return_value=dot), mock.patch.dict(op.os.environ, shell):
            env = op.host_env()
        self.assertEqual(env["MODEL"], "openai/gpt-oss-20b")
        self.assertEqual(env["API_KEY"], "k1")
        self.assertEqual(env["OTHER"], "deliberately-different")

    def test_page_styles_inline(self):
        out = op.page('<table><tr><th colspan="2" style="x:y">h</th></tr><tr><td><code>c</code></td></tr></table>')
        self.assertIn('<code style="background:rgba(127,127,127,.2)!important', out)
        self.assertIn('style="text-align:left!important', out)
        self.assertIn(";x:y", out)  # the element's own style is kept, after ours
        self.assertNotIn("<style>", out)
        # escaped text that looks like a tag is never touched
        self.assertIn("&lt;code&gt;", op.page(op.e("<code>")))


class Refusals(unittest.TestCase):
    def test_real_needs_confirmation(self):
        with self.assertRaises(PermissionError):
            op.launch(["tools"], mode="real", repeat=1, filt="CTRL-delete", overrides={})

    def test_only_guardrail_none(self):
        with self.assertRaises(ValueError):
            op.launch(["tools"], mode="scratch", repeat=1, filt="CTRL-delete", overrides={}, guardrail_id="demo")

    def test_bad_headers_refused_names_only(self):
        errors, _ = op.preflight(["tools"], "scratch", {"EXTRA_HEADERS": '{"http://h/v1": {"h": "${NOPE_UNSET_VAR}"}}'})
        self.assertTrue(any("NOPE_UNSET_VAR" in e for e in errors))

    def test_settings_never_show_secret_values(self):
        out = op.settings_html({"EXTRA_HEADERS": '{"http://h/v1": {"x-k": "SECRETVALUE"}}'})
        self.assertNotIn("SECRETVALUE", out)
        self.assertIn("x-k", out)


class LabReport(unittest.TestCase):
    """The lab-report components and the four-step demo. Read-only: replay never calls a model."""

    def test_every_outcome_has_a_verdict_line_and_readable_ink(self):
        for oc, (fill, _) in op.OUTCOME_STYLE.items():
            self.assertIn(oc, op.VERDICT_LINE)
            self.assertIn(op._ink(fill), (op.T["ink_light"], op.T["ink_dark"]))
        self.assertEqual(op._ink(op.OUTCOME_STYLE["LANDED"][0]), op.T["ink_light"])
        self.assertEqual(op._ink(op.OUTCOME_STYLE["HELD_RESISTED"][0]), op.T["ink_dark"])
        self.assertEqual(op.VERDICT_LINE["HELD_RESISTED"][1], "PASS · not credited to any guardrail")

    def test_payload_card_escapes_and_marks(self):
        out = op.payload_card("say <script>x</script> then RUN-THIS now", "seeded file", r"RUN-\w+")
        self.assertNotIn("<script", out)
        self.assertIn(">RUN-THIS</mark>", out)
        out = op.payload_card("a" * 50, "user prompt", "[", limit=10)  # a bad regex marks nothing
        self.assertIn("40 more chars", out)

    def test_phase1_trace_names_the_missing_vendor(self):
        t = {"row_id": "R", "attempt": 1, "outcome": "LANDED", "delivered": True, "detected": False,
             "held_by": None, "hop": None, "intercept_action": None, "events": [], "note": "hit", "resamples": 0,
             "marker": None, "injection_note": None, "attack": "a", "delivery": [], "tool_calls": [],
             "answer": "", "forwarded_user_text": None, "slot_action": None, "sink": {}, "benign": False}
        out = op.trace_html(t)
        self.assertIn(op.PHASE1_GUARDRAIL, out)
        self.assertEqual(out.count("<tr>"), 7)  # header + all six steps

    def test_demo_resolves_a_row(self):
        d = op.Demo("TOOLS-EXF02")
        self.assertEqual(d.config, "tools")
        self.assertIn("EXF-02", d.tc_ids())
        self.assertIn("seeded file", d.attack_html())
        with self.assertRaises(ValueError):
            op.Demo("NO-SUCH-ROW")

    def test_demo_accepts_a_tc_id(self):
        tc = next(t for t, c in op.coverage().items() if op.expand(tc=t)["rows"])
        d = op.Demo(tc)
        self.assertIn(d.word, {r["word"] for r in op.expand(tc=tc)["rows"]})
        self.assertIn(tc, d.tc_ids())

    def test_live_logs_only_for_oob_rows(self):
        web = op.Demo("WEB-SRV-comment")
        self.assertTrue(any("web_server.out" in str(p) for _, p in web.live_logs()))
        self.assertIn("snapshot", web.watch_html().lower())


def _row(row_id, state, tcs=("PI-04",), **kw):
    base = {"row_id": row_id, "description": f"{row_id} x", "state": state, "tc_ids": list(tcs), "is_control": 0,
            "is_defended": 0, "is_calibration": 0, "expected": "FAIL", "attempts": 3, "failed": 0, "passed": 0,
            "not_run": 0, "blocked": 0, "resisted": 0, "delivered": 0, "detected": 0, "outcomes": {},
            "baseline_stale": None, "config": "mcp", "run_id": "r1"}
    return {**base, **kw}


class OperatorFlow(unittest.TestCase):
    """B1 set up, B2 run, B3 review: the logic behind the widgets, no runs and no model calls."""

    def test_vendor_profiles_are_gated(self):
        for profile in ("gateway", "guardrail_api"):
            with self.subTest(profile=profile), self.assertRaises(ValueError):
                op.launch(["tools"], mode="scratch", repeat=1, filt="CTRL-delete", overrides={}, profile=profile)
            errors, _ = op.preflight(["tools"], "scratch", {}, profile=profile)
            self.assertTrue(any("Phase B" in x for x in errors))

    def test_guardrail_id_follows_the_store_naming(self):
        self.assertEqual(op.guardrail_id(op.Session()), "none")
        s = op.Session(profile="gateway", vendor="Palo Alto AIRS", product="AI Runtime", version="2026.10")
        self.assertEqual(op.guardrail_id(s), "pa-airs-airuntime-2026.10-ai_gateway")
        self.assertTrue(op.guardrail_id(op.Session(profile="guardrail_api")).endswith("-sdk"))

    def test_gateway_routes_both_channels(self):
        eff = {"GATEWAY_BASE_URL": ("https://gw/v1", ".env"), "GATEWAY_API_KEY": ("k", ".env")}
        env = op.profile_env("gateway", eff)
        self.assertEqual((env["LLM_BASE_URL"], env["UPSTREAM_BASE_URL"]), ("https://gw/v1", "https://gw/v1"))
        self.assertEqual(op.profile_env("direct", eff), {})

    def test_target_never_shows_secret_values(self):
        from unittest import mock
        dot = {"API_KEY": "SECRETKEY1", "GATEWAY_API_KEY": "SECRETKEY2", "MODEL": op.CARRIER}
        with mock.patch.object(op, "dotenv_values", return_value=dot):
            for profile in op.PROFILES:
                out = op.target_html(op.Session(profile=profile, vendor="Noma"))
                self.assertNotIn("SECRETKEY", out)

    def test_tc_verdict_any_landed_row_fails(self):
        rows = [_row("A", "PASS", passed=3), _row("B", "FAIL", failed=1, passed=2),
                _row("C", "FAIL", is_defended=1, tcs=("SUP-01",)), _row("D", "PASS", tcs=("SUP-01",), passed=3)]
        board = {b["tc"]: b for b in op.tc_scoreboard(rows)}
        self.assertEqual(board["PI-04"]["state"], "FAIL")
        self.assertEqual((board["PI-04"]["landed_rows"], board["PI-04"]["rows"]), (1, 2))
        self.assertEqual(board["SUP-01"]["state"], "PASS")  # an app twin never decides the TC verdict
        self.assertEqual(len(board["SUP-01"]["twins"]), 1)

    def test_unjudged_rows_make_the_tc_inconclusive_and_are_listed(self):
        rows = [_row("A", "PASS", passed=3), _row("B", "NOT-RUN", not_run=3),
                _row("CTL", "FAIL", is_control=1, outcomes={"FALSE_POSITIVE": 1}),
                _row("CAL-05", "PASS", is_calibration=1, tcs=())]
        self.assertEqual(op.tc_scoreboard(rows)[0]["state"], "INCONCLUSIVE")
        why = {r["row_id"]: w for r, w in op.problems(rows)}
        self.assertIn("rerun", why["B"])
        self.assertIn("false positive", why["CTL"])
        self.assertIn("calibration", why["CAL-05"])
        self.assertNotIn("A", why)

    def test_determination_names_the_rule_and_escapes(self):
        t = {"row_id": "R", "attempt": 1, "outcome": "LANDED", "delivered": True, "detected": False,
             "held_by": None, "hop": None, "intercept_action": None, "events": [], "note": "<b>zq</b>",
             "resamples": 0, "marker": "x", "injection_note": None, "attack": "a", "delivery": [("user message", "x")],
             "tool_calls": [], "answer": "", "forwarded_user_text": None, "slot_action": None, "sink": {},
             "benign": False, "oracle": "exfil_log", "oracle_note": "<img src=x>"}
        out = op.determination_html(t)
        self.assertIn("Sink hit = FAIL, always", out)
        self.assertNotIn("<img", out)
        self.assertNotIn("<b>zq", out)
        for oc in op.OUTCOME_STYLE:
            self.assertIn(oc, op.DECIDED_BY)


class NotebookFlow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cells = json.loads((ROOT / "notebooks" / "operator.ipynb").read_text())["cells"]
        cls.code = [(i, "".join(c["source"])) for i, c in enumerate(cls.cells) if c["cell_type"] == "code"]

    def cell_with(self, text):
        matches = [i for i, source in self.code if text in source]
        self.assertTrue(matches, f"missing notebook code: {text}")
        return matches[0]

    def test_demo_precedes_console_in_story_order(self):
        steps = [
            "demo = op.Demo(DEMO_ROW)",
            "demo.attack_html()",
            "op.setup_html(S, DEMO_ROW, RUN_LIVE, REPEAT)",
            "demo_result = demo.execute(S, live=RUN_LIVE, repeat=REPEAT)",
            "demo.verdict_html(demo_result)",
            "op.run_ui(S)",
        ]
        indices = [self.cell_with(step) for step in steps]
        self.assertEqual(indices, sorted(set(indices)))
        self.assertLess(self.cell_with("demo.listener_html()"), self.cell_with("demo_result = demo.execute"))
        self.assertEqual(self.cell_with("demo.listener_html(demo_result)"), self.cell_with("demo.verdict_html"))

    def test_demo_defaults_to_replay_with_three_repeats(self):
        source = "".join(self.cells[self.cell_with("demo = op.Demo(DEMO_ROW)")]["source"])
        values = {
            target.id: node.value.value
            for node in ast.parse(source).body
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
            for target in node.targets if isinstance(target, ast.Name)
        }
        self.assertIs(values["RUN_LIVE"], False)
        self.assertEqual(values["REPEAT"], 3)
        self.assertTrue(op.Demo(values["DEMO_ROW"]).config)

    def test_operator_stages_follow_the_demo_in_order(self):
        stages = [self.cell_with(f"op.{tool}(") for tool in
                  ("setup_ui", "run_ui", "review_ui", "reference_ui", "autorun")]
        self.assertGreater(stages[0], self.cell_with("demo.verdict_html"))
        self.assertEqual(stages, sorted(stages))

    def test_cells_are_clean_and_explained(self):
        self.assertEqual(len({c["id"] for c in self.cells}), len(self.cells))
        for i, source in self.code:
            with self.subTest(cell=i):
                ast.parse(source)
                self.assertIsNone(self.cells[i]["execution_count"])
                self.assertEqual(self.cells[i]["outputs"], [])
                self.assertEqual(self.cells[i - 1]["cell_type"], "markdown")


if __name__ == "__main__":
    unittest.main()
