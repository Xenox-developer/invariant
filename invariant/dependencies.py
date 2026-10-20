"""Read-only discovery and readiness checks for the trusted host tools."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

CODEX_VERSION = "0.163.0"
CODEX_VERSION_PATTERN = r"codex-cli 0\.163\.0(?:[-+][A-Za-z0-9.+-]+)?\s*"


def managed_codex():
    return Path.home() / ".invariant/tools" / ("codex-" + CODEX_VERSION)


def executable_file(path):
    return path.is_file() and os.access(path, os.X_OK)


def find_codex():
    # An explicitly installed, pinned INVARIANT copy also works when an
    # incompatible global CLI is on PATH. Never replace the global installation.
    managed = managed_codex() / "bin/codex"
    if executable_file(managed):
        return str(managed)
    executable = shutil.which("codex")
    if executable:
        return executable
    if sys.platform == "darwin":
        for applications in (Path("/Applications"), Path.home() / "Applications"):
            candidate = applications / "ChatGPT.app/Contents/Resources/codex-cli/bin/codex"
            if executable_file(candidate):
                return str(candidate)
    raise RuntimeError("Codex CLI was not found. Run invariant setup, or install @openai/codex@0.163.0")


def docker_app():
    if sys.platform == "darwin":
        for applications in (Path("/Applications"), Path.home() / "Applications"):
            app = applications / "Docker.app"
            if app.is_dir():
                return app
    return None


def find_docker():
    executable = shutil.which("docker")
    if executable:
        return executable
    candidates = [Path.home() / ".docker/bin/docker"]
    if sys.platform == "darwin":
        candidates += [directory / "Docker.app/Contents/Resources/bin/docker"
                       for directory in (Path("/Applications"), Path.home() / "Applications")]
    for candidate in candidates:
        if executable_file(candidate):
            return str(candidate)
    raise RuntimeError("Docker CLI was not found. Run invariant setup to install Docker")


def docker_env(executable):
    # Docker's credential helpers may live beside its bundled CLI, even when
    # the caller has not added ~/.docker/bin to their terminal's PATH yet.
    env = os.environ.copy()
    env["PATH"] = str(Path(executable).parent) + os.pathsep + env.get("PATH", "")
    return env


def probe_codex():
    try:
        path = find_codex()
    except RuntimeError as error:
        return {"status": "missing", "path": None, "message": str(error)}
    try:
        result = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=10)
        version = result.stdout.strip()
        ready = result.returncode == 0 and re.fullmatch(CODEX_VERSION_PATTERN, result.stdout) is not None
        return {"status": "ready" if ready else "incompatible", "path": path,
                "message": version[:160] if ready else "This workspace requires Codex CLI 0.163.0 (including prereleases)",
                "version": version[:160]}
    except (OSError, subprocess.SubprocessError):
        return {"status": "unavailable", "path": path, "message": "The Codex executable could not be started"}


def probe_docker():
    try:
        path = find_docker()
    except RuntimeError as error:
        return {"status": "missing", "path": None, "message": str(error)}
    try:
        result = subprocess.run([path, "info", "--format", "{{.OSType}}"], env=docker_env(path),
                                capture_output=True, text=True, timeout=10)
        if result.returncode == 0 and result.stdout.strip() == "linux":
            return {"status": "ready", "path": path, "message": "Docker engine is running (Linux containers)"}
        if result.returncode == 0:
            return {"status": "incompatible", "path": path, "message": "Docker must use Linux containers"}
        # Do not print arbitrary daemon output, URLs, or credential diagnostics.
        return {"status": "unavailable", "path": path,
                "message": "Docker is installed, but its engine is stopped or inaccessible to this account. Check Docker's selected context and permissions"}
    except (OSError, subprocess.SubprocessError):
        return {"status": "unavailable", "path": path, "message": "Docker did not respond; start its engine and retry"}


def inspect_dependencies():
    codex, docker = probe_codex(), probe_docker()
    supported = sys.platform in ("darwin", "linux")
    return {"ready": supported and codex["status"] == docker["status"] == "ready",
            "platform": sys.platform, "supported": supported, "codex": codex, "docker": docker}


def interactive_terminal():
    return sys.stdin.isatty() and sys.stdout.isatty()


def ensure_runtime():
    report = inspect_dependencies()
    if not report["ready"] and interactive_terminal():
        from .setup import guided_setup
        try:
            report = guided_setup(report)
        except KeyboardInterrupt:
            raise RuntimeError("Setup cancelled. Run invariant setup when you are ready") from None
    if not report["ready"]:
        problems = [name + ": " + report[name]["message"] for name in ("codex", "docker")
                    if report[name]["status"] != "ready"]
        if not report["supported"]:
            problems.insert(0, "Agent workspaces require macOS or Linux")
        raise RuntimeError("; ".join(problems) + ". Run invariant setup in an interactive terminal (or invariant setup --check for diagnostics)")
    return report["codex"]["path"], report["docker"]["path"]
