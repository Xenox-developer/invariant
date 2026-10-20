use std::fmt;
use std::time::{Instant, SystemTime};

use crate::{
    Decision, DenialReason, EffectRequest, ExecutionContext, Grant, GrantId, GrantState,
    OperationScope, ResourceIdentity,
};

/// A host-side registration of an opaque handle to a particular run and grant.
/// Construct this only in trusted grant controls. This type neither generates
/// unpredictable handles nor exposes an interface for workload registration.
pub struct CapabilityBinding {
    handle: String,
    execution: ExecutionContext,
    grant_id: GrantId,
}

impl CapabilityBinding {
    pub fn new(handle: impl Into<String>, grant: &Grant) -> Self {
        Self {
            handle: handle.into(),
            execution: grant.execution().clone(),
            grant_id: grant.id().clone(),
        }
    }
}

impl fmt::Debug for CapabilityBinding {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("CapabilityBinding")
            .field("handle", &"[redacted]")
            .field("execution", &self.execution)
            .field("grant_id", &self.grant_id)
            .finish()
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ExecutionState {
    Running,
    Terminated,
}

/// A consistent snapshot supplied by the trusted host, never the workload.
/// `execution` comes from the channel; `resource` comes from current trusted
/// configuration; `admitted_requests` counts admissions for this grant across
/// all processes and connections, not the number of permission checks.
///
/// The future broker must hold its admission lock while obtaining this state,
/// checking permission, and committing budget consumption. An `Allow` result
/// must never be reused after this snapshot changes.
#[derive(Debug, Clone, Copy)]
pub struct AuthorizationState<'a> {
    pub execution: &'a ExecutionContext,
    pub execution_state: ExecutionState,
    pub resource: &'a ResourceIdentity,
    pub grant: Option<&'a Grant>,
    pub capability: Option<&'a CapabilityBinding>,
    pub admitted_requests: u32,
}

/// Check one parsed request against current host-owned authority state.
/// `None` means authoritative state is unavailable, so access fails closed.
/// Clocks must also come from the trusted host.
///
/// This is a read-only policy check: it does not reserve or consume budget,
/// deduplicate requests, record admission, serialize revocation, or dispatch
/// an operation. The broker must perform those steps before any target I/O.
pub fn check_permission(
    request: &EffectRequest,
    state: Option<&AuthorizationState<'_>>,
    wall_now: SystemTime,
    monotonic_now: Instant,
) -> Decision {
    let Some(state) = state else {
        return Decision::Deny(DenialReason::StateUnavailable);
    };
    if state.execution_state != ExecutionState::Running
        || state.execution.principal_id.as_str().is_empty()
        || state.execution.execution_id.as_str().is_empty()
        || state.execution.broker_session_id.as_str().is_empty()
    {
        return Decision::Deny(DenialReason::InvalidExecution);
    }
    let Some(grant) = state.grant else {
        return Decision::Deny(DenialReason::MissingGrant);
    };
    if grant.execution() != state.execution {
        return Decision::Deny(DenialReason::InvalidExecution);
    }
    if grant.resource() != state.resource {
        return Decision::Deny(DenialReason::WrongResource);
    }
    let Some(capability) = state.capability else {
        return Decision::Deny(DenialReason::InvalidCapability);
    };
    if capability.handle != request.capability()
        || capability.execution != *state.execution
        || capability.grant_id != *grant.id()
    {
        return Decision::Deny(DenialReason::InvalidCapability);
    }
    if grant.state() == GrantState::Revoked {
        return Decision::Deny(DenialReason::Revoked);
    }
    if grant.is_expired_at(wall_now, monotonic_now) {
        return Decision::Deny(DenialReason::Expired);
    }
    let in_scope = match grant.scope() {
        OperationScope::ReadInvoices => request.method() == "GET" && request.path() == "/invoices",
    };
    if !in_scope {
        return Decision::Deny(DenialReason::OutOfScope);
    }
    if state.admitted_requests >= grant.max_requests() {
        return Decision::Deny(DenialReason::BudgetExhausted);
    }
    Decision::Allow
}
