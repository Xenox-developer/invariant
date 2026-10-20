use std::time::{Instant, SystemTime};

use invariant_core::{
    AuthorizationState, BrokerSessionId, CapabilityBinding, EffectRequest, ExecutionContext,
    ExecutionId, ExecutionState, Grant, GrantId, MVP_GRANT_LIFETIME, MockHttpOrigin, PrincipalId,
    ResourceId, ResourceIdentity, check_permission,
};

fn request(method: &str, path: &str) -> Result<EffectRequest, Box<dyn std::error::Error>> {
    Ok(EffectRequest::from_json(&serde_json::to_vec(
        &serde_json::json!({
            "request_id": "request-1",
            "capability": "example-registered-handle",
            "method": method,
            "path": path
        }),
    )?)?)
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    // Host-side values: never read these from a program's broker message.
    let context = ExecutionContext {
        principal_id: PrincipalId::new("operator-1"),
        execution_id: ExecutionId::new("run-1"),
        broker_session_id: BrokerSessionId::new("session-1"),
        initial_artifact_digest: [0; 32], // Placeholder; supervisor will hash the artifact.
    };
    let resource = ResourceIdentity {
        resource_id: ResourceId::new("mock-invoice-api"),
        configuration_version: 1,
        origin: MockHttpOrigin("127.0.0.1:8080".parse()?),
        account: "mock-account".into(),
        environment: "test".into(),
        adapter_version: "mock-http-v1".into(),
    };
    let wall = SystemTime::now();
    let monotonic = Instant::now();
    let mut grant = Grant::issue(
        GrantId::new("grant-1"),
        context.clone(),
        resource.clone(),
        wall,
        monotonic,
    )?;
    // The host registers this handle. A real broker must generate a fresh,
    // unpredictable handle and associate it with the execution's channel.
    let capability = CapabilityBinding::new("example-registered-handle", &grant);
    let base = AuthorizationState {
        execution: &context,
        execution_state: ExecutionState::Running,
        resource: &resource,
        grant: None,
        capability: Some(&capability),
        admitted_requests: 0,
    };
    let state = AuthorizationState {
        grant: Some(&grant),
        ..base
    };
    let read = request("GET", "/invoices")?;
    println!(
        "Read invoices: {:?}",
        check_permission(&read, Some(&state), wall, monotonic)
    );
    println!(
        "Delete invoice: {:?}",
        check_permission(
            &request("DELETE", "/invoices/123")?,
            Some(&state),
            wall,
            monotonic
        )
    );
    println!(
        "Expired permission: {:?}",
        check_permission(
            &read,
            Some(&state),
            wall + MVP_GRANT_LIFETIME,
            monotonic + MVP_GRANT_LIFETIME
        )
    );
    println!(
        "Twenty requests already admitted: {:?}",
        check_permission(
            &read,
            Some(&AuthorizationState {
                admitted_requests: 20,
                ..state
            }),
            wall,
            monotonic
        )
    );
    grant.revoke();
    println!(
        "Revoked permission: {:?}",
        check_permission(
            &read,
            Some(&AuthorizationState {
                grant: Some(&grant),
                ..base
            }),
            wall,
            monotonic
        )
    );
    println!("Permission checks only; no requests were sent or budget consumed.");
    Ok(())
}
