use std::time::{Duration, Instant, SystemTime};

use invariant_core::{
    AuthorizationState, BrokerSessionId, CapabilityBinding, Decision, DenialReason, EffectRequest,
    ExecutionContext, ExecutionId, ExecutionState, Grant, GrantId, MVP_GRANT_LIFETIME,
    MockHttpOrigin, PrincipalId, ResourceId, ResourceIdentity, check_permission,
};

struct Fixture {
    context: ExecutionContext,
    resource: ResourceIdentity,
    grant: Grant,
    capability: CapabilityBinding,
    wall: SystemTime,
    monotonic: Instant,
}

impl Fixture {
    fn new() -> Self {
        let context = ExecutionContext {
            principal_id: PrincipalId::new("operator-1"),
            execution_id: ExecutionId::new("run-1"),
            broker_session_id: BrokerSessionId::new("session-1"),
            initial_artifact_digest: [42; 32],
        };
        let resource = ResourceIdentity {
            resource_id: ResourceId::new("mock-invoice-api"),
            configuration_version: 1,
            origin: MockHttpOrigin("127.0.0.1:8080".parse().unwrap()),
            account: "mock-account".into(),
            environment: "test".into(),
            adapter_version: "mock-http-v1".into(),
        };
        let wall = SystemTime::UNIX_EPOCH + Duration::from_secs(10_000);
        let monotonic = Instant::now();
        let grant = Grant::issue(
            GrantId::new("grant-1"),
            context.clone(),
            resource.clone(),
            wall,
            monotonic,
        )
        .unwrap();
        let capability = CapabilityBinding::new("registered-handle", &grant);
        Self {
            context,
            resource,
            grant,
            capability,
            wall,
            monotonic,
        }
    }

    fn state(&self) -> AuthorizationState<'_> {
        AuthorizationState {
            execution: &self.context,
            execution_state: ExecutionState::Running,
            resource: &self.resource,
            grant: Some(&self.grant),
            capability: Some(&self.capability),
            admitted_requests: 0,
        }
    }

    fn check(&self, request: &EffectRequest, state: &AuthorizationState<'_>) -> Decision {
        check_permission(request, Some(state), self.wall, self.monotonic)
    }
}

fn request_with_handle(method: &str, path: &str, handle: &str) -> EffectRequest {
    EffectRequest::from_json(
        &serde_json::to_vec(&serde_json::json!({
            "request_id": "request-1",
            "capability": handle,
            "method": method,
            "path": path,
        }))
        .unwrap(),
    )
    .unwrap()
}

fn request(method: &str, path: &str) -> EffectRequest {
    request_with_handle(method, path, "registered-handle")
}

#[test]
fn approved_invoice_read_is_allowed_with_one_request_remaining() {
    let fixture = Fixture::new();
    let state = AuthorizationState {
        admitted_requests: 19,
        ..fixture.state()
    };
    assert_eq!(
        fixture.check(&request("GET", "/invoices"), &state),
        Decision::Allow
    );
}

#[test]
fn missing_authoritative_state_fails_closed() {
    let fixture = Fixture::new();
    assert_eq!(
        check_permission(
            &request("GET", "/invoices"),
            None,
            fixture.wall,
            fixture.monotonic
        ),
        Decision::Deny(DenialReason::StateUnavailable)
    );
}

#[test]
fn no_grant_means_no_permission() {
    let fixture = Fixture::new();
    let state = AuthorizationState {
        grant: None,
        ..fixture.state()
    };
    assert_eq!(
        fixture.check(&request("GET", "/invoices"), &state),
        Decision::Deny(DenialReason::MissingGrant)
    );
}

#[test]
fn a_terminated_execution_cannot_use_an_active_grant() {
    let fixture = Fixture::new();
    let state = AuthorizationState {
        execution_state: ExecutionState::Terminated,
        ..fixture.state()
    };
    assert_eq!(
        fixture.check(&request("GET", "/invoices"), &state),
        Decision::Deny(DenialReason::InvalidExecution)
    );
}

