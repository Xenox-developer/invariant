# Environment preparation

For a coding task, prefer `invariant task run` with preparation requirements;
it coordinates the steps below and records progress. Use standalone environment
commands when the user specifically wants setup or inspection without dispatch.

## Readiness and project selection

Run from the project the user wants to work on, not from the installed skill's
folder. Requirements for INVARIANT 0.3.1 are macOS/Linux, Python 3.11+, a running
Linux-container Docker engine and the supported Codex CLI with host ChatGPT
sign-in. Linux commands run inside Docker; host macOS APIs and nested Docker
are outside this backend.

```sh
invariant --version
invariant setup --check --json
invariant login --check --json
invariant doctor --json
```

Check process exit codes and response contents. `setup --check --json` returns a
plain report with top-level `ready` and tool statuses, without an envelope;
`login` uses an envelope and reports `data.host_cli_signed_in`. The native experiment
validator verifies the account type and available model/effort. A doctor report
can have `ok: true` while `data.ready` is false: inspect `data.issues`. An absent
project configuration or unbuilt image is a preparation need, not a reason to
replace saved state.

If `invariant` is absent, report the missing prerequisite and use an existing
installation if available. When installation is within the user's request,
install `invariant-workspace` in a dedicated host virtual environment using the
project's installation instructions. Do not change a global Python environment
merely to invoke the skill.

Missing/incompatible Docker or Codex: report the readiness issue and direct the
user to `invariant setup` in a terminal. It can offer installations and starting
Docker; `setup --check --json` never performs either. For missing authentication,
direct the user to `invariant login`; do not read or copy authentication files.
Use the installed setup check's supported Codex version instead of guessing it.

If `.invariant/config.json` is absent in the intended project:

```sh
invariant init
```

`init` is noninteractive, preserves existing configuration, and **does not accept
`--json`**. Never initialize a home directory or filesystem root. Inspect:

```sh
invariant config --json
```

Unlike experiment commands, successful `config --json` returns the configuration
object directly, without an `ok`/`data` envelope. Preserve `profile`,
`workspace_access`, network and resource limits unless the task requires a
change. Preparation requires the general persistent environment and read-write
project access; do not silently switch an existing restricted profile.
New environments default to unrestricted operation: host home/local Docker
access, unlimited resources and no deadlines. Use the saved policy as declared;
the user can enable the restricted preset with `restrictions_enabled true`.

## Build the base and prepare dependencies

Once host readiness succeeds, prepare the base image if needed:

```sh
invariant prepare --agent codex
```

This builds the base without a model turn; it does not accept `--json`. Inspect
its exit status. Select Linux-compatible commands from the project's actual
manifests/documentation, then run them in the persistent environment:

```sh
invariant environment prepare --command "npm ci" --timeout 600 --json
```

`npm ci` is an example, not a universal setup command. New environments enable
internet access by default, including during worker execution. Existing projects
retain their saved network policy. For an offline project, add `--allow-network`
for temporary preparation access without editing project configuration. The timeout must not exceed the
configured nonzero `duration_seconds`; zero disables the session deadline. This command runs as administrator inside Docker
and can modify the **original project's files**; limit it to intended setup.
There is no model call. It returns an envelope containing `data.prepared`,
`exit_code`, `reason` and a saved `log` path. `ok: true` alone does not prove the
preparation command succeeded: require `prepared: true` and a successful exit.
Wait for the command's terminal response before dependent operations. A running
shell session or empty redirected file is not evidence of completion.

For Debian system dependencies, prefer the typed preparation interface:

```sh
invariant environment packages --package cmake --package libarchive-dev \
  --allow-network --timeout 600 --json
```

It retries only acquisition on recognized transient HTTP/network errors, at most
three retries per acquisition phase by default with bounded backoff. After all
downloads succeed, installation runs once with `--no-download`. Permanent errors
and maintainer-script failures are not retried. `--download-retries 0` disables
acquisition retries. Arbitrary `environment prepare --command` is never replayed
automatically. Neither typed nor custom preparation spends a model turn.

## Resource contracts

```sh
invariant environment budget --json
invariant environment budget --container CONTAINER_NAME --json
```

The effective contract contains exact Docker arguments and expected inspect
values, including total memory-plus-swap, PID/nofile limits and scratch mount.
Use this single contract for both arms of a comparison; do not reconstruct a
subset of flags by hand. Verify actual containers before trusting measurements.
A mismatch fails explicitly; auto budgets are resolved using Docker capacity
and the same saved advice as native trials. Different budgets or failed runs
must not be ranked as successful speedups.

## Existing offline network settings

New environments need no network configuration for package downloads. For an
existing offline project, prefer `--allow-network` for preparation. If changing the saved policy itself is
part of the user's request, read and record the previous setting first. If network
access is authorized by the task and currently disabled, enable it explicitly:

```sh
invariant config set command_network internet --json
```

Restore the previous value after temporary preparation, including on failure.
If the previous value was `none`, restoration is:

```sh
invariant config set command_network none --json
```

Account for whether the requested experiment itself needs network access before
freezing its policy. Do not enable it merely because a test or provider call
failed. Internet mode also reaches accessible local-network services.

Installed packages and home settings are checkpointed even after a failed
preparation command, and project edits remain. Inspect the failure and saved log
before deciding whether to issue another command; partial preparation is not
rolled back or retried automatically. For a confirmed interrupted ordinary
session/preparation, after checking it is no longer active:

```sh
invariant environment recover --json
```

Recovery does not replay commands. Reassess the environment before rerunning
the intended setup explicitly. Do not use recovery to interrupt a live session.

## Saved interactive sessions

```sh
invariant chats --json
invariant status --json
invariant guardrails --json
```

Use these to inspect saved chats, ordinary session state and effective limits.
They do not submit an experiment. For a user who wants an interactive terminal
chat, provide `invariant run` (new chat) or `invariant resume CHAT_ID` (saved
chat). Neither accepts `--json` or an inline task flag. Their ordinary sessions
write directly to project files. Installed tools/home settings and chat history
persist; running processes, services and open connections do not. Experiments
use private project copies and explicit apply for delivery. Unrestricted host
mounts also expose original files; only restricted mode enforces host isolation.
