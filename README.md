# INVARIANT

INVARIANT is moving toward parallel agent experiments: define several configurations
in one project, run them independently, compare their results, and bring back the
changes you choose. Humans and AI assistants use the same commands and state.

The target behavior is specified in the updated [MVP contract](MVP_CONTRACT.md)
and [implementation plan](docs/IMPLEMENTATION_PLAN.md). **M0–M5 support frozen studies, detached parallel command/native Codex trials,
evaluation, comparison, stop controls, recovery, explicit retries, resumable logs and
revision-checked selection, continuation with saved tools, and recoverable apply plans.
M6 adds the P01–P21 release verification command. Full release acceptance requires fresh
live Codex and actual macOS/Linux host evidence. The GitHub Actions workflow supplies
Linux fixture evidence for the same-source release gate.** See [the implemented experiment interface](docs/experiments/README.md),
[the clean-install quickstart](docs/experiments/QUICKSTART.md), and
[release verification](docs/experiments/ACCEPTANCE.md). Original source is Apache-2.0.

```sh
invariant experiment init experiments/compare.json
invariant experiment validate experiments/compare.json
invariant experiment run experiments/compare.json --request-id comparison-001
invariant experiment status --request-id comparison-001 --json
invariant experiment results STUDY_ID
invariant experiment logs STUDY_ID --follow
invariant experiment stop STUDY_ID
```

Run these inside an initialized project with a prepared general Linux image. Submission
returns after queuing trials for a detached controller. Closing the terminal or log viewer leaves work running. Add `--wait` to wait for completion. Use `run FILE --create-only` to
freeze without dispatching, then `invariant experiment execute STUDY_ID` to run that
frozen study. Use `recover STUDY_ID` after a controller crash, and `retry TRIAL_ID`
for an explicit new attempt. The starter has two no-op variants to replace with your harness and settings.
Use `experiment init experiments/codex.json --runner codex` for captured Codex prompts
and model/effort variants. `experiment select TRIAL_ID --if-revision REV` saves a choice
without modifying project files. Continue with `experiment continue STUDY_ID --prompt-file next.txt`;
review `experiment diff STUDY_ID` and `experiment apply STUDY_ID --dry-run` before
`experiment apply --plan PLAN_ID`. See the [assistant workflow](docs/experiments/ASSISTANT_GUIDE.md).

To embed the permission model and broker in a Rust application, see the
[Rust libraries guide](docs/RUST_LIBRARY.md) and the
[standalone embedding example](examples/rust-embedding/README.md).
`invariant-core` and `invariant-broker` require Rust 1.90+ and can be used
independently of Docker, Python and Codex.

This checkout also provides controlled Codex workspaces and isolated Python
executions. The instructions below describe those existing features. Their earlier
requirements are preserved in the [legacy contract](docs/LEGACY_MVP_CONTRACT.md);
the existing acceptance suite does not establish acceptance of the new experiment MVP.

New agent projects get a **persistent, general-purpose Linux environment**.
The agent can install any Linux-compatible toolchains, package managers and
applications normally. The whole filesystem and home survive run/resume;
there are no language-specific runtime profiles. See [the architecture](docs/ENVIRONMENT_ARCHITECTURE.md).

## Start a Codex workspace

Install this checkout once with Python 3.11 or newer:

```sh
python3 -m pip install -e /path/to/invariant
```

Check and install the host tools once:

```sh
invariant setup
```

Setup detects existing Codex and Docker installations, offers missing downloads,
and checks that Docker is running. On macOS it can install Docker Desktop in
`~/Applications`; finish Docker's first-launch setup in its own window. Codex
can be installed privately for INVARIANT without Node/npm or changing your
existing CLI. Agent `run` and `resume` also offer setup when needed.
Use `invariant setup --check` for a read-only check, or add `--json` for scripts.
See [guided setup](docs/SETUP.md) for platform support and installation details.

Then create a project:

```sh
mkdir my-project
cd my-project
invariant init
invariant run
```

