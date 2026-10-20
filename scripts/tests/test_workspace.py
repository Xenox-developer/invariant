import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch

from invariant import workspace
from invariant.packages import ENVIRONMENT, USER_BASE
from invariant.codex_rpc import CodexRpc, RpcError, inert


class WorkspacePolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name) / "project"
        self.project.mkdir()
        with contextlib.redirect_stdout(io.StringIO()):
            workspace.init(self.project, automatic=False)

    def test_init_is_idempotent_and_refuses_changed_policy(self):
        config = self.project / ".invariant/config.json"
        before = config.read_bytes()
        with contextlib.redirect_stdout(io.StringIO()):
            workspace.init(self.project, automatic=False)
        self.assertEqual(config.read_bytes(), before)
        config.write_text(json.dumps({**workspace.PROFILE, "command_network": "enabled"}))
        with self.assertRaisesRegex(RuntimeError, "Unsupported"):
            workspace.load_project(self.project)

    def test_configuration_symlinks_are_rejected(self):
        config = self.project / ".invariant/config.json"
        external = self.project.parent / "config"
        config.rename(external)
        config.symlink_to(external)
        with self.assertRaisesRegex(RuntimeError, "symlink"):
            workspace.load_project(self.project)

    def test_hardlinks_symlinks_and_sockets_are_not_exposed(self):
        import socket
        outside = self.project.parent / "secret"
        outside.write_text("host canary")
        candidate = self.project / "bad"
        candidate.symlink_to(outside)
        with self.assertRaises(RuntimeError):
            workspace.check_project_files(self.project)
        candidate.unlink()
        os.link(outside, candidate)
        with self.assertRaisesRegex(RuntimeError, "hard-linked"):
            workspace.check_project_files(self.project)
        candidate.unlink()
        with socket.socket(socket.AF_UNIX) as listener:
            listener.bind(str(candidate))
            with self.assertRaisesRegex(RuntimeError, "special"):
                workspace.check_project_files(self.project)

    @unittest.skipIf(os.getuid() == 0, "root can traverse mode-000 directories")
    def test_unreadable_directories_cannot_hide_unsafe_entries(self):
        hidden = self.project / "unreadable"
        hidden.mkdir()
        (hidden / "host-link").symlink_to(self.project.parent / "outside")
        hidden.chmod(0)
        try:
            with self.assertRaisesRegex(RuntimeError, "Cannot inspect"):
                workspace.check_project_files(self.project)
        finally:
            hidden.chmod(0o700)

    def test_only_remote_executor_and_no_ambient_credentials(self):
        home = Path(self.temp.name) / "codex"
        home.mkdir()
        with patch.dict(os.environ, {"OPENAI_API_KEY": "must-not-inherit", "AWS_SECRET_ACCESS_KEY": "must-not-inherit"}):
            command, env = workspace.codex_configuration(home, "invariant-test", "/usr/bin/codex")
        value = tomllib.loads((home / "environments.toml").read_text())
        self.assertFalse(value["include_local"])
        self.assertEqual(value["default"], "invariant")
        self.assertEqual(value["environments"][0]["args"], ["start", "--attach", "--interactive", "invariant-test"])
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertNotIn("AWS_SECRET_ACCESS_KEY", env)
        self.assertIn("--strict-config", command)
        configured = tomllib.loads((home / 'config.toml').read_text())['shell_environment_policy']['set']
        self.assertTrue(all(configured.get(key) == value for key, value in ENVIRONMENT.items()))
        # These settings affect remote commands only, not trusted host Python.
        self.assertNotIn('PYTHONUSERBASE', env)
        self.assertNotIn(USER_BASE, env['PATH'])
        for name in ("hooks", "plugins", "apps", "browser_use", "computer_use", "image_generation", "multi_agent"):
            self.assertIn(f"features.{name}=false", command)

    def test_container_has_only_project_mounts_and_no_network(self):
        arguments = workspace.container_command("test", self.project, "sha256:image")
        self.assertIn("--network=none", arguments)
        self.assertIn("--read-only", arguments)
        mounts = [arguments[i + 1] for i, arg in enumerate(arguments) if arg == "--mount"]
        self.assertEqual(len(mounts), 2)
        self.assertTrue(mounts[1].endswith("/workspace/.invariant,readonly"))
        self.assertNotIn("docker.sock", " ".join(arguments))
        for key, value in ENVIRONMENT.items():
            self.assertIn('--env=' + key + '=' + value, arguments)

    def test_terminal_controls_are_inert(self):
        text = inert("hello\n\x1b]52;clipboard\x07\u202esecret")
        self.assertNotIn("\x1b", text)
        self.assertNotIn("\u202e", text)
        self.assertIn("\\u{1b}", text)

    def test_failed_recovery_preserves_the_previous_session_record(self):
        state = self.project.parent / "trusted-state"
        state.mkdir()
        previous = {"session": "ws-" + "1" * 32, "pid": 12345}
        workspace.private_write(state / "current.json", previous)
        version = subprocess.CompletedProcess(["codex", "--version"], 0, "codex-cli 0.163.0\n", "")
        with (patch.object(Path, "cwd", return_value=self.project),
              patch.object(workspace, "state_for", return_value=state),
              patch.object(workspace, "ensure_runtime", return_value=("/usr/bin/codex", "/usr/bin/docker")),
              patch.object(workspace.shutil, "which", return_value="/usr/bin/tool"),
              patch.object(workspace.subprocess, "run", return_value=version),
              patch.object(workspace, "remove_owned", side_effect=RuntimeError("daemon unavailable")),
              contextlib.redirect_stdout(io.StringIO())):
            with self.assertRaisesRegex(RuntimeError, "daemon unavailable"):
                workspace.run("gpt-6.1-sol")
        self.assertEqual(json.loads((state / "current.json").read_text()), previous)


class RpcTests(unittest.TestCase):
    def rpc(self, code):
        rpc = CodexRpc([sys.executable, "-u", "-c", code], env=os.environ.copy(), cwd=Path.cwd())
        self.addCleanup(rpc.close)
        return rpc

    def test_protocol_handshake_and_denial_of_escalation(self):
        rpc = self.rpc('''import json,sys
first=json.loads(input()); assert first['method']=='initialize'
print(json.dumps({'id':first['id'],'result':{}}),flush=True)
assert json.loads(input())['method']=='initialized'
req=json.loads(input())
print(json.dumps({'method':'item/commandExecution/requestApproval','id':99,'params':{}}),flush=True)
assert json.loads(input())['result']['decision']=='decline'
print(json.dumps({'id':req['id'],'result':{'safe':True}}),flush=True)
''')
        rpc.initialize()
        self.assertEqual(rpc.request("probe", {}), {"safe": True})

    def test_oversized_frame_fails_closed(self):
        rpc = self.rpc("import sys;sys.stdout.write('x'*(2*1024*1024+1));sys.stdout.flush()")
        with self.assertRaisesRegex(RpcError, "oversized"):
            while True:
                rpc.receive()

    def test_disconnect_does_not_hang(self):
        rpc = self.rpc("pass")
        with self.assertRaisesRegex(RpcError, "closed"):
            rpc.request("initialize", {}, timeout=2)


if __name__ == "__main__":
    unittest.main()
