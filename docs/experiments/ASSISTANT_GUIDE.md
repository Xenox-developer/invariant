# Use INVARIANT from an assistant or terminal

Both callers use `invariant experiment` inside the initialized project. There is one
SQLite store, one set of study/trial/attempt IDs and one selection per study. There is
no assistant-only session, mandatory skill, MCP server or separate configuration.
A person can resume inspection at any time using the same IDs without `--json`.

## Validate → submit → inspect → compare → select

1. Read or create a definition. To create a native starter noninteractively:
   `invariant experiment init experiments/codex.json --runner codex --request-id starter-001 --json`.
   Edit its ordinary files to express the user's configurations and cases. Set the requested
   model/effort explicitly when those are part of the experiment. Add an evaluator for quality.
2. `invariant experiment validate experiments/codex.json --json`.
   Inspect `data.frozen_preview`, source exclusions, resources, catalog settings and trial count.
   Validation does not submit trials or consume a model turn. It may inspect host account metadata.
3. `invariant experiment run experiments/codex.json --request-id study-001 --json`.
   Save `data.id`. This accepts detached execution; `ok:true` does not mean tasks passed.
   Use `--create-only` if the user wants a frozen study without starting work, then
   `invariant experiment execute STUDY_ID --request-id dispatch-001 --json`.
4. Poll `invariant experiment status STUDY_ID --json`, or follow saved output:
   `invariant experiment logs STUDY_ID --follow --json`.
   Save the cursor on each received frame. Reconnect with
   `invariant experiment logs STUDY_ID --follow --after CURSOR --json`.
   Closing this viewer never stops work. `--wait` on run/execute is also a viewer.
5. `invariant experiment results STUDY_ID --json`.
   Compare metrics, failures, missing evaluations, duration and native usage. The denominator
   includes all scheduled first attempts. A retry never silently replaces a failed first attempt.
   No evaluator means quality is unknown. No native usage means usage is unknown, not zero.
6. Read `data.selection.revision` and choose a complete terminal trial/attempt based on
   the user's criteria. Example with current revision 0:
   `invariant experiment select ATTEMPT_ID --if-revision 0 --request-id choice-001 --json`.
   If a trial has multiple attempts, use an explicit attempt ID. Status/results/list now show
   the same choice to human and assistant callers. Selection leaves source/Git files untouched.
   Clear with `invariant experiment select --clear STUDY_ID --if-revision REV --request-id clear-001 --json`.

`--json` is accepted on either side of any experiment subcommand. Nonstream replies are
`{"schema_version":1,"ok":true,"data":...,"error":null}`; errors carry stable
`code`, `message`, `recovery`, `details` and a nonzero process exit. Exit 2 means invalid
invocation/definition; exit 1 means runtime/state/control failure; exit 130 closes a viewer.
Streaming logs use versioned JSONL frames, ending with `follow_end`. Check both process
exit and envelope. No experiment command opens an installer, sign-in prompt or approval UI.

## Conflicts and recovery

- Give **each intended mutation** its own stable `--request-id`. Retry an identical request
  with the same key after a lost acknowledgement. Different contents with that key fail
  `REQUEST_CONFLICT`. Keys are shared across init, study submission and control operations.
  `status --request-id KEY --json` recovers the accepted response. Starter/delete retries
  retain their planned files/cleanup manifest. Never overwrite user-edited starter files.
- If selection returns `SELECTION_CONFLICT`, read the current pointer/revision, explain the
  existing choice, then use a new key and current revision only if replacing it is intended.
  Do not automatically fight another client by repeatedly incrementing revisions.
- `stop STUDY_OR_TRIAL_ID --request-id stop-001 --json` saves cancellation before further
  claims. Check `termination_confirmed` and unresolved IDs. Stop is independent of selection.
- For `dispatch.state == recovery_required`, inspect the reason and run
  `recover STUDY_ID --request-id recovery-001 --json` after restoring Docker/disk access.
  Recovery retains evidence and does not replay uncertain calls. Missing containers and
  old heartbeats do not prove an attempt never ran.
