# INVARIANT MVP Contract

**Version:** 0.7 — parallel agent experiments  
**Date:** October 21, 2026  
**Status:** M0–M5 implement frozen studies, detached parallel command/native Codex trials, capture, evaluation, comparison, stop, recovery/retry, resumable logs, selection, deletion, child continuation and recoverable apply. M6 implements the versioned P01–P21 acceptance/release gate and community documentation. Release acceptance requires a fresh complete report, including real provider and actual macOS/Linux host evidence. Linux host evidence remains unavailable; implementing the gate is not itself a passing release result.  
**Delivery plan:** [Implementation plan](docs/IMPLEMENTATION_PLAN.md)

## 1. Product promise

From one project, run N agent-system configurations independently and asynchronously,
inspect their logs, metrics and outputs, and choose which result to continue or apply.
The user does not need to create N Git branches or maintain N complete environment installations.

A human can configure and operate INVARIANT directly. An external AI assistant can
perform the same operations through its command interface. Both use the same saved
definitions, identifiers, execution history and results. Either can take over at any point.

INVARIANT manages experiment execution and evidence. It does not decide what “best”
means: the user supplies an evaluator and comparison criteria, or inspects outputs.
Isolation supports independent experiments; safety is no longer the primary product promise.
Existing resource limits and execution controls remain applicable.

**MUST** identifies acceptance requirements. The command table defines the target interface. Implemented commands
are documented in [the current experiment guide](docs/experiments/README.md). The previous contract is preserved
as [the legacy specification](docs/LEGACY_MVP_CONTRACT.md).

## 2. Smallest useful workflow

1. Initialize an ordinary project with `invariant init`; Git is optional.
2. Create a shareable experiment definition containing variants, cases and an evaluator.
3. Validate it and launch one study. INVARIANT freezes its inputs and queues its trials.
4. Leave the terminal, keep working elsewhere, or ask an assistant to monitor the study.
5. Compare completed and failed trials with their actual configurations and evidence.
6. Select a trial. Continue from its saved files and installed tools in a new child run,
   or review and explicitly apply its project-file changes.

For example: compare three prompt strategies at two reasoning efforts on the same
five tasks. These are six explicitly listed variants and thirty trials for one
repetition. A concurrency limit of three starts three trials at a time; the rest queue.
Each trial starts from the same baseline plus its declared inputs. Selecting one trial
chooses its concrete result, not a merge of every task result in the winning variant.

The first release MUST support local Linux Docker containers on supported Linux hosts
and Docker Desktop on macOS, without a cloud account for INVARIANT. The supported
CPU architectures follow the verified runtime images. Workloads must be Linux-compatible.

## 3. Core objects and immutable records

| Object | Meaning |
|---|---|
| Definition | Versioned, editable recipe: runner, explicit variants, cases, repetitions, evaluator and scheduling settings. |
| Baseline | Immutable project file manifest and content, exact prepared environment image identity, and effective execution policy. |
| Study | One frozen definition plus baseline and expanded trial list. |
| Variant | Named agent configuration; optionally declared project-file patches for a different implementation. |
| Trial | One variant × case × repetition, with its own workspace, environment changes, runner state and outputs. |
| Attempt | One execution of a trial. Explicit retries add attempts; they never erase failures. |
| Continuation | A new child study/trial using a selected saved state; its parent remains immutable. |
| Selection | A reversible pointer to a trial/attempt; selecting does not modify project files. |
| Apply plan | A reviewable project-file change set bound to a saved result and the current destination state. |

Each saved study MUST include:

- Resolved definition and hashes of prompts, declared patches, case inputs and evaluator sources.
- Project baseline digest; optional Git commit and dirty-state information as additional provenance.
- Exact local environment image ID, available repository digest, runtime/adapter versions and platform.
- Effective model and effort when applicable, parameters, policy, resource allocations and concurrency.
- Trial/attempt identifiers, parent links, timestamps, state transitions and reason codes.
- Logs, declared output artifacts, project-file diff, final filesystem checkpoint status and evaluator results.
- Observed token usage when available; unavailable values remain null with a reason.

