"""The lab-side shim (promptfoo/providers/shim/sitecustomize.py), called the way a lab calls it.

Each test loads the real shim file with a chosen environment over a fake
`Completions.create`, then calls `Completions.create` as the lab's OpenAI client would.
The fake model records what reached it and answers with scripted replies.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from openai.resources.chat.completions import Completions
from openai.types.chat import ChatCompletion

ROOT = Path(__file__).absolute().parents[1]
SHIM = ROOT / "promptfoo" / "providers" / "shim"
sys.path.insert(0, str(SHIM))

SHIM_ENV = ("EXTRA_HEADERS", "OPENROUTER_PROVIDER_ONLY", "GUARDRAIL_PIN_MODEL", "GUARDRAIL_REASONING_EFFORT",
            "GUARDRAIL_TRANSCRIPT", "GUARDRAIL_RESAMPLE", "GUARDRAIL_TRIAL_ID")
BASE = "http://gw.local/v1/"
MESSAGES = [{"role": "user", "content": "where is my parcel?"}]
TOOL_CALL = [{"id": "c1", "type": "function", "function": {"name": "track", "arguments": "{}"}}]


def completion(content="hi", tool_calls=None, finish="stop", choices=True):
    message = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return ChatCompletion.model_validate({
        "id": "x", "object": "chat.completion", "created": 0, "model": "m",
        "choices": [{"index": 0, "finish_reason": finish, "message": message}] if choices else [],
        "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}})


class Shim(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.transcript = Path(tmp.name) / "transcript.jsonl"
        self.sent = []

    def install(self, replies=(completion(),), **env):
        """Load the shim under `env` over a fake model that answers `replies` in turn (the last repeats)."""
        replies = list(replies)

        def model(this, *args, **kwargs):
            self.sent.append(kwargs)
            return replies.pop(0) if len(replies) > 1 else replies[0]

        self.model = model
        patch = mock.patch.object(Completions, "create", model)
        patch.start()
        self.addCleanup(patch.stop)
        clean = {k: v for k, v in os.environ.items() if k not in SHIM_ENV}
        err = io.StringIO()
        with mock.patch.dict(os.environ, {**clean, **env}, clear=True), contextlib.redirect_stderr(err):
            spec = importlib.util.spec_from_file_location("shim_under_test", SHIM / "sitecustomize.py")
            spec.loader.exec_module(importlib.util.module_from_spec(spec))
        return err.getvalue()

    def create(self, **kwargs):
        lab_client = SimpleNamespace(_client=SimpleNamespace(base_url=BASE))
        return Completions.create(lab_client, model="m", messages=MESSAGES, **kwargs)

    def recorded(self):
        return [json.loads(line) for line in self.transcript.read_text(encoding="utf-8").splitlines()]


class Untouched(Shim):
    def test_no_settings_leave_the_client_alone(self):
        self.install()
        self.assertIs(Completions.create, self.model)

    def test_blank_settings_install_nothing(self):
        self.install(EXTRA_HEADERS="  ", OPENROUTER_PROVIDER_ONLY=" , ", GUARDRAIL_PIN_MODEL="",
                     GUARDRAIL_REASONING_EFFORT="", GUARDRAIL_TRANSCRIPT="")
        self.assertIs(Completions.create, self.model)


class Headers(Shim):
    TABLE = json.dumps({BASE.rstrip("/"): {"x-model": "m-1", "x-project": "${PROJ}"}})

    def test_the_endpoint_headers_are_added_and_the_lab_wins_a_clash(self):
        self.install(EXTRA_HEADERS=self.TABLE, PROJ="p-1")
        self.create(extra_headers={"x-model": "lab"})
        self.assertEqual(self.sent[0]["extra_headers"], {"x-model": "lab", "x-project": "p-1"})

    def test_another_endpoint_gets_nothing(self):
        self.install(EXTRA_HEADERS=json.dumps({"http://elsewhere/v1": {"x-model": "no"}}))
        self.create()
        self.assertNotIn("extra_headers", self.sent[0])

    def test_a_bad_table_warns_and_sends_nothing(self):
        for env, named in (({"EXTRA_HEADERS": "{bad"}, "JSONDecodeError"),
                           ({"EXTRA_HEADERS": self.TABLE}, "PROJ")):
            with self.subTest(env=env):
                err = self.install(**env)
                self.assertTrue(err.startswith("[shim] EXTRA_HEADERS not applied: "), err)
                self.assertIn(named, err)
                self.assertIs(Completions.create, self.model)


class Route(Shim):
    def test_the_provider_is_pinned_without_fallbacks(self):
        self.install(OPENROUTER_PROVIDER_ONLY=" deepinfra , other ")
        self.create(extra_body={"seed": 1})
        self.assertEqual(self.sent[0]["extra_body"], {"seed": 1, "provider": {
            "only": ["deepinfra", "other"], "allow_fallbacks": False, "require_parameters": True}})

    def test_a_lab_provider_choice_is_kept(self):
        self.install(OPENROUTER_PROVIDER_ONLY="deepinfra")
        self.create(extra_body={"provider": {"only": ["lab"]}})
        self.assertEqual(self.sent[0]["extra_body"], {"provider": {"only": ["lab"]}})


class Pin(Shim):
    def test_the_model_is_overridden(self):
        self.install(GUARDRAIL_PIN_MODEL="openai/gpt-oss-20b")
        self.create()
        self.assertEqual(self.sent[0]["model"], "openai/gpt-oss-20b")


class Effort(Shim):
    def test_the_effort_is_set_unless_the_lab_set_one(self):
        for extra_body, want in ((None, {"reasoning": {"effort": "low"}}),
                                 ({"reasoning": {"effort": "high"}}, {"reasoning": {"effort": "high"}})):
            with self.subTest(extra_body=extra_body):
                self.sent.clear()
                self.install(GUARDRAIL_REASONING_EFFORT="low")
                self.create(extra_body=extra_body)
                self.assertEqual(self.sent[0]["extra_body"], want)


class Layers(Shim):
    def test_every_layer_reaches_the_model(self):
        self.install(EXTRA_HEADERS=json.dumps({BASE: {"x-model": "m-1"}}), OPENROUTER_PROVIDER_ONLY="deepinfra",
                     GUARDRAIL_PIN_MODEL="pinned", GUARDRAIL_REASONING_EFFORT="low",
                     GUARDRAIL_TRANSCRIPT=str(self.transcript), GUARDRAIL_TRIAL_ID="t-1")
        self.create()
        [sent] = self.sent
        self.assertEqual((sent["model"], sent["extra_headers"], sent["extra_body"]),
                         ("pinned", {"x-model": "m-1", "X-Trial-Id": "t-1"},
                          {"reasoning": {"effort": "low"}, "provider": {
                              "only": ["deepinfra"], "allow_fallbacks": False, "require_parameters": True}}))
        self.assertEqual(len(self.recorded()), 1)


class Recorder(Shim):
    def record(self, replies=(completion(),), resample=None, **env):
        if resample is not None:
            env["GUARDRAIL_RESAMPLE"] = str(resample)
        self.install(replies, GUARDRAIL_TRANSCRIPT=str(self.transcript), **env)

    def test_each_call_is_recorded_as_the_lab_sent_it(self):
        self.record(GUARDRAIL_TRIAL_ID="t-1")
        tools = [{"type": "function", "function": {"name": "track"}}]
        reply = self.create(tools=tools)
        self.assertEqual(reply.choices[0].message.content, "hi")
        [entry] = self.recorded()
        self.assertLessEqual(entry.pop("started_at"), entry.pop("finished_at"))
        self.assertEqual(entry, {"trial_id": "t-1", "resample": 0, "messages": MESSAGES, "tools": tools,
                                 "reply": {"role": "assistant", "content": "hi"}, "finish_reason": "stop",
                                 "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}})

    def test_the_trial_id_is_sent_unless_the_lab_sent_one(self):
        self.record(GUARDRAIL_TRIAL_ID="t-1")
        self.create(extra_headers={"x-a": "1"})
        self.create(extra_headers={"X-Trial-Id": "lab"})
        self.assertEqual([s["extra_headers"] for s in self.sent],
                         [{"x-a": "1", "X-Trial-Id": "t-1"}, {"X-Trial-Id": "lab"}])

    def test_without_a_trial_id_no_header_is_added(self):
        self.record()
        self.create()
        self.assertNotIn("extra_headers", self.sent[0])
        self.assertIsNone(self.recorded()[0]["trial_id"])

    def test_a_reply_without_choices_is_recorded_and_returned(self):
        empty = completion(choices=False)
        self.record([empty], resample=3)
        self.assertIs(self.create(), empty)
        self.assertEqual(len(self.sent), 1)
        entry = self.recorded()[0]
        self.assertEqual((entry["reply"], entry["finish_reason"]), (None, None))

    def test_what_cannot_be_dumped_is_recorded_as_text(self):
        self.record()
        self.create(tools=[SimpleNamespace(name="t")])
        self.assertEqual(self.recorded()[0]["tools"], ["namespace(name='t')"])

    def test_a_failed_recording_never_breaks_the_call(self):
        self.transcript.mkdir()
        self.record()
        self.assertEqual(self.create().choices[0].message.content, "hi")


class Resample(Recorder):
    def test_an_empty_turn_is_resampled_until_it_answers(self):
        for empty in ("", "  \n", None):
            with self.subTest(empty=empty):
                self.sent.clear()
                self.transcript.unlink(missing_ok=True)
                self.record([completion(empty), completion(empty), completion("ok")], resample=3)
                self.assertEqual(self.create().choices[0].message.content, "ok")
                self.assertEqual(len(self.sent), 3)
                self.assertEqual([e["resample"] for e in self.recorded()], [0, 1, 2])

    def test_resampling_stops_at_the_limit(self):
        last = completion("")
        self.record([completion(""), completion(""), last], resample=2)
        self.assertIs(self.create(), last)
        self.assertEqual([e["resample"] for e in self.recorded()], [0, 1, 2])

    def test_a_tool_call_is_not_an_empty_turn(self):
        self.record([completion(None, tool_calls=TOOL_CALL, finish="tool_calls"), completion("x")], resample=3)
        self.create()
        self.assertEqual(len(self.sent), 1)

    def test_these_endings_are_never_resampled(self):
        for finish in ("content_filter", "length"):
            with self.subTest(finish=finish):
                self.sent.clear()
                self.record([completion("", finish=finish), completion("x")], resample=3)
                self.assertEqual(self.create().choices[0].finish_reason, finish)
                self.assertEqual(len(self.sent), 1)

    def test_a_stream_is_never_resampled(self):
        self.record([completion(""), completion("x")], resample=3)
        self.create(stream=True)
        self.assertEqual(len(self.sent), 1)

    def test_no_resampling_without_a_limit(self):
        for resample in (None, "", "0"):
            with self.subTest(resample=resample):
                self.sent.clear()
                self.record([completion(""), completion("x")], resample=resample)
                self.assertEqual(self.create().choices[0].message.content, "")
                self.assertEqual(len(self.sent), 1)


if __name__ == "__main__":
    unittest.main()
