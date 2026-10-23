# Registry release preparation — 0.3.1

This patch prepares the first registry uploads. It does not assert that PyPI or
crates.io has accepted them. See the release notes for actual publication status.

The original `v0.3.0` tag, assets and [full acceptance](RELEASE_VERIFICATION.md)
are preserved. The registry name `invariant-core` was already owned by an unrelated
project, so our core package is now `invariant-workspace-core` 0.1.0. Its public
Rust target stays `invariant_core`; the broker's public types/reexports and the
supervisor's existing imports are unchanged. Broker version remains 0.1.0.
Python version is 0.3.1. The internal supervisor cannot be uploaded to crates.io.

## Fresh verification

- 271 application tests and 11 SDK tests passed on macOS arm64 / Python 3.12.
- Workspace formatting, Clippy with warnings denied, all Rust targets and
  documentation tests passed. Rustdoc built with warnings denied.
- Rust 1.90 exported both standalone archives, checked original LICENSE/NOTICE
  and README contents, and ran extracted tests/examples plus an independent
  consumer. Direct core and broker-reexported types remain interchangeable.
- Cargo's multi-package publish dry run verified dependency ordering and both
  archives without uploading them.
- Python sdist built a wheel under a private umask. A fresh installation outside
  the checkout passed CLI, project, examples and diagnostics checks. Its real
  legacy Docker demo also passed with the renamed local Cargo package.
- Both Python artifacts passed `twine check --strict`.
- The independent embedding application's committed lockfile was refreshed from
  GitHub, and its locked public-Git run passed.
- The PyPI workflow passed actionlint. Its verification script downloaded the
  existing 0.3.0 release, validated exact archive metadata/checksums and rejected
  an invalid tag before starting a subprocess. The actual 0.3.1 run is recorded
  in GitHub Actions after its release assets are uploaded.

No Rust implementation or Python application logic changed beyond the displayed
Python version. The 15 live model turns in the 0.3.0 report remain evidence for
that exact earlier source; they are not presented as a new 0.3.1 acceptance run.
The patch's source manifest, archive hashes and sanitized packaging results are
attached to its own release.

## Owner setup required for registry delivery

Local crates.io credentials and a PyPI trusted publisher were absent at the start
of preparation. The `pypi` GitHub environment and manual upload workflow have been
created. The owner must complete the two registry account steps documented in
[publishing](PUBLISHING.md). Credentials must not be committed or sent in chat.

After successful uploads, verify a fresh registry-only Rust consumer and a PyPI
installation, compare downloaded artifact hashes and update the public installation
instructions. Keep historical tags and their assets unchanged.
