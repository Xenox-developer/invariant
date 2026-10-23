#![doc = include_str!("../README.md")]
#![deny(missing_docs)]

/// The exact policy types used by this broker, also available as `invariant-core`.
pub use invariant_core as core;

mod audit;
mod http;
#[cfg(unix)]
mod unix;

pub use audit::{AuditEvent, AuditJournal, AuditRecord, FileJournal};
pub use http::{HttpResponse, MAX_HEADER_BYTES, MAX_RESPONSE_BYTES, TARGET_TIMEOUT, UpstreamError};
#[cfg(unix)]
pub use unix::{
    MAX_SOCKET_CONNECTIONS, MAX_SOCKET_RESPONSE_BYTES, SOCKET_IO_TIMEOUT, UnixBrokerServer,
};

use std::collections::HashMap;
use std::fmt;
use std::io;
use std::sync::{Arc, Mutex, MutexGuard};
use std::time::{Instant, SystemTime};

use invariant_core::{
    AuthorizationState, BrokerSessionId, CapabilityBinding, Decision, DenialReason, EffectRequest,
    ExecutionContext, ExecutionId, ExecutionState, Grant, GrantId, GrantState, PrincipalId,
    ResourceIdentity, check_permission,
};
use serde::Serialize;
use sha2::{Digest, Sha256};

/// Maximum concurrent admitted HTTP calls for one execution.
pub const MAX_IN_FLIGHT: usize = 4;
/// Keep deduplication metadata bounded, even across operator-issued grants.
pub const MAX_RECORDED_REQUESTS: usize = 1024;

/// Trusted configuration for a fresh broker execution; no workload-supplied URLs.
pub struct BrokerConfig {
    /// Host principal that owns this execution.
    pub principal_id: PrincipalId,
    /// Full immutable resource configuration for this session.
    pub resource: ResourceIdentity,
    /// SHA-256 of the initial workload artifact, computed by trusted host code.
    pub initial_artifact_digest: [u8; 32],
}

/// Fresh wall and monotonic readings supplied by a trusted clock.
#[derive(Clone, Copy)]
pub struct ClockReading {
    /// Wall time used for grant expiry and audit timestamps.
    pub wall: SystemTime,
    /// Monotonic time used to prevent wall-clock rollback from extending a grant.
    pub monotonic: Instant,
}

/// Trusted clock injection supports deterministic expiry tests. Never source
/// either value from a workload message.
pub trait Clock: Send + Sync {
    /// Obtain a fresh pair of trusted readings.
    fn now(&self) -> ClockReading;
}

/// Production clock backed by [`SystemTime::now`] and [`Instant::now`].
pub struct SystemClock;

impl Clock for SystemClock {
    fn now(&self) -> ClockReading {
        ClockReading {
            wall: SystemTime::now(),
            monotonic: Instant::now(),
        }
    }
}

/// Newly issued access for delivery over a trusted channel to this workload only.
/// Debug output redacts the capability; reading the field exposes the raw secret.
pub struct GrantAccess {
    /// Fresh grant identity.
    pub grant_id: GrantId,
    /// Unpredictable handle; never include it in logs or public reports.
    pub capability: String,
    /// Wall-clock deadline; the broker also enforces a monotonic deadline.
    pub expires_at: SystemTime,
}

impl fmt::Debug for GrantAccess {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("GrantAccess")
            .field("grant_id", &self.grant_id)
            .field("capability", &"[redacted]")
            .field("expires_at", &self.expires_at)
            .finish()
    }
}

/// Completion metadata retained for an already admitted request ID.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RecordedOutcome {
    /// An earlier admission is still running or has not established an outcome.
    InProgress,
    /// A response status was durably recorded; response bodies are not retained.
    Completed {
        /// Observed HTTP status, including error statuses.
        status: u16,
    },
    /// The broker could not establish a durably recorded successful result.
    /// This does not establish whether the remote target performed an effect.
    Unknown {
        /// Upstream failure when known; absent if recording the outcome failed.
        reason: Option<UpstreamError>,
    },
}

