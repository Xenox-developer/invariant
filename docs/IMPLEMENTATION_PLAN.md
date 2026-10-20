# INVARIANT implementation plan: parallel agent experiments

**Date:** October 20, 2026  
**Status:** M0–M5 implement frozen studies, detached parallel command/native Codex trials, capture/evaluation/comparison, stop, recovery, retries, resumable logs, revision-checked selection, terminal deletion, immutable child continuation and recoverable apply. M6 implements the complete release gate and community documentation. Actual release acceptance is determined by a fresh report combining same-source macOS evidence and Linux evidence from GitHub Actions.  
**Normative target:** [MVP contract v0.6](../MVP_CONTRACT.md)

## 1. Delivery objective and first vertical slice

Deliver one local experiment engine that humans and external assistants can operate
interchangeably. Start from an ordinary project, launch several declared configurations
from one frozen baseline, compare evidence, and explicitly continue or apply a selected result.
Keep the existing interactive `invariant run` and `invariant resume` usable throughout.

The first vertical slice is deliberately small: one case, two command variants, a pinned
image, private workspaces, two concurrent executions, a captured evaluator, and results
visible through both readable and JSON commands. It validates independence before connecting
live model execution. It is a milestone, not the complete MVP: cancellation, crash recovery,
Codex, safe application, continuation and all acceptance cases remain required.

Do not start with a dashboard, cloud scheduler, MCP server or a new agent framework.
The community contribution is a repeatable local workflow and an inspectable common record.
No additional market research or outreach is required to start this engineering work.

## 2. Existing implementation: reuse and changes needed

| Existing module | Reuse | Required change |
|---|---|---|
| `invariant/cli.py` | Existing setup/chat/config commands | Add an explicit `experiment` namespace without changing positional interpretation of existing commands. |
| `invariant/workspace.py` | Docker preparation/inspection, Codex configuration, task execution and ownership checks | Extract an execution context independent of the original project path. Current direct project bind mount, whole-session project lock, `current.json` and `control.sock` cannot be shared by concurrent trials. |
| `invariant/environment.py` | Stopped filesystem checkpointing, image identity/ownership checks, atomic recording | Parameterize checkpoint ownership by baseline/trial/attempt. Never reuse the ordinary project's single `environment.json` for trial checkpoints. Capture mounted workspaces separately. |
| `invariant/codex_rpc.py` | Bounded host-side Codex transport | Supply per-attempt home/history and a cancellable event sink; keep authentication outside the workload container. |
| `invariant/chats.py` | Native chat identity validation patterns | Preserve the ordinary chat index; record experiment conversations separately. Do not insert N trials into one chat's mutable history. |
| `invariant/resources.py` | Docker capacity and pressure inspection | Add aggregate admission/reservation. Do not independently apply automatic per-chat sizing to N workers. |
| `invariant/configuration.py`, `preferences.py`, `policy.py` | Project settings, policy validation and confirmed model/effort defaults | Resolve into immutable experiment settings. Keep shareable experiment recipes out of the small policy configuration file and keep experiment choices from rewriting ordinary defaults. |
| Existing tests and `docs/MVP_ACCEPTANCE.md` | Regression protection | Preserve the legacy gate, label it correctly, and introduce a separate P01–P21 experiment gate. |

The original Rust invoice broker remains a supported legacy path. The new scheduler belongs
in the Python workspace package; it does not need to be routed through the invoice protocol.
No language-specific environment profiles are introduced.

## 3. Module design and ownership

Both CLI forms call the same `ExperimentEngine` interface. Thin adapters format results;
all authorization-independent experiment semantics live in the engine. A shell-capable
assistant uses the structured CLI exactly as a script does.

```text
Human CLI             Assistant / script using --json
     \                          /
             ExperimentEngine
         /          |           \
Definitions      Study store    Scheduler / controller
                    |                 |
               Snapshots         Runner adapters
                    |           Codex     Command
                    |                 |
                 Evaluation <--- stopped candidate
                    |
              Comparison / selection
                    |
              Continue or apply plan
```

