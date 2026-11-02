"""The lab transcript as the provider stores it (_compact_transcript), from the shim's per-call lines."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).absolute().parents[1]
sys.path.insert(0, str(ROOT / "promptfoo" / "providers"))
import agentic_goat_provider as provider  # noqa: E402

TOOLS = [{"type": "function", "function": {"name": "track"}}]


def call(messages, reply, provider_name="DeepInfra", finish="stop"):
    return {"messages": messages, "tools": TOOLS, "reply": {"role": "assistant", "content": reply},
            "finish_reason": finish, "provider": provider_name}


def user(text):
    return {"role": "user", "content": text}


class Compact(unittest.TestCase):
    def test_a_growing_request_folds_into_one_conversation(self):
        first = [user("where is my parcel?")]
        second = first + [{"role": "assistant", "content": "checking"}, user("thanks")]
        got = provider._compact_transcript([call(first, "checking"), call(second, "done", finish="length")])
        self.assertEqual(got["tools"], TOOLS)
        self.assertEqual(got["conversations"], [{"messages": second, "finish_reason": "length",
                                                 "reply": {"role": "assistant", "content": "done"}}])

    def test_a_request_that_starts_over_opens_a_new_conversation(self):
        got = provider._compact_transcript([call([user("a")], "1"), call([user("b")], "2")])
        self.assertEqual([c["messages"] for c in got["conversations"]], [[user("a")], [user("b")]])

    def test_only_the_latest_conversation_can_grow(self):
        a, b = [user("a")], [user("b")]
        got = provider._compact_transcript([call(a, "1"), call(b, "2"), call(b + [user("more")], "3")])
        self.assertEqual([c["messages"] for c in got["conversations"]], [a, b + [user("more")]])
        self.assertEqual(got["conversations"][0]["reply"]["content"], "1")

    def test_a_resampled_turn_resends_the_same_request_and_folds(self):
        got = provider._compact_transcript([call([user("a")], ""), call([user("a")], "answered")])
        self.assertEqual([c["reply"]["content"] for c in got["conversations"]], ["answered"])

    def test_long_message_text_is_capped(self):
        tool_call = {"role": "assistant", "content": None, "tool_calls": [{"id": "c1"}]}
        got = provider._compact_transcript([call([user("x" * 9000), tool_call], "ok")])
        messages = got["conversations"][0]["messages"]
        self.assertEqual((len(messages[0]["content"]), messages[1]), (8000, tool_call))

    def test_the_upstream_of_every_call_is_kept_in_order(self):
        first = [user("a")]
        calls = [call(first, "1", "DeepInfra"), call(first + [user("b")], "2", "Parasail"),
                 call([user("c")], "3", None)]
        self.assertEqual(provider._compact_transcript(calls)["served_by"], ["DeepInfra", "Parasail", None])

    def test_calls_recorded_before_the_field_existed_read_as_none(self):
        got = provider._compact_transcript([{"messages": [user("a")], "reply": None}])
        self.assertEqual(got["served_by"], [None])


if __name__ == "__main__":
    unittest.main()
