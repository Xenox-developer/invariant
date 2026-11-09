# Durable task automation

The current checkout adds `invariant task` above the existing environment and
experiment interfaces. It moves ordering, progress and replay decisions into
the Docker coordinator. Existing experiments, frozen inputs and SQLite schema
are unchanged; new task records use a separate private version-1 format.

## Submit and observe

From the target project:

```sh
invariant task run --task "Implement the requested feature" \
  --test-command "python3 -B -m unittest discover -s tests" --test-path tests \
  --effort medium --request-id feature-001 --json
invariant task status --request-id feature-001 --json
```

One request performs host readiness → project/base preparation → declared
dependencies → definition → validation → baseline preflight → detached dispatch.
The default is one worker, medium effort and the saved project/account model.
New projects enable command internet access, so preparation and workers can
download dependencies without `--allow-network`. Existing offline policies remain
offline; the flag only overrides networking for preparation.
New environments default to `restrictions_enabled: false`, unlimited resources
and no workload deadlines or capture/log quotas. Container-only storage and the
absence of host home/socket/device access always apply. Enable optional limits
with `invariant config set restrictions_enabled true`. See [storage layout 2](DOCKER_STORAGE.md).
Use `--efforts low medium high` for an explicit comparison, or repeated
`--variant MODEL EFFORT` instead of model/effort flags. `--max-parallel` requests
concurrency subject to the existing Docker capacity policy.

Optional preparation inputs:

- Repeated `--package NAME` for Debian system packages.
- `--prepare-command "npm ci"` for explicit project setup.
- `--allow-network` for this preparation only; the saved network policy is
  never edited. It does not enable worker networking.
- `--preparation-timeout SECONDS` (default 0: disabled; finite values must fit any configured project deadline).
- `--test-timeout SECONDS` (default 0: disabled).

Package names and task inputs are validated before execution. Existing project
settings are preserved; restricted/legacy profiles are rejected for explicit
review. Missing Docker or a supported Codex is reported before worker submission.
The coordinator can build a missing base image, but never silently replaces a
saved environment. Host installation/sign-in retains the explicit setup flow.

`data.state` is `running`, `blocked` or `submitted`; inspect `active_stage`,
`completed`, `error` and `study_id`. An `ok: true` status can describe a blocked
task (exit 1). `submitted` means accepted execution, not passing tests. Use `task wait` for coordinator-owned waiting and `task review` for a bounded
review package. Review deterministically prepares selection and a concrete plan
for one result (comparisons require an explicit attempt). `task apply` requires
that reviewed plan’s digest. No result is applied automatically.

## Idempotency and interruption

Use a stable key per intended task. Retry with the same options and key after
a lost reply, or resume without repeating options:

```sh
invariant task run --request-id feature-001 --json
```

Intent is recorded before each stage, and acknowledgement after it. Concurrent
duplicates see existing progress. A project task lease serializes preparation
requests; the existing project locks still protect sessions, captures and apply.
Completed setup is not repeated. Definition/preflight/submission/dispatch use
stable stage keys in the existing durable engine. A crash after submission but
before acknowledgement cannot create a second worker on resumption.

An interrupted arbitrary preparation command may have had effects. Neither an
old PID nor a missing container proves otherwise. `PREPARATION_UNCERTAIN` blocks
automatic replay; inspect logs and partial effects, recover the environment if
needed, then explicitly request `--retry-preparation` within the authorized task.
This flag never retries a model attempt. Failed evaluations and provider errors
stay in the existing attempt history; there is no blanket "retry everything".
Changed task settings or fixed-test inputs need a new request/setup.

## Safe acquisition retries

```sh
invariant environment packages --package cmake --package libarchive-dev \
  --allow-network --download-retries 3 --json
```

The typed helper updates package indexes with strict error handling, downloads
packages, then installs once with `--no-download`. Only acquisition phases retry
recognized transient HTTP statuses (408/429/500/502/503/504) or network failures,
with bounded exponential delays and an overall preparation timeout. Permanent
lookup/integrity errors and installation/maintainer failures stop immediately.
The generic `environment prepare --command` never replays arbitrary commands.
Package/setup effects are checkpointed even on failure; this is not rollback.

## Resource and measurement contracts

```sh
invariant environment budget --json
invariant environment budget --container CONTAINER_NAME --json
invariant compare measurements.json --json
```