Use a small internal package, proposed as `invariant/experiments/`:

| Module | Responsibility behind its interface |
|---|---|
| `definitions.py` | Strict schema, input capture rules, adapter validation, expansion, canonical hashes and frozen effective settings. |
| `store.py` | SQLite transactions, identifiers, revisions, event sequence, leases, idempotency and references to filesystem blobs. |
| `runtime.py` | Source snapshots, per-attempt writable materialization, verified Docker execution contexts, stopped checkpoint save/load and safe ownership cleanup. |
| `engine.py` | Study lifecycle, controller reconciliation, aggregate scheduling, comparison, selection and continuation. |
| `runners.py` | Codex and command adapters, their configuration validation, events, cancellation and observed settings. |
| `evaluation.py` | Captured evaluator execution, strict result parsing, metric/artifact validation and bounded evidence collection. |
| `promotion.py` | Diff, apply-plan validation, path safety, write journal, backup, rollback and crash reconciliation. |
| `cli.py` | Namespace parsing, human formatting, versioned JSON/JSONL, stable error and exit-code mapping. |

These are proposed file locations, not a requirement to create empty abstractions first.
Keep behavior together until a real second adapter or caller needs a seam. Tests inject a
clock, Docker executor, runner and fault points through the same module interfaces callers
use. Use the real command adapter for deterministic end-to-end fixtures rather than a
separate implementation of the scheduler in tests.

The engine's initial interface groups operations as: `validate`, `submit`, `inspect`,
`cancel`, `recover`, `retry`, `select`, `continue_from`, `plan_apply`, `apply`, and `delete`.
Results are structured records; printing, terminal input and prompting stay in CLI adapters.
State-machine transitions and ownership checks must not be duplicated in formatters.

### Example of switching between manual and assisted use

The following uses the planned interface. A human edits the recipe and launches it:

```sh
invariant experiment validate experiments/compare.json
invariant experiment run experiments/compare.json --request-id comparison-001
```

They can then ask their assistant: “Inspect study STUDY_ID, compare the measured task
results and failures, and select the strongest trial. Show me its diff and an apply plan.”
The assistant calls the same `status`, `results`, `select`, `diff` and `apply --dry-run`
commands with `--json`. It presents the evidence and the returned plan ID. The human
can finish from a terminal:

```sh
invariant experiment apply --plan PLAN_ID
```

STUDY_ID and PLAN_ID stand for identifiers returned by earlier commands. The user could
instead authorize the assistant to apply that plan, or perform every step manually.
There is no mode conversion, conversation ownership requirement or second copy of state.

## 4. Definition and record formats

Use versioned JSON for the initial definition. Keep the schema and examples in
`docs/experiments/schema-v1.json` and `examples/experiments/` when implemented. Reject
unknown structural fields and duplicate keys. Opaque command `parameters` are the exception:
the harness owns their meaning, while INVARIANT records them exactly.

Proposed minimal example, using a command harness already in the user's project:

```json
{
  "schema_version": 1,
  "name": "compare-search-strategies",
  "runner": {
    "type": "command",
    "argv": ["python3", "agent.py"]
  },
  "workspace": {
    "include": ["**/*"],
    "exclude": [".env", ".env.*", "results/**"]
  },
  "variants": [
    {"id": "single", "parameters": {"strategy": "single", "max_steps": 10}},
    {"id": "planner", "parameters": {"strategy": "planner", "max_steps": 10}}
  ],
  "cases": [
    {"id": "task-a", "input_file": "experiments/cases/task-a.json"}
  ],
  "repetitions": 1,
  "execution": {
    "max_parallel": 2,
    "trial_cpus": 1,
    "trial_memory_mib": 1024,
    "trial_timeout_seconds": 600
  },
  "evaluator": {
    "argv": ["python3", "experiments/evaluate.py"],
    "source_paths": ["experiments/evaluate.py"],
    "timeout_seconds": 60
  },
  "artifacts": ["outputs/**"],
  "external_dependencies": []
}
```

