"""Operator-only guided installation, outside every agent workspace."""
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import webbrowser

from . import dependencies as deps
from .codex_rpc import inert

DOCKER_GUIDE = "https://docs.docker.com/desktop/setup/install/mac-install/"
LINUX_GUIDE = "https://docs.docker.com/engine/install/"
PACKAGES = {
    ("darwin", "arm64"): ("darwin-arm64", "aarch64-apple-darwin", "i7Dzk2a5aGmuGM9aNZgaUVTojZ/4bc5lRfosz++Y7X2BGhDkz+cySgbD+NYS1pOxfm0VEbLi9PZ3pAulI4kchw=="),
    ("darwin", "x86_64"): ("darwin-x64", "x86_64-apple-darwin", "Ioj7h+B1ME9ZU1PH1ATfvFV5DiototEpFFaSSbg3kfDifB9djhl5fcJ++g5mpcL/+uVbcnReei+RiSXEx8o+kw=="),
    ("linux", "aarch64"): ("linux-arm64", "aarch64-unknown-linux-musl", "IH2/R+4OarjGvsAf1ll3fdyLwrPgYLl3uzp6u9CyD9sj2Lw+ViDSe3QBZVHdcHJs0eIiZFg6Wprcuy4CRZaUGw=="),
    ("linux", "x86_64"): ("linux-x64", "x86_64-unknown-linux-musl", "uwRaOsrYogLQhcxAM/GoeWrw0QIWEMRo7c9gM8sKAviVkZQHlu9kRLJ5PkufbV+ABGjyZgIwiFdJ0LCJ0N6e8w=="),
}


class SetupCancelled(RuntimeError):
    pass


