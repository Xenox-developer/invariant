import contextlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from invariant import workspace, dependencies


class CodexDiscoveryTests(unittest.TestCase):
    def test_prefers_codex_from_operator_path(self):
        with patch.object(workspace.shutil, "which", return_value="/opt/bin/codex"):
            self.assertEqual(workspace.find_codex(), "/opt/bin/codex")

    def test_detects_desktop_bundle_without_codex_on_path(self):
        for root in (Path("/Applications"), Path.home() / "Applications"):
            bundled = root / "ChatGPT.app/Contents/Resources/codex-cli/bin/codex"
            with (self.subTest(root=root),
                  patch.object(workspace.shutil, "which", return_value=None),
                  patch.object(workspace.sys, "platform", "darwin"),
                  patch.object(Path, "is_file", lambda path: path == bundled),
                  patch.object(workspace.os, "access", side_effect=lambda path, mode: path == bundled)):
                self.assertEqual(workspace.find_codex(), str(bundled))

    def test_does_not_use_a_non_executable_bundle(self):
        with (patch.object(workspace.shutil, "which", return_value=None),
              patch.object(workspace.sys, "platform", "darwin"),
              patch.object(Path, "is_file", return_value=True),
              patch.object(workspace.os, "access", return_value=False)):
            with self.assertRaisesRegex(RuntimeError, "Codex CLI was not found"):
                workspace.find_codex()

    def test_missing_codex_has_specific_installation_message(self):
        with (patch.object(workspace.shutil, "which", return_value=None),
              patch.object(workspace.sys, "platform", "linux")):
            with self.assertRaisesRegex(RuntimeError, "@openai/codex@0.163.0"):
                workspace.find_codex()

    def test_missing_docker_is_reported_separately(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory).resolve()
            with contextlib.redirect_stdout(io.StringIO()):
                workspace.init(project)
            with (patch.object(Path, "cwd", return_value=project),
                  patch.object(dependencies, "probe_codex", return_value={"status": "ready", "path": "/opt/bin/codex", "message": "ready"}),
                  patch.object(dependencies, "find_docker", side_effect=RuntimeError("Docker CLI was not found")),
                  patch.object(dependencies, "interactive_terminal", return_value=False)):
                with self.assertRaisesRegex(RuntimeError, "Docker CLI was not found"):
                    workspace.run("gpt-6.1-sol", effort="medium")


if __name__ == "__main__":
    unittest.main()
