# Parallel agent experiments

INVARIANT can freeze studies, run **parallel command or native Codex trials in Docker**, save their
changed files and environments, evaluate copies of their results, and compare measured
outcomes. Execution uses a detached local controller with stop, recovery, explicit retry
and resumable log following. Selection saves a reversible pointer. Child
continuation and destination-bound, recoverable apply plans are available for selected results. Ordinary Codex chats still use
`invariant run` and `invariant resume`.

Use `invariant experiment acceptance` for P01–P21 release verification, independently
of legacy broker acceptance. See [the quickstart](QUICKSTART.md),
[release gates and platform limitations](ACCEPTANCE.md), and [schema migrations](MIGRATIONS.md).
Gate availability does not imply release acceptance; see the [recorded release evidence](../RELEASE_VERIFICATION.md).

For an end-to-end native example, use `invariant experiment demo ~/invariant-codex-demo --run`.
It compares three Codex reasoning efforts on one coding task with 23 shared checks and
offers selection, continuation and reviewed apply. See [the guided demo](DEMO.md).

## Start a study

For guided setup in your own project, use `invariant experiment init` with no filename.
Terminal answers and noninteractive flags generate the same definition and shared test
adapter. `init --task ... --test-command ... --test-path ... --run --json` requires no
authored files. `review STUDY_ID` reopens selection/continuation/reviewed apply; JSON
returns the same results without prompts. See [guided setup](GUIDED_SETUP.md).

Inside an initialized project, prepare the general Linux image once if necessary:

```sh
invariant init
invariant prepare --agent codex
invariant experiment init experiments/compare.json
```

The starter uses two no-op command variants. Edit its `runner.argv` and parameters to
describe your agent harness. The adjacent guide explains the input files. The definition
and guide are ordinary project files; no Git commit or branch is required.

```sh
invariant experiment validate experiments/compare.json
invariant experiment run experiments/compare.json --request-id first-comparison
invariant experiment list
invariant experiment status --request-id first-comparison
invariant experiment results STUDY_ID
invariant experiment logs STUDY_ID
```

`run` freezes a study and returns after durable dispatch. A detached controller
runs its trials independently of the launching terminal. Add `--wait` to wait for completion;
Ctrl+C closes that viewer and leaves work running.
The two starter variants use `/bin/true`, so they complete without producing quality metrics.
Use `run FILE --create-only` to save a queued study without dispatching it. Execute any
queued study with `invariant experiment execute STUDY_ID`; it uses the frozen
files, not the current project. Re-executing a terminal study returns its records without
replaying commands or model turns. Native Codex definitions execute through the same
controller and saved attempt lifecycle.

Close the launching terminal whenever you want. Inspect or follow the same study from
another terminal, or let an assistant use its ID. Stop requests are saved before queued
claims can proceed. They target only this study/trial's verified containers:

```sh
invariant experiment logs STUDY_ID --follow
invariant experiment stop STUDY_ID --request-id stop-42
invariant experiment recover STUDY_ID --request-id recovery-42
invariant experiment retry TRIAL_ID --request-id retry-42
```

`stop` accepts a study or trial ID. With responsive Docker, the target is owned process
termination within ten seconds of the committed request. Check `termination_confirmed`;
unconfirmed termination returns exit 1 and lists unresolved attempts. A stopped workload
may still be saving its checkpoint. Docker connectivity failures require recovery; no
process stop promises cancellation of external provider billing.

After a controller crash or Docker interruption, use `recover`. The project kernel lease
prevents recovery while a live controller owns work; heartbeat age alone never authorizes
a takeover. Recovery stops/removes uncertain owned containers, preserves available files
and installed tools, and marks claimed attempts interrupted. Missing containers are **not**
proof that a command never ran. Recovery may dispatch only attempts that remained queued.
It does not replay an interrupted command. A failed save keeps the stopped container and
prior checkpoint so recovery can try saving again. Original failure reasons are retained.

`retry TRIAL_ID` adds a new numbered attempt with the original frozen inputs and base
image, and dispatches it. It never overwrites previous attempts. Inspect `status TRIAL_ID
--json` for all attempts. **Comparison still uses attempt 1**; retries do not silently improve
a variant's score. For a changed recipe/evaluator/resource limit, create a new study.
The same request ID/content returns the existing control operation; reusing it for a
different operation fails. `status --request-id KEY` also finds accepted control requests.
Use a fresh key for a new recovery after an earlier recovery returned unresolved work.

