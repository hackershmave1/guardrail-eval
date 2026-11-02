"""The edit hook (.claude/hooks/guard_edit.py): which edits it denies, and what it says.

In process, with synthetic patterns in place of .env (the real ones are never read into a test).
"""

from __future__ import annotations

import io
import json
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).absolute().parents[1]
sys.path.insert(0, str(ROOT / ".claude" / "hooks"))
import guard_edit  # noqa: E402

PATTERNS = {"NAME_FLAG_REGEX": r"globex\s*freight", "BLOCKED_TEXT_REGEX": r"forbidden-token"}


def repo(rel: str) -> str:
    return str(guard_edit.ROOT / rel)


class Hook(unittest.TestCase):
    def run_hook(self, tool_input, patterns=PATTERNS):
        stdin = io.StringIO(json.dumps({"tool_name": "Write", "tool_input": tool_input}))
        err = io.StringIO()
        with mock.patch.object(sys, "stdin", stdin), redirect_stderr(err), \
                mock.patch.object(guard_edit, "load_dotenv", side_effect=lambda env: env.update(patterns)):
            return guard_edit.main(), err.getvalue()

    def test_a_clean_edit_passes_silently(self):
        self.assertEqual(self.run_hook({"file_path": repo("docs/x.md"), "content": "Acme Parcel"}), (0, ""))

    def test_the_company_name_is_denied_by_key_and_path_never_by_the_text(self):
        code, err = self.run_hook({"file_path": repo("docs/x.md"), "content": "ship via Globex  Freight"})
        self.assertEqual(code, 2)
        self.assertIn("docs/x.md matches NAME_FLAG_REGEX", err)
        self.assertNotIn("Globex", err)

    def test_other_blocked_text_is_denied(self):
        code, err = self.run_hook({"file_path": repo("a.py"), "content": "x = 'forbidden-token'"})
        self.assertEqual((code, "BLOCKED_TEXT_REGEX" in err), (2, True))

    def test_the_first_matching_key_is_named(self):
        _, err = self.run_hook({"file_path": repo("a.py"), "content": "globex freight forbidden-token"})
        self.assertIn("NAME_FLAG_REGEX", err)
        self.assertNotIn("BLOCKED_TEXT_REGEX", err)

    def test_every_tools_new_text_is_checked(self):
        for tool_input in ({"file_path": repo("a.py"), "old_string": "x", "new_string": "globex freight"},
                           {"notebook_path": repo("n.ipynb"), "new_source": "globex freight"},
                           {"file_path": repo("a.py"), "edits": [{"new_string": "ok"}, {"new_string": "globex freight"}]}):
            with self.subTest(tool_input=list(tool_input)):
                self.assertEqual(self.run_hook(tool_input)[0], 2)

    def test_old_text_being_removed_is_not_checked(self):
        self.assertEqual(self.run_hook({"file_path": repo("a.py"), "old_string": "globex freight",
                                        "new_string": "Acme Parcel"})[0], 0)

    def test_dotenv_files_and_files_outside_the_repo_are_not_checked(self):
        for path in (repo(".env"), repo(".env.example"), "/tmp/elsewhere/x.md", "docs/relative.md", repo("")):
            with self.subTest(path=path):
                self.assertEqual(self.run_hook({"file_path": path, "content": "globex freight"})[0], 0)

    def test_a_nested_repo_file_is_checked(self):
        self.assertEqual(self.run_hook({"file_path": repo("promptfoo/datasets/web/p.html"),
                                        "content": "globex freight"})[0], 2)

    def test_unset_or_empty_patterns_deny_nothing(self):
        for patterns in ({}, {"NAME_FLAG_REGEX": "", "BLOCKED_TEXT_REGEX": ""}):
            with self.subTest(patterns=patterns):
                self.assertEqual(self.run_hook({"file_path": repo("a.py"), "content": "anything"}, patterns)[0], 0)

    def test_no_tool_input_passes(self):
        stdin = io.StringIO(json.dumps({"tool_name": "Write"}))
        with mock.patch.object(sys, "stdin", stdin):
            self.assertEqual(guard_edit.main(), 0)


class NewText(unittest.TestCase):
    def test_missing_and_empty_fields_read_as_empty(self):
        self.assertEqual(guard_edit.new_text({"content": None, "edits": [{"new_string": None}]}), "\n\n\n")


if __name__ == "__main__":
    unittest.main()
