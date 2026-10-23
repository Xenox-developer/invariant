# Open-source release verification — 0.3.0

Verified October 24, 2026 (Europe/Moscow; evidence timestamps use UTC).

**Result: accepted.** All P01–P21 cases and all 22 stages of the
`open-source-v0.3` profile passed, with no blockers and unchanged source.

## Source and evidence

- Tested source: [`4fb90336da791567c9695ada8cb6e83da43d0645`](https://github.com/Xenox-developer/invariant/commit/4fb90336da791567c9695ada8cb6e83da43d0645).
- Source manifest: 239 files; digest `fba68921d36beee3485eb19b61e53dbed4d612fc3c594fed22eb250449f1336f`.
- Combined report SHA-256: `31565dfe591fcaddcbef426f8eef13cbf8aa2e7d312311a25999345301f0402c`.
- Linux report SHA-256: `2f8d95a31df527acaa212e27ae364e1d080ad5fbadb59005485be6c23dcf2993`.
- Local combined bundle: `.invariant/experiment-acceptance/20261023T224314Z-6444bb84/`.
- [Linux fixture CI](https://github.com/Xenox-developer/invariant/actions/runs/40035395377); [complete artifact](https://github.com/Xenox-developer/invariant/actions/runs/40035395377/artifacts/12536514852).
- Linux artifact archive digest: `sha256:2dac080ae5b60a3f731ef2e3c86e139757d7cfade0cab74c05c0ad9a99e0564d`; GitHub retention is 14 days.
- [Python CI](https://github.com/Xenox-developer/invariant/actions/runs/40035395355): all 10 Linux/macOS × Python 3.11–3.15 jobs passed.
- [Rust CI](https://github.com/Xenox-developer/invariant/actions/runs/40035414320): all four Linux/macOS × Rust 1.90/stable jobs passed.

Preserve the entire local bundle, including imported Linux artifacts. JSON alone
is insufficient. It can contain synthetic project/model content and private native
transcripts; only allowlisted summary evidence is attached to the public release.
Use `invariant experiment acceptance --verify /absolute/path/to/report.json --json`
to verify the full bundle. This record and the release roadmap were added after
verification; the files in the tested source manifest still match the release.
Checksums prove local integrity, not signed provenance.

## Actual host coverage

| Host | Architecture | Python | Docker |
|---|---|---|---|
| macOS | arm64 | 3.12.1 | 27.4.0 |
| Linux | x86_64 | 3.12.15 | 28.0.4 |

Both actual non-containerized host launchers passed 271 application unit tests,
11 SDK tests, all 20 legacy acceptance cases and the 40-trial command matrix.
The macOS aggregate passed all 22 stages. Linux passed all 18 provider-free
stages; its four live stages remain explicitly blocked, with no account
credentials or model calls in CI. macOS supplied 15 real `gpt-6.1-sol` turns:
two native variants, two parent/child apply turns, three ordinary chat/resume
turns and four each for guided setup and demo. Guided/demo compare low,
medium and high reasoning effort. Other native stages use medium effort.

Coverage includes independent frozen evaluators, limits and overlap, OOM and
deadlines, detached controllers, stop/recovery/retry, resumed logs, selection,
continuation, recoverable apply and scoped deletion. New release checks cover
sdist → wheel installation with a private `077` umask, installed legacy Docker
commands, real Python/npm preflight and quality verdicts, all three export
formats, private exact-image backup/restore and continued execution, retention
and safe diagnostics. Fixture cleanup completed without errors.

## Release files

The [0.3.0 beta release](https://github.com/Xenox-developer/invariant/releases/tag/v0.3.0)
contains the tested Python wheel/sdist and Rust 0.1.0 archives, source manifest,
sanitized verification summary and `SHA256SUMS`. Rust archives came from the
passing minimum-toolchain Linux CI consumer check. No registry or image push
is implied. See [installation](INSTALLATION.md), [Rust consumption](RUST_LIBRARY.md),
[compatibility](COMPATIBILITY.md), [backup boundaries](STORAGE_AND_RECOVERY.md)
and the [changelog](../CHANGELOG.md).

The [October 21 verification](RELEASE_VERIFICATION_2026-10-21.md) remains historical
evidence for its own source and earlier release requirements.