Budget returns normalized limits, Docker arguments and expected HostConfig
values. The same function is used by workload creation and verification,
including memory-plus-swap, process/nofile limits and scratch. Both command and
native runner evidence now includes the contract and observed HostConfig subset.
Unlimited contracts explicitly record absent quotas with null limit values and
verify that Docker applies no CPU, memory or process quota. They describe shared
host capacity, not a reserved per-trial allocation.
No container credentials or environment-variable values are exported by it.

The comparison file is an array of two run records. Each contains:

- `state: "completed"`, `passed: true`, positive finite `wall_seconds`.
- Observed `model` and `effort`.
- `contract` with the common SHA-256 `task_digest`, `model`, `effort`, immutable
  `image` ID (`sha256:…`) and `resources` from the budget interface.
- `host_config`, the observed subset corresponding to the contract.
- `calls`: all measured manager/worker calls, each with unique `call_id` and
  final provider `usage`. CLI counters and INVARIANT's `{total, last}` counters
  are supported. Missing `calls`/counters are unknown; an explicit empty list
  means no model calls occurred.

Both evaluations must pass, contracts must match and actual limits must verify.
Otherwise `comparable: false`, exit 1 and null ratios prevent a faster failure
from being declared a speedup. Usage evidence is retained. Duplicate call IDs
are rejected. Cached input and reasoning output are subsets and never added
again. Common unmetered setup must be disclosed separately. These are recorded
measurements, not attestations of adversarial workloads.

## Validation

Regression tests cover concurrent duplicate requests, interrupted setup, lost
dispatch acknowledgements, failures before paid calls, invalid package inputs,
resource drift and token accounting. A local HTTP fixture can return two 503s
before serving a tiny package; success requires three downloads and exactly one
maintainer invocation. Docker smoke tests exercise real preparation and red
baseline preflight while stopping deliberately before worker submission.

## Snapshot, delivery and bounded model responses

`workspace.include/exclude` still govern capture. New `workspace.apply` has
independent `include`, `exclude` and `respect_gitignore` rules. Guided definitions
freeze baseline ignore files for delivery; worker changes to ignore files cannot
widen that scope. Legacy definitions retain previous delivery behavior. All saved
workspace files, including ignored build caches, remain in continuations. No Git
installation is required. Ignore matching supports nested files, anchored and
directory patterns, negation, escaping, character classes and recursive globs;
parent directories must be re-included before children, as with Git.

To refresh a conflicted or stale review, increment `--review-revision N`; repeat
that same revision after a lost response.

Task replies are compact by default. Complete state remains at `record_path`;
`--details` is an explicit diagnostic escape hatch. `task run --wait` and
`task continue --wait` submit first, then observe. `task wait --timeout N` never
creates or retries a worker and stopping the observer never stops the workload.

`task review --request-id KEY [--attempt ID]` returns a bounded patch preview,
conflicts, excluded count, full plan path and digest. It never infers a comparison
winner. Binary, large or truncated changes require full artifact inspection.
`task apply --request-id KEY --review-digest DIGEST [--reviewed-full]` binds the
decision to the exact plan; existing destination/selection/ancestry guards and
journaled rollback remain authoritative. Lost acknowledgements reuse durable
operation keys. Conflict evidence is retained, and host caches are not rewritten.

`task continue --request-id CHILD_KEY --parent ATTEMPT --task TEXT --wait --json`
inherits the exact snapshot, environment and evaluator in a fresh conversation,
without rebuilding preparation. All task usage includes every worker attempt;
outer manager counters are supplied by the caller for full accounting.

## Program-owned runtime and semantic decisions

`invariant task execute --input .invariant/job.json --request-id KEY --apply
--timeout 900 --json` runs the same `Coordinator.execute` interface available to
embedded tool hosts. The input has `operation: run` and `options` with the ordinary
task fields, or `operation: continue`, `parent`, `task`, and optional `evaluation`.
Evaluation accepts a new command, fixed test paths, timeout and structured report
path. It is frozen into a new child while preserving the parent's exact workspace,
image and original evidence. Its baseline preflight uses that saved workspace.

