# Three-agent numerical integration research

Two independent Codex agents implement composite trapezoid and Simpson methods
and compute integrals of x²/x⁴ on [0,1] for n=10,20,40,80. A third agent receives
their actual code/results, independently checks them, and creates CSV, Markdown,
verification JSON and an SVG error plot. Exact references are 1/3 and 1/5.

This is an application example. It does not extend or modify INVARIANT.
The existing native experiment interface runs the first two roles as parallel
variants, then a separate one-role study runs the checker after artifact handoff.
The baseline implements the same dependency sequence with stock Docker and Codex
CLI (`codex exec --json`). `plain_backend.py` imports no INVARIANT code and calls
no INVARIANT commands. Existing containerized Codex binaries and ChatGPT sign-in
are reused for bootstrap; agents compute from the same Linux image in both arms.

## Files

- `specification.py`: shared task/role instructions and experiment recipes.
- `fixed_checks.py`, `evaluate.py`: fixed acceptance checks (35+36+14 = 85).
- `run_experiment.py`: application-level dependency orchestration and measurements.
- `plain_backend.py`: independent Docker/Codex baseline and numeric usage capture.
- `analyze.py`: verification, token/credit accounting and comparison report.
- `results/core-before.json`: fingerprints taken before implementation/execution
  of this example. Existing dirty core changes are preserved and compared by bytes.

## Run

From the repository root, with Docker and the prepared Codex runtime available:

```sh
python3 -B examples/experiments/integration-team-comparison/run_experiment.py \
  --namespace your-unique-run --arm invariant
python3 -B examples/experiments/integration-team-comparison/run_experiment.py \
  --namespace your-unique-run --arm plain
python3 -B examples/experiments/integration-team-comparison/analyze.py your-unique-run
```

A completed arm is never silently replayed. Stable INVARIANT study keys preserve
receipts. Failed preparations/model calls must be inspected and accounted for;
do not delete a paid run and create a new key to hide a failure.

All agent homes and working copies remain in Docker volumes. Host files are the
example's source/input fixtures and exported results/logs. Baseline volumes are
retained after its containers exit; their names appear in its summary for later
inspection/continuation. No host Codex installation or new package is needed.
The baseline's ephemeral bootstrap helpers are not research/model agents.

## Measurement boundaries

Three real task agents run in each arm, not simulated numerical workers. The
provider model and effort are fixed to gpt-6.1-sol / medium, with Codex 0.163.0.
Wall time includes per-arm setup, dispatch, validation, handoff, report generation
and export. Shared prebuilt images and one-time example authoring are outside it.
The separate preparation failure, if any, is preserved and reported separately.

Credit estimates use published Codex Standard token rates, accounting for cached
input as a subset. They are not actual subscription debits or API-dollar costs.
Outer coordinating-chat usage is unavailable and is not invented. One fixed-order
pair cannot establish a general statistical performance ranking; native app-server
and plain CLI wrappers intentionally have different control/tool context overhead.