- Fix host ChatGPT sign-in for `AUTH_REQUIRED`, wait for allowance/capacity for `THROTTLED`,
  or create a new study for changed model/effort/recipe/resource limits. An explicit
  `retry TRIAL_ID --request-id retry-001 --json` creates a new attempt with frozen inputs
  and a fresh native conversation. Do not retry provider failures indefinitely.
- `delete STUDY_ID --json` previews terminal-state cleanup. `delete STUDY_ID --confirm
  --request-id delete-001 --json` performs it. Clear an existing selection first. Deletion
  removes saved files, native histories and owned checkpoints; preserve desired evidence first.

## Continue a saved result

`invariant experiment continue STUDY_ID --prompt-file next-task.txt --request-id child-001 --json`
uses the selected attempt. An explicit trial/attempt ID is also accepted; multiple attempts
require an explicit attempt ID. This creates and dispatches one child trial, with a fresh
conversation and immutable parent link. Add `--create-only` to freeze without execution.
Use `--wait` to observe completion. Parent/sibling files, histories and images are kept.
The new task must be a nonempty project-relative UTF-8 file. Its bytes are frozen; later
edits do not change the task. Child settings, input/evaluator source and network/resource
policy inherit the parent's frozen settings. Patches/fixtures are already present and are
not reset. Command harnesses read `INVARIANT_PROMPT_FILE`; this is not a shell command.
Installed Linux tools and the complete private workspace survive; no running process or
native conversation is resumed. Inspect `data.frozen.parent` for provenance.

## Diff → review plan → explicit apply

1. `invariant experiment diff STUDY_ID --json` describes the selected saved result's changes.
   An explicit trial/attempt ID works too. Review file metadata, text diffs, patch provenance,
   protected/excluded paths and the environment image. No project file is changed.
2. `invariant experiment apply STUDY_ID --dry-run --request-id review-001 --json` saves a
   destination-bound plan. Inspect **all** `data.changes`, `data.conflicts`, `data.excluded`,
   `data.destination_revision`, `data.selection_revision`, and `data.plan_id`. A successful
   preview can have `state:conflicted`; it does not authorize or perform writes.
3. Only when the user intends that exact application, invoke
   `invariant experiment apply --plan PLAN_ID --request-id apply-001 --json`.
   A tool caller's explicit invocation is the same action as a human's invocation. Selection,
   continuation, inspection and a preview alone never authorize project writes.
4. Check `data.applied`. Repeating a committed plan returns its original result without
   writing again. Request IDs also prevent duplicate effects after a lost acknowledgement.

Plans check every affected destination path before any write and check each path again
immediately before replacement. Conflicts and changed selection/destination revisions
fail; create and review a new plan rather than silently overwriting/merging. Unrelated
project edits remain untouched. Apply writes project files only; installed packages and
ordinary project environment/checkpoint are never replaced. Safe relative links and
executable modes are supported. Git/INVARIANT controls and credential paths are excluded.

A child's diff uses its own parent workspace as its baseline. Apply ancestors first if
the original project has not yet received their changes. Do not silently combine trials.

## Interrupted apply and recovery

Use `invariant experiment apply --recover --request-id restore-001 --json` to reconcile
an interrupted apply. Managed writes are rolled back unless the durable commit was already
recorded. New managed project mutations acquire the project lease and reconcile first.
Read-only validation/capture/preview returns `APPLY_RECOVERY_REQUIRED` without modifying
files; invoke explicit recovery before trying it again. Inspection and selection remain usable.

If recovery reports `APPLY_RECOVERY_BLOCKED`, keep the external edit intact. Read the
returned journal/backup locations and blocked paths. Restore the conflicting path to its
recorded before/after **content and mode**, or ask the user to resolve it; then repeat recovery
with a new request ID. Do not remove the journal/active marker or force-copy over external
changes. A rolled-back plan returns `PLAN_RECOVERED`; create a new reviewed plan and key.
External editors do not participate in the lock. The documented small race between the
last check and replacement remains; never claim a multi-file OS transaction.
