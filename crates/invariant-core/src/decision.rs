use serde::Serialize;

/// A trusted, internal policy result. Even `Allow` does not reserve budget,
/// bind a dispatch, or authorize networking without the broker's admission step.
/// Defaulting to denial prevents an uninitialized result from permitting access.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(tag = "decision", content = "reason", rename_all = "snake_case")]
pub enum Decision {
    /// The snapshot permits the request; admission and dispatch are still required.
    Allow,
    /// Access is denied for the supplied reason.
    Deny(DenialReason),
}

impl Default for Decision {
    fn default() -> Self {
        Self::Deny(DenialReason::MissingGrant)
    }
}

/// Stable denial names shared by policy decisions, wire replies and audit records.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum DenialReason {
    /// A request failed structural or size validation.
    InvalidRequest,
    /// The execution is stopped, malformed or does not match the grant.
    InvalidExecution,
    /// No capability binding matches the request, execution and grant.
    InvalidCapability,
    /// No grant is present.
    MissingGrant,
    /// The grant's full resource snapshot differs from current configuration.
    WrongResource,
    /// The method or path is outside the fixed operation scope.
    OutOfScope,
    /// A wall-clock or monotonic deadline has been reached.
    Expired,
    /// The grant has been revoked.
    Revoked,
    /// All admissions available to the grant have been consumed.
    BudgetExhausted,
    /// Authoritative state cannot be obtained.
    StateUnavailable,
    /// A request ID was reused for a different request.
    RequestConflict,
    /// The broker or transport cannot admit more concurrent work.
    CapacityExceeded,
}
