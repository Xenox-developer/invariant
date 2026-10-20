"""Trusted workspace launcher. Only the executor sees the writable project."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid

from .execution import ExecutionContext, project_lock
from .codex_rpc import CodexRpc, RpcError, inert
from .chats import ChatStore, valid_id, MODEL, EFFORT
from . import preferences, resources, recovery, packages
from . import environment as machine
from .dependencies import find_codex, find_docker, docker_env, ensure_runtime
from .policy import DEFAULT_DURATION, LIMITS, PROFILE, DEFAULT_CONFIG, NEW_PROJECT_CONFIG, normalize, resource_limits, scratch_options, unique_object, docker_network, seccomp_filename, network_instructions

ASSETS = Path(__file__).parent / "assets"
IMAGE = "invariant-codex-workspace:0.163.0-v2"
# Docker CLI otherwise injects its saved proxy URLs, including credentials.
PROXY_VARIABLES = tuple(name for base in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "FTP_PROXY")
                        for name in (base, base.lower()))
DISABLED_FEATURES = (
    "apps", "plugins", "remote_plugin", "hooks", "multi_agent", "multi_agent_v2",
    "shell_snapshot", "image_generation", "computer_use", "browser_use",
    "browser_use_external", "in_app_browser", "in_app_local_automation",
    "goals", "tool_suggest", "skill_mcp_dependency_install",
    "workspace_dependencies", "worktrees", "sleep_tool",
)


class SessionStopped(Exception):
    pass


class TurnFailed(RuntimeError):
    pass


def private_write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".invariant-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as output:
            json.dump(value, output, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def state_for(project):
    return Path.home() / ".invariant" / "workspaces" / hashlib.sha256(os.fsencode(project)).hexdigest()[:24]


def init(project=None, *, automatic=True):
    project = (project or Path.cwd()).resolve()
    marker = state_for(project) / 'experiments/apply-active.json'
    if marker.exists() or marker.is_symlink():
        with project_lock(state_for(project)):
            return init(project, automatic=automatic)
    # Initializing a home directory would grant access to credentials and state.
    if project == Path.home().resolve() or project == Path(project.anchor):
        raise RuntimeError("Choose a project folder inside your home directory, rather than your home or filesystem root")
    directory = project / ".invariant"
    if directory.is_symlink():
        raise RuntimeError(".invariant must be a real directory")
    directory.mkdir(mode=0o700, exist_ok=True)
    config = directory / "config.json"
    if config.exists() or config.is_symlink():
        load_project(project)
        print("INVARIANT is already initialized here.")
        return
    # Exclusive creation never replaces an existing project's configuration.
    fd = os.open(config, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as output:
        json.dump(NEW_PROJECT_CONFIG if automatic else DEFAULT_CONFIG, output, indent=2)
        output.write("\n")
    print("Initialized INVARIANT in " + str(project))
    print("Policy: edit this project" + (" and its isolated Linux environment" if automatic else "")
          + "; no command networking; project content and tool results may be sent to the selected OpenAI model.")
    print("Settings: invariant config (edit with invariant config set KEY VALUE)")
    print("Resources:", "managed automatically." if automatic else "manual limits.", "Advanced settings: invariant config")
    print("Next: invariant run")


def load_project(project):
    config = project / ".invariant" / "config.json"
    if config.is_symlink() or config.parent.is_symlink():
        raise RuntimeError("INVARIANT configuration cannot be a symlink")
    try:
        with open(config, "rb", opener=lambda p, flags: os.open(p, flags | os.O_NOFOLLOW | os.O_NONBLOCK)) as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 8192:
                raise ValueError()
            raw = source.read(8193)
            if len(raw) > 8192:
                raise ValueError()
            value = json.loads(raw, object_pairs_hook=unique_object)
    except FileNotFoundError:
        raise RuntimeError("Run invariant init in this folder first") from None
    except (ValueError, OSError):
        raise RuntimeError("Invalid INVARIANT configuration (expected a regular, single-link JSON file up to 8 KiB)") from None
    value = normalize(value)
    trusted = state_for(project).resolve()
    if trusted.is_relative_to(project) or project == Path.home().resolve():
        raise RuntimeError("The project must not contain INVARIANT's trusted state or your home directory")
    return value


def check_project_files(project, *, allow_symlinks=False):
    # Bind mounts preserve hard links and host sockets. Refuse them before
    # exposing the tree. Symlinks are also refused to avoid surprising mounts.
    def unreadable(error):
        raise RuntimeError("Cannot inspect every workspace entry: " + str(error)) from error

    count = 0
    links = {}
    for base, directories, files in os.walk(project, followlinks=False, onerror=unreadable):
        for name in directories + files:
            path = Path(base) / name
            info = path.lstat()
            count += 1
            if count > 200000:
                raise RuntimeError("Workspace exceeds the 200,000-entry startup limit")
            if allow_symlinks and stat.S_ISLNK(info.st_mode):
                continue  # Resolved only inside the container's filesystem.
            if stat.S_ISLNK(info.st_mode) or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                raise RuntimeError("Workspace contains a symlink or special file: " + str(path.relative_to(project)) + ". Move or remove that entry, then run invariant run. No project files were changed")
            if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
                if not allow_symlinks:
                    raise RuntimeError("Workspace contains a hard-linked file: " + str(path.relative_to(project)) + ". Replace it with an independent copy, then run invariant run. No project files were changed")
                key = (info.st_dev, info.st_ino)
                seen, expected, first = links.get(key, (0, info.st_nlink, path))
                if expected != info.st_nlink:
                    raise RuntimeError('Workspace hard links changed during inspection; retry the launch')
                links[key] = (seen + 1, expected, first)
    # Cargo and other build tools use internal hard links. All links must be
    # accounted for inside the mounted project, never elsewhere on the host.
    for seen, expected, path in links.values():
        if seen != expected:
            raise RuntimeError('Workspace contains a hard-linked file with links outside this project: '
                               + str(path.relative_to(project)) + '. Replace it with an independent copy, then retry.')


def docker(*arguments, timeout=30):
    executable = find_docker()
    result = subprocess.run([executable, *arguments], env=docker_env(executable), capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError("Docker " + arguments[0] + " failed: " + inert(result.stderr[-2000:]))
    return result.stdout.strip()


def remove_owned(container):
    executable = find_docker()
    result = subprocess.run([executable, "inspect", container], env=docker_env(executable), capture_output=True, text=True, timeout=30)
    if result.returncode:
        if "No such object" in result.stderr or "No such container" in result.stderr:
            return
        raise RuntimeError("Cannot inspect workspace container during cleanup")
    info = json.loads(result.stdout)[0]
    labels = info["Config"].get("Labels") or {}
    if labels.get("io.invariant.role") != "codex-workspace" or labels.get("io.invariant.session") != container:
        raise RuntimeError("Refusing to remove a container owned by another workload")
    docker("rm", "--force", container)


def prepare(*, persistent=False):
    # Image preparation needs Docker, but not a host Codex installation.
    from .dependencies import probe_docker, interactive_terminal
    status = probe_docker()
    if status["status"] != "ready":
        if interactive_terminal():
            from .setup import guided_setup
            guided_setup()
            status = probe_docker()
        if status["status"] != "ready":
            raise RuntimeError(status["message"] + ". Run invariant setup first")
    print("Preparing the Codex workspace image…", flush=True)
    executable = find_docker()
    result = subprocess.run([executable, "build", "--tag", machine.IMAGE if persistent else IMAGE, "--file",
                             str(ASSETS / ("environment.Dockerfile" if persistent else "codex.Dockerfile")), str(ASSETS)],
                            env=docker_env(executable), capture_output=True, text=True, timeout=600)
    if result.returncode:
        raise RuntimeError("Could not prepare the environment. Check Docker and your internet connection, then retry invariant run. Details: " + inert(result.stderr[-2000:]))
    print("Environment ready.", flush=True)


def execution_context(name, project, image, *, policy=None):
    selected = normalize(policy) if policy is not None else load_project(project)
    return ExecutionContext.create(project=project, workspace=project, state=state_for(project),
                                   image=image, owner_token=name, policy=selected,
                                   control_directory=machine.control_path(project) if machine.enabled(selected) else None)


def container_command(name, project, image, *, policy=None, context=None):
    context = context or execution_context(name, project, image, policy=policy)
    if (context.project != Path(project).resolve() or context.image != image
            or context.owner_token != name):
        raise RuntimeError('Execution context identity does not match container request')
    policy = context.policy
    writable = context.workspace
    limits = resource_limits(policy)
    for path in (writable, context.policy_directory, context.control_directory or ASSETS, ASSETS):
        if any(c in str(path) for c in (",", "\n", "\r")):
            raise RuntimeError("Workspace and installation paths cannot contain commas or newlines")
    if os.getuid() == 0:
        raise RuntimeError("Run agent workspaces as a non-root operator")
    general = machine.enabled(policy)
    if context.checkpoint_owner is not None:
        # Docker otherwise creates a root-owned nested bind-mount destination
        # inside the host's private workspace on native Linux.
        placeholder = writable / '.invariant'
        if placeholder.is_symlink():
            raise RuntimeError('Private workspace policy mount cannot be a symlink')
        placeholder.mkdir(mode=0o700, exist_ok=True)
    command = ["create", "--name", name, "--pull=never", "--interactive", "--init",
            "--label=io.invariant.role=codex-workspace", "--label=io.invariant.session=" + name,
            "--network=" + docker_network(policy), "--user=0:0" if general else f"--user={os.getuid()}:{os.getgid()}", "--cap-drop=ALL",
            "--security-opt=no-new-privileges", "--security-opt=seccomp=" + str(ASSETS / seccomp_filename(policy)),
            f"--cpus={limits['cpus']}", f"--memory={limits['memory_bytes']}",
            f"--memory-swap={limits['memory_bytes'] + limits['extra_swap_bytes']}", f"--pids-limit={limits['processes']}",
            f"--ulimit=nofile={limits['file_descriptors']}:{limits['file_descriptors']}",
            "--restart=no", "--stop-timeout=3", "--log-driver=none",
            "--tmpfs=/scratch:" + scratch_options(policy),
            "--mount", f"type=bind,src={writable},dst=/workspace" + (",readonly" if policy["workspace_access"] == "read-only" else ""),
            "--mount", f"type=bind,src={context.policy_directory},dst=/workspace/.invariant,readonly",
            *[f"--env={key}=" for key in PROXY_VARIABLES]]
    if not general or policy['workspace_access'] == 'read-only':
        command += ['--read-only']
    if general:
        command += ['--label=io.invariant.environment=' + machine.PROFILE,
                    '--label=io.invariant.project=' + machine.project_key(project),
                    *['--cap-add=' + cap for cap in machine.CAPABILITIES],
                    '--mount', f'type=bind,src={context.control_directory},dst=/__invariant,readonly',
                    '--mount', f'type=bind,src={ASSETS / "environment-requirements.toml"},dst=/etc/codex/requirements.toml,readonly',
                    '--workdir=' + context.working_directory,
                    '--entrypoint=' + (context.command[0] if context.command is not None else '/__invariant/codex')]
    if context.checkpoint_owner is not None:
        command += ['--label=io.invariant.owner=' + context.checkpoint_owner]
        if context.checkpoint_owner.startswith('attempt-'):
            attempt = context.checkpoint_owner.rsplit('-', 1)[0]
            command += ['--label=io.invariant.attempt=' + attempt, '--label=io.invariant.dispatch=' + attempt]

    command += [f'--env={key}={value}' for key, value in (machine.ENVIRONMENT if general else packages.ENVIRONMENT).items()]
    for source, destination in context.readonly_mounts:
        command += ['--mount', f'type=bind,src={source},dst={destination},readonly']
    if context.output_directory is not None:
        command += ['--mount', f'type=bind,src={context.output_directory},dst=/__invariant_output']
    command += ['--env=' + key + '=' + value for key, value in context.command_environment]
    command += [image]
    if context.command is not None:
        command += list(context.command[1:])
    elif general:
        command += ['exec-server', '--listen', 'stdio://']
    return command


def verify_container(name, project, image, *, info=None, policy=None, context=None):
    context = context or execution_context(name, project, image, policy=policy)
    if (context.project != Path(project).resolve() or context.image != image
            or context.owner_token != name):
        raise RuntimeError('Execution context identity does not match container inspection')
    policy = context.policy
    writable = context.workspace
    limits = resource_limits(policy)
    if info is None:
        info = json.loads(docker("inspect", name))[0]
    host = info["HostConfig"]
    if context.checkpoint_owner is not None:
        labels = info.get('Config', {}).get('Labels') or {}
        if (labels.get('io.invariant.owner') != context.checkpoint_owner
                or labels.get('io.invariant.session') != name
                or labels.get('io.invariant.project') != machine.project_key(project)):
            raise RuntimeError('Unexpected trial container ownership')
        if context.checkpoint_owner.startswith('attempt-'):
            attempt = context.checkpoint_owner.rsplit('-', 1)[0]
            if any(labels.get(key) != attempt for key in ('io.invariant.attempt', 'io.invariant.dispatch')):
                raise RuntimeError('Unexpected trial dispatch ownership')
    general = machine.enabled(policy)
    if any(row.partition("=")[0] in PROXY_VARIABLES and row.partition("=")[2]
           for row in info["Config"].get("Env") or []):
        raise RuntimeError("Unexpected inherited proxy configuration")
    environment = dict(row.split('=', 1) for row in info['Config'].get('Env') or [] if '=' in row)
    required_environment = machine.ENVIRONMENT if general else packages.ENVIRONMENT
    if any(environment.get(key) != value for key, value in required_environment.items()):
        raise RuntimeError("Unexpected project dependency environment")
    expected = {"NetworkMode": docker_network(policy), "ReadonlyRootfs": not general or policy['workspace_access'] == 'read-only', "Privileged": False,
                "NanoCpus": round(limits['cpus'] * 1000000000), "Memory": limits['memory_bytes'],
                "MemorySwap": limits['memory_bytes'] + limits['extra_swap_bytes'],
                "PidsLimit": limits['processes'], "PublishAllPorts": False}
    if any(host.get(key) != value for key, value in expected.items()):
        raise RuntimeError("Docker did not apply the required workspace profile")
    if set(info.get("NetworkSettings", {}).get("Networks", {})) != {docker_network(policy)}:
        raise RuntimeError("Unexpected workspace network attachments")
    mounts = {(m["Destination"], m["Type"], m["Source"], m["RW"]) for m in info["Mounts"]}
    for key, value in context.command_environment:
        if environment.get(key) != value:
            raise RuntimeError('Unexpected command input environment')
    expected_mounts = {("/workspace", "bind", str(writable), policy["workspace_access"] == "read-write"),
                       ("/workspace/.invariant", "bind", str(context.policy_directory), False)}
    if general:
        machine.verify_control(project, path=context.control_directory)
        expected_mounts |= {('/__invariant', 'bind', str(context.control_directory), False),
                            ('/etc/codex/requirements.toml', 'bind', str(ASSETS / 'environment-requirements.toml'), False)}
        entrypoint = context.command[0] if context.command is not None else '/__invariant/codex'
        if info['Config'].get('Entrypoint') != [entrypoint] or info['Config'].get('WorkingDir') != context.working_directory:
            raise RuntimeError('The protected environment executor was not selected')
    expected_mounts |= {(destination, 'bind', str(source), False) for source, destination in context.readonly_mounts}
    if context.output_directory is not None:
        expected_mounts.add(('/__invariant_output', 'bind', str(context.output_directory), True))
    if context.command is not None and (info['Config'].get('Cmd') or []) != list(context.command[1:]):
        raise RuntimeError('Unexpected command argument vector')
    # Docker inspect represents tmpfs mounts separately on supported engines.
    if {m for m in mounts if m[1] != "tmpfs"} != expected_mounts:
        raise RuntimeError("Unexpected workspace mounts")
    options = host.get("SecurityOpt") or []
    profiles = [json.loads(option.partition("=")[2]) for option in options if option.startswith("seccomp=")]
    capabilities = {cap.removeprefix('CAP_') for cap in host.get('CapAdd') or []}
    if (info["Image"] != image or info["Config"]["User"] != ('0:0' if general else f"{os.getuid()}:{os.getgid()}")
            or capabilities != (set(machine.CAPABILITIES) if general else set()) or not {"ALL"}.issubset(set(host.get("CapDrop") or []))
            or "no-new-privileges" not in options
            or profiles != [json.loads((ASSETS / seccomp_filename(policy)).read_text())]
            or host.get("Binds") or host.get("Devices") or host.get("PortBindings")
            or host.get("PidMode") or host.get("IpcMode") != "private" or not host.get("Init")
            or host.get("Tmpfs") != {"/scratch": scratch_options(policy)}
            or host.get("Ulimits") != [{"Name": "nofile", "Hard": limits['file_descriptors'], "Soft": limits['file_descriptors']}]
            or host.get("RestartPolicy", {}).get("Name") != "no"):
        raise RuntimeError("Unexpected Docker authority")


def codex_configuration(home, container, executable, *, policy=None):
    # Only this executor exists. Codex cannot fall back to a local host shell.
    environment = ('default = "invariant"\ninclude_local = false\n[[environments]]\n'
                   'id = "invariant"\nprogram = ' + json.dumps(find_docker()) + '\nargs = ' +
                   json.dumps(["start", "--attach", "--interactive", container]) + '\n')
    (home / "environments.toml").write_text(environment)
    config = '''approval_policy = "never"
sandbox_mode = "danger-full-access"
web_search = "disabled"
check_for_update_on_startup = false
cli_auth_credentials_store = "file"
project_root_markers = []
[analytics]
enabled = false
[feedback]
enabled = false
[agents]
enabled = false
[shell_environment_policy]
inherit = "none"
[shell_environment_policy.set]
'''
    environment_settings = machine.ENVIRONMENT if policy is not None and machine.enabled(policy) else {'HOME': '/scratch', 'TMPDIR': '/scratch', **packages.ENVIRONMENT}
    config += ''.join(key + ' = ' + json.dumps(value) + '\n' for key, value in environment_settings.items())
    (home / "config.toml").write_text(config)
    # Existing file-based Codex sign-in can be reused without reading or copying
    # tokens into this program. Keyring-only accounts sign in once in this home.
    auth = home / "auth.json"
    original = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "auth.json"
    if not auth.exists() and not auth.is_symlink() and original.is_file():
        auth.symlink_to(original.resolve())
    safe_env = {k: v for k, v in os.environ.items()
                if k in ("PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "TERM",
                         "TMPDIR", "SSL_CERT_FILE", "SSL_CERT_DIR", "CODEX_CA_CERTIFICATE")}
    safe_env["CODEX_HOME"] = str(home)
    safe_env["PATH"] = docker_env(find_docker())["PATH"]
    command = [executable, "app-server", "--strict-config", "--listen", "stdio://"]
    for feature in DISABLED_FEATURES:
        command += ["-c", f"features.{feature}=false"]
    return command, safe_env


def authenticate(rpc):
    account = rpc.request("account/read", {"refreshToken": False}).get("account")
    if account:
        if account.get("type") != "chatgpt":
            raise RuntimeError("This workspace profile requires ChatGPT sign-in; the cached Codex account uses another billing method")
        return
    login = rpc.request("account/login/start", {"type": "chatgpt"})
    print("Sign in with ChatGPT to connect Codex:", flush=True)
    print(inert(login["authUrl"]), flush=True)
    end = time.monotonic() + 300
    while time.monotonic() < end:
        message = rpc.receive()
        if message and message.get("method") == "account/login/completed":
            if not message.get("params", {}).get("success"):
                raise RuntimeError("Codex sign-in did not complete")
            return
    raise RuntimeError("Codex sign-in timed out; start the session again")


class Control:
    def __init__(self, path, session, stop, audit):
        self.path, self.session, self.stop, self.audit = path, session, stop, audit
        self.ready = False
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.bind(str(path))
        os.chmod(path, 0o600)
        self.socket.listen(2)
        self.socket.settimeout(0.2)
        self.closed = threading.Event()
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self):
        while not self.closed.is_set():
            try:
                connection, _ = self.socket.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with connection:
                connection.settimeout(1)
                try:
                    with connection.makefile("rb") as reader:
                        request = json.loads(reader.readline(4097))
                    valid = request.get("session") in (None, self.session)
                    command = request.get("command")
                    ok = valid and (command == "status" or (command == "stop" and request.get("session") == self.session))
                    if ok and command == "stop":
                        self.stop.set()
                        self.audit("operator_stop")
                    connection.sendall((json.dumps({"ok": ok, "session": self.session,
                                                    "ready": self.ready, "running": True}) + "\n").encode())
                except (ValueError, OSError):
                    pass

    def close(self):
        self.closed.set()
        self.socket.close()
        self.thread.join(timeout=2)
        self.path.unlink(missing_ok=True)


def operator(command, session=None, *, machine=False, quiet=False):
    project = Path.cwd().resolve()
    state = state_for(project)
    try:
        current = json.loads((state / "current.json").read_text())
    except FileNotFoundError:
        raise RuntimeError("No active Codex workspace session in this project") from None
    selected = session or current["session"]
    if selected != current["session"]:
        raise RuntimeError("That workspace session is no longer active")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(2)
        connection.connect(str(state / "control.sock"))
        connection.sendall((json.dumps({"command": command, "session": selected}) + "\n").encode())
        with connection.makefile("rb") as reader:
            response = json.loads(reader.readline(4097))
    if not response.get("ok"):
        raise RuntimeError("Workspace command rejected")
    if quiet:
        return response
    if machine:
        print(json.dumps(response))
    else:
        print("Session:", response["session"])
        print("State:", "stopping" if command == "stop" else "ready" if response["ready"] else "starting")
    return response


def validate_effort(rpc, model, effort):
    """Check the signed-in Codex catalog without guessing model capabilities."""
    if effort is None:
        return
    cursor = None
    seen = set()
    while True:
        params = {"limit": 100, "includeHidden": True}
        if cursor is not None:
            params["cursor"] = cursor
        page = rpc.request("model/list", params)
        for entry in page.get("data", []):
            if model not in (entry.get("model"), entry.get("id")):
                continue
            supported = [option["reasoningEffort"] for option in entry.get("supportedReasoningEfforts", [])]
            if effort not in supported:
                choices = ", ".join(supported) or "none advertised"
                raise RuntimeError(f"Model {model} does not support reasoning effort {effort}. Available levels: {choices}")
            return
        cursor = page.get("nextCursor")
        if cursor is None:
            raise RuntimeError(f"Cannot verify reasoning effort for {model}: model is not advertised by your Codex account")
        if cursor in seen or len(seen) >= 100:
            raise RuntimeError("Cannot verify reasoning effort: invalid Codex model catalog pagination")
        seen.add(cursor)



def default_model(rpc):
    """Choose only the account catalog's single visible text-capable default."""
    cursor, seen, defaults = None, set(), []
    while True:
        params = {"limit": 100, "includeHidden": False}
        if cursor is not None:
            params["cursor"] = cursor
        page = rpc.request("model/list", params)
        for item in page.get("data", []):
            if item.get("isDefault") is True and not item.get("hidden") and "text" in item.get("inputModalities", ["text", "image"]):
                defaults.append(item)
        cursor = page.get("nextCursor")
        if cursor is None:
            break
        if not isinstance(cursor, str) or cursor in seen or len(seen) >= 100:
            raise RuntimeError("Cannot select a model: invalid Codex catalog pagination")
        seen.add(cursor)
    if len(defaults) != 1:
        raise RuntimeError("Codex did not advertise one default model. Choose with invariant run --model MODEL_NAME")
    item = defaults[0]
    model, effort = item.get("model") or item.get("id"), item.get("defaultReasoningEffort")
    if not isinstance(model, str) or not re.fullmatch(MODEL, model):
        raise RuntimeError("Codex advertised an invalid default model")
    supported = [entry.get("reasoningEffort") for entry in item.get("supportedReasoningEfforts", [])]
    if effort is not None and (not isinstance(effort, str) or not re.fullmatch(EFFORT, effort) or effort not in supported):
        raise RuntimeError("Codex advertised an invalid default reasoning effort")
    return model, effort