#[test]
fn all_execution_identity_fields_are_bound_to_the_grant() {
    let fixture = Fixture::new();
    let original = &fixture.context;
    let mut changed = vec![original.clone(); 4];
    changed[0].principal_id = PrincipalId::new("other-operator");
    changed[1].execution_id = ExecutionId::new("other-run");
    changed[2].broker_session_id = BrokerSessionId::new("other-session");
    changed[3].initial_artifact_digest = [99; 32];
    for execution in &changed {
        let state = AuthorizationState {
            execution,
            ..fixture.state()
        };
        assert_eq!(
            fixture.check(&request("GET", "/invoices"), &state),
            Decision::Deny(DenialReason::InvalidExecution)
        );
    }
}

#[test]
fn empty_execution_identifiers_fail_closed_even_if_the_grant_matches() {
    let fixture = Fixture::new();
    let mut contexts = vec![fixture.context.clone(); 3];
    contexts[0].principal_id = PrincipalId::new("");
    contexts[1].execution_id = ExecutionId::new("");
    contexts[2].broker_session_id = BrokerSessionId::new("");
    for execution in &contexts {
        let grant = Grant::issue(
            GrantId::new("grant-empty-context"),
            execution.clone(),
            fixture.resource.clone(),
            fixture.wall,
            fixture.monotonic,
        )
        .unwrap();
        let capability = CapabilityBinding::new("registered-handle", &grant);
        let state = AuthorizationState {
            execution,
            grant: Some(&grant),
            capability: Some(&capability),
            ..fixture.state()
        };
        assert_eq!(
            fixture.check(&request("GET", "/invoices"), &state),
            Decision::Deny(DenialReason::InvalidExecution)
        );
    }
}

#[test]
fn every_resource_identity_field_is_checked_not_just_the_name() {
    let fixture = Fixture::new();
    let mut changed = vec![fixture.resource.clone(); 7];
    changed[0].resource_id = ResourceId::new("other-api");
    changed[1].configuration_version = 2;
    changed[2].origin = MockHttpOrigin("127.0.0.2:8080".parse().unwrap());
    changed[3].origin = MockHttpOrigin("127.0.0.1:9090".parse().unwrap());
    changed[4].account = "production-account".into();
    changed[5].environment = "production".into();
    changed[6].adapter_version = "mock-http-v2".into();
    for resource in &changed {
        let state = AuthorizationState {
            resource,
            ..fixture.state()
        };
        assert_eq!(
            fixture.check(&request("GET", "/invoices"), &state),
            Decision::Deny(DenialReason::WrongResource)
        );
    }
}

#[test]
fn an_unregistered_or_forged_handle_does_not_authorize_a_request() {
    let fixture = Fixture::new();
    let read = request("GET", "/invoices");
    let no_capability = AuthorizationState {
        capability: None,
        ..fixture.state()
    };
    assert_eq!(
        fixture.check(&read, &no_capability),
        Decision::Deny(DenialReason::InvalidCapability)
    );
    assert_eq!(
        fixture.check(
            &request_with_handle("GET", "/invoices", "forged-handle"),
            &fixture.state()
        ),
        Decision::Deny(DenialReason::InvalidCapability)
    );
}

#[test]
fn a_copied_handle_cannot_transfer_another_runs_authority() {
    let fixture = Fixture::new();
    let other_context = ExecutionContext {
        execution_id: ExecutionId::new("other-run"),
        ..fixture.context.clone()
    };
    let other_grant = Grant::issue(
        GrantId::new("grant-other-run"),
        other_context,
        fixture.resource.clone(),
        fixture.wall,
        fixture.monotonic,
    )
    .unwrap();
    // Matching handle text alone is insufficient.
    let copied = CapabilityBinding::new("registered-handle", &other_grant);
    let state = AuthorizationState {
        capability: Some(&copied),
        ..fixture.state()
    };
    assert_eq!(
        fixture.check(&request("GET", "/invoices"), &state),
        Decision::Deny(DenialReason::InvalidCapability)
    );
}

#[test]
fn an_old_capability_cannot_authorize_a_replacement_grant_in_the_same_run() {
    let fixture = Fixture::new();
    let replacement = Grant::issue(
        GrantId::new("grant-replacement"),
        fixture.context.clone(),
        fixture.resource.clone(),
        fixture.wall,
        fixture.monotonic,
    )
    .unwrap();
    let state = AuthorizationState {
        grant: Some(&replacement),
        ..fixture.state()
    };
    assert_eq!(
        fixture.check(&request("GET", "/invoices"), &state),
        Decision::Deny(DenialReason::InvalidCapability)
    );
}