The first run chooses the default model advertised by your Codex account and
remembers its confirmed model and effort for this project. It prepares an image;
file-based Codex sign-in is reused when available, otherwise the terminal shows
a ChatGPT sign-in link. Chat with Codex at `You>`. Files persist in the project.
Use `/exit` to finish, Ctrl+C to interrupt a turn, or `invariant status` and
`invariant stop SESSION_ID` from another terminal in this project. Sessions
in new projects default to one hour; `--duration SECONDS` overrides one session.
Existing projects keep their previous settings.
To switch an existing project, run `invariant config set profile codex-environment-v2`,
then run or resume again.

Choose reasoning effort with `--effort`:

```sh
invariant run --model gpt-6.1-sol --effort high
```

Available levels depend on the selected model and account. INVARIANT validates
the level against Codex's model catalog and shows the confirmed effort at
startup. Later, plain `invariant run` reuses these confirmed settings. A newly
selected model starts with its own default effort unless you pass `--effort`.
The original `run --agent codex MODEL` form remains supported.

To continue after closing the terminal, open another terminal in the same project:

```sh
invariant resume
```

Chats save automatically after the first accepted message. `resume` restores
the latest saved chat, including its model and reasoning effort. To choose an
older chat, use `invariant chats`, then `invariant resume CHAT_ID`. Use
`invariant resume --effort high` to change the effort. Each `run` starts a new
chat. Chats created before persistence was added cannot be recovered.

New projects manage command resources automatically. INVARIANT measures Docker's
CPU/memory capacity and chooses finite limits. After a confirmed memory-limit
kill, the next session can use more memory within a measured hard ceiling; it
never replays an interrupted command. A running session keeps its launch budget.
No CPU, memory or process settings are needed for ordinary use.

