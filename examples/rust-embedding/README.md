# Embed INVARIANT from another Rust project

This is an independent Cargo project, outside INVARIANT's workspace. It connects
the public `invariant-workspace-core` and `invariant-broker` Git dependencies, runs a local
mock HTTP server, and verifies one admission, duplicate handling, scope denial,
revocation, capability redaction in the audit, and the Unix transport on Unix.

Requirements: Rust 1.90 or newer and access to GitHub/crates.io for the initial
dependency download. Docker, Python and Codex are not required to run this example.

```sh
cargo run --manifest-path examples/rust-embedding/Cargo.toml
```

Expected output:

```text
PASS: one HTTP call, replay without another admission, scope denial, revocation and private audit
```

The manifest uses Git dependencies. Commit its generated lockfile in a consuming
application or pin a reviewed Git revision. INVARIANT's package verification also
runs this same source against extracted `.crate` archives, replacing only the
dependency locations in a temporary manifest. It does not use the workspace's
source paths or require either library to be published on crates.io.

The program runs on the host, makes one loopback HTTP call and creates temporary
private state that is removed on normal completion. Its sample artifact digest
is illustrative. The libraries retain their fixed mock-invoice scope and do not
provide process isolation; use the supervisor for isolated workloads.