/// Outcome of processing one workload frame, without automatic retry.
#[derive(Debug, PartialEq, Eq)]
pub enum BrokerReply {
    /// The target returned a bounded response that was durably recorded.
    Response(HttpResponse),
    /// The request was denied without a new outbound attempt.
    Denied(DenialReason),
    /// A duplicate returns completion metadata, not another target attempt.
    /// Response bodies are deliberately not retained in the broker's ledger.
    Replay(RecordedOutcome),
    /// An admission was charged but successful completion could not be established.
    UnknownOutcome {
        /// Upstream failure when known; absent if recording the outcome failed.
        reason: Option<UpstreamError>,
    },
}

/// Failure of a trusted broker control; workload replies use [`BrokerReply`].
#[derive(Debug)]
pub enum BrokerError {
    /// State is unhealthy or the admission lock cannot be acquired safely.
    Unavailable,
    /// The execution has already stopped.
    ExecutionTerminated,
    /// A nonexpired, nonexhausted active grant already exists.
    ActiveGrant,
    /// The trusted resource or principal configuration is invalid.
    InvalidConfiguration,
    /// Secure randomness could not generate fresh identities or a handle.
    EntropyUnavailable,
    /// The grant's deadline is not representable by the trusted clock.
    DeadlineOverflow,
    /// Durable audit failed; this broker permanently refuses further admissions.
    Journal(io::Error),
}

impl fmt::Display for BrokerError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            Self::Unavailable => "broker state is unavailable",
            Self::ExecutionTerminated => "execution has terminated",
            Self::ActiveGrant => "an active grant already exists",
            Self::InvalidConfiguration => "invalid trusted broker configuration",
            Self::EntropyUnavailable => "secure randomness is unavailable",
            Self::DeadlineOverflow => "grant deadline overflow",
            Self::Journal(_) => "journal I/O failed",
        })
    }
}

impl std::error::Error for BrokerError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Journal(error) => Some(error),
            _ => None,
        }
    }
}

/// Host-only accounting snapshot; it does not expose capability handles.
#[derive(Debug, Clone, Copy)]
pub struct BrokerStats {
    /// Whether durable accounting remains available.
    pub healthy: bool,
    /// Current execution lifecycle.
    pub execution_state: ExecutionState,
    /// Charged admissions in the current grant, including uncertain outcomes.
    pub current_grant_admissions: u32,
    /// Currently admitted calls whose dispatch has not finished.
    pub in_flight: usize,
    /// Request IDs retained in the execution's deduplication ledger.
    pub recorded_requests: usize,
}

/// Operator-only snapshot. It never contains the capability handle.
#[derive(Debug, Serialize)]
pub struct OperatorStatus {
    /// Execution ID generated when this broker started.
    pub execution_id: String,
    /// Owning principal from trusted configuration.
    pub principal_id: String,
    /// Fresh broker-session ID.
    pub broker_session_id: String,
    /// Full resource configuration frozen for this session.
    pub resource: ResourceIdentity,
    /// Whether durable accounting remains available.
    pub healthy: bool,
    /// Whether the execution can consider new admissions.
    pub running: bool,
    /// Number of admitted calls still in flight.
    pub in_flight: usize,
    /// Includes conservative reservations whose journal commit was uncertain.
    pub total_admissions: usize,
    /// Current grant metadata, without its capability.
    pub grant: Option<OperatorGrant>,
}

/// Serializable operator view of the current fixed-scope grant.
#[derive(Debug, Serialize)]
pub struct OperatorGrant {
    /// Current grant identity.
    pub grant_id: String,
    /// Explicit active/revoked state; expiry must also be considered.
    pub state: &'static str,
    /// Fixed allowed HTTP method.
    pub method: &'static str,
    /// Fixed allowed HTTP path.
    pub path: &'static str,
    /// Maximum charged admissions for this grant.
    pub max_requests: u32,
    /// Admissions charged to this grant.
    pub admissions: u32,
    /// Unconsumed allowance; this does not establish that the grant is unexpired.
    pub remaining_requests: u32,
    /// Wall-clock deadline in milliseconds since the Unix epoch.
    pub expires_at_ms: u128,
    /// Whether delegation is allowed; always false in this version.
    pub delegation: bool,
}

struct RequestRecord {
    fingerprint: String,
    outcome: RecordedOutcome,
}

struct Runtime {
    journal: Box<dyn AuditJournal>,
    healthy: bool,
    next_sequence: u64,
    execution_state: ExecutionState,
    grant: Option<Grant>,
    capability: Option<CapabilityBinding>,
    admitted_requests: u32,
    in_flight: usize,
    requests: HashMap<String, RequestRecord>,
}