One controller owns this project's experiment queue; studies execute serially, with each
study's admitted parallelism inside it. New submissions and explicit retries can join the
queue while a study runs. The controller starts on demand and exits when the queue drains.
It is not a boot service: after a machine/controller crash, explicitly recover before
resuming work. Ordinary chat `run`/`resume` state and controls are separate.

`execute` operates without the original recipe or case files. If the project remains
initialized, it can execute while an ordinary chat works on the original source: trial mounts
and runtime controls are separate. Study capture itself still requires the original project
coordination lock. Missing runtime/setup returns a recovery message, not an installer/login.

Every command accepts `--json`, before or after its subcommand. Example for an assistant:

```sh
invariant experiment run experiments/compare.json --create-only --request-id comparison-42 --json
invariant experiment execute STUDY_ID --json
invariant experiment status --request-id comparison-42 --json
invariant experiment list --json
```

The same IDs work in readable commands. Replies use `schema_version`, `ok`, `data` and
`error`. Nonzero exit codes accompany errors: 2 for invalid invocation/definition, 1 for
preparation or state errors. `--help` uses ordinary CLI help. Status accepts a study,
trial or attempt ID; study status includes its frozen specification and live/terminal attempt records.
`results` works for active studies too; pending/missing data stays explicit. `logs` reads
persisted runner/evaluation text, showing the last 65,536 bytes per file by default;
`--tail-bytes` accepts 1–1,048,576. See the streaming interface below.

A successful detached `run`/`execute` reply means dispatch was durably accepted, not that
work finished. Use `status`, `results`, or `--wait` to observe completion. Preflight failures
appear under `dispatch.state: recovery_required` with a reason and recovery action. Individual
trials can fail, and successful execution can receive `passed:false`. Inspect `results`
for trial states and quality; `ok:true` is not an assertion that the agent solved its task.
Lists are limited to 100 records by default; `--limit` accepts 1–1000.

## Resumable logs and deletion

```sh
invariant experiment logs STUDY_ID --follow --json
invariant experiment logs STUDY_ID --follow --after CURSOR --json
invariant experiment delete STUDY_ID
invariant experiment delete STUDY_ID --confirm
```

`--follow` or `--after` emits versioned JSONL frames instead of a single envelope. Frames
include a monotonically increasing project sequence, attempt ID, phase, kind and cursor.
`log_chunk.data.data_base64` contains the exact redacted bytes, preserving split UTF-8;
`log_end` reports captured/observed byte counts and truncation. `attempt_end` records the
terminal outcome. A final `follow_end` has no sequence and carries the last cursor and
terminal flag. Scope filtering can leave sequence gaps; reconnect with the last received
cursor to receive only later frames. Cursors belong to that project and exact scope ID.
Study/trial streams include subsequent attempts; attempt streams stay on that attempt.
Without `--follow`, `--after` reads currently available frames and returns.

Saved frames survive controller/viewer exit. Ctrl+C or a broken pipe closes only the
viewer; readable output prints its last cursor on Ctrl+C. A study requiring recovery can
remain nonterminal, so its follower waits until recovery or the viewer is closed. Logs
predating M3 remain available through ordinary `logs`; their old text is not backfilled
into the cursor stream. Referenced credential values are redacted before saving both
text and streaming frames. Transformations of credentials are not universally detectable.

Deletion first lists owned paths/images without changing the study. `--confirm` removes
terminal state; active, queued, unresolved or referenced state is refused with reasons.
Shared baselines remain for their other studies; images are ownership-checked and never
force-pruned. A durable deletion manifest allows the same command to finish after a
partial cleanup failure. Deletion removes that study's source snapshot, attempts, logs,
checkpoints, private native chat homes and submission lookup; keep/export needed evidence
first. A selected study is referenced and requires an explicit clear before deletion.
Child studies also retain their parent attempts and images until the children are deleted.

## What is frozen

Each study records the requested recipe, normalized effective recipe, exact image ID,
platform/runtime information, requested and admitted concurrency/resources, project policy,
captured inputs and their hashes, source content manifest, trial matrix and initial attempts.
Command parameters are opaque finite JSON objects. Codex variants resolve model aliases,
account defaults, remembered project settings and reasoning effort through the existing
account catalog. That lookup creates no native conversation or model turn and never
changes ordinary launch preferences.