def chat_turn(rpc, thread_id, text, audit, *, effort=None, on_started=None):
    printed = set()
    turn = None
    completed = None

    def event(message):
        nonlocal completed
        method, params = message.get("method"), message.get("params", {})
        if method == "item/agentMessage/delta":
            printed.add(params.get("itemId"))
            print(inert(params.get("delta", "")), end="", flush=True)
        elif method == "item/started":
            item = params.get("item", {})
            if item.get("type") == "commandExecution":
                print("\n[Running in workspace] " + inert(str(item.get("command", "")))[:300], flush=True)
        elif method == "item/completed":
            item = params.get("item", {})
            if item.get("type") == "agentMessage" and item.get("id") not in printed:
                print(inert(item.get("text", "")), end="", flush=True)
            elif item.get("type") in ("commandExecution", "fileChange"):
                audit("tool_completed", tool=item["type"], status=item.get("status"))
        elif method == "turn/completed":
            completed = params.get("turn", {})

    from .turns import run_turn
    try:
        completed = run_turn(rpc, thread_id, text, effort=effort, event=event,
                             on_started=(lambda turn_id: on_started()) if on_started else None)
    finally:
        print(flush=True)
    if completed:
        audit("turn_completed", status=completed.get("status"))
        if completed.get("status") == "interrupted":
            print("Turn interrupted.")
        if completed.get("status") == "failed":
            raise TurnFailed("Codex turn failed: " + inert(str((completed.get("error") or {}).get("message", "unknown error"))))