def confirm(question):
    if not deps.interactive_terminal():
        return False
    try:
        return input(question + " [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        raise SetupCancelled("Setup cancelled. Run invariant setup when you are ready.") from None


def checked(command, *, timeout=600):
    try:
        result = subprocess.run(command, check=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError("Setup could not complete " + Path(command[0]).name + ". Check the installer output and retry invariant setup") from error
    return result


def download(url, path, limit):
    curl = shutil.which("curl")
    if not curl:
        raise RuntimeError("Install curl to download dependencies, then run invariant setup again")
    print("Downloading " + url + " …", flush=True)
    checked([curl, "--fail", "--location", "--proto", "=https", "--proto-redir", "=https",
             "--connect-timeout", "15", "--max-time", "900", "--max-filesize", str(limit),
             "--output", str(path), url], timeout=920)
    if not path.is_file() or not 0 < path.stat().st_size <= limit:
        raise RuntimeError("Dependency download was empty or exceeded its size limit")


def unpack_codex(package, destination, triple):
    prefix = "package/vendor/" + triple + "/"
    total = count = 0
    with tarfile.open(package, "r:gz") as archive:
        for member in archive:
            if not member.name.startswith(prefix):
                continue
            relative = PurePosixPath(member.name[len(prefix):])
            if relative.is_absolute() or ".." in relative.parts or not relative.parts:
                raise RuntimeError("Unsafe path in the Codex package")
            count += 1
            total += member.size
            if count > 4096 or total > 1024 * 1024 * 1024 or not (member.isfile() or member.isdir()):
                raise RuntimeError("Unsupported entry or excessive size in the Codex package")
            output = destination.joinpath(*relative.parts)
            if member.isdir():
                output.mkdir(parents=True, exist_ok=True, mode=0o700)
                continue
            output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with archive.extractfile(member) as source, output.open("xb") as target:
                shutil.copyfileobj(source, target)
            output.chmod(0o700 if member.mode & 0o111 else 0o600)
    for name in ("codex", "codex-code-mode-host"):
        if not deps.executable_file(destination / "bin" / name):
            raise RuntimeError("Codex package is missing " + name)


def install_codex():
    spec = PACKAGES.get((sys.platform, platform.machine().lower()))
    if spec is None:
        raise RuntimeError("Automatic Codex installation supports macOS/Linux on Apple silicon/ARM64 or x86_64")
    suffix, triple, integrity = spec
    destination = deps.managed_codex()
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if destination.is_symlink():
        raise RuntimeError("Managed Codex installation cannot be a symlink")
    with tempfile.TemporaryDirectory(prefix=".codex-install-", dir=destination.parent) as temporary:
        temporary = Path(temporary)
        package, staged = temporary / "codex.tgz", temporary / "codex"
        staged.mkdir(mode=0o700)
        url = f"https://registry.npmjs.org/@openai/codex/-/codex-{deps.CODEX_VERSION}-{suffix}.tgz"
        download(url, package, 512 * 1024 * 1024)
        digest = hashlib.sha512()
        with package.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
        if base64.b64encode(digest.digest()).decode() != integrity:
            raise RuntimeError("Codex download integrity check failed; nothing was installed")
        unpack_codex(package, staged, triple)
        version = subprocess.run([str(staged / "bin/codex"), "--version"], capture_output=True, text=True, timeout=10)
        if version.returncode or version.stdout.strip() != "codex-cli " + deps.CODEX_VERSION:
            raise RuntimeError("Downloaded Codex did not report the required version")
        previous = temporary / "previous"
        if destination.exists():
            destination.rename(previous)
        try:
            staged.rename(destination)
        except BaseException:
            if previous.exists():
                previous.rename(destination)
            raise
    print("Installed Codex " + deps.CODEX_VERSION + " for INVARIANT. Your global CLI and shell settings were not changed.")


def install_docker_mac():
    if deps.docker_app():
        return deps.docker_app()
    arch = {"arm64": "arm64", "x86_64": "amd64"}.get(platform.machine().lower())
    if arch is None:
        raise RuntimeError("Unsupported Mac architecture for Docker Desktop")
    temporary = Path(tempfile.mkdtemp(prefix="invariant-docker-"))
    mount = temporary / "mount"
    mount.mkdir()
    mounted = False
    try:
        package = temporary / "Docker.dmg"
        download(f"https://desktop.docker.com/mac/main/{arch}/Docker.dmg", package, 4 * 1024 * 1024 * 1024)
        checked(["/usr/bin/hdiutil", "verify", str(package)], timeout=180)
        checked(["/usr/bin/hdiutil", "attach", "-nobrowse", "-readonly", "-mountpoint", str(mount), str(package)], timeout=180)
        mounted = True
        app = mount / "Docker.app"
        requirement = 'anchor apple generic and identifier "com.docker.docker" and certificate leaf[subject.OU] = "9BNSXJN65R"'
        checked(["/usr/bin/codesign", "--verify", "--deep", "--strict", "-R", requirement, str(app)], timeout=180)
        checked(["/usr/sbin/spctl", "--assess", "--type", "execute", str(app)], timeout=180)
        applications = Path.home() / "Applications"
        applications.mkdir(exist_ok=True)
        destination = applications / "Docker.app"
        if destination.exists() or destination.is_symlink():
            raise RuntimeError("Docker.app already exists; it was not overwritten. Open it or use Docker's installer to repair it")
        with tempfile.TemporaryDirectory(prefix=".docker-install-", dir=applications) as staging:
            copy = Path(staging) / "Docker.app"
            checked(["/usr/bin/ditto", str(app), str(copy)], timeout=600)
            copy.rename(destination)
        print("Installed Docker Desktop in " + str(destination))
        return destination
    finally:
        if mounted:
            # Never remove a directory containing a mounted installer volume.
            result = subprocess.run(["/usr/bin/hdiutil", "detach", str(mount)], timeout=30)
            if result.returncode:
                raise RuntimeError("Docker installer could not be unmounted. Eject it in Finder, then remove " + str(temporary))
        shutil.rmtree(temporary)


def apt_supported():
    if sys.platform != "linux" or not shutil.which("apt-get"):
        return False
    try:
        return platform.freedesktop_os_release().get("ID") in ("debian", "ubuntu")
    except OSError:
        return False


def privileged(command):
    if os.getuid() != 0:
        sudo = shutil.which("sudo")
        if not sudo:
            raise RuntimeError("An administrator must install Docker; sudo is unavailable. See " + LINUX_GUIDE)
        command = [sudo, *command]
    checked(command, timeout=900)


def install_docker_linux():
    apt = shutil.which("apt-get")
    privileged([apt, "update"])
    privileged([apt, "install", "--yes", "docker.io"])


def wait_for_docker(timeout=180):
    end = time.monotonic() + timeout
    print("Waiting for Docker. Complete any first-launch setup in Docker's window; press Ctrl+C to stop waiting.", flush=True)
    while time.monotonic() < end:
        if deps.probe_docker()["status"] == "ready":
            return
        time.sleep(2)
    raise RuntimeError("Docker is not ready yet. Complete its setup, check its context/permissions, then run invariant setup again")


def show(report):
    print("INVARIANT setup")
    for name in ("codex", "docker"):
        item = report[name]
        print(name.capitalize() + ": " + item["status"] + " — " + inert(item["message"]))
        if item["path"]:
            print("  " + inert(item["path"]))
    if not report["supported"]:
        print("Agent workspaces currently support macOS and Linux.")


def guided_setup(report=None):
    report = report or deps.inspect_dependencies()
    show(report)
    if report["ready"]:
        print("Ready. In your project, run invariant init, then invariant run --agent codex MODEL_NAME.")
        return report
    if not report["supported"] or not deps.interactive_terminal():
        print("Run invariant setup in an interactive macOS/Linux terminal to install missing dependencies.")
        return report
    state = Path.home() / ".invariant"
    state.mkdir(exist_ok=True, mode=0o700)
    if state.is_symlink() or state.stat().st_uid != os.getuid():
        raise RuntimeError("Unsafe INVARIANT setup directory")
    fd = os.open(state / "setup.lock", os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another INVARIANT setup is already running") from None
        if report["codex"]["status"] != "ready":
            print("Codex can be installed privately from OpenAI's published package (SHA-512 verified). Node/npm are not needed.")
            if confirm("Download and install Codex " + deps.CODEX_VERSION + " for INVARIANT?"):
                install_codex()
        docker = report["docker"]
        if docker["status"] == "missing":
            if sys.platform == "darwin":
                app = deps.docker_app()
                if not app and confirm("Download Docker Desktop from Docker and install it in ~/Applications? Its first launch requires accepting Docker's terms"):
                    install_docker_mac()
            elif apt_supported():
                if confirm("Download and install Docker Engine using your system's docker.io package? This uses sudo and may start its service"):
                    install_docker_linux()
            else:
                print("Install Docker for your distribution: " + LINUX_GUIDE)
                if confirm("Open Docker's official installation guide?"):
                    webbrowser.open(LINUX_GUIDE)
        docker = deps.probe_docker()
        if docker["status"] in ("missing", "unavailable"):
            app = deps.docker_app()
            if app and confirm("Docker is not reachable. Open Docker Desktop and wait for it to become ready?"):
                checked(["/usr/bin/open", str(app)], timeout=15)
                wait_for_docker()
            elif sys.platform == "linux":
                print("Check Docker's service, selected context and account permissions: https://docs.docker.com/engine/install/linux-postinstall/")
                systemctl = shutil.which("systemctl")
                if systemctl and confirm("Start the Docker system service using sudo?"):
                    privileged([systemctl, "start", "docker"])
                print("If access is denied, follow the account-access steps in that guide and open a new login session. INVARIANT does not change group membership.")
        result = deps.inspect_dependencies()
        show(result)
        print("Setup complete. You can start or resume an INVARIANT workspace." if result["ready"]
              else "Setup is incomplete. Resolve the items above and run invariant setup again. No workspace was started.")
        return result
    finally:
        os.close(fd)


def main(*, check=False, machine=False):
    report = deps.inspect_dependencies()
    if machine:
        print(json.dumps(report))
    elif check:
        show(report)
    else:
        try:
            report = guided_setup(report)
        except (KeyboardInterrupt, SetupCancelled):
            print("\nSetup cancelled. Run invariant setup to continue.")
            return 130
    return 0 if report["ready"] else 1
