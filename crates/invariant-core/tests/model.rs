use std::time::{Duration, Instant, SystemTime};

use invariant_core::{
    BrokerSessionId, Decision, DenialReason, EffectRequest, ExecutionContext, ExecutionId, Grant,
    GrantId, GrantState, InvalidRequest, MAX_REQUEST_FRAME_BYTES, MVP_GRANT_LIFETIME,
    MockHttpOrigin, OperationScope, PrincipalId, ResourceId, ResourceIdentity,
};

fn grant(wall: SystemTime, monotonic: Instant) -> Grant {
    Grant::issue(
        GrantId::new("grant-1"),
        ExecutionContext {
            principal_id: PrincipalId::new("operator-1"),
            execution_id: ExecutionId::new("run-1"),
            broker_session_id: BrokerSessionId::new("session-1"),
            initial_artifact_digest: [42; 32],
        },
        ResourceIdentity {
            resource_id: ResourceId::new("mock-invoice-api"),
            configuration_version: 1,
            origin: MockHttpOrigin("127.0.0.1:8080".parse().unwrap()),
            account: "mock-account".into(),
            environment: "test".into(),
            adapter_version: "mock-http-v1".into(),
        },
        wall,
        monotonic,
    )
    .unwrap()
}

fn wire() -> serde_json::Value {
    serde_json::json!({
        "request_id": "request-1",
        "capability": "opaque-handle-1",
        "method": "GET",
        "path": "/invoices"
    })
}

#[test]
fn workload_cannot_supply_identity_or_authority_fields() {
    for field in [
        "principal_id",
        "execution_id",
        "broker_session_id",
        "grant",
        "allowed",
        "resource",
        "url",
        "headers",
        "body",
        "query",
    ] {
        let mut message = wire();
        message[field] = serde_json::json!("attacker-controlled");
        assert_eq!(
            EffectRequest::from_json(&serde_json::to_vec(&message).unwrap()),
            Err(InvalidRequest::Malformed),
            "accepted untrusted field {field}"
        );
    }
}

#[test]
fn duplicate_fields_missing_fields_wrong_types_and_trailing_data_are_rejected() {
    for message in [
        r#"{"request_id":"r","request_id":"s","capability":"c","method":"GET","path":"/invoices"}"#,
        r#"{"request_id":"r","method":"GET","path":"/invoices"}"#,
        r#"{"request_id":"r","capability":true,"method":"GET","path":"/invoices"}"#,
        r#"{"request_id":"r","capability":"c","method":"GET","path":"/invoices"} {}"#,
    ] {
        assert_eq!(
            EffectRequest::from_json(message.as_bytes()),
            Err(InvalidRequest::Malformed)
        );
    }
}

#[test]
fn oversized_frames_are_rejected_before_json_parsing() {
    assert_eq!(
        EffectRequest::from_json(&vec![b' '; MAX_REQUEST_FRAME_BYTES + 1]),
        Err(InvalidRequest::FrameTooLarge)
    );
}

#[test]
fn request_strings_are_nonempty_and_bounded() {
    for (field, maximum) in [
        ("request_id", 128),
        ("capability", 256),
        ("method", 16),
        ("path", 2_048),
    ] {
        for value in [String::new(), "x".repeat(maximum + 1)] {
            let mut message = wire();
            message[field] = serde_json::json!(value);
            assert_eq!(
                EffectRequest::from_json(&serde_json::to_vec(&message).unwrap()),
                Err(InvalidRequest::InvalidField(field))
            );
        }
        let mut message = wire();
        message[field] = serde_json::json!("x".repeat(maximum));
        assert!(EffectRequest::from_json(&serde_json::to_vec(&message).unwrap()).is_ok());
    }
}

#[test]
fn parsing_preserves_forbidden_intent_for_a_later_authorization_decision() {
    let mut message = wire();
    message["method"] = serde_json::json!("DELETE");
    message["path"] = serde_json::json!("/invoices/123?force=true");
    let request = EffectRequest::from_json(&serde_json::to_vec(&message).unwrap()).unwrap();
    assert_eq!(request.method(), "DELETE");
    assert_eq!(request.path(), "/invoices/123?force=true");
    assert!(!format!("{request:?}").contains("opaque-handle-1"));
}

#[test]
fn grant_scope_lifetime_and_resource_snapshot_are_fixed_at_issuance() {
    let wall = SystemTime::UNIX_EPOCH + Duration::from_secs(10_000);
    let issued = grant(wall, Instant::now());
    let mut changed_config = issued.resource().clone();
    changed_config.account = "another-account".into();
    assert_ne!(issued.resource(), &changed_config);
    assert_eq!(issued.scope(), OperationScope::ReadInvoices);
    assert_eq!(issued.max_requests(), 20);
    assert!(!issued.delegation_allowed());
    assert_eq!(
        issued.expires_at().duration_since(wall).unwrap(),
        MVP_GRANT_LIFETIME
    );
}

#[test]
fn expiry_includes_the_exact_deadline_and_resists_wall_clock_rollback() {
    let wall = SystemTime::UNIX_EPOCH + Duration::from_secs(10_000);
    let monotonic = Instant::now();
    let issued = grant(wall, monotonic);
    let before = MVP_GRANT_LIFETIME - Duration::from_nanos(1);
    assert!(!issued.is_expired_at(wall + before, monotonic + before));
    assert!(issued.is_expired_at(wall + MVP_GRANT_LIFETIME, monotonic));
    assert!(issued.is_expired_at(
        wall - Duration::from_secs(60),
        monotonic + MVP_GRANT_LIFETIME
    ));
}

#[test]
fn revocation_is_idempotent_and_does_not_change_grant_identity() {
    let mut issued = grant(SystemTime::now(), Instant::now());
    let id = issued.id().clone();
    issued.revoke();
    issued.revoke();
    assert_eq!(issued.state(), GrantState::Revoked);
    assert_eq!(issued.id(), &id);
}

#[test]
fn decisions_default_to_a_stable_serializable_denial() {
    assert_eq!(
        Decision::default(),
        Decision::Deny(DenialReason::MissingGrant)
    );
    assert_eq!(
        serde_json::to_value(Decision::default()).unwrap(),
        serde_json::json!({"decision": "deny", "reason": "missing_grant"})
    );
}
