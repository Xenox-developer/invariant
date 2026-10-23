use std::fs::{File, OpenOptions};
use std::io::{self, Write};
use std::path::Path;

use invariant_core::{DenialReason, ResourceIdentity};
use serde::Serialize;

use crate::http::UpstreamError;

/// A trusted sink. Returning success MUST mean the full record is durable.
/// The broker serializes calls and permanently fails closed on any sink error.
pub trait AuditJournal: Send {
    /// Durably append one complete record or return an error and fail closed.
    fn append(&mut self, record: &AuditRecord) -> io::Result<()>;
}

/// Creates a fresh journal, never overwriting or resuming an existing session.
/// Its directory must be outside every workload's writable mounts.
pub struct FileJournal {
    file: File,
}

impl FileJournal {
    /// Exclusively create and sync a fresh journal and its parent entry.
    /// Existing files are rejected; on Unix the new file uses mode 0600.
    pub fn create(path: impl AsRef<Path>) -> io::Result<Self> {
        let path = path.as_ref();
        let mut options = OpenOptions::new();
        options.append(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let file = options.open(path)?;
        file.sync_all()?;
        // Persist the new directory entry as well as the file itself.
        let parent = path
            .parent()
            .filter(|p| !p.as_os_str().is_empty())
            .unwrap_or(Path::new("."));
        File::open(parent)?.sync_all()?;
        Ok(Self { file })
    }
}

impl AuditJournal for FileJournal {
    fn append(&mut self, record: &AuditRecord) -> io::Result<()> {
        let mut bytes = serde_json::to_vec(record).map_err(io::Error::other)?;
        bytes.push(b'\n');
        self.file.write_all(&bytes)?;
        self.file.sync_all()
    }
}

/// One ordered audit event, containing identities and digests but no capability.
#[derive(Debug, Serialize)]
pub struct AuditRecord {
    /// Strictly increasing sequence within this broker session.
    pub sequence: u64,
    /// Trusted wall-clock timestamp in milliseconds since the Unix epoch.
    pub timestamp_ms: u128,
    /// Principal owning the execution.
    pub principal_id: String,
    /// Fresh execution ID.
    pub execution_id: String,
    /// Fresh broker-session ID.
    pub broker_session_id: String,
    /// Hexadecimal SHA-256 of the initial workload artifact.
    pub initial_artifact_digest: String,
    /// Full resource configuration frozen for this session.
    pub resource: ResourceIdentity,
    /// Relevant grant ID, when known.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub grant_id: Option<String>,
    /// Relevant workload request ID, when parsing established one.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub request_id: Option<String>,
    /// Digest binding the method, path and resource configuration.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub operation_digest: Option<String>,
    /// Event data flattened into the serialized record.
    #[serde(flatten)]
    pub event: AuditEvent,
}

/// Stable serialized lifecycle and dispatch events emitted by the broker.
#[derive(Debug, Serialize)]
#[serde(tag = "event", rename_all = "snake_case")]
pub enum AuditEvent {
    /// A fresh execution was created.
    ExecutionStarted,
    /// New admissions were stopped.
    ExecutionTerminated,
    /// A trusted grant issuance was committed.
    GrantIssued {
        /// Fixed admission allowance.
        max_requests: u32,
        /// Wall-clock deadline in milliseconds since the Unix epoch.
        expires_at_ms: u128,
    },
    /// A trusted request to issue a grant was rejected.
    GrantRejected {
        /// Stable rejection reason.
        reason: &'static str,
    },
    /// Revocation was durably committed.
    GrantRevoked,
    /// An effect request was refused without a new target attempt.
    Denied {
        /// Stable denial reason.
        reason: DenialReason,
    },
    /// A previously admitted request ID was observed again.
    DuplicateObserved,
    /// A request consumed an admission before any outbound attempt.
    Admitted {
        /// One-based admission number in the current grant.
        ordinal: u32,
    },
    /// The broker committed intent to make the admitted target attempt.
    DispatchIntent,
    /// A bounded target response was recorded.
    ResponseReceived {
        /// Observed HTTP status, including error statuses.
        status: u16,
        /// Number of received body bytes, without persisting the body here.
        body_bytes: usize,
    },
    /// An admitted target attempt did not produce a supported complete response.
    UpstreamFailed {
        /// Transport, timeout or response-validation failure.
        reason: UpstreamError,
    },
}