def run(model=None, *, duration=None, effort=None, resume=None):
    project = Path.cwd().resolve()
    project_policy = load_project(project)
    policy = project_policy
    duration = policy["duration_seconds"] if duration is None else duration
    if type(duration) is not int or not 1 <= duration <= 86400:
        raise RuntimeError("Agent duration must be between 1 and 86400 seconds")
    if effort is not None and (not isinstance(effort, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", effort)):
        raise RuntimeError("Invalid reasoning effort name; use a level supported by the selected model, such as high")
    if model is not None and (not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", model)):
        raise RuntimeError("Supply a model name, for example invariant run --agent codex gpt-6.1-sol")
    executable, _docker_executable = ensure_runtime()
    check_project_files(project, allow_symlinks=machine.enabled(policy))
    state = state_for(project)
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    if state.is_symlink() or state.stat().st_uid != os.getuid():
        raise RuntimeError("Unsafe workspace state directory")
    os.chmod(state, 0o700)
    lock = project_lock(state, message="This project already has an active workspace session or source capture. Return to its terminal, or use invariant status to inspect it")
    session = "ws-" + uuid.uuid4().hex
    container = "invariant-" + session
    audit_path = state / (session + ".jsonl")
    audit_file = os.open(audit_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    audit_lock = threading.Lock()

    def audit(event, **facts):
        with audit_lock:
            record = {"session": session, "event": event, "time": time.time(), **facts}
            os.write(audit_file, (json.dumps(record) + "\n").encode())
            os.fsync(audit_file)

    rpc = control = context = None
    created = False
    registered = False
    stop = threading.Event()
    old_handler = signal.getsignal(signal.SIGTERM)
    old_hup = signal.getsignal(signal.SIGHUP)
    watchdog_done = threading.Event()
    clean = False
    failure = None
    saved_chat = False
    stop_reason = None
    plan = {"mode": "manual", "limits": policy["limits"]}
    seen_oom = 0
    auto_increased = None
    launch_source = "explicit choice" if model is not None else "account default"

    def terminate(_signum, _frame):
        nonlocal stop_reason
        if _signum == signal.SIGHUP:
            stop_reason = "hangup"
        raise SessionStopped()

    def watch():
        nonlocal stop_reason
        end = time.monotonic() + duration
        while not watchdog_done.wait(0.2):
            if stop.is_set() or time.monotonic() >= end:
                stop_reason = "operator" if stop.is_set() else "deadline"
                os.kill(os.getpid(), signal.SIGTERM)
                return

    def check_memory():
        nonlocal seen_oom, auto_increased
        count = resources.pressure(docker, container, persistent=machine.enabled(policy))
        if count is not None and count > seen_oom:
            seen_oom = count
            audit("memory_pressure", oom_kills=count)
            auto_increased = preferences.remember_pressure(state, project, plan)
            if auto_increased:
                print(f"\nThe task hit its memory limit. The next session will use up to {auto_increased} MiB automatically. Continue with invariant resume; interrupted work will not be replayed.", flush=True)
            elif plan["mode"] == "auto":
                print("\nThe task hit the automatic memory ceiling. Increase Docker memory or review advanced settings with invariant config before continuing.", flush=True)
            else:
                print("\nThe task hit its memory limit. Current limits were kept. Use invariant config to review resource settings before continuing.", flush=True)

    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGHUP, terminate)
    try:
        store = ChatStore(state, project)
        saved = store.load(None if resume is True else resume) if resume is not None else None
        if saved:
            saved_chat = True
            launch_source = "saved chat"
            model = saved["model"]
            if effort is None:
                effort = saved["effort"]
        elif model is None:
            remembered = preferences.load(state, project)
            if remembered is None and (state / "latest-chat.json").exists():
                remembered = store.load()
            if remembered:
                launch_source = "remembered project settings"
                model = remembered["model"]
                if effort is None:
                    effort = remembered["effort"]
        if project_policy["resource_mode"] == "auto":
            policy, plan = resources.select(project_policy, project, resources.capacity(docker), preferences.memory_hint(state, project))
        else:
            policy, plan = resources.select(project_policy, project)
        current = state / "current.json"
        if current.exists():
            previous = json.loads(current.read_text())
            if not re.fullmatch(r"ws-[0-9a-f]{32}", previous.get("session", "")):
                raise RuntimeError("Invalid previous workspace record")
            print("Recovering the previous workspace. Project files and saved chats are kept; interrupted commands will not be replayed.", flush=True)
            if machine.enabled(policy) or previous.get('profile') == machine.PROFILE or (state / 'environment.json').exists():
                machine.recover('invariant-' + previous['session'], state, project, audit)
            else:
                remove_owned("invariant-" + previous["session"])
            audit("recovered_previous_session", previous=previous["session"])
        # A transient cache lookup failure must not immediately trigger a network build.
        for attempt in range(2):
            try:
                image = docker("image", "inspect", machine.IMAGE if machine.enabled(policy) else IMAGE, "--format", "{{.Id}}")
                break
            except RuntimeError:
                if attempt == 0:
                    print("Checking the cached environment again…", flush=True)
        else:
            prepare(persistent=machine.enabled(policy))
            image = docker("image", "inspect", machine.IMAGE if machine.enabled(policy) else IMAGE, "--format", "{{.Id}}")
        if machine.enabled(policy):
            machine.prepare_control(project, image)
            image = machine.selected_image(state, project, image)
        private_write(state / "current.json", {"session": session, "pid": os.getpid(), "duration_seconds": duration, "profile": policy['profile']})
        registered = True
        context = ExecutionContext.create(project=project, workspace=project, state=state,
                                          image=image, owner_token=container, policy=policy,
                                          control_directory=machine.control_path(project) if machine.enabled(policy) else None,
                                          event_sink=audit)
        created = True
        docker(*container_command(container, project, image, context=context))
        verify_container(container, project, image, context=context)
        # A previous interrupted host process cannot leave a usable control socket.
        (state / "control.sock").unlink(missing_ok=True)
        control = Control(state / "control.sock", session, stop, audit)
        threading.Thread(target=watch, daemon=True).start()
        home = state / "codex"
        home.mkdir(mode=0o700, exist_ok=True)
        command, env = codex_configuration(home, container, executable, policy=policy)
        audit("session_start", model=model, requested_reasoning_effort=effort, profile=policy, project_policy=project_policy, resources=plan, image=image, project=str(project), duration_seconds=duration)
        rpc = CodexRpc(command, env=env, cwd=state)
        rpc.initialize()
        # This call must connect the configured executor. A missing/unsupported
        # environment fails startup before any model input or local tool call.
        environment = rpc.request("environment/info", {"environmentId": "invariant"}, timeout=60)
        if environment.get("cwd") not in ("file:///workspace", "/workspace"):
            raise RuntimeError("Codex did not connect to the isolated workspace")
        # Use isolated Python so project modules cannot forge the startup check.
        probe = ("import os,tempfile; fd,p=tempfile.mkstemp(prefix='.invariant-startup-',dir='/workspace'); os.close(fd); os.unlink(p)"
                 if policy["workspace_access"] == "read-write" else
                 "import os; assert os.statvfs('/workspace').f_flag & os.ST_RDONLY, 'workspace is not read-only'")
        if machine.enabled(policy):
            docker('exec', container, machine.INSPECTOR, 'startup-' + ('rw' if policy['workspace_access'] == 'read-write' else 'ro'))
        else:
            docker("exec", container, "/usr/local/bin/python3", "-I", "-c", probe)
        audit("executor_connected", environment=environment)
        authenticate(rpc)
        if model is None:
            model, catalog_effort = default_model(rpc)
            if effort is None:
                effort = catalog_effort
        validate_effort(rpc, model, effort)
        thread_params = {"model": model, "cwd": "/workspace",
                            "approvalPolicy": "never", "sandbox": "danger-full-access",
                            "developerInstructions": "Work in /workspace. Docker is the external sandbox. " + network_instructions(policy)
                            + (machine.instructions(policy) if machine.enabled(policy) else packages.instructions(policy))
                            + ("The project is read-only; write temporary files only in /scratch. " if policy["workspace_access"] == "read-only" else "")
                            + "Do not request host access, additional tools, credentials, or sandbox escalation."}
        if saved:
            thread_params.update(threadId=saved["id"], excludeTurns=True)
        else:
            thread_params.update(ephemeral=False, environments=[{"environmentId": "invariant", "cwd": "/workspace"}])
        if effort is not None:
            thread_params["config"] = {"model_reasoning_effort": effort}
        thread = rpc.request("thread/resume" if saved else "thread/start", thread_params)
        thread_id = thread["thread"]["id"]
        if not valid_id(thread_id) or (saved and thread_id != saved["id"]) or thread["thread"].get("ephemeral") is not False:
            raise RuntimeError("Codex did not restore the requested persistent chat")
        if thread.get("model") != model:
            raise RuntimeError("Codex did not select the requested model; refusing a fallback")
        effective_effort = thread.get("reasoningEffort")
        if effort is not None and effective_effort != effort:
            raise RuntimeError(f"Codex did not select reasoning effort {effort}; refusing a fallback")
        environments = thread["thread"].get("environments") or []
        if len(environments) != 1 or environments[0].get("environmentId") != "invariant" or environments[0].get("cwd") != "/workspace":
            raise RuntimeError("Codex did not bind the thread to the isolated workspace")
        preferences.save(state, project, model, effective_effort)
        now = time.time()
        chat = {"schema": 1, "id": thread_id, "project": str(project), "model": model,
                "effort": effective_effort, "created_at": saved["created_at"] if saved else now, "updated_at": now}
        # Native Codex creates durable history on the first accepted turn, not
        # thread/start. An empty new chat must not replace the last saved chat.
        if saved:
            store.save(chat)

        def save_chat():
            nonlocal saved_chat
            chat["updated_at"] = time.time()
            store.save(chat)
            saved_chat = True

        control.ready = True
        audit("session_configured", model=model, reasoning_effort=effective_effort, chat=thread_id, resumed=bool(saved))
        print("INVARIANT Codex workspace · " + model)
        effort_label = effective_effort or "Codex default"
        if effort is None and effective_effort and not saved:
            effort_label += " (Codex default)"
        print("Reasoning effort:", effort_label)
        print("Launch settings:", launch_source, "· remembered for the next run")
        print("Project:", project)
        print("Project access:", policy["workspace_access"])
        print("Files: this project only; host home, credentials and Docker socket are outside the environment.")
        print("Resources:", "managed automatically" if plan["mode"] == "auto" else "your configured limits",
              f"({policy['limits']['cpus']} CPU, {policy['limits']['memory_mib']} MiB memory; details: invariant guardrails)")
        if plan.get("conservative_fallback"):
            print("Docker capacity could not be fully measured; conservative limits were selected.")
        print("Session:", session)
        print("Resumed chat:" if saved else "Chat:", thread_id)
        print("Continue this chat later: invariant resume " + thread_id)
        if not saved:
            print("The chat is saved after your first message is accepted.")
        if saved:
            print("Conversation restored. Workspace files reflect their current contents; interrupted work is not automatically retried.")
        print("Project content and tool results may be sent to OpenAI.")
        if policy["command_network"] == "internet":
            print("Command network: internet enabled, including reachable local-network services. No ports are published.")
        else:
            print("Commands have no network access.")
        if machine.enabled(policy):
            print('Environment: persistent Linux; installed tools, system packages, home and configuration survive run/resume.')
            print('Access:', 'read-only environment.' if policy['workspace_access'] == 'read-only' else 'administrator inside this environment; install tools normally.')
        else:
            print("Python dependencies: saved in .invariant-deps/ and available automatically on run/resume"
                  + (" (read-only in this session)." if policy["workspace_access"] == "read-only" else ". Install with python3 -m pip install PACKAGE."))
        print("Chat below. /exit closes the workspace; Ctrl+C interrupts a turn. Limit: " + str(duration) + " seconds.", flush=True)
        while True:
            try:
                text = input("\nYou> ")
            except EOFError:
                break
            if len(text.encode()) > 65536:
                print("Message is too long (maximum 64 KiB).")
                continue
            if text.strip() in ("/exit", "/quit"):
                break
            if not text.strip():
                continue
            if text.startswith("/"):
                print("Available commands: /exit, /quit. Type a message to chat.")
                continue
            print("\nCodex> ", end="", flush=True)
            audit("turn_start", reasoning_effort=effective_effort)
            try:
                chat_turn(rpc, thread_id, text, audit, effort=effort, on_started=save_chat)
                save_chat()
            except TurnFailed as error:
                print("That turn could not finish. You can send another message or /exit and continue with invariant resume.")
                print("Details:", inert(str(error)))
                if policy["command_network"] == "none":
                    print("Internet is off. For downloads: invariant config set command_network internet, then exit and resume.")
            check_memory()
        clean = True
    except (SessionStopped, KeyboardInterrupt):
        audit("session_interrupted")
        print("\nStopping workspace…", flush=True)
        clean = True
    except BaseException as error:
        failure = error
    finally:
        # A second signal cannot interrupt container removal.
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        old_int = signal.signal(signal.SIGINT, signal.SIG_IGN)
        watchdog_done.set()
        errors = []
        if created:
            try:
                check_memory()
            except (OSError, RuntimeError, subprocess.SubprocessError) as error:
                audit("resource_check_unavailable", error=type(error).__name__)
        if rpc:
            try:
                rpc.close()
            except (OSError, subprocess.SubprocessError) as error:
                errors.append(str(error))
        if created:
            try:
                if machine.enabled(policy):
                    print('Saving the project environment…', flush=True)
                    context.preserve(container)
                else:
                    remove_owned(container)
            except (OSError, RuntimeError, subprocess.SubprocessError) as error:
                errors.append(str(error))
        if control:
            control.close()
        if registered and not errors:
            (state / "current.json").unlink(missing_ok=True)
        audit("session_end", clean=clean and not errors, cleanup_errors=errors)
        os.close(audit_file)
        lock.close()
        signal.signal(signal.SIGTERM, old_handler)
        signal.signal(signal.SIGHUP, old_hup)
        signal.signal(signal.SIGINT, old_int)
        print("Session evidence:", audit_path, flush=True)
        if errors:
            raise RuntimeError("Workspace cleanup failed; the recovery record was kept. Try invariant run to recover the workspace, or invariant resume if a chat was saved. Container " + container + ": " + "; ".join(errors))
    if failure:
        recovery.failure(failure, policy, saved=saved_chat, oom=seen_oom > 0)
        raise failure
    recovery.closed(saved=saved_chat, reason=stop_reason)
    return 0