Model aliases and project defaults MUST resolve before dispatch and be recorded as
actual values. A rerun uses the saved specification unless the user explicitly creates
a modified study. Reuse of the recorded model may fail if the provider no longer offers it.
Changing a recipe, project defaults or remembered launch preferences MUST NOT mutate
queued, running or completed studies. Experiments MUST NOT silently change chat defaults.

Records MUST distinguish requested settings from observed settings and “not captured”
from “empty.” Secrets MUST be referenced by name rather than embedded in definitions.
Schema versions and incompatible-version errors are required.

## 4. Shared environment, independent execution

### Capture once

The engine MUST capture the selected project content, including selected dirty and
untracked files, without requiring a commit. Selection rules and exclusions MUST be
visible during validation. Git internals, INVARIANT control state, credentials and
explicitly excluded paths are not part of the baseline. Declared prompt/evaluator files
are captured even when their location is outside the selected workspace content.

A snapshot MUST be internally consistent. Capture must refuse an active writable
legacy INVARIANT chat; detect ordinary concurrent file changes and retry within a
bound or fail clearly. It MUST NOT silently record a mixture of revisions. Atomic
capture of arbitrary externally written databases is outside scope; those inputs
require a quiescent export supplied by the user.

Use one prepared general-purpose environment image for a study, pinned before trial
launch. A variant may install different tools inside its own writable environment;
that change belongs to that trial. Preparation failure MUST NOT start a partial study.

### Separate writes

Every active trial MUST have a private writable project directory and private container
writable state. It MUST NOT bind-mount the original project as its writable workspace,
write into another trial, or overwrite the project's ordinary saved environment.
Existing mount verification must verify this actual trial context.

Image layers and immutable baseline content SHOULD be shared. The MVP MUST reuse one
base image for all trials without rebuilding/installing it once per variant. Private
workspace copies are acceptable where efficient filesystem cloning is unavailable.
Every active run still needs a process/container and resources; changed files and final
checkpoints consume disk. There is no promise of zero-copy storage or ten free runtimes.

The same rules apply to variant patches and case fixtures: apply them only to private
workspaces and record them. Conflicting or invalid input patches fail preparation.
Changing an agent architecture requires a supported configuration option, a declared
entry point, or an explicit code patch; INVARIANT does not invent that implementation.

External endpoints, provider state and shared databases are not automatically cloned
or isolated. The definition MUST declare known external dependencies. Validation must
explain that live external state can affect comparability.

### Persist explicitly

Completed attempts MUST preserve logs, metrics, selected artifacts, project changes and
a final stopped-container filesystem checkpoint where capture succeeds. That checkpoint
includes installed tools and home files; it excludes mounted inputs, temporary credential
mounts, running processes, memory and open connections. Workspace files are captured
separately because Docker image commits exclude bind mounts.

A checkpoint failure produces an explicit incomplete record and retains recoverable owned
state. It MUST NOT advertise resumability or silently substitute a fresh environment.
No automatic deletion of referenced baselines/results is allowed in the MVP. Show disk
usage and provide explicit deletion with dependency checks; unreferenced scratch may be
cleaned after its recovery metadata is safe.

## 5. Runners, evaluation and comparison

Two production runner adapters are in scope:

- **Codex:** reuse the existing host-side connection and authentication, model/effort
  validation and Docker command execution. Give each attempt separate native chat state
  and history; no shared mutable conversation. Support a captured task prompt and
  declared instruction files. Reject unsupported settings before dispatch.
- **Command:** launch a declared argument vector inside the trial environment. Supply
  read-only JSON files through `INVARIANT_VARIANT_FILE` and `INVARIANT_CASE_FILE`, plus
  an output directory through `INVARIANT_OUTPUT_DIR`. The program can be written in
  any available language and can implement its own agent graph. Its integration with
  a particular model provider is the program's responsibility.

External Codex, Claude or other assistants may operate the CLI independently of which
runner executes a trial. Full native Claude runner integration and automatic import of
its account credentials are not MVP requirements.

If a command needs credentials, the user explicitly names host-side secret references.
Resolve them into private, temporary, read-only mounted files; never copy the host home
or place secret values in the definition, container environment configuration or baseline.
The mounts and reference values are excluded from checkpoint/export metadata. Logs must
redact known secret values, but arbitrary programs can write credentials into their own
outputs; this is not a guarantee that saved outputs are safe to publish.