The definition schema is [schema-v1.json](schema-v1.json). Structural keys are strict;
unknown runner settings, duplicate JSON keys, unsupported schema versions and nonfinite
numbers fail. Relative paths resolve against the project root, not the recipe's directory.
Source inputs must be regular files in the project. Repetitions default to one and trial
expansion is variant × case × repetition, limited to 1,000 trials. Case `input_file` is
optional and defaults to an empty JSON input for command runners. Codex requires a
nonempty UTF-8 `prompt_file` for each case.

`validate` performs the same capture checks and runtime/default resolution as submission
using temporary storage, but creates no study/database. Coordination may create the private
project lock. It requires Docker and the prepared/saved image; a Codex recipe additionally
requires the supported Codex installation and existing ChatGPT sign-in.

## Source selection and patches

`workspace.include` defaults to `["**/*"]`; `workspace.exclude` defaults to an empty list.
The portable glob syntax is `*` and `?` within a path segment, and `**` across directories.
An excluded directory excludes its descendants. Git internals, `.invariant`, `.env`,
`.env.*`, `.ssh`, `.aws`, `.codex`, `auth.json` and explicitly referenced credential files
are always excluded. This is a known-path exclusion policy, not a universal secret detector.

Capture includes selected dirty/untracked files and empty directories. Ordinary Git state
is not modified or required. Symlinks must be relative and resolve to existing included
paths inside the project. Capture copies regular-file contents instead of preserving host
hard links. Sockets, devices and FIFOs cannot be captured. Before/copy/after manifests and
file identity checks reject changing inputs. Live databases still need a quiescent export.

Explicit prompts, evaluator source files, patches, fixtures and the recipe are captured
in a separate protected inputs tree even when outside the workspace include set or excluded
by user glob rules. Protected credential/control paths cannot be explicit inputs.

Optional variant `patch_file` accepts exact UTF-8 unified text diffs with `a/` and `b/`
paths, including file creation/deletion. No binary diffs, renames, fuzzy application or
patching through symlinks. Each target is limited to 16 MiB. Patches are validated against
the baseline without modifying it or the original project. Case `fixtures` is a list of
`{"source":"cases/input.txt","destination":"data/input.txt"}` records. Fixture destinations
cannot escape, traverse a link, replace a directory or overlap a declared variant patch.
Variant patches and case fixtures are materialized only into private trial workspaces.
The original project and its Git state remain unchanged.

## Resources, credentials and persistence

Trial requests must stay within the effective project policy. Automatic project sizing
uses existing capacity detection. The aggregate experiment budget reserves 25% of measured
Docker CPU/memory as headroom and reduces concurrency when needed, while preserving each
trial's requested limits. Impossible requests fail before a study is accepted. One project controller admits no more than the frozen concurrency/budget allows; each
worker retains its reservation through preparation, execution, checkpointing and evaluation.
Evaluators also use that reservation. Current Docker capacity is checked before dispatch;
actual CPU, memory, process and network settings are verified and saved per execution.

Optional `secret_refs` maps workload names to reference names. For reference `PROVIDER`,
set `INVARIANT_SECRET_PROVIDER` to the absolute path of an owned single-link credential file
with mode 0600/0400 and size 1–65,536 bytes. Submission checks availability and excludes it from capture;
it stores the reference name, not token content or its absolute path. At execution, credentials are copied to temporary read-only input files, named in a
JSON map at `INVARIANT_SECRET_FILES`. Values are never injected into Docker environment
configuration. Known raw values are redacted across log chunks. Credential copies are
removed after the runner stops and are not included in checkpoint mounts or snapshots. Do not put credentials directly into parameters,
argument vectors, case files or prompts. Arbitrary workload outputs may contain secrets.

Records live under `~/.invariant/workspaces/<project-key>/experiments/` outside agent-writable
files. SQLite transactions commit a study, all trials, initial attempts and request mapping
together, after immutable filesystem objects have been published and synced. Same request
ID + same recipe content/path returns the original study without recapturing edited source
or re-querying defaults. Changed recipe content/path with the same key is a conflict. Status
by request ID resolves a lost acknowledgement. Use a new request ID for a new experiment.

