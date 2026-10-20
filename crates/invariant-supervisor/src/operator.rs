//! A separate host-only control protocol. Workload effect sockets never route
//! here, and no bearer capabilities are included in replies.
use std::fs::{self, File, Permissions};
use std::io::{self, Read, Write};
use std::os::fd::AsRawFd;
use std::os::unix::fs::{FileTypeExt, MetadataExt, PermissionsExt};
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use invariant_broker::{Broker, BrokerError};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

use crate::{failure, random_name, write_new};

const MAX_REQUEST: usize = 1024;
const MAX_REPLY: usize = 16384;
const IO_TIMEOUT: Duration = Duration::from_secs(2);

#[derive(Debug, Serialize, Deserialize)]
#[serde(tag = "action", rename_all = "snake_case", deny_unknown_fields)]
pub enum OperatorRequest {
    Status { execution_id: Option<String> },
    Grant { execution_id: String },
    Revoke { execution_id: String },
    Stop { execution_id: String },
}

/// An atomic rename is visible through the read-only DIRECTORY mount; an exact
/// file bind would pin the old inode and prevent live capability rotation.
pub(crate) fn publish(directory: &Path, capability: &str) -> io::Result<()> {
    let temporary = directory.join(random_name()?);
    write_new(&temporary, capability.as_bytes(), 0o400)?;
    let result = (|| {
        #[cfg(target_os = "linux")]
        std::os::unix::fs::chown(&temporary, Some(65532), Some(65532))?;
        File::open(&temporary)?.sync_all()?;
        fs::rename(&temporary, directory.join("capability"))?;
        File::open(directory)?.sync_all()
    })();
    if result.is_err() {
        let _ = fs::remove_file(temporary);
    }
    result
}

fn broker_error(error: BrokerError) -> &'static str {
    match error {
        BrokerError::ActiveGrant => "active_grant_exists",
        BrokerError::ExecutionTerminated => "execution_terminated",
        _ => "state_unavailable",
    }
}

fn handle(broker: &Broker, credentials: &Path, request: OperatorRequest) -> Value {
    let expected = match &request {
        OperatorRequest::Status { execution_id } => execution_id.as_deref(),
        OperatorRequest::Grant { execution_id }
        | OperatorRequest::Revoke { execution_id }
        | OperatorRequest::Stop { execution_id } => Some(execution_id.as_str()),
    };
    if expected.is_some_and(|id| id != broker.execution_context().execution_id.as_str()) {
        return json!({"ok":false,"error":"wrong_execution"});
    }
    let result = match request {
        OperatorRequest::Status { .. } => Ok(()),
        OperatorRequest::Grant { .. } => match broker.issue_grant() {
            Ok(access) => {
                if publish(credentials, &access.capability).is_err() {
                    // Issuance is already durable; delivery uncertainty must
                    // never leave a seemingly failed grant quietly usable.
                    return json!({"ok":false,"error":"state_unavailable"});
                }
                Ok(())
            }
            Err(error) => Err(error),
        },
        OperatorRequest::Revoke { .. } => broker.revoke_grant(),
        OperatorRequest::Stop { .. } => broker.stop(),
    };
    if let Err(error) = result {
        return json!({"ok":false,"error":broker_error(error)});
    }
    match broker.operator_status() {
        Ok(status) => json!({"ok":true,"status":status}),
        Err(_) => json!({"ok":false,"error":"state_unavailable"}),
    }
}

fn ready(stream: &UnixStream, events: libc::c_short, deadline: Instant) -> io::Result<()> {
    loop {
        let remaining = deadline
            .checked_duration_since(Instant::now())
            .filter(|d| !d.is_zero())
            .ok_or_else(|| failure("operator frame timed out"))?;
        let timeout = i32::try_from(remaining.as_millis().saturating_add(1)).unwrap_or(i32::MAX);
        let mut descriptor = libc::pollfd {
            fd: stream.as_raw_fd(),
            events,
            revents: 0,
        };
        // Poll one valid descriptor. Nonblocking reads/writes below handle
        // readiness races. Unlike SO_RCVTIMEO, poll also works on macOS after
        // the peer closes with a complete reply still buffered locally.
        let result = unsafe { libc::poll(&mut descriptor, 1, timeout) };
        if result > 0 {
            return Ok(());
        }
        if result == 0 {
            return Err(failure("operator frame timed out"));
        }
        let error = io::Error::last_os_error();
        if error.kind() != io::ErrorKind::Interrupted {
            return Err(error);
        }
    }
}