An evaluator is optional for exploratory runs. When supplied, it MUST be captured before
execution and run after the agent stops, against a separate copy of final candidate state,
with its own deadline and bounded output. The agent cannot rewrite the captured evaluator
or its expected inputs. Evaluation must not change the recorded candidate. This separation
does not establish resistance to an intentionally adversarial candidate.

Evaluator output MUST be versioned JSON containing an optional boolean `passed`, finite
named numeric metrics, and artifact references. Malformed output, a timeout and an absent
evaluator are distinct outcomes. Runner completion, evaluator completion and task quality
are separate fields: exit code zero is not evidence of correctness.

Comparison MUST show scheduled trials, runner successes/failures/cancellations, evaluated
trials, missing evaluations, quality pass counts, denominators, duration and available
metrics per variant and case. Unavailable values MUST NOT become zero. Explicit retries
must remain visible; comparisons default to the first attempt rather than quietly using
only the best retry. Show repetition count and spread when aggregating repeated trials.

No automatic “best” result is required. If the user requests sorting, the metric, direction,
missing-value policy and tie handling must be explicit. One run is not statistical proof.

## 6. Scheduling and lifecycle

Trials MUST run asynchronously and concurrently up to `max_parallel`, constrained by an
aggregate CPU/memory budget within detected Docker capacity with reserved host headroom.
The scheduler MUST reserve resources before launch and release them after verified stop.
It MUST queue excess work rather than multiply existing per-chat limits by N. Validate disk
headroom and report growth; hard per-trial disk quotas are not promised in the first release.

Admission failures, provider throttling and resource exhaustion have distinct reason codes.
No silent model downgrade, automatic increase in authority, hidden retry or host execution
fallback is allowed. Retries are explicit new attempts. The controller may retry transport
queries only when it knows they cannot start duplicate agent work.

Trial states are `queued`, `preparing`, `running`, `evaluating`, `saving`, then `succeeded`,
`failed`, `cancelled` or `interrupted`. `succeeded` means the configured execution and optional
evaluation completed; the separate `passed` field may still be false. Checkpoint failure
prevents success. Every terminal record has a reason and capture-completeness fields.
A study is active until every trial is terminal; its summary reports mixed outcomes.

The local controller MUST outlive the launching terminal. Following logs is optional;
disconnecting a viewer MUST NOT cancel work. Stopping a trial/study must terminate its
owned execution, prevent further queued starts in that scope, and save available evidence.

On controller or Docker failure, reconciliation MUST inspect owned containers and durable
state. Uncertain in-flight attempts become `interrupted` after owned execution is stopped;
they are not silently replayed. A recovery command may restart dispatch of proven never-started
queued trials, and must require an explicit retry for interrupted work. Never stop or adopt
foreign containers. Durable ownership and leases must prevent two controllers dispatching
the same attempt. A recoverable operation must be distinguishable from a lost acknowledgement.

## 7. Manual and AI-assisted operation

There is one experiment engine. The human CLI and structured CLI are thin adapters;
they MUST NOT implement separate scheduling, configuration or result stores.

Definitions live in ordinary, shareable project files such as `experiments/compare.json`,
not only under the currently ignored `.invariant/` directory. Editing them is supported
from any editor or assistant. Definitions do not need Git. Mutable runtime state and
credentials live in protected host state outside agent-writable project directories.

These are the proposed commands, all under a new namespace to preserve existing usage:

| Command | Behavior |
|---|---|
| `invariant experiment init FILE` | Write a commented guide separately and a valid starter JSON definition; never overwrite an existing file. |
| `invariant experiment acceptance` | Run P01–P21 release verification; `--live MODEL` enables real native calls, `--platform-report REPORT` adds same-source host evidence, and `--verify REPORT` validates a saved bundle. |
| `invariant experiment validate FILE` | Resolve/validate inputs; report expanded trial count, capture rules and resource/network requirements without launching. |
| `invariant experiment run FILE` | Capture and durably create a study; return its ID after asynchronous dispatch is registered. |
| `invariant experiment list` | List this project's studies and selection pointers. |
| `invariant experiment status ID` | Inspect a study, trial or attempt and its capture/recovery state. |
| `invariant experiment logs ID --follow` | Read persisted logs and optionally follow new events. |
| `invariant experiment results STUDY_ID` | Compare variants/cases and return artifact references. |
| `invariant experiment stop ID` | Cancel a study or trial and prevent further starts in that scope. |
| `invariant experiment recover STUDY_ID` | Reconcile owned state and dispatch only verified never-started work. |
| `invariant experiment retry TRIAL_ID` | Add an explicit attempt using that trial's frozen inputs. |
| `invariant experiment select TRIAL_ID --if-revision REV` | Record a selection without changing source files; accept an attempt ID if ambiguous. `select --clear STUDY_ID --if-revision REV` clears the pointer. |
| `invariant experiment continue TRIAL_ID --prompt-file FILE` | Create a child study from a complete saved result, with a fresh conversation and explicit new task. |
| `invariant experiment diff TRIAL_ID` | Inspect changes relative to the trial's original project baseline. |
| `invariant experiment apply TRIAL_ID --dry-run` | Generate a bound apply plan with current destination checks. |
| `invariant experiment apply --plan PLAN_ID` | Apply exactly that still-valid plan. |
| `invariant experiment delete ID` | Delete terminal, unreferenced saved state after listing what will be removed; refuse active/referenced state. |

Every command MUST support `--json`. Non-streaming output uses
`{"schema_version":1,"ok":true,"data":{...},"error":null}` or the corresponding
`ok:false` envelope with a stable error code, explanation and recovery action.
Log following uses versioned JSON Lines with sequence numbers and a resumable cursor.
JSON mode writes no banners/prompts to stdout, never waits for interactive confirmation,
and returns a nonzero exit code on error. Missing setup/authentication returns an actionable
error; it does not start an installer in an assistant's tool call. Successful asynchronous
submission means “accepted,” not “experiment succeeded.”

Mutations MUST accept `--request-id` for idempotency. Same identifier and same request
returns the existing operation; different content is a conflict. Selection changes and
apply plans use revisions to reject stale concurrent writes. A human may launch a study,
an assistant may inspect/select it, and the human may apply its reviewed changes without
recreating the study. Ship a short assistant usage guide for this interface; MCP is optional later.

## 8. Continue and apply without losing work

Continuation requires complete saved workspace and environment state. It MUST create a
new child record with its own writable state, preserving the parent and sibling trials.
It starts a fresh conversation with a declared task and a reference to parent evidence.
Native conversation forks, exact mid-turn replay and restoration of running processes
are outside MVP scope. The interface MUST make the new-conversation behavior clear.

Applying is a separate, explicit operation. A plan MUST show created/changed/deleted project
files, differences introduced by declared variant patches, conflicts, and destination revision.
It MUST exclude Git internals, INVARIANT controls and host credentials. Applying does not
install a trial's packages on the host or replace the ordinary project environment; use a
continuation to retain the selected tools. Applying one trial does not combine other trials.

Before any write, check all affected destination paths against the plan and baseline.
Conflicting edits, path escapes, unsafe links and stale plans MUST fail without partial
application. Never resolve conflicts by silently overwriting. Support safe in-project links;
reject changes that would traverse a link outside the destination.

Apply MUST coordinate with active writable legacy chats, use a recoverable write journal
and backup, and restore the previous state after an interrupted write before accepting a
new apply. The guarantee covers INVARIANT-managed writes; ordinary external editors cannot
be locked out. Detect changed paths immediately before replacement and document the remaining
race with an uncooperative writer. Selection, result inspection and validation are read-only.

## 9. Acceptance gate

The parallel-experiment MVP is accepted only when every case below passes with saved
machine-readable evidence. Tests use deterministic command/runner fixtures for exhaustive
failure coverage plus a small real Codex integration gate. Do not substitute model judgments
for independently inspected filesystem, runtime and evaluator observations.

