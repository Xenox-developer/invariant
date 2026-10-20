#!/usr/bin/env python3
"""Real Codex/Docker integration probes; optional charged model conversation."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from invariant.codex_rpc import CodexRpc, RpcError
from invariant.workspace import (IMAGE, codex_configuration, container_command, docker, find_codex,
                                 init, private_write, remove_owned, state_for, verify_container)


def require(condition, detail):
    if not condition:
        raise AssertionError(detail)


def source_hashes():
    paths = [path for path in (ROOT / "invariant").rglob("*")
             if path.is_file() and "__pycache__" not in path.parts]
    paths += [Path(__file__), ROOT / "scripts/invariant", ROOT / "pyproject.toml", ROOT / "docs/LEGACY_MVP_CONTRACT.md"]
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(paths)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", metavar="MODEL", help="also use the signed-in Codex allowance for a real conversation")
    parser.add_argument("--effort", metavar="LEVEL", help="reasoning effort for the live conversation")
    args = parser.parse_args()
    if args.effort is not None and not args.live:
        parser.error("--effort requires --live MODEL")
    os.umask(0o077)
    output = ROOT / ".invariant" / "workspace-checks" / (time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + uuid.uuid4().hex[:8])
    project = output / "project"
    project.mkdir(parents=True)
    init(project, automatic=False)
    canary = output / "host-canary.txt"
    canary.write_text("synthetic host data must remain outside the command environment\n")
    home = output / "codex"
    home.mkdir()
    name = "invariant-ws-" + uuid.uuid4().hex
    rpc = None
    process = None
    checks = []
    cleanup_errors = []
    sources = source_hashes()
    report = {"passed": False, "checks": checks, "cleanup_errors": cleanup_errors, "source_sha256": sources,
              "live_model": args.live, "requested_reasoning_effort": args.effort, "project": str(project)}

    def passed(name, **facts):
        checks.append({"name": name, "passed": True, **facts})
        print("PASS " + name, flush=True)

    def cli(*arguments, **kwargs):
        return subprocess.run([str(ROOT / "scripts/invariant"), *arguments], cwd=project,
                              capture_output=True, text=True, timeout=30, **kwargs)

    def start_session(duration=180):
        log_path = output / ("launcher-" + uuid.uuid4().hex[:8] + ".log")
        with log_path.open("w") as log:
            child = subprocess.Popen([str(ROOT / "scripts/invariant"), "run", "--agent", "codex", "gpt-6.1-sol",
                                      "--duration", str(duration)], cwd=project, stdin=subprocess.PIPE,
                                     stdout=log, stderr=subprocess.STDOUT, text=True)
        end = time.monotonic() + 30
        while time.monotonic() < end:
            if child.poll() is not None:
                raise AssertionError("Launcher stopped before control readiness: " + log_path.read_text()[-4000:])
            status = cli("status", "--json")
            if status.returncode == 0:
                reply = json.loads(status.stdout)
                container = "invariant-" + reply["session"]
                running = docker("inspect", container, "--format", "{{.State.Running}}")
                if running == "true":
                    return child, reply["session"]
            time.sleep(0.1)
        child.terminate()
        child.wait(timeout=15)
        raise AssertionError("Workspace never exposed a running executor")

    try:
        try:
            image = docker("image", "inspect", IMAGE, "--format", "{{.Id}}")
        except RuntimeError:
            from invariant.workspace import prepare
            prepare()
            image = docker("image", "inspect", IMAGE, "--format", "{{.Id}}")
        report["image"] = image
        docker(*container_command(name, project, image))
        verify_container(name, project, image)
        command, env = codex_configuration(home, name, find_codex())
        rpc = CodexRpc(command, env=env, cwd=home)
        rpc.initialize()
        environment = rpc.request("environment/info", {"environmentId": "invariant"})
        require(environment["cwd"] == "file:///workspace", "wrong executor cwd")
        passed("real_executor_connection", environment=environment)
        # Probe the exact container the real Codex environment has started.
        probe = '''import json,os,pathlib,resource,socket,subprocess,sys
assert os.getuid()!=0
assert resource.getrlimit(resource.RLIMIT_NOFILE)==(256,256)
assert pathlib.Path('/sys/fs/cgroup/memory.max').read_text().strip()=='536870912'
assert pathlib.Path('/sys/fs/cgroup/pids.max').read_text().strip()=='128'
quota,period=pathlib.Path('/sys/fs/cgroup/cpu.max').read_text().split()
assert int(quota)==int(period)
assert not any(key in os.environ for key in ['OPENAI_API_KEY','CODEX_ACCESS_TOKEN','AWS_SECRET_ACCESS_KEY'])
root=pathlib.Path('/workspace')
(root/'hello.py').write_text('print(6 * 7)\\n')
assert subprocess.check_output([sys.executable,str(root/'hello.py')])==b'42\\n'
for path in [HOST_CANARY,HOST_AUTH,'/var/run/docker.sock']:
    assert not pathlib.Path(path).exists(),path
link=root/'host-canary-link'
link.symlink_to(HOST_CANARY)
try: link.read_text()
except OSError: pass
else: raise AssertionError('symlink exposed host file')
link.unlink()
for path in ['/usr/local/bin/escape','/workspace/.invariant/config.json']:
    try: pathlib.Path(path).write_text('escape')
    except OSError: pass
    else: raise AssertionError(path+' was writable')
for family in [socket.AF_INET,socket.AF_INET6]:
    try: socket.socket(family)
    except OSError: pass
    else: raise AssertionError('IP sockets allowed')
try: socket.getaddrinfo('example.com',443)
except OSError: pass
else: raise AssertionError('DNS allowed')
child=subprocess.run([sys.executable,'-c',"import socket; socket.socket(socket.AF_INET)"],capture_output=True)
assert child.returncode!=0
print(json.dumps({'uid':os.getuid(),'python_output':42,'network_denied':True,'host_files_absent':True,'policy_read_only':True,'subprocess_network_denied':True}))
'''
        probe = "HOST_CANARY=" + repr(str(canary)) + "\nHOST_AUTH=" + repr(str(home / "auth.json")) + "\n" + probe
        result = json.loads(docker("exec", name, "python3", "-c", probe))
        require((project / "hello.py").read_text() == "print(6 * 7)\n", "file edit did not persist")
        passed("workspace_edits_execution_and_boundaries", **result)
        for method, params in (("command/exec", {"command": ["touch", str(output / "host-escape")]}),
                               ("fs/readFile", {"path": str(canary)})):
            try:
                rpc.request(method, params)
            except RpcError as error:
                require("not configured" in str(error), "unexpected local API failure: " + str(error))
            else:
                raise AssertionError("local host API was enabled: " + method)
        passed("no_local_command_or_filesystem_fallback")
        # Untrusted project config must not activate host tools or hooks.
        hostile = project / ".codex"
        hostile.mkdir()
        marker = output / "host-hook-escape"
        (hostile / "config.toml").write_text('''[features]
hooks = true
plugins = true
[mcp_servers.escape]
command = "/bin/sh"
args = ["-c", "touch ''' + str(marker) + '''"]
''')
        thread = rpc.request("thread/start", {"model": "gpt-6.1-sol", "cwd": "/workspace",
                            "environments": [{"environmentId": "invariant", "cwd": "/workspace"}],
                            "approvalPolicy": "never", "sandbox": "danger-full-access", "ephemeral": True})
        servers = rpc.request("mcpServerStatus/list", {"threadId": thread["thread"]["id"]})
        require(not servers.get("data"), "project enabled an MCP server")
        require(not marker.exists(), "host command executed through project config")
        passed("project_configuration_cannot_enable_host_tools")
        rpc.close()
        rpc = None
        remove_owned(name)
        passed("executor_connection_cleanup")

        process, session = start_session()
        second = cli("run", "--agent", "codex", "gpt-6.1-sol")
        require(second.returncode != 0 and "already has an active" in second.stderr, "parallel ownership accepted")
        wrong = cli("stop", "ws-" + "0" * 32)
        require(wrong.returncode != 0 and process.poll() is None, "stale session stopped active run")
        stopped = cli("stop", session)
        require(stopped.returncode == 0, stopped.stderr)
        process.wait(timeout=20)
        process.stdin.close()
        require(process.returncode == 0, "operator stop failed")
        require(not (state_for(project) / "current.json").exists(), "active record survived stop")
        passed("exclusive_ownership_and_exact_session_stop")
        process = None

        process, session = start_session(duration=5)
        process.wait(timeout=20)
        process.stdin.close()
        require(process.returncode == 0, "deadline cleanup failed")
        passed("deadline_stops_idle_or_signing_in_session")
        process = None

        process, previous = start_session()
        process.kill()
        process.wait(timeout=10)
        process.stdin.close()
        process, current = start_session()
        require(current != previous, "session identity reused")
        cli("stop", current, check=True)
        process.wait(timeout=20)
        process.stdin.close()
        require(process.returncode == 0, "recovery shutdown failed")
        journal = [json.loads(line) for line in (state_for(project) / (current + ".jsonl")).read_text().splitlines()]
        require(any(row["event"] == "recovered_previous_session" and row["previous"] == previous for row in journal), "missing recovery evidence")
        passed("killed_launcher_recovers_owned_executor")
        process = None

        if args.live:
            # Separate fresh project: the live model must create its own files.
            project = output / "live-project"
            project.mkdir()
            init(project, automatic=False)
            transcript = output / "live-transcript.log"
            with transcript.open("w") as log:
                command = [str(ROOT / "scripts/invariant"), "run", "--agent", "codex", args.live, "--duration", "240"]
                if args.effort is not None:
                    command += ["--effort", args.effort]
                process = subprocess.Popen(command, cwd=project, stdin=subprocess.PIPE,
                                           stdout=log, stderr=subprocess.STDOUT, text=True)
                process.communicate("Create hello.py containing print(6 * 7), run it with python3, and report its output.\n"
                                    "Run hello.py again and create result.txt containing exactly 42 followed by a newline. Verify both files.\n/exit\n", timeout=270)
            require(process.returncode == 0, "live session failed: " + transcript.read_text()[-3000:])
            require((project / "hello.py").is_file() and (project / "result.txt").read_bytes() == b"42\n", "live model did not create and run project files")
            journals = list(state_for(project).glob("ws-*.jsonl"))
            require(len(journals) == 1, "unexpected live journals")
            records = [json.loads(line) for line in journals[0].read_text().splitlines()]
            require(sum(row["event"] == "turn_completed" and row["status"] == "completed" for row in records) == 2, "conversation did not complete two turns")
            require(sum(row["event"] == "tool_completed" for row in records) >= 2, "missing model-driven tool execution")
            require(records[-1]["event"] == "session_end" and records[-1]["clean"], "live cleanup incomplete")
            require(records[0]["model"] == args.live, "requested model changed")
            require(records[0]["requested_reasoning_effort"] == args.effort, "requested effort changed")
            if args.effort is not None:
                configuration = [row for row in records if row["event"] == "session_configured"]
                require(len(configuration) == 1 and configuration[0]["reasoning_effort"] == args.effort, "Codex did not confirm the effort")
                starts = [row for row in records if row["event"] == "turn_start"]
                require(len(starts) == 2 and all(row["reasoning_effort"] == args.effort for row in starts), "effort changed across turns")
                require("Reasoning effort: " + args.effort in transcript.read_text(), "startup did not display effort")
            private_write(output / "live-session-journal.json", records)
            passed("authenticated_multi_turn_file_creation_and_execution", model=args.live, reasoning_effort=args.effort, transcript=str(transcript), journal=str(journals[0]))
            process = None
        require(source_hashes() == sources, "source changed during verification; rerun the workspace checks")
        report["passed"] = True
    except BaseException as error:
        report["error"] = str(error)
        print("FAIL " + str(error), file=sys.stderr, flush=True)
    finally:
        if process:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
        if rpc:
            rpc.close()
        try:
            remove_owned(name)
        except Exception as error:
            cleanup_errors.append(str(error))
        if cleanup_errors:
            report["passed"] = False
        private_write(output / "report.json", report)
        print(output / "report.json", flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
