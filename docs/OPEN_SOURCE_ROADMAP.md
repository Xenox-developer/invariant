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

## Remaining stages

2. Complete Python package contents and verify all advertised installed commands.
3. Finish onboarding, environment preparation and test preflight for user projects.
4. Add shareable Markdown/JSON/CSV comparison reports.
5. Add storage overview, cleanup guidance, backup/restore and support diagnostics.
6. Rewrite the main README around one verified user journey and finish community docs.
7. Verify supported platforms against matching final source and prepare release artifacts.

Each stage needs its own concrete acceptance evidence. Earlier acceptance reports
remain historical evidence for their recorded source, not proof of a newer release.
