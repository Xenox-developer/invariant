# Public Rust library verification

Verified October 24, 2026 (Europe/Moscow).

The public Rust library extraction passed all four jobs in the
[Rust libraries workflow](https://github.com/Xenox-developer/invariant/actions/runs/40024386150).
Tested source: [`a5aa5d2c89fe1bce60288be792f0964499b65096`](https://github.com/Xenox-developer/invariant/commit/a5aa5d2c89fe1bce60288be792f0964499b65096).

| Actual host | Rust | Result |
|---|---|---|
| Linux x86_64, `ubuntu-24.04` | 1.90.0 | Passed |
| Linux x86_64, `ubuntu-24.04` | Stable, 1.99.1 | Passed |
| macOS arm64, `macos-14` | 1.90.0 | Passed |
| macOS arm64, `macos-14` | Stable, 1.99.1 | Passed |

Each job checked formatting, Clippy, 75 existing Rust tests across the workspace,
both library README examples as doctests, and public documentation with warnings
treated as errors. Workspace tests include the existing supervisor and Python
Unix-transport integrations.

The separate package gate exported current source, built and verified both crate
archives, checked standalone manifests and original notices, and ran Rust tests
and documentation examples from the extracted packages without the checkout SDK.
An independent application then exercised one real mock HTTP request, replay
without another admission, scope denial, revocation, audit capability redaction
and the Unix socket's response and cleanup. Its direct `invariant_core` imports
and `invariant_broker::core` types compiled as the same types.

All four downloaded reports have `passed: true`, `source_unchanged: true`, zero
command failures and source manifests matching the checked-out library, package
and example files. The source manifest covers those files, not every application
document or the entire Python experiment implementation.

Local macOS checks additionally passed with Rust 1.90.0 and 1.98.1. The standalone
example also ran successfully against Git dependencies fetched from GitHub,
pinned by its committed lockfile. Local Python regression checks passed 254
application tests and 11 SDK tests, including existing uncommitted guided/demo work.

## Evidence

CI artifacts are named `rust-libraries-OS-TOOLCHAIN` and retain the report and
command log for 30 days. Their report SHA-256 values are:

| Artifact | Report SHA-256 |
|---|---|
| `rust-libraries-ubuntu-24.04-1.90.0` | `03d4194dd46697ac619e7a6c2ea26f5377282187a84a35ff47af929b7ea2991a` |
| `rust-libraries-ubuntu-24.04-stable` | `0a6e594ce4ce4634288023f8e9c055554f06ae4f4464ebed107baa9492e1c0af` |
| `rust-libraries-macos-14-1.90.0` | `578933eaf5e062eaab4d5ac0a4ee0a2e83948986462da29f8d8cbf972774691b` |
| `rust-libraries-macos-14-stable` | `efc8162be40a17a9fecc8adb992b8c45c8d481fbd55e4429e533ac0ea4e98c44` |

Downloaded evidence is retained locally under
`.invariant/rust-library-checks/github-40024386150/`, outside Git. Preserve those
files separately if evidence is needed beyond GitHub's artifact retention.

## Scope

The existing Rust import paths, signatures, serialized values, grant limits,
admission behavior and supervisor integration are retained. Both libraries have
standalone README and API documentation, distributable archives, license notices
and Git dependency examples. No registry publication occurred.

This verifies the Rust library stage. Full experiment release acceptance still
requires fresh same-source Docker and live-provider evidence. See the
[remaining release stages](OPEN_SOURCE_ROADMAP.md) and
[library integration guide](RUST_LIBRARY.md).