This is a proposed recipe, not a self-contained runnable example: `agent.py`, its case
and evaluator must exist. Python is only the example harness; argument vectors may name
any executable installed in the Linux image. Do not shell-expand JSON arguments.

Define these rules before implementing the parser:

- All relative source paths resolve against the project root. The definition may be
  anywhere in the project. Captured input digests make its later edits irrelevant to a study.
- Includes/excludes use a documented portable glob syntax; baseline mandatory exclusions
  always take precedence. Display excluded paths, file counts and estimated bytes.
  Respect explicit selection, not implicit Git tracked-only behavior or guessed languages.
- The default environment is the project's saved, verified environment or prepared base.
  Freeze its image ID once. Optional preparation is a separate ordinary workspace session
  before capture; arbitrary per-variant Docker builds are not required in this MVP.
- `execution` values are requests. Project limits/network policy remain authoritative;
  missing values resolve from them. Add aggregate CPU/memory budgets to this section,
  defaulting to a recorded capacity/headroom calculation. Reject infeasible allocations
  or explain reduced concurrency during validation; never silently reduce a trial's RAM.
- Each case's input file is arbitrary JSON. A Codex case additionally names a captured
  `prompt_file`; variants use validated `model`, `effort` and optional `instructions_file`
  rather than unrecognized native flags. Missing model/effort resolves through the existing
  catalog/default logic. Command `parameters` are not treated as native Codex settings.
- Optional `patch_file` per variant and declared case fixture files are applied in a
  documented order: baseline, variant patch, case fixtures. Reject overlapping conflicting
  changes. Provide examples for code changes and a harness whose architecture is declarative.
- Optional `secret_refs` map workload names to host-side reference names, never values.
  Command runners receive mounted file paths, not injected secret values in Docker image
  configuration. Reject missing references before dispatch. Codex uses the existing host
  authentication route; no universal account importer is implied.
- Bound definition size, expanded trial count, captured bytes, log bytes, artifact bytes,
  event frames and evaluator duration. Initial implementation defaults: 1 MiB definition,
  1,000 trials, 1 GiB source, 64 MiB logs/attempt, 256 MiB declared artifacts/attempt,
  2 MiB event/result frames and 60-second evaluator timeout. Oversized input fails before
  dispatch; log/artifact truncation is explicit and never fabricates complete capture.
  Limits may be configured within documented host safeguards and are frozen per study.

Evaluation receives `INVARIANT_CANDIDATE_DIR`, captured case/variant files, and
`INVARIANT_EVALUATION_OUTPUT_DIR` in a separate execution context. It emits one result:

```json
{
  "schema_version": 1,
  "passed": false,
  "metrics": {"tests_passed": 8, "tests_total": 10},
  "artifacts": ["test-report.json"]
}
```

Metric keys are strings and values finite numbers; `passed` may be null when no boolean
judgment is supplied. Artifact references must resolve under the evaluator's output directory.
Capture evaluator sources in a protected input tree and execute that copy, not the candidate's
similarly named files. Evaluation uses a copy of the saved root filesystem and workspace;
its own output cannot alter the trial. No automatic quality judgment without an evaluator.

### Durable state

Extend the existing private project state directory; do not put controller files in a
trial's workspace. Suggested layout:

```text
~/.invariant/workspaces/<project-key>/
  ...existing ordinary chat state...
  experiments/
    store.sqlite3
    controller.sock
    controller.lock
    baselines/<baseline-id>/...
    studies/<study-id>/definition.json
    attempts/<attempt-id>/{workspace,events.jsonl,artifacts,...}
    apply/<plan-id>/{plan.json,journal.json,backup/...}
```

SQLite owns state transitions, leases, request IDs, selection revisions and reference
counts; immutable filesystem objects hold large content. Never claim filesystem writes
and a database commit are one atomic operation. Write content to owned temporary paths,
fsync and rename, then commit references; reconciliation finds orphaned staged content.
Use digest verification and record checkpoint completeness before marking an attempt successful.
Keep credentials and ordinary Codex history outside captured objects.

