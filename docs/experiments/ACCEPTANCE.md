# Experiment MVP acceptance and release verification

`invariant experiment acceptance` is the P01–P21 gate. The legacy
`invariant acceptance` still runs A01–A20 and is included as a regression.

```sh
invariant experiment acceptance --live MODEL_NAME --effort medium
invariant experiment acceptance --verify /absolute/path/to/report.json --json
```

Run from the source checkout, or pass `--checkout /absolute/path/to/invariant` when the
command comes from a wheel. No initialized application project is needed. Docker gates
run serially. Both the current and compatibility Codex images are rebuilt, and the
inventory covers these plus the Python and supervisor images. The live option authorizes seven small model turns: two native variants,
a native parent/child continuation, and three ordinary chat resume turns. The command
matrix makes no provider calls. Missing credentials/model/provider access is blocked;
it cannot substitute fixture coverage for a live pass. Network-dependent package checks
also need their public package sources reachable.

Reports are saved under `.invariant/experiment-acceptance/RUN/report.json` with explicit
case status, contract/source hashes, actual host and Docker identity, fresh component
reports, per-command logs, cleanup errors, independent trial receipts, inspected resource
allocations and dependency/notice inventories. Source edits during verification invalidate
acceptance. The verifier checks report/artifact checksums, required observations, complete
case IDs, supporting stage outcomes and actual platform evidence. These are local evidence
bundles, not signed attestations; review provenance before accepting someone else's bundle.
Evidence may contain project/model content; review before sharing.

Exit **0** means all P01–P21 cases and release prerequisites passed. Exit **1** means a
verification failure. Exit **2** means valid but incomplete/blocked release evidence.
`--verify` returns 2 for an intact blocked report. No skipped or unavailable check counts
as passing. Ordinary unit tests need no Docker or provider sign-in.

| Cases | Evidence |
|---|---|
| P01/P02 | Strict schema/model tests, changing-source/exclusion tests, frozen CLI creation and retries |
| P03/P04 | Ten command variants × two cases × two repetitions; overlap receipts; inspected limits/mounts/images; private tools; non-Git execution |
| P05/P06 | Concurrent CLI creation, selection races, shared human/JSON IDs and both full apply handoffs |
| P07/P20 | Two real isolated native variants with recorded model/effort and independent quality verdicts |
| P08–P12 | Aggregate reservations, real detached controller/viewer exit, bounded stop, foreign-container preservation, OOM/deadlines and crash/checkpoint faults |
| P11 provider paths | Explicitly injected auth/throttling/model failures; no unnecessary paid failure calls |
| P13/P14 | Captured evaluator tamper resistance, malformed/nonfinite output, wrong zero-exit answers, cases/repetitions and first-attempt denominators |
| P15–P18 | Diff/plans, manual/JSON apply, links/conflicts, crash rollback, private child tools/files, capture locks and reference-aware deletion |
| P19 | Legacy suite, workspace/config/guardrails/easy workflow/network/general tools and real same-chat resume |
| P21 | Clean source export → built wheel → fresh virtualenv → exact published quickstart; macOS and Linux host bundles |

## Platform evidence

Users need only their own supported computer and Docker. Cross-platform evidence is a
maintainer release check. Docker's Linux guest does not test host-side macOS/Linux paths,
locking, subprocess lifetime or package installation.

Run the fixture gate on Linux (CI workflow supplied), download its whole evidence bundle,
and provide that report to a macOS run of the same source:

```sh
invariant experiment acceptance --live MODEL_NAME --effort medium \
  --platform-report /path/to/linux-bundle/report.json
```

The reports must have identical source manifests, successful local fixture gates and
complete artifact bundles. The aggregate copies the other platform's evidence into its
own bundle. Reports record whether the launcher itself is containerized; such runs do
not satisfy actual host-platform evidence. A Linux container on a macOS engine is not
counted as a separate Linux host.
The GitHub workflow verifies its Linux bundle's integrity, unchanged source and actual
host identity before accepting the fixture gate. JSON output stays on stdout; stage
progress goes to stderr so CI shows which check is running.

Fixture-only workspace checks use a source-tree test entrypoint with an empty credential
home. It skips sign-in, retains the real Codex app-server/catalog, Docker executor and
thread creation, and rejects provider turns and login requests. Production entrypoints
do not load this fixture. The separate live stages still require real authentication.
Linux trial cleanup transfers root-created files back to the operator only after stop,
using the protected static inspector in a bounded, offline container mounted to the
private trial files. It does not run candidate software or mount the original project.

Until both matching host bundles verify, the release stays blocked on P21. A passing
macOS-only run or a green fixture workflow alone does not imply full release acceptance.

## Release limitations

No publication, package upload or image push occurs in this gate. Apache-2.0 covers original
source; inspect the [licensing inventory](../LICENSING.md) before distributing binaries/images.
The first implementation uses full private workspace copies and Docker writable layers,
not zero-copy filesystem clones. Reproduction freezes available local inputs, not remote
services, clocks, random sources or model responses. Disk headroom is checked but hard
per-trial disk quotas are not provided. Apply retains content/modes/links, not ACLs/xattrs;
external editors can race the final check, and detected concurrent edits block recovery.