/// One host-bound execution. Share the same instance across its connections and
/// processes; constructing a broker per request would create a new execution.
/// Only trusted operator code may call `issue_grant`, `revoke_grant`, or `stop`.
pub struct Broker {
    context: ExecutionContext,
    resource: ResourceIdentity,
    resource_digest: String,
    clock: Arc<dyn Clock>,
    runtime: Mutex<Runtime>,
}

#[derive(Default)]
struct Metadata<'a> {
    grant_id: Option<&'a str>,
    request_id: Option<&'a str>,
    operation_digest: Option<&'a str>,
}

fn hex(bytes: &[u8]) -> String {
    use std::fmt::Write;
    let mut text = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        write!(text, "{byte:02x}").expect("writing to String cannot fail");
    }
    text
}

fn fresh_id(prefix: &str) -> Result<String, BrokerError> {
    let mut bytes = [0; 32];
    getrandom::fill(&mut bytes).map_err(|_| BrokerError::EntropyUnavailable)?;
    Ok(format!("{prefix}-{}", hex(&bytes)))
}

fn fingerprint(parts: &[&[u8]]) -> String {
    let mut hash = Sha256::new();
    for part in parts {
        hash.update((part.len() as u64).to_be_bytes());
        hash.update(part);
    }
    hex(&hash.finalize())
}

