"""Headers by endpoint (EXTRA_HEADERS): the parser, the shim on a lab-style client call, the slot's
upstream call, the run_evals.sh gate and run.json. A local echo server stands in for the model:
no model calls, nothing written outside a temp dir.

    venv/bin/python -m unittest discover -s tests
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).absolute().parents[1]
SHIM = ROOT / "promptfoo" / "providers" / "shim"
PY = sys.executable
sys.path.insert(0, str(SHIM))
import extra_headers as eh  # noqa: E402

REPLY = {"id": "x", "object": "chat.completion", "created": 0, "model": "m",
         "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "hi"}}]}
LAB = ("from openai import OpenAI; import sys; c = OpenAI(base_url=sys.argv[1], api_key='dummy');"
       "print(c.chat.completions.create(model='m', messages=[{'role':'user','content':'x'}]).choices[0].message.content)")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Parser(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(eh.parse(""), {})
        self.assertEqual(eh.parse(None), {})

    def test_prefix_boundary_and_longest_wins(self):
        t = eh.resolve(eh.parse(json.dumps({"http://h:1/v1": {"x-model": "a"}, "http://h:1": {"x-model": "b"},
                                            "http://h:": {"x-wrong": "1"}})), {})
        self.assertEqual(eh.for_url(t, "http://h:1/v1/chat/completions"), {"x-model": "a"})
        self.assertEqual(eh.for_url(t, "http://h:1/other"), {"x-model": "b"})
        self.assertEqual(eh.for_url(t, "http://h:10/v1"), {})

    def test_vars_filled_and_missing_named(self):
        table = eh.parse(json.dumps({"http://h/v1": {"x-project-id": "${PROJ}"}}))
        self.assertEqual(eh.resolve(table, {"PROJ": "p1"})["http://h/v1"], {"x-project-id": "p1"})
        with self.assertRaises(KeyError) as cm:
            eh.resolve(table, {})
        self.assertIn("PROJ", str(cm.exception))

    def test_bad_shape(self):
        for raw in ("{bad", "[]", '{"http://h": "not-a-dict"}'):
            with self.assertRaises(ValueError):
                eh.parse(raw)

    def test_names_never_values(self):
        names = eh.names(eh.parse(json.dumps({"http://h/v1": {"x-b": "secret", "x-a": "v"}})))
        self.assertEqual(names, {"http://h/v1": ["x-a", "x-b"]})


class Wire(unittest.TestCase):
    """The shim and the slot against a local echo server that records the x-* headers it gets."""

    @classmethod
    def setUpClass(cls):
        cls.seen: list[dict] = []
        seen = cls.seen

        class Echo(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                seen.append({k.lower(): v for k, v in self.headers.items() if k.lower().startswith("x-")})
                body = json.dumps(REPLY).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        cls.srv = ThreadingHTTPServer(("127.0.0.1", free_port()), Echo)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_port}/v1"
        cls.tmp = tempfile.TemporaryDirectory()
        cls.env = {**os.environ, "API_KEY": "dummy", "PROJ": "proj-123", "PYTHONPATH": str(SHIM),
                   "GUARDRAIL_TRANSCRIPT": os.devnull, "GUARDRAIL_TRIAL_ID": "t-1",
                   "EXTRA_HEADERS": json.dumps({cls.base: {"x-model": "m-1", "x-project-id": "${PROJ}"}})}

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.tmp.cleanup()

    def lab_call(self, env):
        r = subprocess.run([PY, "-c", LAB, self.base], env=env, capture_output=True, text=True, timeout=60)
        return r, (self.seen.pop() if self.seen else {})

    def test_shim_sends_endpoint_headers(self):
        r, h = self.lab_call(self.env)
        self.assertEqual(r.stdout.strip(), "hi", r.stderr)
        self.assertEqual((h.get("x-model"), h.get("x-project-id"), h.get("x-trial-id")), ("m-1", "proj-123", "t-1"))

    def test_shim_records_model_call_times(self):
        path = Path(self.tmp.name) / "timed-transcript.jsonl"
        r, _ = self.lab_call({**self.env, "GUARDRAIL_TRANSCRIPT": str(path)})
        self.assertEqual(r.returncode, 0, r.stderr)
        call = json.loads(path.read_text().splitlines()[-1])
        self.assertEqual(call["trial_id"], "t-1")
        self.assertGreater(call["started_at"], 0)
        self.assertGreaterEqual(call["finished_at"], call["started_at"])
        self.assertEqual(call["reply"]["content"], "hi")

    def test_shim_other_endpoint_gets_nothing(self):
        r, h = self.lab_call({**self.env, "EXTRA_HEADERS": json.dumps({"http://other.example/v1": {"x-model": "no"}})})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("x-model", h)

    def test_shim_unset_var_warns_without_value(self):
        env = {k: v for k, v in self.env.items() if k != "PROJ"}
        r, h = self.lab_call(env)
        self.assertIn("EXTRA_HEADERS not applied", r.stderr)
        self.assertIn("PROJ", r.stderr)
        self.assertNotIn("x-model", h)

    def slot_env(self, **kw):
        env = {**self.env, "UPSTREAM_BASE_URL": self.base, "GUARDRAIL_SLOT_PORT": str(free_port()),
               "SLOT_LOG": str(Path(self.tmp.name) / "slot_log.jsonl"), **kw}
        env.pop("PYTHONPATH")
        return env

    def test_slot_upstream_gets_headers(self):
        env = self.slot_env()
        slot = subprocess.Popen([PY, str(ROOT / "promptfoo/guardrail_slot/proxy.py")], env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            url = f"http://127.0.0.1:{env['GUARDRAIL_SLOT_PORT']}"
            for _ in range(80):
                try:
                    urllib.request.urlopen(url + "/health")
                    break
                except OSError:
                    time.sleep(0.1)
            req = urllib.request.Request(url + "/v1/chat/completions", method="POST",
                                         data=json.dumps({"model": "m", "messages": []}).encode(),
                                         headers={"Content-Type": "application/json", "X-Trial-Id": "t-2"})
            self.assertEqual(urllib.request.urlopen(req, timeout=30).status, 200)
            h = self.seen.pop()
            self.assertEqual((h.get("x-model"), h.get("x-project-id")), ("m-1", "proj-123"))
        finally:
            slot.kill()
            slot.wait()

    def test_slot_refuses_bad_table(self):
        for kw in ({"EXTRA_HEADERS": "{bad"}, {"PROJ": ""}):
            r = subprocess.run([PY, str(ROOT / "promptfoo/guardrail_slot/proxy.py")], env=self.slot_env(**kw),
                               capture_output=True, text=True, timeout=30)
            self.assertNotEqual(r.returncode, 0)

    def test_run_evals_gate(self):
        meta = str(ROOT / "scripts/run_meta.py")
        self.assertEqual(subprocess.run([PY, meta, "check-headers"], env=self.env).returncode, 0)
        bad = {**self.env, "EXTRA_HEADERS": json.dumps({self.base: {"x": "${NOPE_UNSET_VAR}"}})}
        r = subprocess.run([PY, meta, "check-headers"], env=bad, capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("NOPE_UNSET_VAR", r.stderr)

    def test_run_json_records_names_only(self):
        rd = Path(self.tmp.name) / "rundir"
        subprocess.run([PY, str(ROOT / "scripts/run_meta.py"), "start", str(rd), "rid", "tools", "1"],
                       env=self.env, cwd=ROOT, capture_output=True, check=True)
        text = (rd / "run.json").read_text()
        self.assertEqual(json.loads(text)["extra_headers"], {self.base: ["x-model", "x-project-id"]})
        self.assertNotIn("proj-123", text)


if __name__ == "__main__":
    unittest.main()
