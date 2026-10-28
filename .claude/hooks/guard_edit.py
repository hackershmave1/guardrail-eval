"""PreToolUse guard for Claude Code file edits (Write / Edit / NotebookEdit).

Blocks an edit whose new text matches NAME_FLAG_REGEX (the real company name) or
BLOCKED_TEXT_REGEX (other text that must never enter the repo). Both live in .env only, so the
patterns themselves are never committed. Exit 2 = deny; the reason (never the matched text) goes
to stderr. .env itself and files outside the repo are not checked.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "promptfoo" / "guardrail_slot"))
from proxy import load_dotenv  # noqa: E402  (the one .env parser, shared with ingest and the slot)

KEYS = ("NAME_FLAG_REGEX", "BLOCKED_TEXT_REGEX")


def new_text(tool_input: dict) -> str:
    parts = [tool_input.get(k) or "" for k in ("content", "new_string", "new_source")]
    parts += [e.get("new_string") or "" for e in tool_input.get("edits") or []]
    return "\n".join(parts)


def main() -> int:
    tool_input = json.load(sys.stdin).get("tool_input") or {}
    path = Path(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
    if ROOT not in path.parents or path.name.startswith(".env"):
        return 0
    env: dict[str, str] = {}
    load_dotenv(env)
    text = new_text(tool_input)
    for key in KEYS:
        rx = env.get(key)
        if rx and re.search(rx, text, re.I):
            print(f"Blocked: the new text for {path.relative_to(ROOT)} matches {key} from .env. "
                  "Remove that text; it must never enter the repo.", file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
