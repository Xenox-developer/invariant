# Job protocol

## Ordinary coding task

Create a private project-relative JSON file, for example `.invariant/job.json`:

```json
{
  "operation": "run",
  "options": {
    "task": "Implement the requested behavior",
    "test_command": "python3 -B -m unittest discover -s tests",
    "test_paths": ["tests"],
    "effort": "medium"
  }
}
```

Use the actual task, fixed test paths and commands. Then:

```sh
invariant task execute --input .invariant/job.json --request-id feature-001 \
  --apply --timeout 900 --json
```

Omit `--apply` for inspection/comparison alone. Use repeated-job stable IDs, never
a new ID to recover a missing acknowledgement. Options accept `packages` (Debian
package names), `prepare_command`, `allow_network`, `preparation_timeout` (600s),
`test_timeout` (120s), `apply_include`/`apply_exclude`, and explicit `model`.
Network permission is per preparation call. Existing settings are preserved.
Known download failures have bounded acquisition retries; installation scripts,
arbitrary commands and uncertain paid calls are not blindly repeated.

If the test tool writes a JSON report with `tests`, `failures`, `errors` and
`passed`, specify `test_report` and the corresponding command output argument.
The report path must be outside protected state and fixed test roots. Example:
`"test_command": "python3 tests/check.py --output check-report.json"`,
`"test_report": "check-report.json"`. Old reports are removed before evaluation;
counts must be nonnegative and agree with the exit code. Missing/invalid reports
produce unknown quality. Other test formats need an explicit adapter; without a
report the command's verdict is available but test counts remain unknown.

The code coordinator performs readiness, initialization without settings reset,
dependencies, definition validation, red-baseline preflight, worker dispatch and
waiting. Failed/unknown evaluation never leads to automatic review/application.
A valid red baseline is allowed; missing tools/timeouts/unknown evaluation block
readiness. A bounded semantic reviewer runs only after terminal passing evidence,
using the selected model/effort. Its decision and token usage are saved. The model
cannot authorize host writes beyond the caller's `--apply` flag.

Responses include `execution.receipts` (frozen evaluation version, command,
structured checks, snapshot digests, resource evidence and artifact paths),
`decision`, `application.verification`, and combined worker + reviewer `usage`.
`application.applied` plus successful verification confirms delivery at its saved
timestamp. Do not repeat hashing, Docker setup or log discovery with model tools.
The semantic review still checks the actual diff against the task.

The reviewer uses a stable private runtime with a fresh thread per decision,
the requested worker model/effort, and no coding/discovery capabilities. It gets
the complete task and complete patch plus the selected attempt's verified check
scope. Other attempts and full integrity/accounting records remain in artifacts.
The coordinator binds its response to the exact plan/evidence digest outside
the model input, then rechecks the guarded application. This compact input does
not authorize shortened patches, omitted requirements or automatic approval.
Compact evidence must retain operational proof too: native workers record
executor command-completion events in a private hash-bound journal. The reviewer
receives a bounded selection of complete short commands with their actual exit
codes, plus explicit omission counts. Worker assertions are not execution proof;
missing operational evidence still blocks approval. Large source-writing
commands stay in the private journal rather than duplicating the full patch.

Native workers receive a bounded file inventory and probed command
availability. They may inspect any needed implementation or fixed-test source;
the inventory is not a substitute for reading code. Validation is a blocking
tool response. Keep the runtime's supported tool transport: disabling Code Mode's
host does not expose direct tools for Code Mode-only models. In exec, await the
validation with the configured default yield deadline; do not request early
yielding or poll it through model turns.

For paired measurements, use a run-scoped namespace for every external resource
and verify it is free before dispatch. Include automatically created permission
review/model child sessions in the ledger; a parent session counter alone is
not full accounting. For already-authorized isolated workloads, configure the
comparison harness to avoid an additional model-based permission loop. Keep
semantic review and all protected evaluations unchanged. Preserve failed runs
and report their spending separately from the measured pair.

Baseline readiness runs the complete protected evaluator directly on frozen
inputs, without a dummy implementation worker or a saved baseline environment.
The prepared candidate is captured once and reused by validation and dispatch;
launch still checks the live account, requested model/effort and resource limits.

The implementation worker calls `invariant_validate` for its full shared suite.
First complete required persistent-state changes and stop application services
cleanly. The coordinator captures a durable candidate and runs protected tests
in a disposable copy; test databases/caches do not inflate the saved worker
environment. The worker is paused during this evaluation, preserving its tool
connection. Its memory/PID reservation and the evaluator's allocation together
stay within the original job budget; only the evaluator consumes CPU. A red
result restores the worker's full limits and resumes the same connection for
repairs within the existing budget. A passing result
freezes that candidate: finish the conversation without more changes.

