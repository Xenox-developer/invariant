# Installation, updates and removal

INVARIANT's Python CLI runs on macOS and Linux with Python 3.11+. Full experiment
host verification covers macOS arm64 and Linux x86_64. The Python workflow checks
each declared interpreter version; Windows/WSL workspaces are not a verified target.
Rust libraries have their own [requirements](RUST_LIBRARY.md).

## Install a released version

```sh
python3 -m venv ~/.venvs/invariant
. ~/.venvs/invariant/bin/activate
python3 -m pip install "invariant-workspace==0.3.1"
invariant --version
invariant setup
invariant login
invariant prepare --agent codex
```

The package is available on [PyPI](https://pypi.org/project/invariant-workspace/0.3.1/).
You can instead download a wheel
from the repository's GitHub release, compare its SHA-256 against `SHA256SUMS`,
and run `python3 -m pip install /absolute/path/to/FILE.whl`. The wheel includes
runtime assets and the legacy launcher's build sources; it needs no adjacent
checkout. For a source installation pinned to the same Python release, use
`python3 -m pip install "git+https://github.com/Xenox-developer/invariant.git@v0.3.1"`.
Activate this virtual environment in another terminal before using `invariant`.

Setup checks existing tools before offering changes. Its installations need
network access and explicit terminal consent. It can install a private Codex
without Node/npm or changing the global CLI. Docker Desktop's first-launch
terms/permissions must be completed in its window. On Linux, your normal user
must be allowed to access Docker; INVARIANT does not change socket permissions
or run agent workspaces as a root host user. See [host setup](SETUP.md).

`invariant login` uses the detected supported host CLI and its existing auth
store. Native experiments require ChatGPT sign-in; API-key-only authentication
does not satisfy that requirement. `invariant login --check --json` checks host
CLI sign-in without opening login. Native preflight also checks the account type
and the exact model/effort. It does not make a model call.

Command-only experiments do not require an account, though preparing the common
image downloads the pinned Codex distribution and base packages. The three-turn
native demo is described in the [main README](../README.md).

## Development installation

```sh
git clone https://github.com/Xenox-developer/invariant.git
cd invariant
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -e .
```

Editable installation is for development. `invariant acceptance` and complete
experiment release verification require source tests; the installed experiment
gate accepts `--checkout PATH`. User commands, demos, examples and ordinary
environment preparation work from the wheel. Rust is needed on the host only
for Rust development/embedding and source acceptance checks.

## Upgrade

1. Finish or stop/recover active work and exit ordinary sessions.
2. Back up your project files/configuration separately. On 0.3.x, run
   `invariant experiment backup --output ~/invariant-before-upgrade.tar.gz`.
3. Preserve the current installer/version, then install the intended newer tag
   or verified wheel in the virtual environment.
4. Run `invariant --version` and `invariant doctor`. Review migration notes before
   opening saved state with the new version.

Versions predating the backup command require the consistent state/image backup
procedure in [schema migrations](experiments/MIGRATIONS.md). Do not copy a live
SQLite file by itself or assume a recipe includes saved environments.
Known old stores migrate on write. Downgrade of migrated state is unsupported;
restore a consistent backup using its matching version and original paths.

## Remove the CLI

```sh
. ~/.venvs/invariant/bin/activate
python3 -m pip uninstall invariant-workspace
```

Uninstalling does not delete project files, saved experiments/chats, private
Codex tools or Docker images. To reclaim experiment data first, inspect
`invariant experiment storage` and preview retention cleanup. Selected or
referenced studies require explicit deselection/dependency cleanup. Never use a
global Docker prune as INVARIANT cleanup. See [storage and recovery](STORAGE_AND_RECOVERY.md).
