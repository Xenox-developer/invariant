# invariant-broker

Embed INVARIANT's execution-bound capability broker in a trusted Rust host.
It serializes admissions, durably records audit events, deduplicates request IDs
and makes bounded HTTP calls to the configured mock invoice endpoint.

The library needs Rust 1.90 or newer. It does not require Docker, Python or Codex
for direct Rust use. The Unix socket server is available on Unix targets.
The maintained host verification targets are macOS and Linux.

## Add to a Rust project

The library is available from Git. A crates.io release has not been published.
Commit your application's `Cargo.lock` to retain the resolved Git revision,
or add `rev` to pin a reviewed commit.

```toml
[dependencies]
invariant-broker = { git = "https://github.com/Xenox-developer/invariant.git" }
```

Use `invariant_broker::core` for the broker's exact policy types. Existing direct
dependencies on `invariant-core` and its root imports remain compatible.

## Create and revoke access

This example creates a fresh durable file journal and exercises denial and
revocation. It makes no HTTP calls. In an application, choose a private state
directory outside all workload-writable paths and supply the real artifact digest.

```rust
use std::fs;
use std::time::SystemTime;
use invariant_broker::{Broker, BrokerConfig, BrokerReply, FileJournal};
use invariant_broker::core::{DenialReason, MockHttpOrigin, PrincipalId, ResourceId, ResourceIdentity};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let nonce = SystemTime::now().duration_since(SystemTime::UNIX_EPOCH)?.as_nanos();
    let state = std::env::temp_dir().join(format!("invariant-example-{}-{nonce}", std::process::id()));
    fs::create_dir(&state)?;
    let broker = Broker::start(
        BrokerConfig {
            principal_id: PrincipalId::new("example-operator"),
            initial_artifact_digest: [42; 32],
            resource: ResourceIdentity {
                resource_id: ResourceId::new("mock-invoices"),
                configuration_version: 1,
                origin: MockHttpOrigin("127.0.0.1:8080".parse()?),
                account: "example-account".into(),
                environment: "test".into(),
                adapter_version: "mock-http-v1".into(),
            },
        },
        FileJournal::create(state.join("audit.jsonl"))?,
    )?;
    let before_grant = br#"{"request_id":"read-1","capability":"unissued","method":"GET","path":"/invoices"}"#;
    assert_eq!(broker.execute(before_grant), BrokerReply::Denied(DenialReason::MissingGrant));

    let access = broker.issue_grant()?; // Trusted operator action only.
    // Deliver the handle only to this execution; never print or persist it in reports.
    let request = format!(
        r#"{{"request_id":"read-2","capability":"{}","method":"GET","path":"/invoices"}}"#,
        access.capability,
    );
    broker.revoke_grant()?;
    assert_eq!(broker.execute(request.as_bytes()), BrokerReply::Denied(DenialReason::Revoked));
    assert_eq!(broker.stats()?.current_grant_admissions, 0);
    broker.stop()?;
    drop(broker);
    fs::remove_dir_all(state)?;
    Ok(())
}
```

For an allowed call, keep a grant active, run a mock server at the trusted
configured origin and pass the request to `Broker::execute`. The library's
`broker` example runs that complete exchange, checks duplicate handling and
revocation, and independently counts target calls.

## Lifecycle and guarantees

- Keep one broker instance per execution and share it across all its connections
  and processes, usually with `Arc<Broker>`. Creating one per request resets the
  execution, allowance and ledger.
- Only trusted host code may start executions, issue or revoke grants, and stop
  the broker. The Unix workload protocol exposes effect requests only.
- The fixed scope is exactly `GET /invoices`, with 20 admissions per grant,
  a 15-minute lifetime and no delegation. Configuration is frozen at startup.
- Admissions and dispatch intent are recorded before target I/O. A request ID
  cannot cause a second outbound attempt, including across replacement grants
  in the same execution. Replays return outcome metadata, not the original body.
- Journal errors permanently fail the broker closed. A custom `AuditJournal`
  implementation must return success only after a complete record is durable.
- HTTP responses, error statuses, timeouts and uncertain outcomes consume the
  admission. No HTTP retry, redirect following, proxy or DNS lookup occurs.
- Revocation and stop prevent new admissions; an earlier admitted call can finish.
  A lost response does not prove that the remote target performed no effect.
- File journals are new-session records. This library does not resume authority
  or rebuild an execution from an old journal after a process restart.

The default adapter supports plain HTTP to an explicit IP and port, identity
content encoding, and Content-Length or connection-close framing. It does not
provide arbitrary HTTP operations, TLS or chunked transfer decoding. Limits are
four in-flight calls, a five-second total HTTP deadline, 32 KiB response headers
and a 1 MiB response body. These limitations are part of the current interface.

## Unix transport

On Unix, `UnixBrokerServer::start_in` creates a private endpoint bound to the
same `Arc<Broker>`. Workloads cannot choose an execution identity or issue grants.
Use `shutdown` to observe cleanup errors rather than relying solely on Drop.

The protocol uses a four-byte big-endian length and bounded JSON. Each request
payload is at most 16 KiB; response bodies use Base64. See the
[version-1 protocol](https://github.com/Xenox-developer/invariant/blob/main/docs/UNIX_PROTOCOL.md).

This library does not isolate a workload process. A Unix socket's permissions
do not isolate other processes running as the same user. The Docker supervisor
owns container isolation and process lifecycle; the Python experiment engine
is a separate application. Direct host examples are not sandboxed.

## Compatibility and development

The current release series is `0.1.x`. Existing public signatures, serialized
replies, audit events, limits and admission semantics are retained. Incompatible
public interface changes require a minor version increase and migration notes.
The `core` re-export is additive and uses the same types, not wrapper copies.

From the repository root:

```sh
cargo test --locked -p invariant-broker
cargo run --locked -p invariant-broker --example broker
cargo doc --locked -p invariant-broker --no-deps
```

The `python` example and Python transport integration tests additionally need
the source checkout's SDK, example files and Python 3. The self-contained Rust
example and README test have no such dependency. See the
[contribution guide](https://github.com/Xenox-developer/invariant/blob/main/CONTRIBUTING.md).

## License

Apache-2.0. See the repository's
[LICENSE](https://github.com/Xenox-developer/invariant/blob/main/LICENSE) and
[NOTICE](https://github.com/Xenox-developer/invariant/blob/main/NOTICE).