fn read_exact(stream: &mut UnixStream, mut bytes: &mut [u8], deadline: Instant) -> io::Result<()> {
    stream.set_nonblocking(true)?;
    while !bytes.is_empty() {
        ready(stream, libc::POLLIN, deadline)?;
        let count = match stream.read(bytes) {
            Ok(count) => count,
            Err(error)
                if matches!(
                    error.kind(),
                    io::ErrorKind::WouldBlock | io::ErrorKind::Interrupted
                ) =>
            {
                continue;
            }
            Err(error) => return Err(error),
        };
        if count == 0 {
            return Err(failure("incomplete operator frame"));
        }
        bytes = &mut bytes[count..];
    }
    Ok(())
}
fn read_frame(stream: &mut UnixStream, maximum: usize) -> io::Result<Vec<u8>> {
    let deadline = Instant::now() + IO_TIMEOUT;
    let mut prefix = [0; 4];
    read_exact(stream, &mut prefix, deadline)?;
    let length = u32::from_be_bytes(prefix) as usize;
    if length == 0 || length > maximum {
        return Err(failure("invalid operator frame size"));
    }
    let mut bytes = vec![0; length];
    read_exact(stream, &mut bytes, deadline)?;
    Ok(bytes)
}
fn write_frame(stream: &mut UnixStream, value: &impl Serialize, maximum: usize) -> io::Result<()> {
    let bytes = serde_json::to_vec(value)?;
    if bytes.is_empty() || bytes.len() > maximum {
        return Err(failure("operator frame too large"));
    }
    let deadline = Instant::now() + IO_TIMEOUT;
    stream.set_nonblocking(true)?;
    for mut part in [
        (bytes.len() as u32).to_be_bytes().as_slice(),
        bytes.as_slice(),
    ] {
        while !part.is_empty() {
            ready(stream, libc::POLLOUT, deadline)?;
            let count = match stream.write(part) {
                Ok(count) => count,
                Err(error)
                    if matches!(
                        error.kind(),
                        io::ErrorKind::WouldBlock | io::ErrorKind::Interrupted
                    ) =>
                {
                    continue;
                }
                Err(error) => return Err(error),
            };
            if count == 0 {
                return Err(failure("operator connection closed"));
            }
            part = &part[count..];
        }
    }
    Ok(())
}

pub fn operator_command(state: &Path, request: &OperatorRequest) -> io::Result<Value> {
    let mut stream = UnixStream::connect(state.join("operator.sock"))?;
    write_frame(&mut stream, request, MAX_REQUEST)?;
    serde_json::from_slice(&read_frame(&mut stream, MAX_REPLY)?).map_err(io::Error::other)
}

pub(crate) fn remove_stale(state: &Path) -> io::Result<()> {
    let path = state.join("operator.sock");
    match fs::symlink_metadata(&path) {
        Ok(metadata) if metadata.file_type().is_socket() => fs::remove_file(path),
        Ok(_) => Err(failure("unexpected operator endpoint; preserving file")),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error),
    }
}

