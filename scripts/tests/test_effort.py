import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from invariant import cli, workspace


class EffortCliTests(unittest.TestCase):
    def test_forwards_effort_on_either_side_of_model_argument(self):
        for arguments in (["run", "--agent", "codex", "gpt-6.1-sol", "--effort", "high"],
                          ["run", "--effort", "ultra", "--agent", "codex", "gpt-6.1-sol"]):
            with self.subTest(arguments=arguments), patch.object(sys, "argv", ["invariant", *arguments]), patch.object(workspace, "run") as run:
                cli.main()
                run.assert_called_once_with("gpt-6.1-sol", duration=None, effort=arguments[arguments.index("--effort") + 1])

    def test_rejects_effort_outside_agent_run(self):
        for arguments in (["init"], ["prepare", "--agent", "codex"], ["run", "program.py"], ["status"], ["demo"]):
            with self.subTest(arguments=arguments), patch.object(sys, "argv", ["invariant", *arguments, "--effort", "high"]), contextlib.redirect_stderr(io.StringIO()) as error:
                with self.assertRaises(SystemExit) as raised:
                    cli.main()
                self.assertEqual(raised.exception.code, 2)
                self.assertIn("--effort applies only to run --agent codex", error.getvalue())


class EffortCatalogTests(unittest.TestCase):
    def test_uses_paginated_model_capabilities_including_hidden_models(self):
        rpc = Mock()
        rpc.request.side_effect = [
            {"data": [{"model": "other"}], "nextCursor": "page2"},
            {"data": [{"model": "selected", "supportedReasoningEfforts": [{"reasoningEffort": "ultra"}]}], "nextCursor": None},
        ]
        workspace.validate_effort(rpc, "selected", "ultra")
        self.assertEqual(rpc.request.call_args_list[0].args, ("model/list", {"limit": 100, "includeHidden": True}))
        self.assertEqual(rpc.request.call_args_list[1].args[1]["cursor"], "page2")

    def test_default_avoids_capability_lookup(self):
        rpc = Mock()
        workspace.validate_effort(rpc, "selected", None)
        rpc.request.assert_not_called()

    def test_rejects_unsupported_effort_with_available_choices(self):
        rpc = Mock()
        rpc.request.return_value = {"data": [{"model": "selected", "supportedReasoningEfforts": [{"reasoningEffort": "low"}, {"reasoningEffort": "high"}]}]}
        with self.assertRaisesRegex(RuntimeError, "Available levels: low, high"):
            workspace.validate_effort(rpc, "selected", "ultra")

    def test_rejects_unknown_models_and_broken_pagination(self):
        for page, message in (({"data": [], "nextCursor": None}, "not advertised"),
                              ({"data": [], "nextCursor": "repeated"}, "pagination")):
            with self.subTest(page=page):
                rpc = Mock()
                rpc.request.return_value = page
                with self.assertRaisesRegex(RuntimeError, message):
                    workspace.validate_effort(rpc, "selected", "high")


class EffortSessionTests(unittest.TestCase):
    def session(self, requested, reported):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        project, state = root / "project", root / "state"
        project.mkdir()
        with contextlib.redirect_stdout(io.StringIO()):
            workspace.init(project, automatic=False)
        rpc = Mock()
        calls = []

        def request(method, params, **kwargs):
            calls.append((method, params))
            if method == "environment/info":
                return {"cwd": "file:///workspace"}
            if method == "account/read":
                return {"account": {"type": "chatgpt"}}
            if method == "model/list":
                return {"data": [{"model": "selected", "supportedReasoningEfforts": [
                    {"reasoningEffort": value} for value in ("low", "medium", "high", "xhigh", "max", "ultra")]}]}
            if method == "thread/start":
                return {"thread": {"id": "00000000-0000-0000-0000-000000000001", "ephemeral": False, "environments": [{"environmentId": "invariant", "cwd": "/workspace"}]},
                        "model": "selected", "reasoningEffort": reported}
            if method == "turn/start":
                kwargs["event"]({"method": "turn/completed", "params": {"turn": {"status": "completed"}}})
                return {"turn": {"id": "turn"}}
            raise AssertionError("Unexpected request: " + method)

        rpc.request.side_effect = request
        output = io.StringIO()
        error = None
        version = subprocess.CompletedProcess([], 0, "codex-cli 0.163.0\n", "")
        with (patch.object(Path, "cwd", return_value=project),
              patch.object(workspace, "state_for", return_value=state),
              patch.object(workspace, "ensure_runtime", return_value=("/usr/bin/codex", "/usr/bin/docker")),
              patch.object(workspace.shutil, "which", return_value="/usr/bin/tool"),
              patch.object(workspace.subprocess, "run", return_value=version),
              patch.object(workspace, "docker", return_value="sha256:image"),
              patch.object(workspace, "verify_container"),
              patch.object(workspace, "remove_owned"),
              patch.object(workspace, "Control"),
              patch.object(workspace, "codex_configuration", return_value=(["codex"], {})),
              patch.object(workspace, "CodexRpc", return_value=rpc),
              patch("builtins.input", side_effect=["first", "second", "/exit"]),
              contextlib.redirect_stdout(output)):
            try:
                workspace.run("selected", effort=requested)
            except RuntimeError as caught:
                error = caught
        records = [json.loads(line) for line in next(state.glob("ws-*.jsonl")).read_text().splitlines()]
        return calls, records, output.getvalue(), error

    def test_selected_effort_reaches_thread_and_every_turn_and_is_recorded(self):
        for effort in ("low", "medium", "high", "xhigh", "max", "ultra"):
            with self.subTest(effort=effort):
                calls, records, output, error = self.session(effort, effort)
                self.assertIsNone(error)
                thread = next(params for method, params in calls if method == "thread/start")
                self.assertEqual(thread["config"], {"model_reasoning_effort": effort})
                turns = [params for method, params in calls if method == "turn/start"]
                self.assertEqual(len(turns), 2)
                self.assertTrue(all(turn["effort"] == effort for turn in turns))
                self.assertEqual(records[0]["requested_reasoning_effort"], effort)
                self.assertEqual(next(row for row in records if row["event"] == "session_configured")["reasoning_effort"], effort)
                self.assertIn("Reasoning effort: " + effort, output)
                self.assertTrue(records[-1]["clean"])

    def test_omission_preserves_codex_default(self):
        calls, records, output, error = self.session(None, "medium")
        self.assertIsNone(error)
        self.assertFalse(any(method == "model/list" for method, _ in calls))
        self.assertNotIn("config", next(params for method, params in calls if method == "thread/start"))
        self.assertTrue(all("effort" not in params for method, params in calls if method == "turn/start"))
        self.assertIn("Reasoning effort: medium (Codex default)", output)

    def test_clamped_effort_cannot_start_a_turn(self):
        calls, records, output, error = self.session("high", "medium")
        self.assertIn("refusing a fallback", str(error))
        self.assertFalse(any(method == "turn/start" for method, _ in calls))
        self.assertFalse(records[-1]["clean"])

    def test_unsupported_effort_cannot_create_a_thread(self):
        calls, records, output, error = self.session("unsupported", "high")
        self.assertIn("does not support", str(error))
        self.assertFalse(any(method in ("thread/start", "turn/start") for method, _ in calls))


if __name__ == "__main__":
    unittest.main()