A writable ordinary chat holds the same project coordination lock used during capture.
An active chat/capture or stale ordinary recovery record blocks submission. Finish/recover
that chat first. Capturing one study does not reserve the project for its queued lifetime.

Preparation failures retain private diagnostics under `preparation-failures/`. Partial
staging/published files are not accepted studies until referenced by a committed transaction.
Unreferenced preparation staging/diagnostics are retained for inspection. Terminal study
deletion uses the explicit command above; never manually remove a referenced baseline. Current ordinary chat state/checkpoints are never used as trial records.

## Execution, evaluation and comparison

Each trial has a fresh container writable layer and a private copy of the frozen source.
It receives `INVARIANT_VARIANT_FILE` (full variant JSON), `INVARIANT_CASE_FILE` (case input
JSON), `INVARIANT_OUTPUT_DIR` (`/workspace/outputs`) and `INVARIANT_ATTEMPT_ID`. Argument
vectors are passed literally, without host-shell expansion. Executables can use any language
installed in the selected Linux image. Read-only policies remain read-only; workloads that
need project outputs must have an appropriate writable trial policy.

The controller verifies container ownership, actual mounts and resource settings before
starting it. Runner logs are bounded by the frozen log limit. A failed command, deadline,
OOM or lost Docker connection is recorded separately. No host execution or automatic retry
is used. Resource reservations are released only after confirmed stop; uncertain cleanup
blocks further execution until explicit recovery confirms ownership and process stop.

After stopping the runner, INVARIANT saves its project files, manifest, changes relative to
the baseline, declared artifacts, and stopped root-filesystem checkpoint. Installed tools
and home state survive in that candidate image. Workspaces and artifacts occupy real disk;
shared base layers do not make changed environments free. Records show copied bytes, host
headroom and image logical size (which includes shared layers). Docker daemon free space is
reported unknown. Hard disk quotas are not provided. Capture/checkpoint failures retain
owned stopped state and prevent a successful trial status.

An optional evaluator runs after agent stop, in a separate writable copy of the candidate
and its saved image. Its sources come from a read-only copy of the original captured inputs;
editing the project's evaluator or the trial's copy cannot replace it. All imported evaluator
source files must be listed in `source_paths`. The evaluator's working directory is that
protected source tree, preserving project-relative paths in its argument vector. Executable
source permissions are preserved. `INVARIANT_CANDIDATE_DIR` points at `/workspace` and
`INVARIANT_EVALUATION_OUTPUT_DIR` is a separate writable output directory.

The evaluator prints exactly one version-1 JSON result, with optional boolean/null `passed`,
finite numeric `metrics`, and relative artifact paths under its output directory. Reports
must be existing regular files; links and path escapes are rejected. Malformed output,
missing evaluation, nonzero exit, deadline and output-limit failures remain distinct.
Evaluation does not alter the saved candidate. This separation does not make evaluators
immune to intentionally adversarial candidate programs or modified system tools.

Comparison shows each variant/case, repetitions, first-attempt policy, runner outcomes,
evaluated and missing counts, quality passes/failures and their denominators. Missing values
are never treated as zero. Metric summaries include counts, missing values, mean, range and
sample spread. A false quality verdict is a completed evaluation, not a runner failure.
There is no automatic winner or claim of statistical significance.

## Verification and remaining work

```sh
python3 -B -m unittest discover -s scripts/tests -p 'test_experiment*.py' -v
python3 scripts/test_experiments_creation.py
python3 scripts/test_experiments_execution.py
python3 scripts/test_experiments_lifecycle.py
```

The Docker gates require prepared images. They test real private mounts, shared images,
parallel command overlap, persisted tools, captured evaluators, mixed failures, comparison
and cross-client use without spending model tokens. M1/M2/M3 databases migrate transactionally
to storage schema 5 when an operation first writes state; frozen definitions stay unchanged.
The [legacy broker/workspace gate](../MVP_ACCEPTANCE.md) remains separate from acceptance
of the full [experiment contract](../../MVP_CONTRACT.md).

The M3 gate also kills detached controllers at create/start/save points, injects checkpoint
and disk faults, resumes log cursors, tests stop under queue pressure, and checks foreign
container protection and explicit deletion. Docker connectivity failures have injected unit
coverage; it does not restart the shared host daemon. M4 adds the live native gate below.
M5 adds the continuation/apply gate below. M6 still owns full experiment release acceptance.