## 5. Implementation sequence and milestone gates

Implement in order. Each milestone must deliver its checks before its dependent milestone.
A passing milestone is not permission to label the entire contract accepted.

### M0 — Preserve behavior and establish the contract seam

- Mark the new contract/plan as targets and archive the legacy requirements. This documentation
  step is complete. The runtime extraction and legacy report relabeling are implemented.
- Inventory current assumptions in `workspace.py`, `environment.py`, CLI routing and native
  chat setup. Extract an explicit execution context: project identity, writable workspace,
  state directory, pinned image, policy, ownership token and event sink.
- Preserve the existing interactive adapter using that context; verify its mounts against
  the supplied workspace rather than assuming the original source path.
- Update legacy acceptance emitter labels from `MVP_CONTRACT.md section 9` to
  `docs/LEGACY_MVP_CONTRACT.md section 9`. Update source manifests to hash the appropriate
  contract and make the two gates visibly distinct. Do not rewrite historical evidence.
- Record the release license decision before publication; check dependency/license compatibility.
  [The licensing record](LICENSING.md) keeps publication gated on owner selection and the release inventory.

**Gate:** existing chat/environment and broker regression suites pass; no experiment behavior
is advertised as available. Coverage: P19 groundwork.

### M1 — Frozen definitions and durable study creation

- Implement strict definition parsing, adapter-specific validation, canonical effective
  specification, deterministic trial expansion and content identifiers.
- Implement source capture with manifest verification before/after materialization, safe
  links, explicit exclusions and refusal during an active writable ordinary chat. Hold
  the existing project coordination lock during capture; ordinary chat launch and apply
  must respect it. Release it before trials run.
- Resolve model/defaults and pin the saved environment image before publishing the study.
  Validate required local inputs and secret references. Preserve failed preparation diagnostics.
- Add the store, request-id conflict handling and initial study revisions. Mutable selection revision checks arrive with M4. A submit acknowledgement
  returns the durable study ID; uncertainty is inspectable by request ID without resubmission.
- Implement `experiment init`, `validate`, `run`, `list` and `status` through the shared engine.
  `run` may queue work until M2; label that milestone state honestly.

**Implemented interface:** see [the M0/M1 guide](experiments/README.md). Frozen retry inputs are stored; M3 adds explicit numbered retry attempts.

**Gate:** deterministic creation, frozen retry inputs, concurrent creation/idempotency,
non-Git and dirty-project capture tests. Coverage: P01, P02, P06, P18 groundwork.

### M2 — First vertical slice: command runner and two concurrent trials

Implemented with an attached controller. `run FILE` executes command studies;
`--create-only` preserves submission-only use and `execute STUDY_ID` dispatches existing
frozen studies. `logs` and `results` expose persisted evidence to both CLI clients.
See [the implemented interface](experiments/README.md). M3 adds detached dispatch without changing the frozen study format.

- Materialize private workspaces from one immutable baseline. Create private container
  writable state from the same image ID and verify actual mounts/policy.
- Implement the command runner's parameter/case files, output directory, bounded events,
  exit status and cancellation handle. Provide deterministic fixture programs with distinct
  markers, failures, delays and a small evaluator. No paid provider calls are needed here.
- Implement a local controller with aggregate reservations and a pending queue. Model both
  runner and evaluator resource use; evaluator execution also consumes a slot/reservation.
  Fail a launch cleanly if capacity disappears instead of switching to the host.
- Add persisted logs, artifact capture, stopped filesystem checkpoint and project snapshot
  capture. Reuse base layers; expose estimated/observed disk use honestly.
- Add evaluation, strict metric parsing and initial `logs`/`results`. Compare all scheduled
  trials with separate runner/evaluation/quality fields and explicit missing data.

**Gate:** two genuinely overlapping runs, correct parameter isolation and two distinct
results, untouched source/Git, one base image, saved tools and immutable evaluator.
Run through both CLI forms. Coverage: P03/P04 foundations, P05 inspection, P07 command,
P08 basic, P13, P14.

