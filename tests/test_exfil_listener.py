"""_maybe_start_exfil: this lab's OOB listener must own localhost:9999 before a run (REQ-OR-02).

The OS edges are stubbed: the /proc scan (_exfil_pids), the port probe (_port_up),
os.kill, subprocess.Popen and time.sleep.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).absolute().parents[1]
sys.path.insert(0, str(ROOT / "promptfoo" / "providers"))
import agentic_goat_provider as provider  # noqa: E402


class Listener(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.lab = self.root / "labs" / "06-cross-server"
        self.lab.mkdir(parents=True)
        self.ours = self.lab / "exfil_server.py"
        self.ours.write_text("# listener\n")
        self.other = self.root / "labs" / "01-poisoning" / "exfil_server.py"
        self.env = {"PATH": "/usr/bin"}
        for name, value in (("REPO_ROOT", self.root),):
            p = mock.patch.object(provider, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.kill = self.stub(provider.os, "kill")
        self.popen = self.stub(provider.subprocess, "Popen")
        self.sleep = self.stub(provider.time, "sleep")

    def stub(self, target, name, **kw):
        p = mock.patch.object(target, name, **kw)
        self.addCleanup(p.stop)
        return p.start()

    def start(self, pids, port, cfg=None):
        """Run with these listeners found in /proc and this sequence of port probes."""
        self.stub(provider, "_exfil_pids", return_value=pids)
        self.port = self.stub(provider, "_port_up", side_effect=list(port))
        provider._maybe_start_exfil(self.lab, {"start_exfil": True} if cfg is None else cfg, self.env)


class Skipped(Listener):
    def test_rows_without_start_exfil_touch_nothing(self):
        for cfg in ({}, {"start_exfil": ""}, {"start_exfil": False}):
            with self.subTest(cfg=cfg):
                self.start([(11, str(self.other))], [], cfg=cfg)
                self.assertEqual((self.kill.call_count, self.popen.call_count, self.port.call_count), (0, 0, 0))


class AlreadyOurs(Listener):
    def test_our_running_listener_is_kept_and_others_are_stopped(self):
        self.start([(11, str(self.ours)), (12, str(self.other))], [True])
        self.kill.assert_called_once_with(12, 15)
        self.popen.assert_not_called()

    def test_our_listener_with_the_port_down_is_restarted(self):
        self.start([(11, str(self.ours))], [False, False, True])
        self.kill.assert_not_called()
        self.popen.assert_called_once()


class Takeover(Listener):
    def test_other_lab_listener_is_stopped_then_ours_started(self):
        self.start([(12, str(self.other))], [True, False, False, True])
        self.kill.assert_called_once_with(12, 15)
        args, kw = self.popen.call_args
        self.assertEqual(args[0], [sys.executable, "-u", str(self.ours)])
        self.assertEqual((kw["cwd"], kw["env"], kw["start_new_session"]), (str(self.lab), self.env, True))
        self.assertEqual(kw["stdout"].name, str(self.root / "results/listeners/06-cross-server.log"))

    def test_live_log_is_appended_not_replaced(self):
        live = self.root / "results/listeners/06-cross-server.log"
        live.parent.mkdir(parents=True)
        live.write_text("EARLIER CALLBACK\n")
        self.start([], [False, True])
        self.assertEqual(live.read_text(), "EARLIER CALLBACK\n")
        self.assertEqual(self.popen.call_args.kwargs["stdout"].mode, "a")

    def test_relative_script_path_is_never_taken_for_ours(self):
        self.start([(13, "exfil_server.py")], [False, True])
        self.kill.assert_called_once_with(13, 15)

    def test_a_listener_that_is_already_gone_is_ignored(self):
        self.kill.side_effect = OSError("no such process")
        self.start([(12, str(self.other))], [False, True])
        self.popen.assert_called_once()

    def test_waits_for_the_old_listener_to_release_the_port(self):
        self.start([(12, str(self.other))], [True, True, True, False, False, True])
        self.assertEqual(self.port.call_count, 6)
        self.popen.assert_called_once()

    def test_waits_for_ours_to_come_up(self):
        self.start([], [False, False, False, True])
        self.assertEqual(self.port.call_count, 4)


if __name__ == "__main__":
    unittest.main()