| ID | Required observable outcome |
|---|---|
| P01 | Validate schema/adapter settings, input paths and trial expansion; reject unsupported settings and unsafe/invalid inputs before dispatch. |
| P02 | Freeze dirty/untracked source, prompts, evaluator, effective defaults and image identities; later edits cannot change a study or retry. |
| P03 | Run ten distinct variants with at least two overlapping active trials on a suitably sized host; original project, Git refs and sibling outputs remain unchanged. Also run without Git. |
| P04 | Reuse one pinned prepared image across those trials; show separate writable state and workspace, and preserve installed tools in a selected saved result. |
| P05 | Launch via readable CLI, inspect/select via JSON, apply via readable CLI; reverse the clients in a second scenario using the same IDs and state. Machine mode never prompts. |
| P06 | Concurrent requests with the same request ID create one study/attempt; stale selection revisions and mismatched request contents are rejected. |
| P07 | Run a real Codex adapter and a Linux command adapter; validate model/effort and record actual settings; command parameters/case files reach the intended trial only. |
| P08 | Aggregate reservations cap actual concurrency and allocations; queued trials start as slots free; stop prevents queued starts. |
| P09 | Close the launching terminal and viewer; work continues, and a new process can inspect/follow the same logs. |
| P10 | Cancel active work within a documented bound, stop its owned processes and preserve partial evidence; no foreign container is affected. |
| P11 | Runner failure, provider throttling, evaluator failure, OOM, deadline and checkpoint failure are separately recorded; siblings continue and retries do not hide failure. |
| P12 | Crash controller at launch/save acknowledgement points; recovery neither duplicates work nor replays interrupted trials; incomplete state remains explicitly recoverable. |
| P13 | Captured evaluator remains unchanged by candidate edits; it runs after agent stop against a copy, enforces limits and rejects malformed/nonfinite metrics. |
| P14 | Compare cases/repetitions with explicit denominators, missing data and attempt policy; a zero-exit but incorrect candidate does not become a quality pass. |
| P15 | Selection changes no source files; diff and dry-run plan match the selected result; an explicit valid plan applies only the reviewed changes. |
| P16 | Dirty conflicts, stale plans, escaping paths/links, concurrent INVARIANT writes and interrupted apply cannot silently overwrite work or leave an unreported partial apply. |
| P17 | Continue a completed trial into a child with its installed tools and files; parent/siblings remain immutable and the fresh conversation is disclosed. |
| P18 | Capture during a writable legacy chat or changing source fails safely; credentials/control files are excluded; deletion refuses referenced/active state. |
| P19 | Existing init/run/resume/chats/config/guardrails and legacy broker acceptance remain passing; experiment results do not overwrite ordinary chat preferences/checkpoints. |
| P20 | Two small real Codex variants finish through the isolated executor, produce evaluable outputs and can be compared; provider-unavailable is blocked, not a passing release result. |
| P21 | A clean-checkout user can follow the quickstart; Linux and macOS Docker runs verify the declared platform support, readable recovery and JSON tool workflow. |

The ten-variant concurrency test need not pay for ten model calls. P20 requires real
provider execution, separately configured and explicitly invoked. Offline deterministic
coverage can pass while the release remains unaccepted if that live gate has not run.
Reports MUST identify contract version/hash, test revision, runtime, omissions and cleanup.
The existing A01–A20 suite targets the archived contract; its current historical root-path
label must not be interpreted as acceptance of these P01–P21 requirements.

## 10. Limits and release exclusions

This MVP promises recorded, repeatable starting conditions and inspectable outcomes,
not identical generated tokens or bit-for-bit reruns. Provider nondeterminism, model
availability, architecture/platform differences, live network state and unpinned package
downloads can change outcomes. A seed is recorded only where the runner actually supports it;
it is not an LLM determinism switch.

Out of scope: GUI, hosted/distributed scheduling, automatic experiment search, automatic
architecture generation, a new agent framework, universal provider login, mandatory MCP,
GPU/device orchestration, Windows containers, memory/process snapshots, merging several
winners, public bundle registry and guaranteed portable/offline reproduction across machines.
Shareable recipes and comparison JSON are useful community artifacts; exporting every
credential-free environment into a portable bundle is a later feature with its own contract.

The first release MUST include examples, machine-interface documentation, an explicit
repository-level open-source license, and an honest support/limitation statement. License
selection is a release decision; this specification does not relicense existing dependencies.