### M3 — Detached operation, cancellation and failure recovery (implemented)

- Make controller lifetime independent of the caller's terminal/process group. Use one
  experiment controller per project, short durable dispatch transactions and per-attempt
  ownership/leases. Ordinary chat state/control files remain separate.
- Persist intent before creating a container; label every owned container with its attempt
  and dispatch token. Reconcile crashes between container creation, start and acknowledgement
  without creating a second execution. A lease expiry alone is not proof that work never ran.
- Add `stop`, `recover`, `retry`, resumable log cursors and request-id lookup. Prevent queued
  starts after cancellation is committed. Terminate only positively identified owned work.
- On recovery, stop uncertain owned work, preserve what is available, mark interrupted and
  offer explicit retry. Proven never-started work may dispatch after explicit recovery.
- Keep the previous checkpoint and stopped container if saving fails. Add typed recovery
  actions for authentication, image missing, source changed, capacity, disk, throttling,
  evaluator and checkpoint failures. Do not auto-replay model calls or commands.
- Set the cancellation target at 10 seconds from accepted local stop request to owned
  process termination under a responsive daemon; report an explicit unresolved state if
  Docker is unreachable. Provider billing cancellation is not guaranteed by process stop.
- Implement terminal-state deletion and reference checks; show references that prevent deletion.

**Gate:** terminal close, controller kill, Docker restart, disk/checkpoint fault injection,
concurrent controller startup and cancellation under queue pressure. Coverage: P06, P08,
P09, P10, P11, P12, P18 deletion.

**Implemented interface/evidence:** [experiment guide](experiments/README.md),
`python3 scripts/test_experiments_lifecycle.py` and the experiment unit tests.
The controller is a private detached Python process with a project kernel lease and heartbeat.
It runs one study at a time with bounded parallel attempts within that study. Durable claim,
create/start/checkpoint intents and attempt-specific Docker labels precede effects. Recovery
never infers “not run” from a missing container or stale heartbeat. SQLite schema 3 retains
numbered attempts, control request IDs, dispatches, cancellation and redacted log frames.
Deletion requires a preview and `--confirm`; shared sources stay referenced. Docker outage
and disk failure have injected coverage; the test does not restart the shared host daemon.
No native model call is made by this gate. Native provider-specific throttling/authentication
classification belongs with M4; arbitrary command failures retain their exit/log evidence.

### M4 — Codex trials and interchangeable clients (implemented)

- Adapt existing Codex turn execution to event sinks and cancellation instead of direct
  printing. Authenticate on the host; allocate independent native chat homes and identifiers
  per attempt. Do not share mutable project chat history or allow a host command fallback.
- Validate catalog-supported model/effort; capture native observed settings and usage where
  supplied. Unsupported/unavailable settings fail clearly, without substituting another model.
- Support variant instructions and captured case prompts. Run user-designed multi-agent systems
  through the command adapter when their harness exposes a graph configuration; do not silently
  enable unsupported native multi-agent modes in the existing Codex profile.
- Finish versioned JSON envelopes, stable exit/error codes, request IDs and noninteractive
  behavior for every experiment command. Add `select` with revision checks.
- Write `docs/experiments/ASSISTANT_GUIDE.md` showing validate → submit → poll/follow → compare →
  select → diff/plan → apply, including conflict and recovery handling. This guide calls the
  CLI and stores no parallel assistant-only state. No mandatory skill installer or MCP server.

**Gate:** a human launches, a scripted tool caller inspects/selects, and the human sees the
same selection; reverse the roles. Two real small Codex variants run in isolated executors.
Coverage: P05, P06 selection, P07 Codex, P19 defaults/history, P20.

