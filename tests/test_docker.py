"""The container packaging (Dockerfile, compose.yaml, .dockerignore, docker/entrypoint.sh).

Text checks only: no Docker daemon, no build. They hold the properties that make the container safe
to hand out: keys and results never enter the image, no lab listener is published (REQ-ENV-03),
and the image and bootstrap.sh install the same pinned promptfoo.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).absolute().parents[1]


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def service_block(compose: str, name: str) -> str:
    """The lines of one top-level service in compose.yaml, up to the next service."""
    m = re.search(rf"^  {name}:.*?(?=^  \S|\Z)", compose, re.M | re.S)
    if not m:
        raise AssertionError(f"no service {name!r} in compose.yaml")
    return m.group(0)


class DockerIgnoreTest(unittest.TestCase):
    def test_keys_results_and_host_envs_stay_out_of_the_image(self):
        ignored = {ln.strip() for ln in read(".dockerignore").splitlines()}
        for entry in (".env", "results/", "venv/", "**/node_modules/"):
            self.assertIn(entry, ignored)


class ComposeTest(unittest.TestCase):
    compose = read("compose.yaml")

    def test_eval_service_publishes_no_port(self):
        self.assertNotRegex(service_block(self.compose, "eval"), re.compile(r"^\s+ports:", re.M))

    def test_notebook_port_is_loopback_only(self):
        ports = re.findall(r'-\s*"([^"]+)"', service_block(self.compose, "notebook"))
        self.assertEqual(ports, ["127.0.0.1:8888:8888"])

    def test_env_is_mounted_read_only_and_never_created(self):
        block = service_block(self.compose, "eval")
        self.assertRegex(block, r"source: \./\.env\s+target: /app/\.env\s+read_only: true")
        self.assertIn("create_host_path: false", block)

    def test_results_are_a_host_mount(self):
        self.assertIn("./results:/app/results", service_block(self.compose, "eval"))


class PromptfooPinTest(unittest.TestCase):
    def test_version_file_is_one_exact_version(self):
        self.assertRegex(read("promptfoo/VERSION").strip(), r"^\d+\.\d+\.\d+$")

    def test_image_and_bootstrap_install_the_pinned_version(self):
        self.assertIn("promptfoo/VERSION", read("Dockerfile"))
        self.assertIn('promptfoo@$(cat promptfoo/VERSION)', read("scripts/bootstrap.sh"))


class EntrypointTest(unittest.TestCase):
    def test_every_documented_command_has_a_case(self):
        script = read("docker/entrypoint.sh")
        for cmd in ("run)", "notebook)", "test)", "report)", "shell)"):
            self.assertIn(f"  {cmd}", script)

    def test_image_never_copies_a_dotenv(self):
        self.assertNotRegex(read("Dockerfile"), r"COPY[^\n]*\.env\b")


if __name__ == "__main__":
    unittest.main()
