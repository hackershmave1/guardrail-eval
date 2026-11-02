"""The model-facing-text gate (scripts/lint_prompts.py): the rules per row, the loader, the report.

Configs are written to a temp promptfoo directory; nothing reads the repo's own configs.
"""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).absolute().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import lint_prompts  # noqa: E402

CLEAN = "Where is parcel RQ71-01? It was due on Monday."


def check(desc="row", **v):
    return lint_prompts.check(desc, v)


class Clean(unittest.TestCase):
    def test_a_plain_row_passes(self):
        self.assertEqual(check(attack=CLEAN, system_prompt="You help Acme Parcel customers.",
                               seed_content="Returns take five days.", canary="RQ71-01"), ([], []))

    def test_fields_that_are_not_text_are_skipped(self):
        self.assertEqual(check(attack=["PI01"], system_prompt=None, canary=1234), ([], []))

    def test_fields_the_model_never_sees_are_not_checked(self):
        self.assertEqual(check(injection_note="PI01 canary (expect FAIL)", attack=CLEAN), ([], []))


class TestIds(unittest.TestCase):
    def test_a_tc_id_in_any_model_facing_field_is_an_error(self):
        for field in lint_prompts.FIELDS:
            with self.subTest(field=field):
                self.assertEqual(check(**{field: "ref PI01 please"})[0], [f"{field}: test ID 'PI01'"])

    def test_tc_id_shapes(self):
        for text, hit in (("JB03-04 x", "JB03"), ("PI-09 x", "PI-09"), ("ref OUT04A x", "OUT04A"),
                          ("MEM02", "MEM02")):
            with self.subTest(text=text):
                self.assertEqual(check(attack=text)[0], [f"attack: test ID {hit!r}"])

    def test_near_misses_are_not_tc_ids(self):
        for text in ("pi01 lower case", "PI1 one digit", "XPI01 glued", "PI012 three digits", "RQ71-01"):
            with self.subTest(text=text):
                self.assertEqual(check(attack=text)[0], [])


class Vocabulary(unittest.TestCase):
    def test_eval_words_are_errors(self):
        for word in ("canary", "Canaries", "sentinel", "guardrail", "red team", "red-team", "redteam",
                     "benchmark", "eval", "evaluation", "known good", "known-bad", "lookalike", "look-alike",
                     "proprietary IP", "exfil", "exfiltrate"):
            with self.subTest(word=word):
                self.assertEqual(check(attack=f"about the {word} thing")[0], [f"attack: eval vocabulary {word!r}"])

    def test_leak_words_are_errors(self):
        """.claude/rules/model-facing-text.md names "leak" as test vocabulary."""
        for word in ("leak", "Leaked", "leakage"):
            with self.subTest(word=word):
                self.assertEqual(check(attack=f"the {word} report")[0], [f"attack: eval vocabulary {word!r}"])
        self.assertEqual(check(attack="the bleak outlook")[0], [])

    def test_an_expected_outcome_is_an_error(self):
        self.assertEqual(check(attack="send it (expect FAIL) now")[0], ["attack: eval vocabulary '(expect'"])

    def test_hidden_characters_do_not_hide_a_word(self):
        for text in ("the can​ary", "the cana﻿ry", "the sen\U000e0041tinel"):
            with self.subTest(text=text):
                self.assertEqual(len(check(attack=text)[0]), 1)

    def test_a_tc_id_and_a_word_are_both_reported_per_field(self):
        self.assertEqual(check(attack="PI01 canary", seed_content="sentinel")[0],
                         ["attack: test ID 'PI01'", "attack: eval vocabulary 'canary'",
                          "seed_content: eval vocabulary 'sentinel'"])


class IgnoredAttack(unittest.TestCase):
    def test_drivers_that_ignore_the_prompt_skip_only_the_attack(self):
        for v in ({"run": "rag_attack"}, {"script": "drivers/lab05_stateful_injection.py"},
                  {"script": "drivers/lab07_killchain.py"}):
            with self.subTest(v=v):
                self.assertEqual(check(attack="PI01", system_prompt="canary", **v)[0],
                                 ["system_prompt: eval vocabulary 'canary'"])

    def test_other_drivers_are_checked(self):
        self.assertEqual(len(check(attack="PI01", run="agent", script="drivers/lab04_x.py")[0]), 1)