M4 implementation uses one shared turn driver with terminal/event adapters. Each native
attempt has a private host Codex home and fresh thread/turn IDs; a guard pipe terminates
the host app-server process group when its controller dies. Authentication stays on the
host; all workload tools run through the verified Docker executor. Frozen model/effort
are checked against the account catalog and actual thread response, with rerouting
rejected and native usage saved when supplied. SQLite schema 4 adds a selected attempt
pointer. Selection uses explicit revision checks, shared request IDs and no project writes;
selected studies cannot be deleted until the choice is cleared. The starter, human CLI
and JSON interface all call the same engine. See `scripts/test_experiments_codex.py` for
the opt-in two-turn real Codex gate and `docs/experiments/ASSISTANT_GUIDE.md` for handoff.
This delivers the M4 portions of P05/P06/P07/P19/P20, not M5 apply or full MVP acceptance.

### M5 — Continue a selected result and safely apply changes (implemented)

- Implement `continue` as a new child study from a complete selected workspace/checkpoint,
  with a fresh conversation, captured new prompt and immutable parent link. Use the parent's
  final workspace as the child's baseline for its own diffs. Refuse missing/incomplete state.
- Implement content-based project diffs, including declared variant patches, safe links,
  new files and deletions. Keep workspace changes separate from environment/tool installation.
- Implement dry-run apply plans bound to result identity and destination file digests. Reject
  conflicts across the whole plan before writes; do not silently three-way merge in the MVP.
- Apply under project coordination with a staged journal and backup. Check affected files
  before each replacement, journal every write and restore on failure. Startup must reconcile
  unfinished apply operations before allowing another project mutation. Do not overwrite
  newly detected external edits during rollback; retain recovery data and explain intervention.
- A tool caller's explicit `apply --plan` is the same action as a human's invocation. The CLI
  does not infer permission to apply merely because an assistant selected a winner.

**Gate:** continuation retains installed tools; selection and dry-run are read-only;
conflicting, escaping and stale changes fail without a silent overwrite; crash-injected
apply restores or exposes a blocked recoverable state. Coverage: P15, P16, P17, P18 coordination.

M5 implementation adds schema 5 lineage and durable apply-plan records. Continuation
copies the complete stopped private workspace, reuses the exact owned image, freezes a
new prompt and creates one child trial with fresh native history. Applied variant patches
and fixtures are not replayed. A command harness receives `INVARIANT_PROMPT_FILE`.
Diffs use each child's own parent baseline. Host apply excludes controls and credential
paths, validates safe links and the whole destination, and stages content/backups before
journaled per-entry writes. Project kernel leases reconcile interrupted writes before
new managed project mutations; read-only capture/preview requires explicit recovery.
External changes during rollback remain untouched and block writes with journal/backups
retained. The filesystem and Docker gates are `scripts/tests/test_experiments_apply.py`
and `scripts/test_experiments_apply.py` (optional two-turn native parent/child gate).
M6 still owns complete P01–P21 acceptance and cross-platform release claims.

### M6 — Release gate and community documentation

**Implementation:** `invariant experiment acceptance` runs the P01–P21 gate separately
from legacy acceptance, with fresh component reports, source/contract/artifact hashes,
host identity, required-observation checks and an offline bundle verifier. The command
matrix expands ten variants across two cases and two repetitions; concurrent CLI creation,
OOM, clean-wheel quickstart and both manual/JSON apply handoffs augment the prior gates.
The live option includes native variants, parent/child continuation and legacy chat resume.
Apache-2.0 was selected by the owner. Dependency/notice inventory, schema/migration policy,
quickstart, contributor documentation and an actual-Linux CI workflow are included.
The Linux host gate runs on GitHub Actions. P21 and full release acceptance require
its verified artifact bundle combined with same-source macOS evidence. See [verification](experiments/ACCEPTANCE.md).

- Add `scripts/test_experiments_acceptance.py` and a named experiment acceptance command,
  separate from legacy `invariant acceptance`. Save versioned P01–P21 reports, logs, runtime
  observations, source/contract hashes, failure reasons, cleanup and platform identity.
- Expand the command fixture study to ten variants, multiple cases and repetitions. Prove
  overlap using independent start/end receipts, allocations through Docker inspection and
  source/sibling immutability using content hashes. No extra Git branches are created.
