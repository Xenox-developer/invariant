# INVARIANT

**Compare coding-agent configurations locally, then keep the changes you choose.**

Run the same task in independent Linux workspaces, evaluate each result with the
same frozen checks, compare quality/time/token usage, and continue or apply a
selected result. People and AI assistants use the same commands and saved state.

[![Python checks](https://github.com/Xenox-developer/invariant/actions/workflows/python.yml/badge.svg)](https://github.com/Xenox-developer/invariant/actions/workflows/python.yml)
[![Rust libraries](https://github.com/Xenox-developer/invariant/actions/workflows/rust-libraries.yml/badge.svg)](https://github.com/Xenox-developer/invariant/actions/workflows/rust-libraries.yml)

[Documentation](docs/experiments/README.md) · [Examples](examples/projects/README.md) ·
[Issues](https://github.com/Xenox-developer/invariant/issues) ·
[Contributing](CONTRIBUTING.md) · [Security](SECURITY.md) · [Apache-2.0](LICENSE)

## What you get

- One frozen project snapshot and shared prepared image; separate files,
  conversations and writable environment layers for each trial.
- Native Codex model/effort comparisons or any Linux command-based agent harness.
- Captured evaluators, explicit missing results and separate retry history.
- Detached execution, resumable logs, bounded stop and explicit recovery.
- Saved selection and continuation with the chosen files and installed tools.
- Diff review and a destination-checked apply plan before project writes.
- Markdown/JSON/CSV reports, storage retention, private backups and diagnostics.

**Status: beta, 0.3.x.** Interfaces and known limitations are documented; release
checks apply to the exact recorded source. See [release evidence](docs/RELEASE_VERIFICATION.md)
and [compatibility](docs/COMPATIBILITY.md).

## Requirements

| Requirement | Details |
|---|---|
| Host | macOS or Linux; full host evidence targets macOS arm64 and Linux x86_64 |
| Python | 3.11 or newer; see the Python CI matrix for individually checked versions |
| Docker | Running Linux-container engine; Docker Desktop on macOS |
| Native agent | Supported Codex CLI 0.163.0, including supported prereleases, and host ChatGPT sign-in |
| Resources | Room for container images and full workspace copies; effective parallelism depends on Docker CPU/RAM |

`invariant setup` checks Docker/Codex and offers missing installations. Rust,
Node/npm and a globally installed Codex are not required for the Python CLI.
Codex can be installed privately for INVARIANT. Your project's tools run inside
Linux; prepare the dependencies your task needs. Windows workspaces are unsupported.

## Install

Use a virtual environment on the host:

```sh
python3 -m venv ~/.venvs/invariant
. ~/.venvs/invariant/bin/activate
python3 -m pip install "git+https://github.com/Xenox-developer/invariant.git@v0.3.0"
invariant --version
```

Expected version: `invariant 0.3.0`. Git is needed for this installation method.
Release wheels can also be installed with `python3 -m pip install /path/to/FILE.whl`;
check their published SHA-256 values. No PyPI package or public container image
publication is implied. See [installation, updates and removal](docs/INSTALLATION.md).

## Quickstart: three real Codex configurations

```sh
invariant setup
invariant login
invariant prepare --agent codex
invariant experiment demo ~/invariant-codex-demo --run
```

This creates a separate project, asks three Codex configurations to implement one
calendar utility, and evaluates every result with the same 23 checks. Compare the
observed test verdict, runtime and reported tokens. Choose a result to inspect,
continue or review/apply; nothing is automatically declared the winner.

**The first run uses three real model turns from your signed-in account.** A
continuation uses another turn. Configurations share provider limits. Omit `--run`
to create and inspect the demo without Docker, sign-in or model calls. Repeating
the same demo command reopens its saved study without replaying completed turns.
Closing the viewer leaves detached work running.

[Detailed demo](docs/experiments/DEMO.md) ·
[Command-only quickstart with no model calls](docs/experiments/QUICKSTART.md)

## Use your own project

Inside your project:

```sh
invariant init
invariant experiment init
```

Answer prompts for the task, Linux test command, fixed test paths and configurations.
The guided run checks the baseline evaluator before model calls. Ordinary red tests
are allowed; missing commands, timeouts and broken evaluation block the run.

For scripts or assistants, provide the same inputs as flags:

```sh
invariant experiment init --name caching \
  --task "Implement caching while preserving the existing behavior" \
  --test-command "npm test" --test-path tests --test-path package.json \
  --request-id caching-v1 --run --json
```

Replace the command and paths with your actual project. Use repeated `--variant
MODEL EFFORT` pairs to compare models, or `--model` and `--efforts` for one model.
Unavailable settings fail before dispatch; INVARIANT does not substitute a model.
`--json` never opens setup or selection prompts.

Install required tools/dependencies explicitly, without a model turn:

```sh
invariant config set command_network internet
invariant environment prepare --command "YOUR_LINUX_SETUP_COMMAND"
invariant config set command_network none
```

Preparation can write the project and persists installed tools inside Docker.
No package manager or language is inferred. See
[environment preparation and preflight](docs/ENVIRONMENT_PREPARATION.md).

Generate complete starter projects from the installed package:

```sh
invariant experiment example python ~/invariant-python-example
invariant experiment example javascript ~/invariant-javascript-example
```

Their README files include dependency preparation, intentionally red tests and
commands for a real comparison. Generation itself makes no model calls.

## Review, continue and apply

```sh
invariant experiment list
invariant experiment review STUDY_ID
invariant experiment export STUDY_ID --output comparison.md
```

The review flow lets you select a completed result, inspect its diff, continue
with a new task, or apply a reviewed plan. Selection changes no source files.
Continuation uses the saved files/tools in a fresh conversation. For child results,
apply ancestors before children; the guided flow handles the order and conflicts.

The same actions are available individually:

```sh
invariant experiment select ATTEMPT_ID --if-revision REVISION
invariant experiment diff STUDY_ID
invariant experiment apply STUDY_ID --dry-run
invariant experiment apply --plan PLAN_ID
invariant experiment continue STUDY_ID --task "Add useful documentation" --wait
```

[Assistant interface](docs/experiments/ASSISTANT_GUIDE.md) ·
[Report formats and privacy](docs/experiments/REPORTS.md)

## Come back later, stop and recover

```sh
invariant experiment logs STUDY_ID --follow
invariant experiment stop STUDY_ID
invariant experiment recover STUDY_ID
invariant experiment retry TRIAL_ID
```

Recovery does not replay an interrupted provider call. Retry is an explicit new
attempt, while comparisons keep first-attempt denominators. Logs can resume from
saved cursors. Failed or unknown outcomes remain visible.

```sh
invariant doctor --json
invariant experiment storage
invariant experiment cleanup --older-than-days 30
invariant experiment backup --output ~/invariant-backup.tar.gz
```

Cleanup previews first; add `--confirm` only after reviewing it. Active, selected
and referenced results are protected. Private backups include exact images and
saved experiments; restore requires matching project/state paths. See
[storage, backup and recovery](docs/STORAGE_AND_RECOVERY.md).

## Data, isolation and limitations

Project files, prompts, evaluators and configuration are frozen locally. Native
Codex sends task content and tool results to the provider. Host authentication
stays outside workload containers. Private native histories can contain full
prompts and tool outputs. Summary reports omit those by default; private backups
omit host authentication and native conversation homes.

Commands run in Docker without host home, credentials or Docker control. Command
networking is off by default. Explicit internet mode permits reachable internet
and local-network services, without a domain allowlist. Trials run as administrator
inside their Linux environment. This is not a production multi-tenant security claim.

Experiments use full workspace copies and saved Docker layers. Disk headroom is
checked, but there are no hard disk quotas. Frozen inputs do not make model
responses, clocks, random sources or remote services deterministic. Captured tests
cannot be replaced by candidate edits, but evaluators are not an attestation
against deliberately adversarial code or a modified runtime.

Apply preserves contents, modes and safe links, not ACLs/xattrs. It detects conflicts
and journals recovery; external writers can race its final check. It performs no
automatic merge. Backups are private, unencrypted, same-path archives; they are
not portable redacted reports or whole-host backups.

## Documentation and contributions

- [Complete experiment interface](docs/experiments/README.md)
- [Installation and host setup](docs/INSTALLATION.md)
- [Compatibility, schemas and upgrades](docs/COMPATIBILITY.md)
- [Standalone Rust libraries and embedding](docs/RUST_LIBRARY.md)
- [Ordinary Codex workspace and legacy broker workflows](docs/LEGACY_WORKFLOWS.md)
- [Contributing and tests](CONTRIBUTING.md)
- [Changelog](CHANGELOG.md) and [security reports](SECURITY.md)

For a bug report, include the command, INVARIANT version, sanitized diagnostic
report and a small reproduction. Review logs and detailed exports before sharing.
Use the issue templates; report vulnerabilities privately.

## License

Original INVARIANT source is [Apache-2.0](LICENSE). See [NOTICE](NOTICE) and the
[licensing inventory guidance](docs/LICENSING.md) for third-party components.
Docker Desktop, Codex and provider accounts retain their own terms.