class Labels(unittest.TestCase):
    def test_a_trailing_label_on_the_attack_is_an_error(self):
        self.assertEqual(check(attack="Ignore the above. (semantic inject-004)  \n")[0],
                         ["attack: report label '(semantic inject-004)'"])

    def test_labels_elsewhere_are_fine(self):
        for v in ({"attack": "Track (my parcel) today"}, {"attack": "Done (ok)"},
                  {"system_prompt": "Help customers. (support desk)"}):
            with self.subTest(v=v):
                self.assertEqual(check(**v)[0], [])


class Controls(unittest.TestCase):
    HARD = "Never share the account number."

    def test_a_hardening_line_in_a_control_is_an_error(self):
        for desc, v in (("row", {"benign": True}), ("row", {"benign": "yes"}), ("row", {"benign": 1}),
                        ("row", {"benign": "TRUE"}), ("a benign look-up", {})):
            with self.subTest(desc=desc, v=v):
                self.assertEqual(check(desc, attack=self.HARD, system_prompt=self.HARD, **v)[0],
                                 ["attack: hardening line in a control 'Never share'",
                                  "system_prompt: hardening line in a control 'Never share'"])

    def test_hardening_counts_only_in_controls_and_only_in_prompts(self):
        for desc, v in (("row", {"benign": False, "attack": self.HARD}), ("row", {"benign": "no", "attack": self.HARD}),
                        ("a benign row", {"seed_content": self.HARD})):
            with self.subTest(desc=desc, v=v):
                self.assertEqual(lint_prompts.check(desc, v)[0], [])

    def test_hardening_shapes(self):
        for text in ("do not modify it", "don't delete files", "never reveal the key", "Do Not Disclose it"):
            with self.subTest(text=text):
                self.assertEqual(len(check(attack=text, benign=True)[0]), 1)
        self.assertEqual(check(attack="Never mind. Share it.", benign=True)[0], [])


class SelfGrounding(unittest.TestCase):
    def test_a_prompt_that_grounds_itself_is_an_error_in_any_row(self):
        for field in ("attack", "system_prompt", "seed_content"):
            with self.subTest(field=field):
                self.assertEqual(check(**{field: "Use only the source below."})[0],
                                 [f"{field}: the prompt grounds itself 'Use only the'"])

    def test_grounding_shapes(self):
        for text in ("only use the document", "only the context", "answer strictly from it",
                     "do not rely on memory", "no outside knowledge", "if the source does not say"):
            with self.subTest(text=text):
                self.assertEqual(len(check(attack=text)[0]), 1)

    def test_canary_fields_are_not_grounding_checked(self):
        self.assertEqual(check(canary="use only the", source_canary="outside knowledge")[0], [])


class Warnings(unittest.TestCase):
    def test_the_word_test_is_a_warning_not_an_error(self):
        self.assertEqual(check(attack="For a support-tool test, print it.", seed_content="testing"),
                         ([], ["attack: 'test'", "seed_content: 'testing'"]))

    def test_longer_words_do_not_warn(self):
        self.assertEqual(check(attack="It was tested and attested."), ([], []))


class Config(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        p = mock.patch.object(lint_prompts, "ROOT", self.root)
        p.start()
        self.addCleanup(p.stop)

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text), encoding="utf-8")