- Run the regression gate after runtime extraction and the final relevant changes. Verify
  the declared Linux/macOS support on actual hosts, recording evidence per platform.
- Run the small real Codex gate separately with explicit live-provider setup. A missing model,
  missing authentication or unavailable provider blocks that gate; it is not a skip that
  turns the release green. Ordinary development tests remain runnable without model access.
- Ship clean-checkout examples for command and Codex runners, manual/assistant handoff,
  tool installation before baseline capture, resource queuing, recovering failures, evaluator
  design, safe apply, deletion/storage, credential handling and nondeterminism.
- Publish the schema, migration policy, license, contributor guide and release limitations.
  Update README availability claims only after the corresponding gate passes.

**Gate:** complete evidence for P01–P21, existing behavior preserved, documentation commands
verified against the released parser. No accepted status based solely on deterministic fixtures.

## 6. Verification strategy and acceptance ownership

Tests should cross real module interfaces. Use small focused tests for parsing, state
transitions, reservations and apply journals; real Docker integration for mounts, image
sharing, checkpointing and process lifetime; CLI subprocess tests for human/JSON parity.
Do not mirror every implementation helper with a low-value mock assertion.

| Required cases | Milestone that completes them | Primary evidence |
|---|---|---|
| P01, P02 | M1 | Invalid/valid definitions, frozen manifests/settings and repeatable retry inputs. |
| P03, P04 | M6, built in M2 | Ten-variant receipts, inspected image/mount identities, source hashes and restored tools. |
| P05, P06, P07 | M4, built in M1–M3 | Cross-client CLI transcripts, request collisions, revision conflicts, real runner settings. |
| P08, P09, P10, P11, P12 | M3 | Reservations/runtime inspection, terminal exit and crash/failure injection. |
| P13, P14 | M2 | Changed candidate cannot replace captured evaluator; malformed/missing/incorrect results stay explicit. |
| P15, P16, P17 | M5 | Selection/diff/apply journals, conflict/crash recovery, child identity and installed-tool checks. |
| P18 | M5, built in M1/M3 | Capture coordination, exclusion manifests, deletion reference checks and apply locking. |
| P19 | M6, maintained from M0 | Existing regression evidence plus project defaults/checkpoint/history isolation. |
| P20 | M4, repeated for release in M6 | Two real Codex trials and independent evaluator results through Docker. |
| P21 | M6 | Clean-checkout guide execution and platform-specific CLI/recovery evidence. |

Fixtures must include an agent that returns zero with an incorrect output, one that edits
an evaluator's project copy, one that writes a unique sibling marker, a package installed
only in one trial, and one that outlives its viewer. Inject failures after intent persistence,
container creation, checkpoint creation and each apply write. Unknown outcome must stay
unknown until reconciliation; this is central to trustworthy experiment records.

## 7. Scope discipline and practical risks

- **Disk growth:** shared bases reduce repeated setup, but changed environments and workspaces
  still cost storage. Surface this before ten long trials; retain references and require explicit
  deletion. Efficient cross-platform workspace cloning and hard disk quotas can follow later.
- **Source consistency:** INVARIANT can coordinate its own writers, not every editor or database.
  Use a verified capture and conservative apply checks; require exports for live databases.
- **Network reproducibility:** pin what the user supplies and record network policy. Do not
  describe fresh package downloads or a changing external model as deterministic replay.
- **Experiment fairness:** use the same baseline/cases and visible resource allocations. Do not
  infer superiority from completion alone or discard failed trials to improve a summary.
- **Runtime extraction:** the current project-wide lock and singleton state are correct for
  one chat but wrong for a shared parallel runtime. Refactor them before trying N launches.
- **Assistant integration:** the smallest reliable integration is documented CLI plus JSON.
  Add MCP or native assistant skills only after the common engine works and a caller needs them.

The next coding task is **M4**, native Codex trials and selection through interchangeable clients.
Do not implement all planned commands as empty shells or start with ten live Codex processes.
