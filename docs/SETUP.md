# Guided setup

Install INVARIANT with Python 3.11+, then run from any folder:

```sh
invariant setup
```

It reports Codex and Docker independently, verifies the supported Codex version,
and checks that Docker is reachable and using Linux containers. Ready tools are
left alone. In an interactive terminal, a missing or incompatible Codex prompts
for a private installation; missing Docker prompts for installation; a stopped
Docker prompts to open/start it. Every installation/start question defaults to
No. EOF and Ctrl+C cancel. A second setup cannot install concurrently.

`invariant run --agent codex MODEL` and `invariant resume` offer the same flow
when dependencies are unusable. Setup completes before any executor is created
or chat input is submitted. A declined/failed installation leaves startup
blocked with instructions to rerun setup. Ready launches do not prompt.

For checks only:

```sh
invariant setup --check
invariant setup --check --json
```

`invariant setup --json` is also read-only. These return exit status 0 when ready
and 1 otherwise; they never download, install, launch Docker or change files.
Non-interactive runs never interpret piped chat text as install consent.

## Codex

INVARIANT detects a managed copy first, then the operator's PATH, then the
ChatGPT application's bundled CLI on macOS. Existing versions must match
0.163.0, including prereleases, because the execution adapter is version-pinned.
An incompatible global version is reported without replacing it.

After confirmation, setup downloads the exact official OpenAI npm platform
package for macOS/Linux ARM64 or x86_64. It verifies an embedded SHA-512 digest,
extracts regular files/directories with path, link and size checks, verifies the
native executable's version, then atomically installs the directory at:

```text
~/.invariant/tools/codex-0.163.0/
```

The Code Mode host and bundled resources are included. This is a native install:
Node/npm, sudo, shell configuration changes and a global npm install are not
required. `curl` is required for downloads and uses certificate-verified HTTPS;
setup does not execute a downloaded shell installer. Staging/download failures
leave a previous managed installation intact. This managed copy is used only
by INVARIANT; native Codex continues to handle authentication during chat launch.

## Docker on macOS

Setup detects `docker` on PATH, `~/.docker/bin/docker`, and the bundled CLI in
`/Applications/Docker.app` or `~/Applications/Docker.app`. It can find a new
installation in the same terminal without editing shell startup files. Docker's
CLI directory is included in child processes' PATH so its credential helpers
can be found. Setup uses the selected Docker context; it does not switch contexts.

For a missing Docker installation, setup offers an architecture-matched official
`desktop.docker.com` DMG. It verifies the disk image, mounts it read-only, checks
Docker's signed application identity/team and Gatekeeper assessment, then copies
it into `~/Applications/Docker.app`. It never replaces an existing Docker.app.
The installer volume is detached and temporary files removed after installation.

It then offers to open Docker Desktop and waits up to three minutes for engine
readiness. Complete any terms, permissions or first-launch choices in Docker's
window. INVARIANT does not pass `--accept-license`, disable Gatekeeper or change
privileged Docker settings. A timeout gives instructions to finish and rerun;
a CLI alone is never treated as proof the engine is ready.

## Docker on Linux

For Debian and Ubuntu with APT, setup offers to install the distribution's
`docker.io` package using `sudo apt-get update` and `sudo apt-get install --yes
docker.io`. The operating system's package manager verifies package integrity.
If needed, a separate prompt offers `sudo systemctl start docker`.

Your account must already have permission to access the engine before an agent
can start. Setup does not alter group membership, elevate the agent launcher or
change socket permissions. If access is denied, follow Docker's post-install
instructions and open a new login session. For other distributions, setup offers
to open Docker's official installation guide and reports that setup is incomplete
until engine readiness succeeds. Windows agent workspaces remain unsupported.

## Verification

```sh
python3 -m unittest discover -s scripts/tests -p test_setup.py -v
python3 -m unittest discover -s scripts/tests -v
python3 scripts/test_codex_workspace.py
```

Tests cover missing/incompatible/stopped tools, terminal and read-only flows,
consent/decline/EOF, installer integrity, archive traversal/link rejection,
private Codex replacement, Docker signature checks, and CLI exit status.
Mocked Docker installer tests do not claim that a new Docker Desktop installation
was performed on every test host. Real readiness checks use the installed engine.

Sources: [OpenAI CLI installation](https://learn.chatgpt.com/docs/cli),
[Docker Desktop for Mac](https://docs.docker.com/desktop/setup/install/mac-install/),
[Debian Docker package](https://wiki.debian.org/Docker),
[Docker Engine installation](https://docs.docker.com/engine/install/),
[Linux account access](https://docs.docker.com/engine/install/linux-postinstall/).