#[test]
fn wrong_methods_routes_and_url_variants_are_denied_without_normalization() {
    let fixture = Fixture::new();
    for (method, path) in [
        ("DELETE", "/invoices/123"),
        ("POST", "/invoices"),
        ("HEAD", "/invoices"),
        ("get", "/invoices"),
        ("GET ", "/invoices"),
        ("GET", "/invoices/"),
        ("GET", "/invoices?"),
        ("GET", "/invoices?tenant=other"),
        ("GET", "/invoices#fragment"),
        ("GET", "/%69nvoices"),
        ("GET", "/invoices%3Ftenant=other"),
        ("GET", "/invoices/../invoices"),
        ("GET", "//invoices"),
        ("GET", "http://127.0.0.1:8080/invoices"),
        ("GET", "/invoices\0"),
    ] {
        assert_eq!(
            fixture.check(&request(method, path), &fixture.state()),
            Decision::Deny(DenialReason::OutOfScope),
            "unexpected permission for {method:?} {path:?}"
        );
    }
}

#[test]
fn a_revoked_grant_is_denied() {
    let mut fixture = Fixture::new();
    fixture.grant.revoke();
    assert_eq!(
        fixture.check(&request("GET", "/invoices"), &fixture.state()),
        Decision::Deny(DenialReason::Revoked)
    );
}

#[test]
fn either_expiry_clock_denies_at_the_exact_deadline() {
    let fixture = Fixture::new();
    let read = request("GET", "/invoices");
    for (wall, monotonic) in [
        (fixture.wall + MVP_GRANT_LIFETIME, fixture.monotonic),
        (fixture.wall, fixture.monotonic + MVP_GRANT_LIFETIME),
        (
            fixture.wall - Duration::from_secs(60),
            fixture.monotonic + MVP_GRANT_LIFETIME,
        ),
    ] {
        assert_eq!(
            check_permission(&read, Some(&fixture.state()), wall, monotonic),
            Decision::Deny(DenialReason::Expired)
        );
    }
    let before = MVP_GRANT_LIFETIME - Duration::from_nanos(1);
    assert_eq!(
        check_permission(
            &read,
            Some(&fixture.state()),
            fixture.wall + before,
            fixture.monotonic + before
        ),
        Decision::Allow
    );
}

#[test]
fn exhausted_and_overdrawn_budgets_are_denied() {
    let fixture = Fixture::new();
    for admitted_requests in [20, 21, u32::MAX] {
        let state = AuthorizationState {
            admitted_requests,
            ..fixture.state()
        };
        assert_eq!(
            fixture.check(&request("GET", "/invoices"), &state),
            Decision::Deny(DenialReason::BudgetExhausted)
        );
    }
}

#[test]
fn permission_checks_do_not_consume_admissions_or_replace_broker_accounting() {
    let fixture = Fixture::new();
    let state = AuthorizationState {
        admitted_requests: 19,
        ..fixture.state()
    };
    let read = request("GET", "/invoices");
    for _ in 0..30 {
        assert_eq!(fixture.check(&read, &state), Decision::Allow);
    }
    assert_eq!(state.admitted_requests, 19);
    let consumed = AuthorizationState {
        admitted_requests: 20,
        ..state
    };
    assert_eq!(
        fixture.check(&read, &consumed),
        Decision::Deny(DenialReason::BudgetExhausted)
    );
}

#[test]
fn unchanged_request_observes_grant_issuance_and_revocation() {
    let mut fixture = Fixture::new();
    let read = request("GET", "/invoices");
    let missing = AuthorizationState {
        grant: None,
        ..fixture.state()
    };
    assert_eq!(
        fixture.check(&read, &missing),
        Decision::Deny(DenialReason::MissingGrant)
    );
    assert_eq!(fixture.check(&read, &fixture.state()), Decision::Allow);
    fixture.grant.revoke();
    assert_eq!(
        fixture.check(&read, &fixture.state()),
        Decision::Deny(DenialReason::Revoked)
    );
}

#[test]
fn debug_output_does_not_reveal_the_registered_handle() {
    let fixture = Fixture::new();
    let debug = format!("{:?}", fixture.state());
    assert!(debug.contains("[redacted]"));
    assert!(!debug.contains("registered-handle"));
}