fn milliseconds(time: SystemTime) -> u128 {
    time.duration_since(SystemTime::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
}

impl Broker {
    /// Create a fresh execution with secure IDs and durably record its start.
    /// The journal must be private, fresh and outside workload-writable paths.
    pub fn start(
        config: BrokerConfig,
        journal: impl AuditJournal + 'static,
    ) -> Result<Self, BrokerError> {
        Self::start_with_clock(config, journal, Arc::new(SystemClock))
    }

    /// Start with a trusted injectable clock, retaining normal admission behavior.
    pub fn start_with_clock(
        config: BrokerConfig,
        journal: impl AuditJournal + 'static,
        clock: Arc<dyn Clock>,
    ) -> Result<Self, BrokerError> {
        if config.principal_id.as_str().is_empty()
            || config.resource.resource_id.as_str().is_empty()
            || config.resource.account.is_empty()
            || config.resource.environment.is_empty()
            || config.resource.adapter_version.is_empty()
            || config.resource.origin.0.port() == 0
            || config.resource.origin.0.ip().is_unspecified()
            || config.resource.origin.0.ip().is_multicast()
        {
            return Err(BrokerError::InvalidConfiguration);
        }
        let resource_bytes =
            serde_json::to_vec(&config.resource).map_err(|_| BrokerError::InvalidConfiguration)?;
        let broker = Self {
            context: ExecutionContext {
                principal_id: config.principal_id,
                execution_id: ExecutionId::new(fresh_id("run")?),
                broker_session_id: BrokerSessionId::new(fresh_id("session")?),
                initial_artifact_digest: config.initial_artifact_digest,
            },
            resource: config.resource,
            resource_digest: fingerprint(&[&resource_bytes]),
            clock,
            runtime: Mutex::new(Runtime {
                journal: Box::new(journal),
                healthy: true,
                next_sequence: 1,
                execution_state: ExecutionState::Running,
                grant: None,
                capability: None,
                admitted_requests: 0,
                in_flight: 0,
                requests: HashMap::new(),
            }),
        };
        {
            let mut state = broker.lock()?;
            broker.record(
                &mut state,
                AuditEvent::ExecutionStarted,
                Metadata::default(),
            )?;
        }
        Ok(broker)
    }

    /// Host-generated identity permanently bound to this broker instance.
    pub fn execution_context(&self) -> &ExecutionContext {
        &self.context
    }

    fn lock(&self) -> Result<MutexGuard<'_, Runtime>, BrokerError> {
        self.runtime.lock().map_err(|_| BrokerError::Unavailable)
    }

    fn record(
        &self,
        state: &mut Runtime,
        event: AuditEvent,
        metadata: Metadata<'_>,
    ) -> Result<(), BrokerError> {
        if !state.healthy {
            return Err(BrokerError::Unavailable);
        }
        let Some(next) = state.next_sequence.checked_add(1) else {
            state.healthy = false;
            return Err(BrokerError::Unavailable);
        };
        let record = AuditRecord {
            sequence: state.next_sequence,
            timestamp_ms: milliseconds(self.clock.now().wall),
            principal_id: self.context.principal_id.as_str().into(),
            execution_id: self.context.execution_id.as_str().into(),
            broker_session_id: self.context.broker_session_id.as_str().into(),
            initial_artifact_digest: hex(&self.context.initial_artifact_digest),
            resource: self.resource.clone(),
            grant_id: metadata.grant_id.map(str::to_owned),
            request_id: metadata.request_id.map(str::to_owned),
            operation_digest: metadata.operation_digest.map(str::to_owned),
            event,
        };
        if let Err(error) = state.journal.append(&record) {
            state.healthy = false;
            return Err(BrokerError::Journal(error));
        }
        state.next_sequence = next;
        Ok(())
    }

    /// Explicit trusted issuance; never exposed through the workload protocol.
    pub fn issue_grant(&self) -> Result<GrantAccess, BrokerError> {
        let mut state = self.lock()?;
        if !state.healthy {
            return Err(BrokerError::Unavailable);
        }
        if state.execution_state != ExecutionState::Running {
            return Err(BrokerError::ExecutionTerminated);
        }
        let now = self.clock.now();
        if state.grant.as_ref().is_some_and(|grant| {
            grant.state() == GrantState::Active
                && !grant.is_expired_at(now.wall, now.monotonic)
                && state.admitted_requests < grant.max_requests()
        }) {
            self.record(
                &mut state,
                AuditEvent::GrantRejected {
                    reason: "active_grant_exists",
                },
                Metadata::default(),
            )?;
            return Err(BrokerError::ActiveGrant);
        }
        let grant = Grant::issue(
            GrantId::new(fresh_id("grant")?),
            self.context.clone(),
            self.resource.clone(),
            now.wall,
            now.monotonic,
        )
        .map_err(|_| BrokerError::DeadlineOverflow)?;
        let handle = fresh_id("cap")?;
        let access = GrantAccess {
            grant_id: grant.id().clone(),
            capability: handle.clone(),
            expires_at: grant.expires_at(),
        };
        self.record(
            &mut state,
            AuditEvent::GrantIssued {
                max_requests: grant.max_requests(),
                expires_at_ms: milliseconds(grant.expires_at()),
            },
            Metadata {
                grant_id: Some(grant.id().as_str()),
                ..Metadata::default()
            },
        )?;
        state.capability = Some(CapabilityBinding::new(handle, &grant));
        state.grant = Some(grant);
        state.admitted_requests = 0;
        Ok(access)
    }

    /// Shares the admission lock; success means revocation is durably committed.
    pub fn revoke_grant(&self) -> Result<(), BrokerError> {
        let mut state = self.lock()?;
        if !state.healthy {
            return Err(BrokerError::Unavailable);
        }
        if state.execution_state != ExecutionState::Running {
            return Err(BrokerError::ExecutionTerminated);
        }
        let Some(grant) = state.grant.as_ref() else {
            return Ok(());
        };
        if grant.state() == GrantState::Revoked {
            return Ok(());
        }
        let grant_id = grant.id().as_str().to_owned();
        self.record(
            &mut state,
            AuditEvent::GrantRevoked,
            Metadata {
                grant_id: Some(&grant_id),
                ..Metadata::default()
            },
        )?;
        if let Some(grant) = state.grant.as_mut() {
            grant.revoke();
        }
        Ok(())
    }

    /// Stops new admissions. Operations admitted earlier may still complete.
    pub fn stop(&self) -> Result<(), BrokerError> {
        let mut state = self.lock()?;
        if !state.healthy {
            return Err(BrokerError::Unavailable);
        }
        if state.execution_state == ExecutionState::Terminated {
            return Ok(());
        }
        self.record(
            &mut state,
            AuditEvent::ExecutionTerminated,
            Metadata::default(),
        )?;
        state.execution_state = ExecutionState::Terminated;
        Ok(())
    }

    /// Obtain current accounting under the admission lock, without mutation.
    pub fn stats(&self) -> Result<BrokerStats, BrokerError> {
        let state = self.lock()?;
        Ok(BrokerStats {
            healthy: state.healthy,
            execution_state: state.execution_state,
            current_grant_admissions: state.admitted_requests,
            in_flight: state.in_flight,
            recorded_requests: state.requests.len(),
        })
    }

    /// Obtain a serializable operator snapshot without capability secrets.
    pub fn operator_status(&self) -> Result<OperatorStatus, BrokerError> {
        let state = self.lock()?;
        let now = self.clock.now();
        let grant = state.grant.as_ref().map(|grant| {
            let status = if grant.state() == GrantState::Revoked {
                "revoked"
            } else if grant.is_expired_at(now.wall, now.monotonic) {
                "expired"
            } else if state.admitted_requests >= grant.max_requests() {
                "exhausted"
            } else {
                "active"
            };
            OperatorGrant {
                grant_id: grant.id().as_str().into(),
                state: status,
                method: "GET",
                path: "/invoices",
                max_requests: grant.max_requests(),
                admissions: state.admitted_requests,
                remaining_requests: grant.max_requests().saturating_sub(state.admitted_requests),
                expires_at_ms: milliseconds(grant.expires_at()),
                delegation: false,
            }
        });
        Ok(OperatorStatus {
            execution_id: self.context.execution_id.as_str().into(),
            principal_id: self.context.principal_id.as_str().into(),
            broker_session_id: self.context.broker_session_id.as_str().into(),
            resource: self.resource.clone(),
            healthy: state.healthy,
            running: state.execution_state == ExecutionState::Running,
            in_flight: state.in_flight,
            total_admissions: state.requests.len(),
            grant,
        })
    }

    fn deny(
        &self,
        state: &mut Runtime,
        request: Option<&EffectRequest>,
        digest: Option<&str>,
        reason: DenialReason,
    ) -> BrokerReply {
        let grant_id = state
            .grant
            .as_ref()
            .map(|grant| grant.id().as_str().to_owned());
        let result = self.record(
            state,
            AuditEvent::Denied { reason },
            Metadata {
                grant_id: grant_id.as_deref(),
                request_id: request.map(EffectRequest::request_id),
                operation_digest: digest,
            },
        );
        BrokerReply::Denied(if result.is_ok() {
            reason
        } else {
            DenialReason::StateUnavailable
        })
    }

    /// Transport errors carry no trusted request identity. Keep their denials
    /// in the same protected journal and fail closed if recording fails.
    #[cfg(unix)]
    pub(crate) fn reject_transport(&self, reason: DenialReason) -> BrokerReply {
        match self.lock() {
            Ok(mut state) => self.deny(&mut state, None, None, reason),
            Err(_) => BrokerReply::Denied(DenialReason::StateUnavailable),
        }
    }

    /// Host-only entry point for one execution's channel frames. The Unix
    /// transport also enforces the frame limit before allocating its payload.
    pub fn execute(&self, frame: &[u8]) -> BrokerReply {
        let parsed = EffectRequest::from_json(frame);
        let mut state = match self.lock() {
            Ok(state) => state,
            Err(_) => return BrokerReply::Denied(DenialReason::StateUnavailable),
        };
        if !state.healthy {
            return BrokerReply::Denied(DenialReason::StateUnavailable);
        }
        let request = match parsed {
            Ok(request) => request,
            Err(_) => return self.deny(&mut state, None, None, DenialReason::InvalidRequest),
        };
        let operation_digest = fingerprint(&[
            request.method().as_bytes(),
            request.path().as_bytes(),
            self.resource_digest.as_bytes(),
        ]);
        let now = self.clock.now();
        // Validate present authority before consulting deduplication metadata.
        // A replay needs no additional budget, but cannot bypass revocation,
        // expiry, stopped executions, or capability authentication.
        let authority = AuthorizationState {
            execution: &self.context,
            execution_state: state.execution_state,
            resource: &self.resource,
            grant: state.grant.as_ref(),
            capability: state.capability.as_ref(),
            admitted_requests: 0,
        };
        if let Decision::Deny(reason) =
            check_permission(&request, Some(&authority), now.wall, now.monotonic)
        {
            return self.deny(&mut state, Some(&request), Some(&operation_digest), reason);
        }
        let Some(grant) = state.grant.as_ref() else {
            return self.deny(
                &mut state,
                Some(&request),
                Some(&operation_digest),
                DenialReason::MissingGrant,
            );
        };
        let grant_id = grant.id().as_str().to_owned();
        let request_fingerprint = fingerprint(&[
            operation_digest.as_bytes(),
            request.capability().as_bytes(),
            grant_id.as_bytes(),
        ]);
        let metadata = Metadata {
            grant_id: Some(&grant_id),
            request_id: Some(request.request_id()),
            operation_digest: Some(&operation_digest),
        };
        if let Some(record) = state.requests.get(request.request_id()) {
            if record.fingerprint != request_fingerprint {
                return self.deny(
                    &mut state,
                    Some(&request),
                    Some(&operation_digest),
                    DenialReason::RequestConflict,
                );
            }
            let outcome = record.outcome;
            return if self
                .record(&mut state, AuditEvent::DuplicateObserved, metadata)
                .is_ok()
            {
                BrokerReply::Replay(outcome)
            } else {
                BrokerReply::Denied(DenialReason::StateUnavailable)
            };
        }
        if state.admitted_requests >= grant.max_requests() {
            return self.deny(
                &mut state,
                Some(&request),
                Some(&operation_digest),
                DenialReason::BudgetExhausted,
            );
        }
        if state.in_flight >= MAX_IN_FLIGHT || state.requests.len() >= MAX_RECORDED_REQUESTS {
            return self.deny(
                &mut state,
                Some(&request),
                Some(&operation_digest),
                DenialReason::CapacityExceeded,
            );
        }
        let ordinal = state.admitted_requests + 1;
        let committed = self
            .record(&mut state, AuditEvent::Admitted { ordinal }, metadata)
            .is_ok();
        // No other control/admission operation can interleave with this commit.
        // A failed sync can still have persisted the record. Conservatively
        // retain the reservation and report uncertainty; never refund/retry it.
        state.admitted_requests = ordinal;
        state.requests.insert(
            request.request_id().to_owned(),
            RequestRecord {
                fingerprint: request_fingerprint,
                outcome: RecordedOutcome::InProgress,
            },
        );
        if !committed {
            state
                .requests
                .get_mut(request.request_id())
                .expect("reserved request exists")
                .outcome = RecordedOutcome::Unknown { reason: None };
            return BrokerReply::UnknownOutcome { reason: None };
        }
        if self
            .record(
                &mut state,
                AuditEvent::DispatchIntent,
                Metadata {
                    grant_id: Some(&grant_id),
                    request_id: Some(request.request_id()),
                    operation_digest: Some(&operation_digest),
                },
            )
            .is_err()
        {
            state
                .requests
                .get_mut(request.request_id())
                .expect("admitted request exists")
                .outcome = RecordedOutcome::Unknown { reason: None };
            return BrokerReply::UnknownOutcome { reason: None };
        }
        state.in_flight += 1;
        // The HTTP destination and operation are fixed by trusted configuration.
        // Revocation can commit while this already-admitted attempt is in flight.
        drop(state);
        let response = http::get_invoices(self.resource.origin.0);
        let mut state = match self.lock() {
            Ok(state) => state,
            Err(_) => return BrokerReply::UnknownOutcome { reason: None },
        };
        state.in_flight -= 1;
        let event = match &response {
            Ok(response) => AuditEvent::ResponseReceived {
                status: response.status,
                body_bytes: response.body.len(),
            },
            Err(reason) => AuditEvent::UpstreamFailed { reason: *reason },
        };
        let recorded = self
            .record(
                &mut state,
                event,
                Metadata {
                    grant_id: Some(&grant_id),
                    request_id: Some(request.request_id()),
                    operation_digest: Some(&operation_digest),
                },
            )
            .is_ok();
        let outcome = if !recorded {
            RecordedOutcome::Unknown { reason: None }
        } else {
            match &response {
                Ok(response) => RecordedOutcome::Completed {
                    status: response.status,
                },
                Err(reason) => RecordedOutcome::Unknown {
                    reason: Some(*reason),
                },
            }
        };
        state
            .requests
            .get_mut(request.request_id())
            .expect("admitted request exists")
            .outcome = outcome;
        match (recorded, response) {
            (true, Ok(response)) => BrokerReply::Response(response),
            (true, Err(reason)) => BrokerReply::UnknownOutcome {
                reason: Some(reason),
            },
            (false, _) => BrokerReply::UnknownOutcome { reason: None },
        }
    }
}