pub(crate) struct OperatorServer {
    path: PathBuf,
    identity: (u64, u64),
    stopping: Arc<AtomicBool>,
    failed: Arc<AtomicBool>,
    thread: Option<JoinHandle<io::Result<()>>>,
}
impl OperatorServer {
    pub fn start(state: &Path, credentials: &Path, broker: Arc<Broker>) -> io::Result<Self> {
        let path = state.join("operator.sock");
        let listener = UnixListener::bind(&path)?;
        fs::set_permissions(&path, Permissions::from_mode(0o600))?;
        listener.set_nonblocking(true)?;
        let metadata = fs::symlink_metadata(&path)?;
        let identity = (metadata.dev(), metadata.ino());
        let stopping = Arc::new(AtomicBool::new(false));
        let stopped = stopping.clone();
        let failed = Arc::new(AtomicBool::new(false));
        let thread_failed = failed.clone();
        let credentials = credentials.to_path_buf();
        let thread = thread::spawn(move || {
            while !stopped.load(Ordering::Acquire) {
                match listener.accept() {
                    Ok((mut stream, _)) => {
                        stream.set_nonblocking(false)?;
                        let reply = match read_frame(&mut stream, MAX_REQUEST).and_then(|b| {
                            serde_json::from_slice::<OperatorRequest>(&b).map_err(io::Error::other)
                        }) {
                            Ok(request) if !stopped.load(Ordering::Acquire) => {
                                handle(&broker, &credentials, request)
                            }
                            _ => json!({"ok":false,"error":"invalid_request"}),
                        };
                        if reply["error"] == "state_unavailable" {
                            thread_failed.store(true, Ordering::Release);
                            let _ = broker.stop();
                        }
                        let _ = write_frame(&mut stream, &reply, MAX_REPLY);
                    }
                    Err(error) if error.kind() == io::ErrorKind::WouldBlock => {
                        thread::sleep(Duration::from_millis(5))
                    }
                    Err(error) => return Err(error),
                }
            }
            Ok(())
        });
        Ok(Self {
            path,
            identity,
            stopping,
            failed,
            thread: Some(thread),
        })
    }
    pub fn shutdown(&mut self) -> io::Result<()> {
        self.stopping.store(true, Ordering::Release);
        let joined = match self.thread.take() {
            Some(thread) => thread
                .join()
                .map_err(|_| failure("operator server panicked"))?,
            None => Ok(()),
        };
        let removed = match fs::symlink_metadata(&self.path) {
            Ok(metadata)
                if metadata.file_type().is_socket()
                    && (metadata.dev(), metadata.ino()) == self.identity =>
            {
                fs::remove_file(&self.path)
            }
            Ok(_) => Err(failure("operator endpoint changed; preserving replacement")),
            Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
            Err(error) => Err(error),
        };
        joined.and(removed)
    }
    pub fn healthy(&self) -> bool {
        !self.failed.load(Ordering::Acquire)
            && self.thread.as_ref().is_some_and(|t| !t.is_finished())
    }
}
impl Drop for OperatorServer {
    fn drop(&mut self) {
        let _ = self.shutdown();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use invariant_broker::{BrokerConfig, BrokerReply, FileJournal, UnixBrokerServer};
    use invariant_core::{DenialReason, MockHttpOrigin, PrincipalId, ResourceId, ResourceIdentity};
    use std::net::Shutdown;

    struct Fixture {
        state: PathBuf,
        credentials: PathBuf,
        broker: Arc<Broker>,
        server: OperatorServer,
    }
    impl Fixture {
        fn new() -> Self {
            let state = Path::new("/tmp").join(random_name().unwrap());
            fs::create_dir(&state).unwrap();
            fs::set_permissions(&state, Permissions::from_mode(0o700)).unwrap();
            let credentials = state.join("credentials");
            fs::create_dir(&credentials).unwrap();
            publish(&credentials, "not-a-grant").unwrap();
            let broker = Arc::new(
                Broker::start(
                    BrokerConfig {
                        principal_id: PrincipalId::new("operator"),
                        initial_artifact_digest: [0; 32],
                        resource: ResourceIdentity {
                            resource_id: ResourceId::new("mock"),
                            configuration_version: 1,
                            origin: MockHttpOrigin("127.0.0.1:1".parse().unwrap()),
                            account: "mock".into(),
                            environment: "test".into(),
                            adapter_version: "mock-v1".into(),
                        },
                    },
                    FileJournal::create(state.join("audit.jsonl")).unwrap(),
                )
                .unwrap(),
            );
            let server = OperatorServer::start(&state, &credentials, broker.clone()).unwrap();
            Self {
                state,
                credentials,
                broker,
                server,
            }
        }
        fn id(&self) -> String {
            self.broker
                .execution_context()
                .execution_id
                .as_str()
                .to_owned()
        }
        fn call(&self, request: OperatorRequest) -> Value {
            operator_command(&self.state, &request).unwrap()
        }
        fn effect(&self, capability: &str) -> BrokerReply {
            self.broker.execute(&serde_json::to_vec(&json!({"request_id":random_name().unwrap(),"capability":capability,"method":"GET","path":"/invoices"})).unwrap())
        }
        fn raw(&self, bytes: &[u8]) -> Value {
            let mut stream = UnixStream::connect(self.state.join("operator.sock")).unwrap();
            stream
                .write_all(&(bytes.len() as u32).to_be_bytes())
                .unwrap();
            stream.write_all(bytes).unwrap();
            serde_json::from_slice(&read_frame(&mut stream, MAX_REPLY).unwrap()).unwrap()
        }
    }
    impl Drop for Fixture {
        fn drop(&mut self) {
            let _ = self.broker.stop();
            let _ = self.server.shutdown();
            let _ = fs::remove_dir_all(&self.state);
        }
    }

    #[test]
    fn live_controls_issue_revoke_replace_and_stop_without_returning_capabilities() {
        let fixture = Fixture::new();
        assert!(
            fixture.call(OperatorRequest::Status { execution_id: None })["status"]["grant"]
                .is_null()
        );
        assert_eq!(
            fixture.effect("not-a-grant"),
            BrokerReply::Denied(DenialReason::MissingGrant)
        );
        let issued = fixture.call(OperatorRequest::Grant {
            execution_id: fixture.id(),
        });
        assert_eq!(issued["status"]["grant"]["state"], "active");
        let first = fs::read_to_string(fixture.credentials.join("capability")).unwrap();
        assert!(!issued.to_string().contains(&first));
        assert_eq!(
            fixture.call(OperatorRequest::Grant {
                execution_id: fixture.id()
            })["error"],
            "active_grant_exists"
        );
        assert_eq!(
            fs::read_to_string(fixture.credentials.join("capability")).unwrap(),
            first
        );
        let revoked = fixture.call(OperatorRequest::Revoke {
            execution_id: fixture.id(),
        });
        assert_eq!(revoked["status"]["grant"]["state"], "revoked");
        assert_eq!(
            fixture.effect(&first),
            BrokerReply::Denied(DenialReason::Revoked)
        );
        fixture.call(OperatorRequest::Revoke {
            execution_id: fixture.id(),
        }); // idempotent
        let replacement = fixture.call(OperatorRequest::Grant {
            execution_id: fixture.id(),
        });
        assert_ne!(
            issued["status"]["grant"]["grant_id"],
            replacement["status"]["grant"]["grant_id"]
        );
        let second = fs::read_to_string(fixture.credentials.join("capability")).unwrap();
        assert_ne!(first, second);
        assert_eq!(
            fixture.effect(&first),
            BrokerReply::Denied(DenialReason::InvalidCapability)
        );
        assert_eq!(
            fixture.call(OperatorRequest::Stop {
                execution_id: fixture.id()
            })["status"]["running"],
            false
        );
        assert_eq!(
            fixture.call(OperatorRequest::Grant {
                execution_id: fixture.id()
            })["error"],
            "execution_terminated"
        );
        assert_eq!(
            fixture.call(OperatorRequest::Revoke {
                execution_id: fixture.id()
            })["error"],
            "execution_terminated"
        );
        assert_eq!(
            fs::metadata(fixture.state.join("operator.sock"))
                .unwrap()
                .permissions()
                .mode()
                & 0o777,
            0o600
        );
        let audit = fs::read_to_string(fixture.state.join("audit.jsonl")).unwrap();
        assert!(!audit.contains(&first) && !audit.contains(&second));
        assert_eq!(
            audit
                .lines()
                .filter(|l| l.contains("\"event\":\"grant_revoked\""))
                .count(),
            1
        );
    }

    #[test]
    fn wrong_executions_unknown_constraints_and_oversized_frames_cannot_issue_grants() {
        let fixture = Fixture::new();
        assert_eq!(
            fixture.call(OperatorRequest::Grant {
                execution_id: "old-run".into()
            })["error"],
            "wrong_execution"
        );
        for bytes in [
            br#"{"action":"grant"}"#.as_slice(),
            br#"{"action":"grant","execution_id":"x","budget":100}"#,
            br#"{"action":"grant","execution_id":"x","delegation":true}"#,
            br#"{"action":"grant","action":"status","execution_id":"x"}"#,
            br#"{"action":"grant","execution_id":"x","execution_id":"y"}"#,
            br#"{"action":"extend","execution_id":"x"}"#,
        ] {
            assert_eq!(fixture.raw(bytes)["error"], "invalid_request");
        }
        for length in [0, MAX_REQUEST as u32 + 1, u32::MAX] {
            let mut stream = UnixStream::connect(fixture.state.join("operator.sock")).unwrap();
            stream.write_all(&length.to_be_bytes()).unwrap();
            let reply: Value =
                serde_json::from_slice(&read_frame(&mut stream, MAX_REPLY).unwrap()).unwrap();
            assert_eq!(reply["error"], "invalid_request");
        }
        assert!(fixture.broker.operator_status().unwrap().grant.is_none());
        assert_eq!(fixture.broker.stats().unwrap().current_grant_admissions, 0);
    }

    #[test]
    fn partial_control_frames_expire_without_issuing_authority() {
        let fixture = Fixture::new();
        let mut stream = UnixStream::connect(fixture.state.join("operator.sock")).unwrap();
        stream.write_all(&[0]).unwrap();
        let start = Instant::now();
        let deadline = Instant::now() + IO_TIMEOUT * 3;
        let mut prefix = [0; 4];
        read_exact(&mut stream, &mut prefix, deadline).unwrap();
        let mut bytes = vec![0; u32::from_be_bytes(prefix) as usize];
        assert!(bytes.len() <= MAX_REPLY);
        read_exact(&mut stream, &mut bytes, deadline).unwrap();
        let reply: Value = serde_json::from_slice(&bytes).unwrap();
        assert_eq!(reply["error"], "invalid_request");
        assert!(start.elapsed() >= IO_TIMEOUT);
        assert!(fixture.broker.operator_status().unwrap().grant.is_none());
    }

    #[test]
    fn failed_capability_delivery_stops_the_execution_after_committed_issuance() {
        let fixture = Fixture::new();
        fs::remove_file(fixture.credentials.join("capability")).unwrap();
        fs::remove_dir(&fixture.credentials).unwrap();
        assert_eq!(
            fixture.call(OperatorRequest::Grant {
                execution_id: fixture.id()
            })["error"],
            "state_unavailable"
        );
        assert!(!fixture.broker.operator_status().unwrap().running);
        assert!(!fixture.server.healthy());
        assert_eq!(
            fixture.broker.operator_status().unwrap().total_admissions,
            0
        );
        assert_eq!(
            fixture.call(OperatorRequest::Grant {
                execution_id: fixture.id()
            })["error"],
            "execution_terminated"
        );
    }

    #[test]
    fn replacing_the_operator_endpoint_never_causes_its_replacement_to_be_deleted() {
        let mut fixture = Fixture::new();
        fs::remove_file(fixture.state.join("operator.sock")).unwrap();
        fs::write(fixture.state.join("operator.sock"), b"preserve").unwrap();
        assert!(fixture.server.shutdown().is_err());
        assert_eq!(
            fs::read(fixture.state.join("operator.sock")).unwrap(),
            b"preserve"
        );
        assert!(remove_stale(&fixture.state).is_err());
    }

    #[test]
    fn effect_socket_rejects_operator_commands() {
        let fixture = Fixture::new();
        let mut effects =
            UnixBrokerServer::start_in(fixture.broker.clone(), &fixture.state).unwrap();
        let mut stream = UnixStream::connect(effects.socket_path()).unwrap();
        let bytes = serde_json::to_vec(&OperatorRequest::Grant {
            execution_id: fixture.id(),
        })
        .unwrap();
        stream
            .write_all(&(bytes.len() as u32).to_be_bytes())
            .unwrap();
        stream.write_all(&bytes).unwrap();
        let reply: Value =
            serde_json::from_slice(&read_frame(&mut stream, MAX_REPLY).unwrap()).unwrap();
        assert_eq!(reply, json!({"type":"denied","reason":"invalid_request"}));
        assert!(fixture.broker.operator_status().unwrap().grant.is_none());
        let _ = stream.shutdown(Shutdown::Both);
        effects.shutdown().unwrap();
    }
}