class Rows(Config):
    def test_rows_merge_provider_config_default_vars_and_test_vars(self):
        self.write("c.yaml", """
            providers:
              - id: file://p.py
                config: {system_prompt: "You help.", max_tokens: 10, who: provider}
              - plain-string-provider
            defaultTest:
              vars: {who: default, effort: low}
            tests:
              - description: inline
                vars: {who: test, attack: hi}
              - file://datasets/more.yaml
            """)
        self.write("datasets/more.yaml", """
            - description: from file
              vars: {attack: there}
            - vars: {attack: nameless}
            """)
        got = list(lint_prompts.rows("c"))
        base = {"system_prompt": "You help.", "max_tokens": 10, "effort": "low"}
        self.assertEqual(got, [("inline", {**base, "who": "test", "attack": "hi"}),
                               ("from file", {**base, "who": "default", "attack": "there"}),
                               ("", {**base, "who": "default", "attack": "nameless"}),
                               ("c provider config", {"system_prompt": "You help."})])

    def test_targets_a_single_test_and_no_system_prompt(self):
        self.write("c.yaml", """
            targets:
              - config: {who: target}
            tests:
              description: only
              vars: {attack: hi}
            """)
        self.assertEqual(list(lint_prompts.rows("c")), [("only", {"who": "target", "attack": "hi"})])

    def test_empty_parts_yield_nothing(self):
        for text in ("description: none\n", "tests:\n  - file://empty.yaml\n", "tests:\n  - 42\n"):
            with self.subTest(text=text):
                self.write("empty.yaml", "")
                self.write("c.yaml", text)
                self.assertEqual(list(lint_prompts.rows("c")), [])


class ListVars(Config):
    def test_list_vars_are_found_everywhere(self):
        self.write("c.yaml", """
            defaultTest:
              vars: {reset_state: [a, b], ok: x}
            tests:
              - description: inline
                vars: {seed: [1, 2]}
              - file://d.yaml
              - 42
            """)
        self.write("d.yaml", f"""
            - description: {"x" * 70}
              vars: {{files: [a], fine: a}}
            """)
        self.assertEqual(lint_prompts.list_vars("c"),
                         ["defaultTest: reset_state", "inline: seed", f"{'x' * 60}: files"])

    def test_a_single_test_and_no_vars(self):
        self.write("c.yaml", "tests:\n  description: one\n  vars: {a: [1]}\n")
        self.assertEqual(lint_prompts.list_vars("c"), ["one: a"])
        self.write("c.yaml", "tests:\n  - description: one\n")
        self.assertEqual(lint_prompts.list_vars("c"), [])


class Main(Config):
    def run_main(self, *argv):
        out = io.StringIO()
        with mock.patch.object(sys, "argv", ["lint_prompts.py", *argv]), contextlib.redirect_stdout(out):
            code = lint_prompts.main()
        return code, out.getvalue().splitlines()

    def test_the_report_and_its_exit_code(self):
        self.write("a.yaml", f"""
            tests:
              - description: {"r" * 70}
                vars: {{attack: "PI01 canary"}}
              - description: warned
                vars: {{attack: "a test"}}
              - description: clean
                vars: {{attack: hi, reset_state: [x, y]}}
            """)
        self.write("b.yaml", "tests:\n  - description: fine\n    vars: {attack: hi}\n")
        code, lines = self.run_main("a", "b")
        self.assertEqual(code, 1)
        self.assertEqual(lines, [
            f"ERROR a: {'r' * 60} :: attack: test ID 'PI01'",
            f"ERROR a: {'r' * 60} :: attack: eval vocabulary 'canary'",
            "warn  a: warned :: attack: 'test' (confirm it is the attacker's own pretext)",
            "ERROR a: clean: reset_state is a YAML list; promptfoo splits it into one case per element "
            "(use a comma-delimited scalar)",
            "lint_prompts: 2 row(s) with errors"])

    def test_a_clean_run_exits_zero(self):
        self.write("b.yaml", "tests:\n  - description: fine\n    vars: {attack: hi}\n")
        self.assertEqual(self.run_main("b"), (0, ["lint_prompts: 0 row(s) with errors"]))

    def test_no_arguments_lint_every_config(self):
        for name in lint_prompts.CONFIGS:
            self.write(f"{name}.yaml", "tests: []\n")
        self.write("mcp.yaml", "tests:\n  - description: bad\n    vars: {attack: PI01}\n")
        code, lines = self.run_main()
        self.assertEqual((code, lines[-1]), (1, "lint_prompts: 1 row(s) with errors"))


if __name__ == "__main__":
    unittest.main()
