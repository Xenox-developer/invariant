# Guided MVP demo

With Docker running, use one command from the project directory:

```sh
./scripts/invariant demo
```

The guide runs automatically. Use `--step` to pause before issuing and revoking
the grant, or `--auto` to explicitly select automatic mode. Step mode requires
an interactive terminal. Ctrl-C stops the demonstration and requests cleanup.

The first run prepares missing Docker images; progress is saved in `prepare.log`.
Python 3 and the Docker CLI are required on the host. The compiler runs inside
the image build. This uses the same supported local Linux Docker profile as the
[execution supervisor](DOCKER_EXECUTION.md).

## What you see

One Python program remains in the same isolated execution throughout:

1. **Start without permission.** Its invoice request is denied with `missing_grant`.
2. **Issue permission.** The guide uses the protected operator endpoint to issue
   `GET /invoices`, 20 reads, 15 minutes and no delegation.
3. **Use the allowance.** The program summarizes one invoice totaling 42, attempts
   a forbidden deletion, and uses all 20 reads. Deletion is denied with
   `out_of_scope`; read 21 is denied with `budget_exhausted`.
4. **Revoke and stop.** The guide revokes the grant. The same program observes
   `revoked`, including when repeating a previously admitted ID, then checks a
   new request. The operator stop command terminates the execution.
5. **Verify the evidence.** The guide compares 20 broker admissions with the
   independently maintained mock API counter, validates the audit sequence and
   program identity, and saves the report. The final summary is:

```text
VERIFIED: 1 invoice, total 42; exactly 20 API requests.
Audit: denied → granted → 20 reads → exhausted → revoked → stopped.
Demo complete. The isolated program has been removed.
```

Only mock data is used. The workload has the fixed Docker isolation profile;
it mounts its own broker socket and read-only capability delivery, without the
operator endpoint or Docker socket. Capability delivery can briefly produce a
safe `invalid_capability` denial; the program waits using new explicit request
IDs. Only explicit local capacity denials are retried with the same ID. Unknown
outcomes and transport failures abort the demonstration without automatic retry.

## Report and cleanup

Each invocation creates `.invariant/demo/<UTC timestamp>-<random suffix>/`.
The final path is printed in the terminal. `.invariant/demo/latest.json` records
the most recent result, including interrupted or failed runs.

| File | Contents |
|---|---|
| `REPORT.md` | Five stages, invoice summary and a readable audit timeline |
| `demo.json` | Verified facts, execution identity, source hash and cleanup result |
| `evidence/iv-*/audit.jsonl` | Full protected broker journal |
| `evidence/iv-*/configuration.json`, `isolation.json` | Actual Docker configuration and effective limits |
| `evidence/iv-*/result.json` | Final execution state and captured inert output |
| `run.log` | Captured supervisor output, including the trusted mock API counter |
| `commands.json`, `command-*.log` | Operator and Docker command results |

Each demo uses a new dedicated volume and controller, selected by its own owner
label. It can run separately from the normal launcher without issuing commands
to that launcher's execution. The guide exports only the supervisor's evidence
whitelist; capability delivery and staged source are excluded. Files and report
directories use owner-only permissions.

On normal completion or interruption, the guide removes its controller,
workload and temporary volume. It attempts to preserve partial evidence when a
run is interrupted after startup. Cleanup failures produce an incomplete result
instead of claiming success. A forced kill of the host guide or an unresponsive
Docker daemon can prevent immediate cleanup; the run retains the supervisor's
15-minute execution limit and the contract's recovery limitations.

This is a demonstration, not the complete acceptance gate. Run
`./scripts/invariant acceptance` for all A01–A20 cases and supporting regressions;
that command also runs the guided demo and includes its report in the bundle.