## Native Codex experiments and saved selection (M4)

```sh
invariant experiment init experiments/codex.json --runner codex
# Edit the definition and adjacent prompt; optionally set each variant's
# model, effort, instructions_file or patch_file.
invariant experiment validate experiments/codex.json
invariant experiment run experiments/codex.json --request-id native-001
invariant experiment results STUDY_ID
invariant experiment select TRIAL_ID --if-revision 0 --request-id choose-001
invariant experiment status STUDY_ID --json
invariant experiment select --clear STUDY_ID --if-revision 1 --request-id clear-001
```

The native starter contains two variants using the same account defaults; edit their
settings/instructions to make them meaningfully different. Model and effort resolve once
at study creation (including remembered launch preferences), then are frozen. Execution
rechecks the account catalog and observed settings. A missing setting or model reroute
fails the attempt; INVARIANT never substitutes a cheaper or more available model.

Each attempt uses its own host Codex home, fresh persistent thread and one model turn.
Captured case `prompt_file` and variant `instructions_file` are read from the frozen
study, even if original project files change. The host app-server authenticates with the
existing ChatGPT sign-in; the executor gets no host auth, local tools or ordinary chat
history. No login/installer prompt opens in experiments. Multiple trials share the signed-in
account's rate/usage limits. Throttled, authentication and context failures remain explicit;
retry creates a fresh attempt/thread from the original baseline. It does not resume an
uncertain provider call. Remote provider cancellation/billing cannot be guaranteed.

Results retain requested and observed model/effort, native thread/turn IDs and CLI version
under `runner.native`. `token_usage` contains native numeric counters when reported;
missing usage stays `null` with `usage_unavailable_reason`. No token cost or determinism
is inferred. Published runner logs contain text/tool events with known secret-file values
redacted; native histories are private host state and may contain full prompts/tool results.
They are not portable/redacted exports. Study deletion also removes these private homes.

The ordinary project chat history, remembered model/effort and checkpoint are independent
of experiments. A killed controller closes the private host app-server through a guard
pipe; recovery separately stops/preserves the owned Docker executor. Native multi-agent
features are disabled in the current Codex profile. To compare a user-designed graph or
other agent system, use the language-independent command adapter.

Selection requires a terminal attempt with confirmed process stop and complete workspace
and environment capture. It records a pointer only, including when an evaluator says
`passed:false`; choosing quality is your decision. A trial with retries needs an explicit
attempt ID. `results` still compares first attempts and separately exposes the selected
pointer. Status, results and list expose its revision. Every selection/clear increments
that revision; stale revisions fail with `SELECTION_CONFLICT`. An identical request ID
replays its original response without another increment. A selected study must be cleared
before deletion. Read [ASSISTANT_GUIDE.md](ASSISTANT_GUIDE.md) for interchangeable clients.

Native protocol integration follows the [official Codex app-server documentation](https://learn.chatgpt.com/docs/app-server)
and the installed experimental schema. Existing supported Codex CLI/version checks still apply.

Run `python3 scripts/test_experiments_codex.py --live MODEL --effort medium` to use two
small real model turns for the M4 gate. Without `--live`, the script exits with setup
instructions and makes no model call; this is not a passing native release gate.

## Continue and apply a selected result (M5)

```sh
# Select a completed result first, using the revision shown by results/status.
invariant experiment select ATTEMPT_ID --if-revision 0
# Write the next task in next-task.txt, then:
invariant experiment continue STUDY_ID --prompt-file next-task.txt --request-id next-001
invariant experiment results CHILD_STUDY_ID

# Review and explicitly apply the original result's project changes.
invariant experiment diff STUDY_ID
invariant experiment apply STUDY_ID --dry-run --request-id review-001
invariant experiment apply --plan PLAN_ID --request-id apply-001
# Reconcile an interrupted apply if needed:
invariant experiment apply --recover --request-id recovery-001
```

A study ID means its selected attempt. Explicit trial/attempt IDs are also accepted;
a retried trial requires an explicit attempt ID. Continuation freezes a new task and creates
one child study/trial from the complete saved workspace and exact owned environment image.
It inherits the selected variant, case input, captured evaluator and frozen resource/network
policy. Variant patches and case fixtures are not applied again. Native Codex starts a new
conversation in a new private home; command harnesses receive a read-only task file through
`INVARIANT_PROMPT_FILE`. Add `--create-only` or `--wait` as on ordinary experiment runs.
The child has its own baseline/diffs; its parent/siblings remain immutable and cannot be
deleted while the child references them. Missing/incomplete files or images fail explicitly.

Private generated configuration, installed tools and Linux link text stay in the child.
Applying writes only reviewed project-file changes. It never installs the checkpoint's
packages on the host or replaces the ordinary chat's environment. Protected paths (including
case aliases of Git, INVARIANT controls and credential names), known credential references,
and source exclusions cannot be applied. Diffs show these omissions. Relative links must
resolve through included in-project paths; dangling, absolute, cyclic, protected-target and
escaping links are rejected for host apply. Such link text can remain in private continuations.
Case-colliding/case-only replacements and hard-linked destination files are rejected.

