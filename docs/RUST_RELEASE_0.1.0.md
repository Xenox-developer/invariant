# Rust registry release — 0.1.0

Published October 26, 2026. Both packages are available on crates.io:

| Package | Version | SHA-256 |
|---|---|---|
| [invariant-workspace-core](https://crates.io/crates/invariant-workspace-core/0.1.0) | 0.1.0 | `79219858cedab4363bec6d40ce074a50830b3e24849d1393383203f0949a83c6` |
| [invariant-broker](https://crates.io/crates/invariant-broker/0.1.0) | 0.1.0 | `2c470a33bcfeca4dc8a207860f7ffecf5553df12095aa27dcb93a77d578398b3` |

Source: [`rust-v0.1.0`](https://github.com/Xenox-developer/invariant/tree/rust-v0.1.0), commit `e4b571f1845607dec651d240a322af15db261138`.

The Rust implementation, dependency declarations and workspace lockfile match
`v0.3.1`. The library READMEs now document registry installation. The core package
name is `invariant-workspace-core`; its `invariant_core` library target and the
broker reexports remain unchanged. Core was uploaded first; broker was uploaded
after Cargo confirmed the core was available in the registry.

## Fresh registry verification

- Downloaded both versioned crates.io archives and matched their SHA-256 values
  against the registry metadata and the exact archives sent by Cargo.
- Created an independent application in a temporary directory with an empty
  Cargo home and separate build directory. Both dependencies resolved to
  `registry+https://github.com/rust-lang/crates.io-index`, version 0.1.0.
- On Rust 1.90, generated its lockfile, ran Clippy with warnings denied and
  executed the example. Direct core and broker-reexported types compiled together.
- Verified one real loopback HTTP call, replay without another admission, scope
  denial, revocation, private audit, and the framed Unix transport with cleanup.
- Refreshed the committed embedding example to registry dependencies and verified
  its locked run separately.
- Rechecked the Python README installation in a fresh environment from PyPI:
  `invariant-workspace==0.3.1`, version/help commands passed.

Before upload, standalone source-export archives passed Rust 1.90 tests, examples,
README doctests and independent consumer verification. Workspace doctests and
Rustdoc with warnings denied also passed. The final documentation changes passed
the Python distribution check, including sdist → wheel installation and the
selected README image. No new live-provider acceptance run was needed for these
documentation and dependency-location changes; full 0.3.0 evidence retains its
original source scope.

## GitHub release assets

The [0.3.1 release](https://github.com/Xenox-developer/invariant/releases/tag/v0.3.1)
keeps its original Python artifacts and preparation evidence. Its original Rust
archives precede the registry README update and remain historical assets.
The additional `published-*.crate` assets contain the exact registry bytes;
`CRATES_IO_SHA256SUMS` covers those files and `registry-publication.json`.
The original `SHA256SUMS` continues to cover the original assets. Neither the
Python tag nor previously uploaded package versions were replaced.