Existing projects retain manual limits. To opt an existing project into automatic
sizing, run `invariant config set resource_mode auto`. Editing a `limits.*` setting
selects manual mode. [Automatic resources and recovery](docs/CODEX_WORKSPACE.md#automatic-resources-and-recovery)
explains limits, private preferences and recovery messages.

View guardrails from your project folder:

```sh
invariant guardrails
invariant guardrails --json
```

With a running workspace, this inspects Docker and the executor's kernel and
shows actual limits. Run it in another terminal while chatting. Without a
running session, it clearly labels the configured policy as not live-verified.
A mismatch or unavailable inspection is reported with a nonzero exit status.

Change this project's settings:

```sh
invariant config
invariant config set limits.cpus 2
invariant config set limits.memory_mib 1024
invariant config set duration_seconds 3600
invariant config set workspace_access read-only
```

Settings are stored in `.invariant/config.json` and apply to the next run or
resume. `--duration` overrides one session. `workspace_access read-write` enables
edits again. Process, file-descriptor, scratch and additional-swap limits are also
configurable; see [all supported settings](docs/CODEX_WORKSPACE.md#change-project-settings).
Networking is disabled by default. To let Codex download packages during chat:

```sh
invariant config set command_network internet
invariant resume  # or start a new run
```

Ask Codex to install whichever tools and dependencies it needs normally. It has
administrator access inside its isolated Linux environment, including system
package installation. Installed software, home settings and toolchains are saved
together and restored automatically on run/resume, even offline. Read-only
sessions can use saved tools but cannot modify the project or OS.
Disable internet with `invariant config set command_network none`.
Internet mode permits outbound connections to reachable internet and local services;
it has no domain allowlist. The policy and control executables stay read-only;
host home, credentials and Docker control remain outside the environment.
See [the architecture and limits](docs/ENVIRONMENT_ARCHITECTURE.md).
A running session keeps its launch settings; `guardrails` shows pending changes separately.

The selected model may receive project content and command results. Commands
run in a container with no host home, credentials or Docker socket. Internet
access requires the explicit project opt-in above.
By default this project is writable and can be changed or deleted by the agent;
read-only mode denies project writes.
Project symlinks and internal hard links are supported in the general environment;
hard links outside the project and special files remain rejected at startup. Linux kernel/device capabilities and
privileged workloads require separate backend support; technology freedom does
not grant host access.
See [Codex workspace sessions](docs/CODEX_WORKSPACE.md) for the exact boundary,
supported runtime, evidence and verification commands.

## Run an isolated Python program

For a narrated walkthrough, run:

```sh
./scripts/invariant demo
```

It prepares missing images, starts one isolated program without permission,
issues the fixed grant, demonstrates the 20-read limit, then revokes and stops
the program. The report includes an invoice summary, audit trail and independent
mock API count. Use `demo --step` to pause between stages. See the
[guided demo](docs/GUIDED_DEMO.md) for details and saved evidence.

With Docker running:

```sh
./scripts/invariant prepare
./scripts/invariant run examples/invoice_summary.py
```

The supervisor creates a fresh execution and mock-invoice grant, verifies the Docker isolation profile, captures bounded output, and removes the container afterward. The program has no direct network sockets, runs as non-root with a read-only filesystem, and receives one CPU, 256 MiB memory, 32 tasks and 64 MiB private scratch. Only its own broker socket is exposed. On Mac, a trusted Linux control container hosts the Rust supervisor and broker inside Docker's VM.

Run `./scripts/invariant test` for real Docker checks and `./scripts/invariant evidence` to copy their evidence into `.invariant/docker-evidence/`. See [Docker execution](docs/DOCKER_EXECUTION.md) for controls, placement, recovery and supported platforms.

## Issue and revoke access while a program runs

Start `./scripts/invariant run examples/live_grants.py --no-grant` in one terminal.
In another terminal, use `./scripts/invariant status` to find the execution ID,
then `./scripts/invariant grant EXECUTION_ID` and
`./scripts/invariant revoke EXECUTION_ID`. Use `stop EXECUTION_ID` to end a run.
Changes require the exact execution ID. Grants retain the fixed 20-read,
15-minute scope. See [operator controls](docs/OPERATOR_CONTROLS.md) for the demo,
status output, replacement rules and isolation details.

## Run Python through the broker

From the project directory, with Python 3 available:

```sh
cargo run -p invariant-broker --example python
```

The demo launches a local mock API and runs two Python programs through the same execution's socket and grant:

```text
Invoices read: 1
Total amount: 42
Duplicate read: completed status returned; no new API call
DELETE /invoices/123 -> denied (out_of_scope)
GET /invoices?tenant=other -> denied (out_of_scope)
After revocation: denied (revoked)
Admissions charged: 1
Target requests received: 1
```

The current path is **Python program → dedicated Unix socket → broker → mock API**, with decisions and admissions saved in the audit journal. The launcher supplies `INVARIANT_SOCKET`, `INVARIANT_CAPABILITY`, and `PYTHONPATH`; no pip installation is needed. The examples run on the host and are not sandboxed.

See [invoice_summary.py](examples/invoice_summary.py), [denied_effects.py](examples/denied_effects.py), and the [Python client](sdk/python/invariant_client.py). The [Unix protocol specification](docs/UNIX_PROTOCOL.md) describes framing, response types, bounds, lifecycle, and failure behavior.

The server supports four active connections, checks the 16 KiB request-frame limit before allocating payloads, and enforces total frame read/write deadlines. Each accepted connection uses the listener's fixed broker context. Workload messages cannot issue grants or supply execution identity. Reconnecting does not reset authority, budgets, or duplicate detection. HTTP bodies are carried as bounded Base64 and decoded to bytes by the client. The client never retries automatically.

## Run the broker demo

From the project directory:

```sh
cargo run -p invariant-broker --example broker
```

This starts a local mock invoice API, issues one grant, performs a real approved read, denies deletion, detects a duplicate, and revokes the grant. It prints:

```text
Read invoices: HTTP 200, [{"id":"invoice-1","amount":42}]
Delete invoice: Denied(OutOfScope)
Duplicate read: Replay(Completed { status: 200 })
After revocation: Denied(Revoked)
Admissions charged: 1
Target requests received: 1
```

The demo also prints the path to a new audit journal under `.invariant/`. Journals are newline-delimited JSON, created with owner-only file permissions on Unix. Existing journals are never overwritten or resumed. They contain decisions and correlation metadata, excluding capability handles and response bodies.

## What the broker enforces

- One fresh execution and broker session per instance, with an immutable resource snapshot.
- Explicit host-side grant issuance and revocation, with fresh random grant IDs and capability handles.
- Serialized checking, budget consumption, and durable admission recording before target I/O. An uncertain commit retains its reservation and stops further admissions.
- At most 20 admissions per grant and four concurrent outbound attempts per execution.
- Execution-scoped duplicate detection: repeats return in-progress, completed status, or unknown-outcome metadata without another target attempt. Response bodies are not retained for replay. Reusing an admitted ID under a different grant fails.
- No automatic retries or budget refunds after an admission. A timeout, upstream error, or journal failure cannot authorize another attempt.
- Durable revocation ordered against admissions. Earlier admitted attempts may finish; later admissions are denied.
- A fixed `GET /invoices` over direct TCP to the configured mock IP and port, with a five-second total I/O deadline, a 32 KiB header limit, and a 1 MiB body limit. There is no DNS, proxy routing, redirect following, or arbitrary caller URL/header/body forwarding.

The HTTP adapter accepts Content-Length or connection-close response framing. Chunked transfer encoding, compression, protocol upgrades, and more than 128 response headers are unsupported and fail closed. HTTP error responses still consume an allowance.

The deduplication ledger keeps at most 1,024 request records across grants in one execution; reaching that limit returns `CapacityExceeded` without forgetting earlier IDs. Temporary saturation of the four worker slots returns the same reason without consuming budget. A fresh, explicitly approved request ID is required to request data again after a completed attempt; retrying the same ID returns only its recorded status.

`Broker` is a host library: all connections for one execution share the instance attached to their Unix listener. Only trusted operator code may invoke its grant and lifecycle controls. `FileJournal` is the durable file adapter for this local prototype; custom `AuditJournal` and `Clock` implementations are trusted code and must uphold their documented contracts.

## MVP acceptance

Docker execution now has a prepared image, supervisor, startup checks, resource limits, cancellation, output capture and restart cleanup. Trusted operator commands provide live status, grant issuance, revocation and stop. The earlier host-side examples remain transport demonstrations; they do not isolate Python.

Restarting creates fresh execution/session identities; previous grants and handles are never restored, and pending operations are not replayed. The supervisor removes leftover workloads belonging to its state directory before accepting new work.

Run `./scripts/invariant acceptance` for the complete A01–A20 contract matrix and supporting regression checks. It rebuilds the images, exercises real Docker workloads and deterministic broker fault/concurrency fixtures, and saves independent API receipts, audit journals, isolation configuration and command results under `.invariant/acceptance/`. Every required case must pass. See [MVP acceptance](docs/MVP_ACCEPTANCE.md) for coverage, prerequisites and evidence format.

## Run the example

From the project directory:

```sh
cargo run -p invariant-core --example model
```

The example prints these decisions:

```text
Read invoices: Allow
Delete invoice: Deny(OutOfScope)
Expired permission: Deny(Expired)
Twenty requests already admitted: Deny(BudgetExhausted)
Revoked permission: Deny(Revoked)
Permission checks only; no requests were sent or budget consumed.
```

## How checking works

`EffectRequest::from_json` parses the program's request with bounded fields. The host supplies an `AuthorizationState` from its own execution, resource, grant, capability registration, and admission count. `check_permission` compares the request against that state and returns `Decision::Allow` or a denial reason.

Only an exact `GET /invoices` is in scope. An active, unexpired grant, a running execution, the correct capability binding, a matching resource configuration, and remaining request budget are all required. Missing authoritative state fails closed. Workload messages cannot supply or override identity, grants, or authorization decisions.

The checker reads a snapshot. It does not consume budget or make `Allow` a reusable permission token. The broker owns grant state and performs the admission operation under one lock, then releases that lock while the approved HTTP attempt is in flight.

## Verify

```sh
./scripts/invariant acceptance
```

Individual checks:

```sh
cargo test --workspace --all-targets
cargo clippy --workspace --all-targets -- -D warnings
cargo fmt --all --check
PYTHONPATH=sdk/python python3 -B -m unittest discover -s sdk/python/tests -v
./scripts/invariant test
python3 -B scripts/test_supervisor_lifecycle.py
python3 -B scripts/test_operator_controls.py
```
