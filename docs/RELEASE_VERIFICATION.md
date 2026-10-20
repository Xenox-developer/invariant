# MVP release verification — P21 complete

Verified October 21, 2026 (Europe/Moscow; evidence timestamps use UTC).

**Result: P01–P21 passed.** The combined report has `accepted: true`, all 18 stages
passed, no blockers, and unchanged verified source. Its complete imported Linux
bundle is retained alongside the macOS evidence.

## Source and evidence

- Tested source commit: [`bcaf39dc1e57b59252522db14203c7c118d0cb23`](https://github.com/Xenox-developer/invariant/commit/bcaf39dc1e57b59252522db14203c7c118d0cb23).
- Source manifest digest: `81d6853154929b37a9ccd3b5e241b238e57a6c8aa5fb223873fda896a68208fb`.
- [Successful Linux GitHub Actions run](https://github.com/Xenox-developer/invariant/actions/runs/39511667205).
- [Linux artifact](https://github.com/Xenox-developer/invariant/actions/runs/39511667205/artifacts/12305519262), named `linux-experiment-evidence`; GitHub retention is 14 days.
- GitHub artifact archive digest: `sha256:f09dc9581fcd13b4cade0946d766cbd61ddfb8c5cd075c13f25e03d1187ec25a`.
- Combined local report: `.invariant/experiment-acceptance/20261020T230716Z-01a4ee90/report.json`.
- Combined report file SHA-256: `b83fda7938d59787ab23c89d2c1db5f032e3713ee09ccae8461bb3edf55faf42`.
- Linux report file SHA-256: `9ac39bff2df0bfaf6a012b72f90c7249d10a87f7b21205e23111c6795a7271ea`.

Generated evidence is excluded from Git. Preserve the whole combined report directory,
including its copied platform bundle; a report JSON alone is insufficient. The verifier
checks checksums, stage observations and host/source correspondence, rather than trusting
a top-level success flag. These bundles are integrity records, not signed attestations.
This verification record was added after the checks; the tested files are enumerated in
the report's source manifest.

## Actual hosts and coverage

| Host | Python | Docker | Verified scope |
|---|---|---|---|
| GitHub `ubuntu-24.04`, x86_64, kernel 6.17.0-1022-azure | 3.12.15 | 28.0.4 | All 16 fixture stages; actual non-containerized Linux launcher |
| macOS, arm64, Darwin 24.6.0 | 3.12.1 | 27.4.0 | All 18 stages, including real-provider checks and imported Linux evidence |

Both hosts passed 233 Python unit tests plus 11 SDK tests, the Rust/legacy acceptance
suite, and a ten-variant matrix with two cases and two repetitions (40 command trials
per host). Checks include isolation, independent evaluation, resource reservations,
OOM/deadlines, detached execution, stop/recovery/retry, resumable logs, continuation,
recoverable apply, reference-aware deletion, and clean wheel installation followed by
the published manual/JSON quickstart. Cleanup completed without errors.

The macOS gate additionally completed seven real Codex turns with `gpt-6.1-sol` at
`medium` effort: two evaluated native variants, two parent/child continuation turns,
and three ordinary chat/resume turns. Linux CI deliberately made no provider calls and
received no Codex account credentials; its live stages remain explicitly blocked in
its fixture report. The combined report obtains those live results from macOS.

## Failures corrected

- Native Linux retains root ownership on files created by administrator-mode trials.
  Pre-created policy mount destinations and a bounded offline static helper now return
  stopped private trial files to the operator, without following symlinks or mounting
  the original project. Its resources fit the minimum supported trial reservation.
- Recovery now accepts a crash that occurs before a phase's workspace exists, without
  replaying claimed work or treating the missing directory as an ownership failure.
- Fixture-only workspace checks use an empty credential home and the real Codex
  app-server, catalog, thread creation and Docker executor. Their test entrypoint
  skips sign-in and rejects provider turns/login requests; production entrypoints
  and separate live authentication checks retain their normal behavior.
- Linux host Rust unit fixtures no longer require privileged ownership changes.
  Production capability delivery to workload UID 65532 remains exercised by the
  real Docker legacy suite.

CI now verifies the downloaded-style evidence bundle against its exact source and
actual Linux host identity. JSON results stay on stdout; stage progress uses stderr.

## Verify or reproduce

From the checkout that retains the generated evidence:

```sh
invariant experiment acceptance --verify \
  .invariant/experiment-acceptance/20261020T230716Z-01a4ee90/report.json --json
```

This command returned exit 0 with `valid: true` and `accepted: true`.
For a new release, run the Linux workflow, download its complete artifact, then run:

```sh
invariant experiment acceptance --live gpt-6.1-sol --effort medium \
  --platform-report /absolute/path/to/linux-bundle/report.json
```

Use matching source manifests on both hosts. See [acceptance and release limitations](experiments/ACCEPTANCE.md).
