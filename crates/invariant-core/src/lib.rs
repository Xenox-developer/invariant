//! The MVP's data model and permission checker, not yet an execution environment.
//!
//! Trusted host code constructs contexts, resources, and grants. Workload input
//! enters through [`EffectRequest::from_json`]; it cannot supply authority fields.
//! A [`Decision`] is an internal policy result, never a dispatch permit. The
//! future broker must bind a decision to the exact request and durably commit
//! admission before making an outbound attempt.
//!
//! See `examples/model.rs` for the complete construction flow.

use std::fmt;
use std::net::SocketAddr;
use std::time::{Duration, Instant, SystemTime};

use serde::{Deserialize, Serialize};

mod permissions;

pub use permissions::{AuthorizationState, CapabilityBinding, ExecutionState, check_permission};

pub const MVP_REQUEST_BUDGET: u32 = 20;
pub const MVP_GRANT_LIFETIME: Duration = Duration::from_secs(15 * 60);
pub const MAX_REQUEST_FRAME_BYTES: usize = 16 * 1024;

// Distinct ID types prevent accidentally using a grant ID as an execution ID.
// These constructors label trusted state; they do not generate unique IDs or
// make the caller an authority issuer. The supervisor must supply fresh IDs.
macro_rules! identifier {
    ($($name:ident),+ $(,)?) => {$(
        #[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize)]
        #[serde(transparent)]
        pub struct $name(String);

        impl $name {
            pub fn new(value: impl Into<String>) -> Self {
                Self(value.into())
            }

            pub fn as_str(&self) -> &str {
                &self.0
            }
        }
    )+};
}

identifier!(
    PrincipalId,
    ExecutionId,
    BrokerSessionId,
    ResourceId,
    GrantId
);

/// Established by the supervisor and attached to a channel on the host.
/// Deliberately cannot be deserialized from a workload message.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ExecutionContext {
    pub principal_id: PrincipalId,
    pub execution_id: ExecutionId,
    pub broker_session_id: BrokerSessionId,
    /// SHA-256 of the initial artifact, computed by trusted host code.
    pub initial_artifact_digest: [u8; 32],
}

/// The MVP only supports plain HTTP to a configured mock IP and port.
/// No workload-supplied hostname, URL, or DNS lookup is needed.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(transparent)]
pub struct MockHttpOrigin(pub SocketAddr);

impl fmt::Display for MockHttpOrigin {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "http://{}", self.0)
    }
}

/// An operator-configured resource snapshot. A grant retains its own copy.
/// The broker must freeze configuration for its session and compare identities
/// in full, not just `resource_id`, before admission.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct ResourceIdentity {
    pub resource_id: ResourceId,
    pub configuration_version: u64,
    pub origin: MockHttpOrigin,
    pub account: String,
    pub environment: String,
    pub adapter_version: String,
}

/// There is intentionally no arbitrary URL/method scope in the first version.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum OperationScope {
    ReadInvoices,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum GrantState {
    Active,
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
    /// Enforcing one active grant per execution belongs to the future store.
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

    pub fn id(&self) -> &GrantId {
        &self.id
    }

    pub fn execution(&self) -> &ExecutionContext {
        &self.execution
    }

    pub fn resource(&self) -> &ResourceIdentity {
        &self.resource
    }

    pub fn scope(&self) -> OperationScope {
        OperationScope::ReadInvoices
    }

    pub fn max_requests(&self) -> u32 {
        MVP_REQUEST_BUDGET
    }

    pub fn delegation_allowed(&self) -> bool {
        false
    }

    pub fn issued_at(&self) -> SystemTime {
        self.issued_at
    }

    pub fn expires_at(&self) -> SystemTime {
        self.expires_at
    }

    /// Equality with either deadline means expired. Monotonic time prevents
    /// a backward wall-clock adjustment from extending this session's grant.
    pub fn is_expired_at(&self, wall_now: SystemTime, monotonic_now: Instant) -> bool {
        wall_now >= self.expires_at || monotonic_now >= self.monotonic_deadline
    }

    pub fn state(&self) -> GrantState {
        self.state
    }

    /// An in-memory state change only. The future broker must serialize and
    /// durably record revocation before acknowledging it to the operator.
    pub fn revoke(&mut self) {
        self.state = GrantState::Revoked;
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct DeadlineOverflow;

impl fmt::Display for DeadlineOverflow {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("grant deadline exceeds the clock's representable range")
    }
}

impl std::error::Error for DeadlineOverflow {}

/// Untrusted intent. Parsing establishes syntax and size limits, not authority.
/// A forged capability or forbidden operation can parse successfully and must
/// still be rejected by [`check_permission`].
#[derive(PartialEq, Eq)]
pub struct EffectRequest {
    request_id: String,
    capability: String,
    method: String,
    path: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct WireEffectRequest {
    request_id: String,
    capability: String,
    method: String,
    path: String,
}

impl EffectRequest {
    /// The transport must also cap its frame before buffering it in memory.
    /// String lengths below are in UTF-8 bytes. No normalization is performed:
    /// [`check_permission`] requires exactly `GET` and `/invoices`.
    pub fn from_json(frame: &[u8]) -> Result<Self, InvalidRequest> {
        if frame.len() > MAX_REQUEST_FRAME_BYTES {
            return Err(InvalidRequest::FrameTooLarge);
        }
        let wire: WireEffectRequest =
            serde_json::from_slice(frame).map_err(|_| InvalidRequest::Malformed)?;
        for (field, value, maximum) in [
            ("request_id", wire.request_id.as_str(), 128),
            ("capability", wire.capability.as_str(), 256),
            ("method", wire.method.as_str(), 16),
            ("path", wire.path.as_str(), 2_048),
        ] {
            if value.is_empty() || value.len() > maximum {
                return Err(InvalidRequest::InvalidField(field));
            }
        }
        Ok(Self {
            request_id: wire.request_id,
            capability: wire.capability,
            method: wire.method,
            path: wire.path,
        })
    }

    pub fn request_id(&self) -> &str {
        &self.request_id
    }

    pub fn capability(&self) -> &str {
        &self.capability
    }

    pub fn method(&self) -> &str {
        &self.method
    }

    pub fn path(&self) -> &str {
        &self.path
    }
}

impl fmt::Debug for EffectRequest {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("EffectRequest")
            .field("request_id", &self.request_id)
            .field("capability", &"[redacted]")
            .field("method", &self.method)
            .field("path", &self.path)
            .finish()
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum InvalidRequest {
    FrameTooLarge,
    Malformed,
    InvalidField(&'static str),
}

impl fmt::Display for InvalidRequest {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::FrameTooLarge => f.write_str("request frame exceeds 16 KiB"),
            Self::Malformed => f.write_str("invalid request structure"),
            Self::InvalidField(field) => write!(f, "invalid length for {field}"),
        }
    }
}

impl std::error::Error for InvalidRequest {}

/// A trusted, internal policy result. Even `Allow` does not reserve budget,
/// bind a dispatch, or authorize networking without the broker's admission step.
/// Defaulting to denial prevents an uninitialized result from permitting access.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(tag = "decision", content = "reason", rename_all = "snake_case")]
pub enum Decision {
    Allow,
    Deny(DenialReason),
}

impl Default for Decision {
    fn default() -> Self {
        Self::Deny(DenialReason::MissingGrant)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum DenialReason {
    InvalidRequest,
    InvalidExecution,
    InvalidCapability,
    MissingGrant,
    WrongResource,
    OutOfScope,
    Expired,
    Revoked,
    BudgetExhausted,
    StateUnavailable,
    RequestConflict,
    CapacityExceeded,
}
