use std::fmt;
use std::time::{Duration, Instant, SystemTime};

use serde::Serialize;

use crate::{ExecutionContext, GrantId, ResourceIdentity};

/// Maximum number of admitted requests in a grant.
pub const MVP_REQUEST_BUDGET: u32 = 20;
/// Fixed grant lifetime, enforced against wall and monotonic clocks.
pub const MVP_GRANT_LIFETIME: Duration = Duration::from_secs(15 * 60);

/// There is intentionally no arbitrary URL/method scope in the first version.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum OperationScope {
    /// Exactly `GET /invoices`, with no query string or alternate path.
    ReadInvoices,
}

/// Host-controlled lifecycle of a grant, independently of its expiry.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum GrantState {
    /// Issued and not revoked; expiry and remaining budget still require checking.
    Active,
    /// Revoked by trusted host code.
    Revoked,
}

/// A host-issued permission record, with fixed MVP scope, budget, and lifetime.
/// This value does not track admissions, deduplication, or remaining budget.
/// It cannot be deserialized from a workload or used as a bearer credential.
#[derive(Debug)]
pub struct Grant {
    id: GrantId,
    execution: ExecutionContext,
    resource: ResourceIdentity,
    issued_at: SystemTime,
    expires_at: SystemTime,
    monotonic_deadline: Instant,
    state: GrantState,
}

impl Grant {
    /// Called only after a trusted operator authorizes issuance. The caller
    /// supplies trusted clocks. Returns an error if a deadline overflows.
    /// The broker must enforce one active grant per execution.
    pub fn issue(
        id: GrantId,
        execution: ExecutionContext,
        resource: ResourceIdentity,
        issued_at: SystemTime,
        monotonic_now: Instant,
    ) -> Result<Self, DeadlineOverflow> {
        Ok(Self {
            id,
            execution,
            resource,
            issued_at,
            expires_at: issued_at
                .checked_add(MVP_GRANT_LIFETIME)
                .ok_or(DeadlineOverflow)?,
            monotonic_deadline: monotonic_now
                .checked_add(MVP_GRANT_LIFETIME)
                .ok_or(DeadlineOverflow)?,
            state: GrantState::Active,
        })
    }

    /// Identity supplied at issuance.
    pub fn id(&self) -> &GrantId {
        &self.id
    }

    /// Exact execution snapshot retained at issuance.
    pub fn execution(&self) -> &ExecutionContext {
        &self.execution
    }

    /// Exact resource snapshot retained at issuance.
    pub fn resource(&self) -> &ResourceIdentity {
        &self.resource
    }

    /// Fixed operation scope of this grant.
    pub fn scope(&self) -> OperationScope {
        OperationScope::ReadInvoices
    }

    /// Maximum admissions, not the number of policy checks.
    pub fn max_requests(&self) -> u32 {
        MVP_REQUEST_BUDGET
    }

    /// Whether the workload may delegate authority; always false in this version.
    pub fn delegation_allowed(&self) -> bool {
        false
    }

    /// Trusted wall-clock issuance time.
    pub fn issued_at(&self) -> SystemTime {
        self.issued_at
    }

    /// Wall-clock deadline; the independent monotonic deadline also applies.
    pub fn expires_at(&self) -> SystemTime {
        self.expires_at
    }

    /// Equality with either deadline means expired. Monotonic time prevents
    /// a backward wall-clock adjustment from extending this session's grant.
    pub fn is_expired_at(&self, wall_now: SystemTime, monotonic_now: Instant) -> bool {
        wall_now >= self.expires_at || monotonic_now >= self.monotonic_deadline
    }

    /// Explicit lifecycle state, without inferring expiry or remaining budget.
    pub fn state(&self) -> GrantState {
        self.state
    }

    /// An in-memory state change only. The broker must serialize and
    /// durably record revocation before acknowledging it to the operator.
    pub fn revoke(&mut self) {
        self.state = GrantState::Revoked;
    }
}

/// A grant deadline could not be represented by a supplied host clock.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct DeadlineOverflow;

impl fmt::Display for DeadlineOverflow {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("grant deadline exceeds the clock's representable range")
    }
}

impl std::error::Error for DeadlineOverflow {}
