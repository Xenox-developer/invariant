# Operator controls

Grant and revoke invoice access while the same isolated Python program runs.
The operator interface is separate from the workload's effect socket.

## Try it

Build updated images once with `./scripts/invariant prepare`.
In the first terminal:

```sh
./scripts/invariant run examples/live_grants.py --no-grant
```

The supervisor prints the execution ID and announces that controls are ready.
The program initially has no grant. In a second terminal:

```sh
./scripts/invariant status
./scripts/invariant grant EXECUTION_ID
./scripts/invariant status EXECUTION_ID
./scripts/invariant revoke EXECUTION_ID
```

Replace `EXECUTION_ID` with the full `run-...` value. After granting, wait until
status shows at least one admission before revoking. The program finishes after
observing revocation and displays its captured output:

```text
Before grant: denied (missing_grant)
After grant: invoice total 42
After revocation: denied (revoked)
```

Each probe is an explicit new call. The client never retries or automatically
replays denied requests. No Python restart or code change is needed. Workload
output stays captured until termination; it cannot inject operator commands.

## Commands and guarantees

| Command | Behavior |
|---|---|
| `status [EXECUTION_ID]` | Show health, fixed resource, grant state, expiry, remaining allowance and total admissions |
| `grant EXECUTION_ID` | Explicitly issue the fixed grant and deliver a fresh capability |
| `revoke EXECUTION_ID` | Durably revoke the current grant against the admission lock |
| `stop EXECUTION_ID` | Commit execution termination; the supervisor then removes the container |

Add `--json` for structured output. Every mutation requires an exact execution ID;
stale or mismatched IDs fail. Only read-only status can discover a run without one.
Ctrl-C in the run terminal also requests cancellation.

A grant permits only `GET /invoices` on the execution's immutable mock resource,
with 20 admissions, 15 minutes and no delegation. Other budgets, scopes, resources,
expiry and policy fields are unsupported. A second active grant is rejected.
Replacing a revoked, expired or exhausted grant requires an explicit new `grant`
command. It creates a new grant ID and capability. Reconnecting or reading a file
never renews authority. Replacement neither extends the execution deadline nor
forgets previously admitted request IDs. Status distinguishes the current grant's
count from the execution's cumulative admissions.

`run PROGRAM` retains its explicit initial grant for the quick example; use
`--no-grant` for operator-managed access. Successful revocation means the journal
record is durable. Earlier admissions may still finish; later admissions fail.
Successful stop means new admissions have stopped. Wait for the original run's
final report to confirm container removal.

Commands never retry automatically. If a reply is lost or times out, inspect
status before choosing another action: issuance may already have committed.

## Isolation and capability delivery

The launcher finds the single matching trusted control container and runs the
Rust operator client through `docker exec`. Docker access is the local operator's
existing trusted authority; no remote interface or additional user authentication
is introduced. The control endpoint is `/state/operator.sock`, mode `0600`, under
the supervisor's private `0700` state directory. Each listener is bound to one
broker execution. The protocol accepts 1 KiB length-prefixed JSON requests,
16 KiB replies and two-second total frame I/O deadlines. Unknown fields, duplicate
fields, unsupported actions and missing mutation targets fail. One control worker
serializes mutations and capability publication. Broker admission still uses its
own shared serialization lock.

The workload receives a read-only `/authority` directory containing only its
current capability. The directory is mode `0500`; the file is mode `0400`, both
assigned to workload UID 65532. The host parent stays private. Atomic replacement
through this directory mount lets an existing client see a new handle without
restarting. Operator replies and normal command output never contain the handle.

`BrokerClient.from_env()` uses `INVARIANT_CAPABILITY_FILE` when supplied. Before
each explicit request it reads at most 257 bytes and enforces the 256-byte bound.
Invalid or missing delivery fails before connecting, with no fallback or retry.
Static `INVARIANT_CAPABILITY` remains supported by the host-side examples.

The file conveys no authority without a matching server-side grant. Issuance is
durably committed before publication. Delivery failure marks the control server
failed, stops the broker and terminates execution. Old capabilities cannot use
replacement grants. The workload mounts neither `/state`, the operator socket nor
the Docker socket. Operator messages sent to its effect socket are rejected.

A request can already carry the previous handle when the operator changes the
grant. The broker safely returns `invalid_capability` for that request. The live
demo keeps waiting with a new explicit probe and request ID; the SDK does not
automatically retry or replay the denial.

The control endpoint closes at termination. Restart cleanup removes stale
workloads and the old operator socket before a fresh execution can start.
New commands must still match the new execution ID.

## Verification

```sh
cargo test --workspace --all-targets --offline
PYTHONPATH=sdk/python python3 -B -m unittest discover -s sdk/python/tests -v
./scripts/invariant test
python3 -B scripts/test_operator_controls.py
python3 -B scripts/test_supervisor_lifecycle.py
./scripts/invariant evidence
```

The operator Docker test synchronizes on audit records and status. It verifies
missing-grant → allowed → revoked in one Python client, explicit replacement,
capability rotation, old-ID preservation, wrong-target and unsupported-constraint
rejection, the 20-read limit, protected delivery and stop/cleanup. Its target count
must be 21: one first-grant read plus 20 replacement reads.

Results are saved in `.invariant/operator-controls.json`. Per-run broker journals
retain grant issuance/rejection/revocation, admissions and termination without
capability handles. The evidence command exports these journals and the inspected
Docker profile to `.invariant/docker-evidence/`. Exports copy only known evidence
files and omit capability directories and staged program source, including during
a live run.