The coordinator owns setup, waiting, evidence and application. Only after a
passing terminal result does a fresh bounded reviewer receive task, complete
patch/plan and execution facts. It inherits the selected model/effort without
fallback. No semantic model is called during the coordinator's waiting loop.
An embedding tool host should await the runtime rather than wake its model to
poll CLI sessions; a shell-only client still needs its own process await facility.
Comparisons require an explicit choice, and oversized/binary changes or conflicts
return to full explicit review. The caller's --apply flag is independent of the
model's approval, and both are required for automatic guarded writes.

Reviewer intent, thread/turn identity, decision and usage are durable. A lost
response is recovered by reading the retained completed turn, never starting a
new one. Uncertain completion remains REVIEW_UNCERTAIN. Completed requests reuse
the same decision and apply plan. Host apply verifies written entries and fixed
inputs under the existing mutation lease before committing its journal; conflict
recovery preserves external changes. Receipts bind evaluation version, task and
snapshot digests, structured checks, resources, decision and application proof.

`test_report` declares a project-relative JSON report written by the fixed test
command, with tests/failures/errors/passed. Stale reports are removed first and
counts must agree with exit status. Failure events may outnumber test methods,
as in unittest subtests. Missing/invalid reports produce unknown quality. Generic
commands without this adapter retain their exit verdict and unknown test counts.

Native workers and semantic reviewers save per-response provider observations
(total/last) separately from final session totals. Increasing observations count
as one response only when last equals the cumulative delta; duplicate events
are ignored and gaps stay explicit. Reports reconcile every observed response
against final counters. Summing sessions and their responses would double count.
The ordinary direct CLI's persistent rollout token_count events can be analyzed
through the same module. Session counts and individual response counts are distinct.

Baseline preflight now runs the entire captured evaluator without a dummy worker
or baseline Docker commit. The coordinator publishes one prepared snapshot and
reuses it through validation/preflight/dispatch. Only an identical frozen baseline
can reuse a completed readiness result; a changed image, input or resource policy
requires a fresh evaluation. Control executables are cached by immutable image,
while evaluator inputs remain baseline-specific.

Native workers without secret references receive `invariant_validate`, an
argument-free host tool bound to the current thread/turn. It freezes the worker,
durably snapshots its complete workspace and Linux filesystem, then evaluates
that candidate with protected inputs in a disposable environment under the same
resource limits. Memory and PIDs are partitioned between the paused worker and
evaluator so their sum stays within the original job budget; the paused worker
uses no CPU. Its tool connection stays open. Red checks return bounded feedback,
restore the original limits and resume the worker in the same turn/deadline. Passing checks stop the
worker stopped; the model finishes with a final message. Test databases and test
caches belong to the disposable evaluator rather than the saved worker image.

The coordinator accepts a receipt only if its frozen definition, workspace,
checkpoint ownership and stopped container lifecycle still match. It publishes
the saved candidate without another Docker commit or full test run. Unknown or
interrupted evaluations do not authorize reuse; lost acknowledgements retain
their journals without automatic execution replay. Custom/secret-bearing workers
and omitted/invalidated validations retain the independent post-run evaluator.
Review, conflict handling, ancestor ordering and verified host application remain
unchanged. Preparation stages and snapshot creation record separate timings.

Semantic review has a separate `ReviewInput`: complete requirements and patches,
the selected attempt's protected test scope/verdict, and verified resource status.
Private paths, accounting and integrity records remain in the coordinator. The
reply schema is stable; the coordinator binds each response to the exact plan
and evidence in its private journal. Legacy digest-bearing decisions remain
recoverable; missing or mismatched bindings never authorize application.
Native execution also keeps a durable hash-bound command-completion journal.
Compact review includes complete bounded command witnesses and their actual
exit codes, preserving proof of operational steps without duplicating large
source-writing scripts. Omission counts remain explicit; worker claims never
substitute for missing execution evidence.

Review threads use a stable private home/cwd with a new history per decision and
coding/discovery features disabled. Native implementation threads retain the
model's supported tool transport; the host's default script-yield deadline spans
the job budget so an awaited validation suspends inference until its final result.
Disabling Code Mode's host does not switch Code Mode-only models to direct tools.
Workers receive a bounded manifest-derived file inventory and
probed command availability, retaining access to the complete workspace and tests.

Single `task execute` jobs apply after passing configured checks and semantic review by default.
Use `--no-apply` to export only. Comparison jobs never select/apply a winner.