A plan records the result identity, selection revision, destination contents/modes and
ancestor-directory identities. It shows created/modified/deleted entries and declared-patch
provenance. Preview does not write project files; valid planning can return `state:conflicted`
and a list of conflicts. A conflicted plan cannot apply. New plans are required after affected
files, directory ancestry or selection change. Unrelated edits are preserved. Directory
removal/replacement refuses unplanned contents, including excluded/untracked children.
A child's changes apply against its parent baseline: apply the ancestor first if necessary.
INVARIANT does not automatically merge different trials or perform a three-way merge.

Apply stages file content and backups in private host state, takes the same project lease as
ordinary chats/source capture, validates the whole plan, and records each write intent before
its effect. Files/links use per-entry atomic replacement; deletions, directory changes and
permission changes are journaled too. A lost acknowledgement after the durable commit returns
the committed result without replay. An interrupted uncommitted operation rolls back on the
next managed project mutation or explicit `apply --recover`. Read-only capture/validation/
preview requests recovery instead of silently changing files. Read-only result inspection and
selection remain available. Active or unrecovered ordinary chats block apply.

If an external writer changes a path during rollback, recovery preserves it, retains the
journal/backups, returns `APPLY_RECOVERY_BLOCKED`, and blocks further managed project writes.
The error identifies paths and backup locations. Restore the recorded before/after contents
and modes, then recover. Never delete the active marker to bypass recovery. A recovered plan
cannot be used for a new apply: create and review another plan. Known disk/backup failures
before the first write leave project files intact; recovery data is retained on restore failure.

The guarantee covers INVARIANT-managed writes. External editors cannot be locked out.
Directory descriptors prevent following supplied symlinks; paths are checked immediately
before replacement, but a concurrent uncooperative writer can still race that final check.
This is a recoverable multi-file operation, not an atomic filesystem-wide transaction.
Plans are limited to 10,000 changed paths and 32 MiB of metadata, alongside existing capture
limits. Saved plans/backups are deleted with their unreferenced terminal study.

Verification:

```sh
python3 -B -m unittest discover -s scripts/tests -p test_experiments_apply.py -v
python3 scripts/test_experiments_apply.py
# Two real native model turns, using the signed-in host ChatGPT allowance:
python3 scripts/test_experiments_apply.py --live MODEL --effort medium
```

These verify M5 on the recorded host/Docker profile. Full P01–P21 release acceptance,
ten-variant/community release packaging and cross-platform claims remain M6 work.

Private journals carry an integrity digest. Malformed/corrupted recovery metadata returns
`CORRUPT_PLAN` and keeps project writes blocked; restore the private metadata from backup
rather than removing the marker. File apply/rollback preserves contents, executable modes
and link targets. It does not clone host ACLs, extended attributes or original timestamps.

## Installation, project tools and saved data

See [installation](../INSTALLATION.md) for standalone packages and upgrades,
[environment preparation](../ENVIRONMENT_PREPARATION.md) for explicit Linux dependencies
and baseline preflight, [reports](REPORTS.md) for Markdown/JSON/CSV export and privacy,
and [storage and recovery](../STORAGE_AND_RECOVERY.md) for retention, private exact-image
backups, restore journals and sanitized support diagnostics. Generate installed starter
projects with `invariant experiment example python DIRECTORY` or
`invariant experiment example javascript DIRECTORY`.