`execution.receipts[].evaluation.reused_validation` identifies reuse. Its private
receipt binds the complete workspace, environment image, evaluator, test inputs,
command, seed and resource policy. Changed files or a resumed environment require
fresh validation. A worker-written report cannot authorize reuse. Missing,
interrupted or unknown validation never becomes a pass or an automatic replay.
Custom runners, secret-bearing studies and workers that omit the tool retain
the independent post-run evaluator. No acceptance tests or semantic review are
removed by this optimization; application and continuation use the exact saved
candidate. All worker repair responses and reviewer usage remain included.

Tool hosts can await `invariant.task_runtime.Coordinator.execute(...)` directly;
no model is invoked while it waits. A CLI client may expose a running shell
session: await that same process without reconstructing its stages. The CLI alone
cannot suspend the surrounding application's model loop; use its await mechanism.
Timeout/closing observation does not prove that a worker/reviewer did not run.

## Continuation with its own evaluation

```json
{
  "operation": "continue",
  "parent": "attempt-PARENT_ID",
  "task": "Implement the requested follow-up",
  "evaluation": {
    "test_command": "STAGE=2 python3 tests/check.py --output check-report.json",
    "test_report": "check-report.json"
  }
}
```

Execute with a fresh request ID. This reuses the exact parent snapshot/tools and
starts a fresh worker conversation. Evaluation overrides accept `test_command`,
`test_paths`, `test_timeout`, `test_report`. New fixed tests are captured before
execution; parent evidence stays immutable. The child runs baseline preflight
against its saved workspace, never a new host snapshot. Without overrides it
inherits the original evaluator; make sure it covers the follow-up. Changing
model/policy still requires a new study. Apply ancestors before children.

## Comparisons and explicit review

Ordinary options create one worker. For requested comparisons replace `effort`
with `efforts`, or use `variants` as `[MODEL, EFFORT]` pairs instead of model/effort
flags. `max_parallel` is optional. Each worker consumes its own usage. Execute
returns multiple results without choosing a winner. Choose based on quality,
time and complete usage, then use the explicit interfaces:

```sh
invariant task review --request-id KEY --attempt ATTEMPT_ID --json
invariant task apply --request-id KEY --review-digest DIGEST --json
```

A conflict or `requires_full_review` requires explicit inspection. Read the full
plan at `full_review_path` and actual content at `snapshot_path`/`desired_files_path`.
Only after inspection use `--reviewed-full`. For a fresh plan after resolving a
conflict, increment `--review-revision N`, keeping N stable on lost replies.
Never copy snapshot caches onto the host or discard conflict journals.

## Recovery and accounting

- Repeat execute with the same input/key after a lost response. Completed stages,
  reviews and applications replay saved results without another model turn.
- `REQUEST_CONFLICT`: the key binds different content. Use a new key only for a
  genuinely new task. `REVIEW_UNCERTAIN` retains thread/turn and usage paths;
  completed retained review turns are recovered read-only. No automatic replay
  occurs when completion cannot be established.
- `blocked`/unknown quality: inspect the returned stage/error and its evidence
  path. For interrupted preparation inspect partial effects, recover if needed,
  then explicitly `task run --request-id KEY --retry-preparation --json` within
  existing authorization. This flag never retries model attempts.
- Unsupported model/effort/auth/throttling: restore the requested capability or
  report the blocker; never silently switch settings or loop over paid retries.
- Owned execution: `experiment stop STUDY --request-id STOP_KEY --json`, or
  `experiment recover STUDY --request-id RECOVERY_KEY --json`. Verify termination;
  recovery does not replay uncertain calls. Explicit justified retries use
  `experiment retry TRIAL --request-id RETRY_KEY --json`; retain all usage/history.
- Interrupted apply: `experiment apply --recover --request-id KEY --json`.
  Preserve blocked external edits and journals; a rolled-back plan needs a new
  review. Never delete markers to force progress.

All available per-response provider observations are saved separately and
reconciled with final session counters. Duplicate notifications are not extra
calls; missing/mismatched observations remain explicit gaps. Final session totals
already include their individual responses: never add both totals and responses.
Outer caller usage must also be included in a full comparison when available;
unknown counters stay unknown. Shared unmetered setup must be disclosed.

`environment budget --json` emits the common resource contract;
`environment budget --container NAME --json` verifies limits. `invariant compare
MEASUREMENTS.json --json` requires completed passing runs with matching task,
model/effort, starting image and observed resources. A faster failure is not a
speedup. `experiment init/validate/preflight/run` remain available for custom
recipes; low-level init defaults to a comparison, so one worker needs explicit
`--efforts medium`. Full lower-level responses can be large: save and inspect
specific artifacts instead of repeatedly injecting them into model context.

Exit 2 is invalid input, 1 runtime/state failure, 130 interrupted observation.
JSON commands never open installers or sign-in dialogs. `task status --json` is
compact; `--details` is only for targeted diagnostics.
