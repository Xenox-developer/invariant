# Changelog

## 0.3.1 — 2026-10-24

First registry release. Runtime logic and state formats are unchanged.

Registry delivery completed October 26, 2026: Python `invariant-workspace` 0.3.1
on PyPI, and `invariant-workspace-core` / `invariant-broker` 0.1.0 on crates.io.
The Rust packages use tag `rust-v0.1.0`, with updated installation documentation
and unchanged implementation. A fresh registry-only Rust 1.90 consumer and
PyPI installation passed; see [publication verification](docs/RUST_RELEASE_0.1.0.md).

- Publish the policy library as `invariant-workspace-core` because the crates.io
  name `invariant-core` belongs to an unrelated project. The Rust library/import
  name remains `invariant_core`; dependency aliases preserve broker/supervisor imports.
- Mark the internal supervisor as non-publishable.
- Automate GitHub Release builds and registry uploads with PyPI/crates.io trusted
  publishing, version checks, verified release assets and resumable uploads.
- Preserve the original `v0.3.0` tag and its verification evidence.

## 0.3.0 — 2026-10-24

Initial packaged open-source release preparation, building on the verified
parallel-experiment source milestone from October 21.

### Added

- Standalone documented Rust core/broker libraries with unchanged existing imports,
  independent archive/consumer checks and Linux/macOS Rust 1.90/stable CI.
- Guided project experiments, packaged Python/JavaScript examples, a real native
  Codex demo and interactive review/continuation/apply of saved results.
- Explicit dependency preparation without model turns and baseline test preflight.
- Allowlisted Markdown, JSON and CSV reports with separate retries and unknown values.
- Storage accounting, reference-aware retention, private image/state backups and
  recoverable same-path restore with previous-state retention.
- Safe diagnostics, host sign-in/status commands, community templates and compatibility policy.

### Fixed

- Installed legacy demos/build commands no longer require neighboring checkout files.
- Legacy workload image code has explicit readable modes after installation under a private host umask.
- User reports are written in the working project instead of the installed package.
- Deleting trial checkpoints preserves untagged parent images still needed by saved studies or prepared environments.
- Distribution checks build sdist → wheel and exercise the installed interface.

### Compatibility

- Existing experiment recipes, IDs, revision checks, retry/comparison semantics,
  reviewed apply and ordinary workspace/broker behavior are retained.
- Private SQLite schema remains 5. Report and private-backup formats are version 1.
- Codex remains pinned to 0.163.0. New native runs still use the signed-in account's
  real allowance; recovery never silently replays a model call.
- No portable backup relocation, hard disk quota, automatic merge, deterministic
  model behavior, public image push or PyPI publication is implied.

Full acceptance evidence is recorded separately for the exact tested source in
[release verification](docs/RELEASE_VERIFICATION.md).
