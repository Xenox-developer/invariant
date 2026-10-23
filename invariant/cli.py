#!/usr/bin/env python3
"""Trusted Desktop/Linux launcher. The workload never mounts the Docker socket."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone
import re
from .policy import DEFAULT_DURATION
from . import __version__
from .distribution import legacy_root, source_checkout

ROOT = legacy_root()
STATE_VOLUME = "invariant-supervisor-state-v1"
PYTHON_IMAGE = "invariant-python:local"
CONTROL_IMAGE = "invariant-supervisor:local"


def docker(*args, capture=False):
    return subprocess.run(["docker", *args], check=True, text=True,
                          stdout=subprocess.PIPE if capture else None,
                          timeout=600 if args[0] == "build" else 30)


def main():
    if len(sys.argv)>1 and sys.argv[1]=='environment':
        from .environment_cli import main as environment_main
        return environment_main(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == 'experiment':
        from .experiments.cli import main as experiment_main
        return experiment_main(sys.argv[2:])
    parser = argparse.ArgumentParser(description="Create isolated agent workspaces or run bounded Python programs. Use invariant experiment --help for frozen studies.")
    parser.add_argument('--version', action='version', version='invariant '+__version__)
    parser.add_argument("command", choices=("setup", "init", "prepare", "run", "resume", "chats", "guardrails", "config", "demo", "test", "acceptance", "evidence", "status", "grant", "revoke", "stop"))
    parser.add_argument("target", nargs="?", help="model for agent run; chat ID for resume (omit for latest); Python file or execution ID")
    parser.add_argument("config_key", nargs="?", help="setting name for config set")
    parser.add_argument("config_value", nargs="?", help="setting value for config set")
    parser.add_argument("--agent", choices=("codex",), help="run an interactive Codex workspace; target is the model name")
    parser.add_argument("--model", help="choose and remember a Codex model for this project")
    parser.add_argument("--effort", metavar="LEVEL", help="model reasoning effort, e.g. medium, high, ultra; omit for Codex default on run, or saved effort on resume")
    parser.add_argument("--duration", type=int, default=None, help="session seconds; defaults to project configuration for Codex, 900 for Python")
    parser.add_argument("--no-grant", action="store_true", help="start without access; issue it later with grant")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--check", action="store_true", help="check setup readiness without installing or starting anything")
    pace = parser.add_mutually_exclusive_group()
    pace.add_argument("--step", action="store_true", help="pause between guided demo stages")
    pace.add_argument("--auto", action="store_true", help="run the guided demo automatically (default)")
    args = parser.parse_intermixed_args()
    if args.model is not None and args.command != "run":
        parser.error("--model applies only to run; resume keeps the saved chat's model")
    if args.model is not None and args.target is not None:
        parser.error("choose a model with either --model or a positional model name, not both")
    agent_run = args.command == "run" and (args.agent is not None or args.model is not None or args.target is None
        or (not args.target.endswith('.py') and not Path(args.target).exists()))
    if args.command == "config":
        if args.agent or args.effort is not None or args.duration is not None or args.no_grant or args.check or args.step or args.auto:
            parser.error("config takes only an optional set KEY VALUE and --json")
        if (args.target is None and (args.config_key is not None or args.config_value is not None)) or (args.target is not None and (args.target != "set" or args.config_key is None or args.config_value is None)):
            parser.error("use invariant config, or invariant config set KEY VALUE")
        from .configuration import main as config_main
        return config_main(args.target, args.config_key, args.config_value, machine=args.json)
    if args.config_key is not None or args.config_value is not None:
        parser.error("unexpected arguments; additional positional arguments apply only to config set")
    if args.check and args.command != "setup":
        parser.error("--check applies only to setup")
    if args.command == "guardrails":
        if args.target or args.agent or args.effort is not None or args.no_grant or args.step or args.auto or args.duration is not None:
            parser.error("guardrails takes only --json; run it inside your initialized project")
        from .guardrails import main as guardrails_main
        return guardrails_main(machine=args.json)
    if args.command == "setup":
        if args.target or args.agent or args.effort or args.no_grant or args.step or args.auto or args.duration is not None:
            parser.error("setup takes only --check and --json")
        from .setup import main as setup_main
        return setup_main(check=args.check, machine=args.json)
    if args.effort is not None and not (args.command == "resume" or agent_run):
        parser.error("--effort applies only to run --agent codex or resume")
    if args.agent and args.command not in ("run", "prepare", "resume"):
        parser.error("--agent applies only to run, prepare or resume")
    if args.command == "chats":
        if args.target or args.no_grant or args.step or args.auto or args.duration is not None:
            parser.error("chats takes only --json")
        from .chats import list_chats
        return list_chats(machine=args.json)
    if args.command == "resume":
        if args.no_grant or args.json or args.step or args.auto:
            parser.error("resume takes an optional chat ID, --effort and --duration")
        if args.duration is not None and not 1 <= args.duration <= 86400:
            parser.error("agent duration must be between 1 and 86400 seconds")
        from .workspace import run
        return run(None, duration=args.duration, effort=args.effort, resume=args.target or True)
    if args.command == "init":
        if args.target or args.no_grant or args.json or args.step or args.auto or args.duration is not None:
            parser.error("init takes no arguments; run it inside your project folder")
        from .workspace import init
        init()
        return
    if args.agent or agent_run:
        if args.no_grant or args.json or args.step or args.auto:
            parser.error("agent workspaces use their project configuration; these options do not apply")
        from .workspace import prepare, run
        if args.command == "prepare":
            if args.target:
                parser.error("prepare --agent codex takes no model argument")
            prepare(persistent=True)
        else:
            if args.duration is not None and not 1 <= args.duration <= 86400:
                parser.error("agent duration must be between 1 and 86400 seconds")
            return run(args.model or args.target, duration=args.duration, effort=args.effort)
        return
    if args.command in ("status", "stop") and (
            (args.target and args.target.startswith("ws-")) or
            (args.command == "status" and not args.target and (Path.cwd() / ".invariant" / "config.json").exists())):
        from .workspace import operator
        operator(args.command, args.target, machine=args.json)
        return
    if args.command != "demo" and (args.step or args.auto):
        parser.error("--step and --auto apply only to demo")
    if args.command == "demo":
        if args.target or args.no_grant or args.json or args.duration is not None:
            parser.error("demo takes only --step or --auto")
        sys.path.insert(0, str(ROOT / "scripts"))
        from guided_demo import main as demo_main
        sys.exit(demo_main(step=args.step))
    if args.command == "acceptance":
        subprocess.run([sys.executable, str(source_checkout() / "scripts" / "test_mvp_acceptance.py")], check=True)
        return
    if args.command in ("status", "grant", "revoke", "stop"):
        if args.command != "status" and not args.target:
            parser.error(args.command + " requires an execution ID; use status to find it")
        if args.target and not re.fullmatch(r"run-[0-9a-f]{64}", args.target):
            parser.error("invalid execution ID")
        controllers = docker("ps", "-q", "--filter", "label=io.invariant.role=control", "--filter", "volume=" + STATE_VOLUME, capture=True).stdout.split()
        if len(controllers) != 1:
            raise RuntimeError("No single active supervisor; start a run and wait for operator controls to be ready")
        command = ["docker", "exec", controllers[0], "invariant-supervisor", "--state", "/state", "--operator", args.command]
        if args.target:
            command += ["--execution", args.target]
        result = subprocess.run(command, capture_output=True, text=True, timeout=15)
        try:
            reply = json.loads(result.stdout)
        except ValueError:
            raise RuntimeError("Operator controls are unavailable; wait for readiness or check the run terminal") from None
        if args.json:
            print(json.dumps(reply))
        elif not reply.get("ok"):
            print("Command rejected: " + reply.get("error", "unknown_error"), file=sys.stderr)
        else:
            status = reply["status"]
            print("Execution:", status["execution_id"])
            print("State:", "unavailable" if not status["healthy"] else "running" if status["running"] else "stopped")
            resource = status["resource"]
            print("Resource:", resource["resource_id"], "version", resource["configuration_version"])
            print("Account/environment:", resource["account"] + "/" + resource["environment"])
            print("Admissions across all grants:", status["total_admissions"])
            grant = status["grant"]
            if grant is None:
                print("Grant: none — invoice access is denied")
            else:
                print("Grant:", grant["state"], "(" + grant["grant_id"] + ")")
                print("Access:", grant["method"], grant["path"])
                print("Remaining reads:", grant["remaining_requests"], "of", grant["max_requests"])
                print("Expires:", datetime.fromtimestamp(grant["expires_at_ms"] / 1000, timezone.utc).isoformat())
        if not reply.get("ok") or result.returncode != 0:
            sys.exit(1)
        return
    if args.command == "prepare":
        for filename, tag in (("python.Dockerfile", PYTHON_IMAGE), ("supervisor.Dockerfile", CONTROL_IMAGE)):
            docker("build", "--file", str(ROOT / "docker" / filename), "--tag", tag, str(ROOT))
        return
    if args.command == "evidence":
        # Explicit operator export; the volume holds only trusted supervisor data.
        output = Path.cwd() / ".invariant" / "docker-evidence"
        output.mkdir(parents=True, exist_ok=True, mode=0o700)
        result = docker("create", "--network=none", "--mount", f"type=volume,src={STATE_VOLUME},dst=/state,readonly", CONTROL_IMAGE, "--state", "/state", "--export-evidence", capture=True)
        container = result.stdout.strip()
        try:
            docker("start", "--attach", container)
            docker("cp", container + ":/export/.", str(output))
        finally:
            docker("rm", "--force", container)
        print(output)
        return
    program_path = Path(args.target) if args.target else None
    if args.command == "run" and (program_path is None or not program_path.is_file()):
        parser.error("run requires a Python program file")
    args.duration = DEFAULT_DURATION if args.duration is None else args.duration
    if not 1 <= args.duration <= 900:
        parser.error("duration must be between 1 and 900 seconds")
    docker("volume", "create", STATE_VOLUME, capture=True)
    volume = json.loads(docker("volume", "inspect", STATE_VOLUME, capture=True).stdout)[0]
    command = ["docker", "run", "--rm", "--pull=never", "--network=none",
               "--label=io.invariant.role=control", "--mount", "type=bind,src=/var/run/docker.sock,dst=/var/run/docker.sock",
               "--mount", f"type=volume,src={STATE_VOLUME},dst=/state"]
    if args.command == "run":
        program = str(program_path.resolve())
        if "," in program or "\n" in program:
            parser.error("unsupported program filename")
        command += ["--mount", f"type=bind,src={program},dst=/program.py,readonly"]
    command += [CONTROL_IMAGE, "--state", "/state", "--daemon-state", volume["Mountpoint"], "--image", PYTHON_IMAGE]
    command += ["--self-test"] if args.command == "test" else ["--program", "/program.py", "--duration", str(args.duration)]
    if args.command == "run" and args.no_grant:
        command += ["--no-grant"]
    # Docker forwards SIGINT/SIGTERM to the supervisor's cleanup handler.
    os.execvp(command[0], command)


def entrypoint():
    try:
        sys.exit(main())
    except (subprocess.SubprocessError, OSError, RuntimeError) as error:
        print(f"INVARIANT launcher: {error}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    entrypoint()
