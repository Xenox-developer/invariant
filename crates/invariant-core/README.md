# invariant-core

Host-owned capability grants, bounded request parsing and permission checks for
[INVARIANT](https://github.com/Xenox-developer/invariant).

Use this library to embed the existing INVARIANT permission model in trusted Rust
code. It needs Rust 1.90 or newer and has no Docker, Python, Codex, filesystem or
network runtime requirement. Its scope remains the fixed mock invoice operation:
`GET /invoices`, at most 20 admissions per grant, for 15 minutes, without delegation.

## Add to a Rust project

The library is available from Git. A crates.io release has not been published.
Commit the consuming project's `Cargo.lock` to retain the resolved Git revision,
or add `rev` to pin the dependency to a reviewed commit.

```toml
[dependencies]
invariant-core = { git = "https://github.com/Xenox-developer/invariant.git" }
```

## Check a request

Trusted host code supplies identity, resource configuration, clocks and grants.
Only the effect request comes from the untrusted workload.

```rust
use std::time::{Instant, SystemTime};
use invariant_core::{
    AuthorizationState, BrokerSessionId, CapabilityBinding, Decision, EffectRequest,
    ExecutionContext, ExecutionId, ExecutionState, Grant, GrantId, MockHttpOrigin,
    PrincipalId, ResourceId, ResourceIdentity, check_permission,
};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let execution = ExecutionContext {
        principal_id: PrincipalId::new("operator-1"),
        execution_id: ExecutionId::new("run-1"),
        broker_session_id: BrokerSessionId::new("session-1"),
        initial_artifact_digest: [42; 32], // SHA-256 supplied by the trusted host.
    };
    let resource = ResourceIdentity {
        resource_id: ResourceId::new("mock-invoices"),
        configuration_version: 1,
        origin: MockHttpOrigin("127.0.0.1:8080".parse()?),
        account: "example-account".into(),
        environment: "test".into(),
        adapter_version: "mock-http-v1".into(),
    };
    let wall = SystemTime::now();
    let monotonic = Instant::now();
    let grant = Grant::issue(
        GrantId::new("grant-1"), execution.clone(), resource.clone(), wall, monotonic,
    )?;
    // Illustrative only: a broker must generate an unpredictable handle and fresh IDs.
    let capability = CapabilityBinding::new("example-handle", &grant);
    let request = EffectRequest::from_json(
        br#"{"request_id":"read-1","capability":"example-handle","method":"GET","path":"/invoices"}"#,
    )?;
    let state = AuthorizationState {
        execution: &execution,
        execution_state: ExecutionState::Running,
        resource: &resource,
        grant: Some(&grant),
        capability: Some(&capability),
        admitted_requests: 0,
    };
    assert_eq!(check_permission(&request, Some(&state), wall, monotonic), Decision::Allow);
    assert!(matches!(check_permission(&request, None, wall, monotonic), Decision::Deny(_)));
    Ok(())
}
```

## Integration contract

`Decision::Allow` is a read-only policy result. It does not consume a request,
reserve budget, deduplicate, persist an admission or authorize an outbound call
by itself. Use `invariant-broker` for serialized admission, durable audit and HTTP
dispatch, or implement all those responsibilities in your trusted host.

Obtain a consistent authorization snapshot and fresh trusted clocks under the
same admission lock. Do not reuse a decision after state changes. The transport
must enforce the frame limit before allocating a request payload.

Identifier constructors label trusted state; they do not generate identities or
grant authority. Request parsing deliberately rejects workload-supplied identity,
resource and grant fields. Debug output redacts capability handles.

This library performs no process isolation. The Docker supervisor and the Python
experiment CLI remain separate consumers. Existing root imports such as
`invariant_core::Grant` remain the supported interface; internal modules are private.
Existing signatures, serialized values, limits and permission semantics are unchanged.
The current release series is `0.1.x`; incompatible public interface changes require
a minor version increase and migration notes. Before 1.0, no general-purpose policy
language or arbitrary HTTP scope is promised.

## Development

From the repository root:

```sh
cargo test --locked -p invariant-core
cargo run --locked -p invariant-core --example model
cargo doc --locked -p invariant-core --no-deps
cargo package --locked -p invariant-core
```

The README example is also a Rust documentation test. See the repository's
[contribution guide](https://github.com/Xenox-developer/invariant/blob/main/CONTRIBUTING.md).

## License

Apache-2.0. See the repository's
[LICENSE](https://github.com/Xenox-developer/invariant/blob/main/LICENSE) and
[NOTICE](https://github.com/Xenox-developer/invariant/blob/main/NOTICE).
