# Embed the Rust libraries

INVARIANT offers two independently consumable Rust libraries:

| Crate | Use it for | Runtime requirements |
|---|---|---|
| [`invariant-workspace-core`](../crates/invariant-core/README.md) | Typed host identities, fixed-scope grants, bounded request parsing and read-only permission checks | Rust standard library and Serde; no external runtime |
| [`invariant-broker`](../crates/invariant-broker/README.md) | Serialized admissions, durable audit, request deduplication, bounded HTTP dispatch and Unix effect sockets | Host filesystem and loopback/network access for admitted HTTP calls; Unix for sockets |

Both require Rust 1.90 or newer and are published on crates.io as version 0.1.0:
[core](https://crates.io/crates/invariant-workspace-core/0.1.0) and
[broker](https://crates.io/crates/invariant-broker/0.1.0). Existing direct crate
imports remain valid. The core dependency alias preserves `invariant_core`.

```toml
[dependencies]
invariant-core = { package = "invariant-workspace-core", version = "0.1.0" }
invariant-broker = "0.1.0"
```

Use `invariant_broker::core` to access the exact policy types used by the broker.
Applications that only need policy checks can depend on `invariant-workspace-core`
alone, using the dependency alias `invariant-core` to retain `invariant_core` imports.
Commit the consuming application's lockfile to retain resolved versions.
The published Rust source is tagged [`rust-v0.1.0`](https://github.com/Xenox-developer/invariant/tree/rust-v0.1.0).

## Run an independent application

The [embedding example](../examples/rust-embedding/README.md) is a separate Cargo
project with registry dependencies, outside the INVARIANT workspace:

```sh
cargo run --locked --manifest-path examples/rust-embedding/Cargo.toml
```

It makes one real request to a local mock HTTP server, verifies request replay
without another admission, denies an out-of-scope request, revokes access and
checks that the audit omits the capability. On Unix it also verifies the framed
effect socket and endpoint cleanup. Docker, Python and Codex are not required.

The example supplies an illustrative artifact digest. A production trusted host
must hash its actual artifact and keep broker state outside workload-writable paths.

## Responsibilities and compatibility

The core library is a policy model, not an admission store. `Decision::Allow`
alone does not authorize a network call or consume an allowance. The broker owns
the admission lock, ledger and durable journal, and fails closed on journal failure.

Both retain the existing fixed scope: exactly `GET /invoices`, 20 admissions per
grant, a 15-minute lifetime, no delegation and explicit trusted grant controls.
The HTTP adapter remains plain HTTP to a configured IP and port, without DNS,
proxies, automatic retries or redirect following. It is not a general HTTP proxy.

The libraries do not provide process isolation. The supervisor owns Docker
execution and workload lifecycle. The Python package owns agent workspaces and
parallel experiments; those interfaces are not exposed as Rust library functions.

The extraction preserves root import paths, public signatures, wire values,
audit fields, limits and permission/admission semantics. Private implementation
modules may change. For the current `0.1.x` series, incompatible public changes
require a minor version increase and migration notes. No binary ABI is promised.

## Verify the distributable packages

From the repository root, with Python 3.11+ for the verification driver:

```sh
python3 -B scripts/test_rust_library.py
# Require the already installed minimum Rust toolchain and cached dependencies:
python3 -B scripts/test_rust_library.py --toolchain 1.90.0 --offline
```

The check exports source to a temporary checkout, packages both crates, checks
LICENSE/NOTICE and README contents, runs the extracted packages' Rust tests and
documentation examples, and runs the same independent application against only
the extracted archives. It does not depend on published registry versions of
the two crates and never publishes packages. Reports include source and archive
hashes, command exit codes and a check for concurrent source changes.

The broker package excludes the Python transport tests/example, which need
sibling checkout files. Those remain part of workspace regression checks.
Rust-only broker tests and the self-contained Rust example remain in the archive.

The [Rust libraries workflow](../.github/workflows/rust-libraries.yml) verifies
Rust 1.90 and stable on Linux and macOS. This is independent of full experiment
release acceptance, which still requires its Docker and live-provider evidence.
See [Contributing](../CONTRIBUTING.md) for the remaining application checks.
