use std::fs::{File, OpenOptions};
use std::io::{self, Write};
use std::path::Path;

use invariant_core::{DenialReason, ResourceIdentity};
use serde::Serialize;

use crate::http::UpstreamError;

/// A trusted sink. Returning success MUST mean the full record is durable.
/// The broker serializes calls and permanently fails closed on any sink error.
pub trait AuditJournal: Send {
    fn append(&mut self, record: &AuditRecord) -> io::Result<()>;
}

/// Creates a fresh journal, never overwriting or resuming an existing session.
/// Its directory must be outside every workload's writable mounts.
pub struct FileJournal {
    file: File,
}

impl FileJournal {
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

#[derive(Debug, Serialize)]
pub struct AuditRecord {
    pub sequence: u64,
    pub timestamp_ms: u128,
    pub principal_id: String,
    pub execution_id: String,
    pub broker_session_id: String,
    pub initial_artifact_digest: String,
    pub resource: ResourceIdentity,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub grant_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub request_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub operation_digest: Option<String>,
    #[serde(flatten)]
    pub event: AuditEvent,
}

#[derive(Debug, Serialize)]
#[serde(tag = "event", rename_all = "snake_case")]
pub enum AuditEvent {
    ExecutionStarted,
    ExecutionTerminated,
    GrantIssued {
        max_requests: u32,
        expires_at_ms: u128,
    },
    GrantRejected {
        reason: &'static str,
    },
    GrantRevoked,
    Denied {
        reason: DenialReason,
    },
    DuplicateObserved,
    Admitted {
        ordinal: u32,
    },
    DispatchIntent,
    ResponseReceived {
        status: u16,
        body_bytes: usize,
    },
    UpstreamFailed {
        reason: UpstreamError,
    },
}
