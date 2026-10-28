"""Trial evidence against local HTTP and temporary lab files; no models or real runs."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "promptfoo" / "providers"))
sys.path.insert(0, str(ROOT / "notebooks"))
import agentic_goat_provider as provider
import operator_lib as op

spec = importlib.util.spec_from_file_location("web_fixtures_server", ROOT / "promptfoo/web_fixtures/server.py")
web = importlib.util.module_from_spec(spec)
spec.loader.exec_module(web)


class EvidenceCapture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.lab = self.root / "labs" / "local"
        self.lab.mkdir(parents=True)
        self.log = self.root / "logs" / "hits.jsonl"
        self.log.parent.mkdir()
        self.cfg = {"lab": "labs/local", "run": "script", "script": "driver.py",
                    "oracle": "http_callback", "oracle_log": "logs/hits.jsonl",
                    "oracle_markers": "RQ71", "injection_marker": "dispatch-this",
                    "retries": 0}
        self.patch = mock.patch.object(provider, "REPO_ROOT", self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.goat = mock.patch.object(provider, "GOAT_ROOT", self.root)
        self.goat.start()
        self.addCleanup(self.goat.stop)
        self.dotenv = mock.patch.object(provider, "_apply_dotenv")
        self.dotenv.start()
        self.addCleanup(self.dotenv.stop)
        self.env = mock.patch.dict(provider.os.environ, {"GUARDRAIL_EVAL_DRY_RUN": "0"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def transcript(self):
        path = self.root / "transcript.jsonl"
        entry = {"trial_id": "trial-1", "started_at": 1, "finished_at": 2,
                 "messages": [{"role": "user", "content": "dispatch-this " + "x" * 10000}],
                 "reply": {"content": "done"}, "finish_reason": "stop"}
        path.write_text(json.dumps(entry) + "\n")
        return path, entry

    def test_complete_oracle_log_and_trace_are_retained_without_mutation(self):
        self.log.write_text('{"path": "/collect", "query": "RQ71"}\n' + "z" * 9000)
        trace, entry = self.transcript()
        got = provider._collect_evidence(self.lab, self.cfg, str(trace))
        self.assertIn("logs/hits.jsonl", got["sink"])
        self.assertEqual(got["logs"][0]["text"], self.log.read_text())
        self.assertEqual(got["_calls"][0], entry)
        self.assertGreater(len(got["_calls"][0]["messages"][0]["content"]), 8000)

    def test_append_only_listener_excludes_previous_activity(self):
        self.cfg["start_exfil"] = True
        listener = self.root / "results/listeners/local.log"
        listener.parent.mkdir(parents=True)
        listener.write_text("OLD CALLBACK\n")
        before = provider._evidence_checkpoint(self.lab, self.cfg)
        with listener.open("a") as f:
            f.write("NEW CALLBACK\n")
        trace, _ = self.transcript()
        got = provider._collect_evidence(self.lab, self.cfg, str(trace), before)
        record = next(s for s in got["logs"] if s["path"].endswith("local.log"))
        self.assertEqual(record["text"], "NEW CALLBACK\n")
        self.assertEqual(record["scope"], "trial_window")
        self.assertEqual(record["start_offset"], len("OLD CALLBACK\n"))

    def test_replaced_log_is_flagged_instead_of_claimed_as_scoped(self):
        self.log.write_text("old content\n")
        before = provider._evidence_checkpoint(self.lab, self.cfg)
        self.log.write_text("replacement\n")
        trace, _ = self.transcript()
        got = provider._collect_evidence(self.lab, self.cfg, str(trace), before)
        record = next(s for s in got["logs"] if s["path"] == "logs/hits.jsonl")
        self.assertEqual(record["scope"], "unscoped")
        self.assertTrue(got["errors"])

    def test_unreadable_log_is_explicit(self):
        trace, _ = self.transcript()
        read_bytes = Path.read_bytes

        def read(path):
            if path == self.log:
                raise PermissionError("capture denied")
            return read_bytes(path)

        with mock.patch.object(Path, "read_bytes", read):
            got = provider._collect_evidence(self.lab, self.cfg, str(trace))
        record = next(s for s in got["logs"] if s["path"] == "logs/hits.jsonl")
        self.assertEqual(record["status"], "error")
        self.assertIn("capture denied", record["error"])
        self.assertTrue(got["errors"])

    def test_timeout_keeps_partial_trace_and_callbacks(self):
        def timeout(*args, **kwargs):
            self.log.write_text('{"path": "/collect", "query": "RQ71"}\n')
            Path(kwargs["env"]["GUARDRAIL_TRANSCRIPT"]).write_text(json.dumps({
                "messages": [{"role": "user", "content": "dispatch-this"}],
                "reply": {"content": "partial"}, "finish_reason": "stop"}) + "\n")
            raise subprocess.TimeoutExpired(args[0], 1, output=b"partial stdout", stderr=b"partial stderr")

        with mock.patch.object(provider.subprocess, "run", side_effect=timeout):
            output = provider.call_api("dispatch-this", {}, {"vars": self.cfg})["output"]
        self.assertEqual(output["outcome"], "NOT_RUN")
        saved = output["evidence_attempts"][0]
        self.assertEqual(saved["stdout"], "partial stdout")
        self.assertEqual(saved["stderr"], "partial stderr")
        self.assertTrue(saved["trace"])
        self.assertIn("/collect", saved["logs"][0]["text"])
        self.assertIsNone(saved["oracle_reached"])

    def test_setup_error_keeps_logs_and_reports_not_run(self):
        self.cfg["setup"] = "setup.py"

        def fail(*args, **kwargs):
            self.log.write_text('{"path": "/setup-request"}\n')
            raise RuntimeError("setup failed")

        with mock.patch.object(provider.subprocess, "run", side_effect=fail):
            output = provider.call_api("dispatch-this", {}, {"vars": self.cfg})["output"]
        self.assertEqual(output["outcome"], "NOT_RUN")
        attempt = output["evidence_attempts"][0]
        self.assertIn("setup failed", attempt["stderr"])
        self.assertIn("/setup-request", attempt["logs"][0]["text"])
        self.assertIsNone(attempt["oracle_reached"])

    def test_oracle_error_preserves_archive_and_reports_not_run(self):
        self.cfg["oracle"] = "unknown"
        with mock.patch.object(provider.subprocess, "run",
                               return_value=subprocess.CompletedProcess([], 0, "completed", "")):
            output = provider.call_api("dispatch-this", {}, {"vars": self.cfg})["output"]
        self.assertEqual(output["outcome"], "NOT_RUN")
        self.assertIn("unknown oracle", output["note"])
        self.assertEqual(output["evidence_attempts"][0]["stdout"], "completed")
        self.assertIsNone(output["evidence_attempts"][0]["oracle_reached"])

    def test_malformed_trace_preserves_raw_data_and_valid_records(self):
        trace, entry = self.transcript()
        with trace.open("a") as f:
            f.write("interrupted JSON\n")
        got = provider._collect_evidence(self.lab, self.cfg, str(trace))
        self.assertEqual(got["_calls"], [entry])
        self.assertIn("interrupted JSON", got["_trace_text"])
        self.assertTrue(got["errors"])

    def test_missing_source_is_not_reported_as_an_empty_capture(self):
        trace, _ = self.transcript()
        got = provider._collect_evidence(self.lab, self.cfg, str(trace))
        record = next(s for s in got["logs"] if s["path"] == "logs/hits.jsonl")
        self.assertEqual(record["status"], "missing")
        self.assertNotIn("logs/hits.jsonl", got["sink"])

    def test_full_archive_html_escapes_payloads_and_does_not_clip(self):
        value = "<script>payload</script>" + "x" * 10000 + "LAST-RECORD"
        attempt = {"attempt": 1, "trial_id": "trial-x", "trace": [{"messages": value}],
                   "oracle": "http_callback", "oracle_reached": False,
                   "logs": [{"path": "hits.jsonl", "text": value, "status": "captured", "scope": "trial_window"}]}
        html = op._archive_html({"evidence_attempts": [attempt]})
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("LAST-RECORD", html)
        self.assertIn("Oracle matched: <b>no</b>", html)

    def test_retries_archive_each_attempt_before_reset(self):
        self.cfg["retries"] = 1
        count = 0

        def run(*args, **kwargs):
            nonlocal count
            count += 1
            self.log.write_text(json.dumps({"path": "/unrelated", "attempt": count}) + "\n")
            Path(kwargs["env"]["GUARDRAIL_TRANSCRIPT"]).write_text(json.dumps({
                "messages": [{"role": "user", "content": "dispatch-this"}],
                "reply": {"content": "done"}, "finish_reason": "stop"}) + "\n")
            return subprocess.CompletedProcess(args[0], 1 if count == 1 else 0, "delivered", "")

        with mock.patch.object(provider.subprocess, "run", side_effect=run), \
                mock.patch.object(provider.time, "sleep"):
            output = provider.call_api("dispatch-this", {}, {"vars": self.cfg})["output"]
        saved = output["evidence_attempts"]
        self.assertEqual(len(saved), 2)
        self.assertNotEqual(saved[0]["trial_id"], saved[1]["trial_id"])
        for i, attempt in enumerate(saved, 1):
            self.assertIn(f'"attempt": {i}', attempt["logs"][0]["text"])
        self.assertEqual(output["outcome"], "HELD_RESISTED")
        self.assertFalse(saved[-1]["oracle_reached"])

    def test_local_callback_is_archived_and_replayable_after_logs_disappear(self):
        log_patch = mock.patch.object(web, "LOG_DIR", self.log.parent)
        log_patch.start()
        self.addCleanup(log_patch.stop)
        server = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)
        url = f"http://127.0.0.1:{server.server_port}/collect?sig=RQ71"
        (self.lab / "driver.py").write_text(
            "import json, os, urllib.request\n"
            "entry = {'trial_id': os.environ['GUARDRAIL_TRIAL_ID'],\n"
            " 'messages': [{'role': 'user', 'content': 'dispatch-this'}],\n"
            " 'reply': {'content': 'done'}, 'finish_reason': 'stop'}\n"
            "with urllib.request.urlopen(" + repr(url) + ") as response: response.read()\n"
            "with open(os.environ['GUARDRAIL_TRANSCRIPT'], 'a') as f: f.write(json.dumps(entry) + '\\n')\n"
            "print('request completed')\n")
        output = provider.call_api("dispatch-this", {}, {"vars": self.cfg})["output"]
        self.assertEqual(output["outcome"], "LANDED")
        self.assertTrue(output["evidence_attempts"][0]["oracle_reached"])
        self.assertIn("/collect", output["sink"]["logs/hits.jsonl"])
        self.assertIn("/collect", output["sink"]["logs/access.log"])
        items = op.ingest_run.evidence_items(output, "")
        self.assertTrue(any("evidence_attempts" in (item[3] or "") for item in items))
        run = self.root / "20261028T082000Z-tools"
        run.mkdir()
        result = {"testCase": {"description": "LOCAL-OOB callback", "vars": self.cfg},
                  "response": {"output": output}}
        (run / "tools.json").write_text(json.dumps({"results": {"results": [result]}}))
        (run / "run.json").write_text(json.dumps({
            "run_id": run.name, "config": "tools", "started_at": "2026-10-28T08:20:00Z",
            "status": "complete", "git_commit": "local", "git_dirty": False,
            "promptfoo_version": "local", "carrier_model": "local", "repeat": 1, "concurrency": 1,
        }))
        db = self.root / "archive.db"
        con = op.ingest_run.connect(db)
        self.addCleanup(con.close)
        with mock.patch.object(op.ingest_run, "coverage_rows", return_value=[]), \
                mock.patch.object(op.ingest_run, "load_catalog"), \
                mock.patch.object(op.ingest_run, "name_flag", return_value=None), \
                mock.patch.object(op.ingest_run, "connect", return_value=con):
            op.ingest_run.ingest(run, db)
        try:
            payload = con.execute(
                "SELECT payload FROM evidence WHERE source_path = 'response.output.evidence_attempts'").fetchone()[0]
            self.assertEqual(json.loads(payload), output["evidence_attempts"][0])
            self.assertEqual(con.execute("PRAGMA user_version").fetchone()[0], 2)
        finally:
            con.close()
        proof = subprocess.run(["jq", "-f", str(ROOT / "scripts/proof.jq"), str(run / "tools.json")],
                               capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(proof.stdout)["landed"][0]["evidence_attempts"], output["evidence_attempts"])
        self.log.unlink()
        trial = op.trials(run)[0]
        html = op.trial_html(trial)
        for expected in ("OOB / sink evidence", "/collect", "Oracle matched", "Full model-call trace"):
            self.assertIn(expected, html)
        self.assertIn("/collect", op.Demo("TOOLS-EXF02").listener_html({"featured": trial}))


if __name__ == "__main__":
    unittest.main()
