# MVP acceptance

This is the **legacy broker/workspace acceptance guide**. Cases A01–A20 refer to
[the archived contract, section 9](LEGACY_MVP_CONTRACT.md#9-required-acceptance-evidence), not
the new [parallel-experiment contract](../MVP_CONTRACT.md). Current report emitters
name the archived contract; older saved reports may still label that historical target
`MVP_CONTRACT.md section 9`. Historical evidence is not rewritten. A passing legacy report
does not establish acceptance of the experiment MVP. Its P01–P21 cases are defined
in the current contract and scheduled in the [implementation plan](IMPLEMENTATION_PLAN.md).

Run the complete acceptance gate from the project directory:

```sh
./scripts/invariant acceptance
```

Requirements: Rust/Cargo, Python 3, Docker CLI, and a running local Linux Docker
daemon or Docker Desktop using Linux containers. The supervisor supports the
documented rootful cgroup v2 profile on arm64/amd64. Leave the normal INVARIANT
launcher idle: the existing operator and restart regression scripts require
exclusive use of its normal state volume. The A01–A20 suite itself uses a fresh,
separate volume and does not resume an existing execution.

The command runs formatting, strict linting, all Rust and Python tests, rebuilds
both pinned-base images, executes all 20 cases, and runs the existing Docker,
operator and lifecycle regression scenarios. It also verifies the guided demo
and copies its report and public evidence into the acceptance bundle under
`guided-demo/`. Any required failure returns a nonzero exit code. A partial run
cannot count as acceptance. `invariant test`
remains the shorter Docker smoke test; it does not replace this gate.

## Evidence

Each invocation creates `.invariant/acceptance/<UTC timestamp>-<random suffix>/`:

| File | Evidence |
|---|---|
| `REPORT.md` | Human-readable result and A01–A20 checklist |
| `summary.json` | Overall gate, including supporting checks and cleanup |
| `acceptance.json` | All contract cases, setup, expectation, actual observations and failures |
| `A01/case.json` … `A20/case.json` | Individual case results |
| `*-target.jsonl` | Independent target receipts, durably recorded before returning a response |
| `*-audit.jsonl`, `audit.jsonl` | Broker decisions, durable admissions, attempts and outcomes |
| `configuration.json`, `isolation.json` | Platform/runtime/image identity, actual mounts, namespaces and limits |
| `commands.json`, numbered `.log` files | Commands, exit codes, elapsed times and complete output |
| `source-sha256.json` | Tested source and contract hashes |

`.invariant/acceptance/latest.json` points to the most recent overall result,
including a failed run. Always check `summary.json` for the overall gate: the
20-case matrix can finish successfully while a later regression or cleanup
check fails. A run that stops before the matrix starts has command logs and an
overall failure report, without fabricated case results.

The exporter copies a fixed whitelist of public evidence. Capability files,
staged Python sources, sockets, policy locks and crash leftovers are excluded.
Evidence files and directories use owner-only permissions. The runner removes
its controller, dedicated volume and any of its remaining workload containers;
it does not prune unrelated Docker resources.

The mock logs **every** received route, including forbidden operations. A03
first performs one explicit direct `DELETE` to prove that the forbidden target
operation exists, then checks that every broker probe adds zero receipts. That
baseline request is identified separately in the case observations.

## Contract coverage

| Case | Exercise |
|---|---|
| A01 | Two different invoice summaries, including a client written directly against the wire protocol, in real Docker runs |
| A02 | Missing/forged/cross-run capabilities and caller-supplied identities across two simultaneously live Unix listeners |
| A03 | Methods, routes, encoding/query/URL variations, bodies, headers, unknown/duplicate fields |
| A04 | Redirect source and destination with separate target logs |
| A05 | Simultaneous Docker executions, network/DNS/host/file probes and another live execution's socket |
| A06 | 30 barrier-synchronized distinct requests, exactly 20 admissions and 10 exhausted replies |
| A07 | Reconnection and child Python processes share one exhausted grant |
| A08 | Duplicate requests while the first response is held; changed fields and grant conflicts |
| A09 | Revocation commits while a request is gated before admission |
| A10 | A target-received request completes after revocation; later admission denied |
| A11 | Exact trusted-clock expiry boundaries and backward wall-clock adjustment |
| A12 | Failures before admission, after sync, before send and after target receipt |
| A13 | Journal errors and unavailable authoritative state |
| A14 | Real controller crash with an unfinished request, orphan removal, fresh identity, closed old channel and rejected old handle |
| A15 | Frozen resource configuration and changed target/session |
| A16 | Frame/response/connection bounds plus Docker disk, tasks, memory, output, duration and descriptor limits |
| A17 | Stop with a connected socket and attempted workload grant renewal |
| A18 | Correlation of allowed, denied, failed and unknown requests; protected journal and inert output |
| A19 | One unchanged Python process observes operator issue/revoke through the protected control channel |
| A20 | Unknown/unsupported grant constraints, wrong execution and a second active grant |

Clock and journal fault fixtures use the existing trusted Rust library seams.
These fixtures are in the separate `invariant-acceptance` executable. The
production supervisor and workload protocol gain no fault-injection or policy
override options. Native broker fixtures exercise the same broker and journal
implementation used in Docker; sandbox claims require the real Docker cases.

The operator probe also has a deterministic test through the real Python SDK
and a scripted Unix peer. It covers a safe denial during capability delivery:
a request carrying the previous capability can be denied while a new grant is
being issued. The probe keeps waiting with new request IDs; it does not replay
the rejected operation or treat that denial as an authorized read.

Concurrency uses barriers, held target responses and durable journal/target
observations. Polling only waits for an observable state transition, with a
deadline; it does not assume that sleeping means an admission/revocation won a
race. Unknown outcomes retain their reservation and are never replayed.

Passing applies to the recorded execution profile and probes. It does not prove
the absence of container escapes or authorize production multi-tenant use. The
contract's exclusions continue to apply.
