# Open-source release work

This checklist tracks the staged public release preparation requested on
October 23, 2026. Rust library work began first. Each coherent change is tested,
committed and pushed independently; existing local work must be preserved.

## 1. Public Rust libraries

- [x] Separate the core's private implementation while retaining root imports.
- [x] Document every public core/broker item and compile README examples.
- [x] Add standalone package metadata, LICENSE and NOTICE files.
- [x] Make the broker's exact core types available through `invariant_broker::core`.
- [x] Preserve existing policy, admission, serialization and lifecycle behavior.
- [x] Verify the workspace on Rust 1.90, including the existing supervisor.
- [x] Verify both distributable archives from an independent Rust consumer.
- [x] Run the new Linux/macOS Rust CI matrix and record its outcome.

Completed October 24, 2026 (Europe/Moscow). See the
[verification record](RUST_LIBRARY_VERIFICATION.md) for the tested source and CI evidence.

Libraries are consumed through Git for now. Registry publication and full
experiment release acceptance are separate from extracting these libraries.

## 2–7. Packaged open-source release

- [x] Complete Python sdist/wheel contents and verify installed commands outside a checkout.
- [x] Add onboarding, dependency preparation without models and baseline preflight.
- [x] Export allowlisted Markdown, JSON and CSV comparisons with explicit private-content flags.
- [x] Add honest storage accounting, scoped retention, private backups, recoverable restore and diagnostics.
- [x] Rewrite the English README, ship Python/JavaScript examples and finish contribution/security/issue documentation.
- [x] Pass matching-source macOS/Linux acceptance, Python/Rust CI, and prepare tested release files with checksums.

Completed October 24, 2026. The [0.3.0 verification record](RELEASE_VERIFICATION.md)
identifies the tested source, actual hosts, model turns, CI and archive evidence.
The release remains a documented beta; registry publication, hard disk quotas
and portable cross-path backups are outside its declared boundaries.
