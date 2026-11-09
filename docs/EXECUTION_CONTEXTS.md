# Reusable Docker execution contexts

`RunContext` is application infrastructure for sequencing jobs. Applications own
roles and dependencies; INVARIANT owns isolated workspaces, protected evaluation,
continuation snapshots, durable identifiers and export.

```python
from invariant.run_context import RunContext
from invariant.experiments.preflight import check_frozen

run = RunContext('/absolute/project', key='research-001')
study = run.submit('experiment.json', request_id='research-001-methods')
assert check_frozen(run.engine, study['id'], request_id='research-001-baseline')['ready']
run.execute(study['id'])
result = run.engine.results(study['id'])  # inspect, without file export
artifacts = run.collect()                # explicit, content-checked export
saved_ref = run.run_ref

# A later process can observe the same context, without reimporting sources.
restored = RunContext('/absolute/project', run_ref=saved_ref)
status = restored.status()
```

A second recipe can be submitted to the same context after adding its input
artifacts. Each study freezes its own source/evaluator; each attempt retains a
separate workspace and Codex home. A run key is idempotent and binds the project's
policy. Changing policy requires a new run context. Request keys still bind
individual immutable submissions, preventing duplicate model calls after a lost
acknowledgement. Existing experiment/task/chat continuation APIs remain available.

The CLI equivalents are `invariant context prepare --key KEY --json`, followed by
`context submit --ref RUN --input RECIPE --request-id KEY`, `context execute
--ref RUN --study STUDY`, `context status --ref RUN`, and `context collect --ref RUN`.
Existing commands and the default experiment Python client remain compatible.

## Persistent manager and storage

A trusted Python manager now serves requests over a mode-0600 Unix socket in its
Docker state volume. Docker exec launches only a thin socket client. The manager
keeps connections/caches across calls; a failed request does not restart it.
Maintenance runs in the same process. No control port is published. Agents still
receive neither the Docker socket nor host home/devices.

Model metadata is scoped by ChatGPT authentication, pinned Codex version and a
five-minute TTL. Account changes invalidate it; exact requested model/effort are
resolved from the cached advertised catalog and checked against thread startup.
No model turn is created to query metadata. Authentication remains in Docker.

Protected Codex/inspector executables are cached once per immutable image in
Docker-owned shared storage, mounted read-only into executors. Repeated accesses
check file identity/stat fingerprints; changed content is rehashed and corruption
fails closed. Existing project runtime copies and historical snapshots remain
intact. Mutable environment dependencies and agent homes remain task-specific.

Source synchronization compares content manifests and skips unchanged revisions.
Observation does not synchronize sources or deliver changes. Exports use a durable
Docker index and verify the existing host artifact inventory before reuse; external
artifact edits cause a fresh export, preserving the edited copy. Source application
continues to require the existing checks, review and conflict guards.

## Tools and resource permissions

New projects use `tool_profile: coding`. It selects a compact coding toolset
independently of CPU/RAM quotas and internet/package access. Use `invariant config
set tool_profile full` to request the broader tool profile. Saved configurations
without this field retain their legacy full-profile behavior. Resource and storage
boundaries remain separate. Job instructions and bounded workspace facts are sent
once as task input; the common developer prefix remains stable.

## Evidence

Numeric spans for manager operations, runtime preparation, catalog requests and
Codex RPC methods are retained in Docker under project `traces/`, and exported as
logs. Spans contain identifiers, durations and success state, never credentials or
request bodies. Native results include context character counts, configuration and
dynamic-tool hashes, initial provider input usage and reconciled response counters.
Existing validation receipts retain snapshot/evaluator timings and exact digests.

The repeated integral study is in
`examples/experiments/integration-team-comparison/results/v3/`. Its fixed acceptance
suite is unchanged (35 + 36 + 14 checks), and cold/warm execution reverses arm order.

## Container-internal handoff and bounded observation

Related studies can consume exact accepted files without exporting them to the
computer first. Recipes and other source inputs must already be present in the
project; only the explicitly mapped result files are overlaid into the frozen
baseline and, where declared, the evaluator inputs.

```python
methods = run.submit('methods.json', request_id='methods-001', sync=False)
run.execute(methods['id'])
rows = run.engine.results(methods['id'])['trials']
references = [
    {'attempt_id': row['attempt_id'],
     'result_digest': row['capture']['content_digest'],
     'files': {name: f"inputs/{row['variant_id']}/{name}"
               for name in ('solution.py', 'result.json')}}
    for row in rows
]
checker = run.submit('checker.json', request_id='checker-001',
                     result_inputs=references, sync=False)
run.execute(checker['id'])
artifacts = run.collect()  # export only when the application needs host artifacts
next_artifacts = run.collect(log_cursor=artifacts['log_cursor'])
run.close()
```

References bind to accepted snapshots and participate in the idempotency key.
Unverified, mismatched, excluded, linked or protected source files are rejected.
Destination ancestry is checked for links/traversal. Each receiving task has its
own frozen byte copy; a reference never gives it write access to another agent.
Existing recipes and calls without `result_inputs` retain their behavior.

`RunContext` uses one attached manager connection, closed explicitly or on process
exit. Connection loss never automatically replays a mutation: reopen the context
and reconcile its request ID. `status()` returns compact study/trial/attempt
identities and states in one request; `status(details=True)` retrieves full saved
information. Neither form synchronizes sources or applies changes.

Collection gets manifests and the durable export index together. A verified
unchanged staging bundle is reused; unchanged host artifacts are checked by their
byte inventory before reuse. External edits produce a separate fresh export. Log
cursors return changed files and deleted-path metadata; an expired/unknown cursor
returns a full snapshot. The complete logs remain in Docker, independent of delta
exports. The cursor revision index is bounded; it does not delete task history.

## Validated completion and preparation

Codex recipes can opt into `"runner": {"type": "codex", "completion": "validated"}`.
This requires a protected evaluator and no secret references. Legacy recipes use
`completion: message` behavior. On a passing `invariant_validate`, the coordinator
has already frozen the workspace and environment and independently stopped the
evaluator/executor. It requests the supported turn interruption before
acknowledging the tool call, records the actual server turn status, and accepts
the task by receipt instead of requiring a final model-written message. Failed
checks retain the existing repair loop. Provider usage remains observed and
reconciled, never replaced with estimated counts or a fabricated completed turn.

A successful receipt is revalidated and its immutable snapshot is atomically
published as the attempt's canonical snapshot. Revalidation hashes the stopped
workspace without copying it into another temporary tree. Publication records its
intent and final location; an interrupted publication retains both the receipt
and candidate and must be reconciled without replaying a model request. Complete
rootfs checkpoints, dependency directories and private agent histories remain
available to existing continuation APIs. No shared hard links or cross-attempt
writable snapshot references were introduced, so existing garbage-collection
ownership/reference rules still apply.

`RunContext.execute()` runs baseline evaluation by default and can overlap it with
agent/executor handshakes when resources are unlimited. A dispatch barrier blocks
model turns until readiness is confirmed. Configured resource limits force
sequential preparation to avoid expanding the budget. `preflight=False` retains
the lower-level execution behavior for applications that already own the gate.

An environment is reused by identity, without reinstalling dependencies for each
command. Python `.venv` directories stay at `/workspace/...` across continuation;
rootfs changes stay in Docker checkpoints. This is compatible with venv's fixed
interpreter paths, without treating a venv as a replacement for apt/npm/system
state. Installed environments are not disposable download caches.

Host publication is serialized per project across independent clients, covering
manifest lookup, copying and index acknowledgement together. A zero-byte lock in
the exported logs directory prevents concurrent unchanged collections from
publishing duplicate directories. It is local output metadata; no agent home,
configuration, credentials or dependency cache is stored there.
