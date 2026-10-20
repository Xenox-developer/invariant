use std::fs::{self, DirBuilder, Metadata, Permissions};
use std::io::{self, Read, Write};
use std::net::Shutdown;
use std::os::unix::fs::{DirBuilderExt, FileTypeExt, MetadataExt, PermissionsExt};
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use base64::{Engine as _, engine::general_purpose::STANDARD};
use invariant_core::{DenialReason, ExecutionState, MAX_REQUEST_FRAME_BYTES};
use serde::Serialize;

use crate::{
    Broker, BrokerReply, MAX_IN_FLIGHT, MAX_RESPONSE_BYTES, RecordedOutcome, UpstreamError,
};

pub const MAX_SOCKET_CONNECTIONS: usize = MAX_IN_FLIGHT;
pub const SOCKET_IO_TIMEOUT: Duration = Duration::from_secs(2);
pub const MAX_SOCKET_RESPONSE_BYTES: usize = MAX_RESPONSE_BYTES.div_ceil(3) * 4 + 1024;

#[derive(Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
enum WireReply {
    Response { status: u16, body_base64: String },
    Denied { reason: DenialReason },
    Replay { outcome: WireOutcome },
    UnknownOutcome { reason: Option<UpstreamError> },
}

#[derive(Serialize)]
#[serde(tag = "state", rename_all = "snake_case")]
enum WireOutcome {
    InProgress,
    Completed { status: u16 },
    Unknown { reason: Option<UpstreamError> },
}

impl From<BrokerReply> for WireReply {
    fn from(reply: BrokerReply) -> Self {
        match reply {
            BrokerReply::Response(response) => Self::Response {
                status: response.status,
                body_base64: STANDARD.encode(response.body),
            },
            BrokerReply::Denied(reason) => Self::Denied { reason },
            BrokerReply::Replay(outcome) => Self::Replay {
                outcome: match outcome {
                    RecordedOutcome::InProgress => WireOutcome::InProgress,
                    RecordedOutcome::Completed { status } => WireOutcome::Completed { status },
                    RecordedOutcome::Unknown { reason } => WireOutcome::Unknown { reason },
                },
            },
            BrokerReply::UnknownOutcome { reason } => Self::UnknownOutcome { reason },
        }
    }
}

fn remaining(deadline: Instant) -> io::Result<Duration> {
    deadline
        .checked_duration_since(Instant::now())
        .filter(|value| !value.is_zero())
        .ok_or_else(|| io::Error::new(io::ErrorKind::TimedOut, "socket frame deadline expired"))
}

/// `false` means clean EOF before any bytes; partial frames are always errors.
fn read_exact(
    stream: &mut UnixStream,
    mut buffer: &mut [u8],
    deadline: Instant,
) -> io::Result<bool> {
    let mut started = false;
    while !buffer.is_empty() {
        stream.set_read_timeout(Some(remaining(deadline)?))?;
        match stream.read(buffer) {
            Ok(0) if !started => return Ok(false),
            Ok(0) => {
                return Err(io::Error::new(
                    io::ErrorKind::UnexpectedEof,
                    "truncated socket frame",
                ));
            }
            Ok(count) => {
                started = true;
                buffer = &mut buffer[count..];
            }
            Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
            Err(error) => return Err(error),
        }
    }
    Ok(true)
}

fn read_frame(stream: &mut UnixStream) -> io::Result<Option<Vec<u8>>> {
    let deadline = Instant::now() + SOCKET_IO_TIMEOUT;
    let mut prefix = [0; 4];
    if !read_exact(stream, &mut prefix, deadline)? {
        return Ok(None);
    }
    let length = u32::from_be_bytes(prefix) as usize;
    if length == 0 || length > MAX_REQUEST_FRAME_BYTES {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "invalid socket frame length",
        ));
    }
    // Length is checked before allocation and before reading any payload.
    let mut frame = vec![0; length];
    if !read_exact(stream, &mut frame, deadline)? {
        return Err(io::Error::new(
            io::ErrorKind::UnexpectedEof,
            "missing socket frame payload",
        ));
    }
    Ok(Some(frame))
}

fn write_reply(stream: &mut UnixStream, reply: BrokerReply, timeout: Duration) -> io::Result<()> {
    let payload = serde_json::to_vec(&WireReply::from(reply)).map_err(io::Error::other)?;
    if payload.len() > MAX_SOCKET_RESPONSE_BYTES {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "socket response limit exceeded",
        ));
    }
    let prefix = (payload.len() as u32).to_be_bytes();
    let deadline = Instant::now() + timeout;
    for mut bytes in [prefix.as_slice(), payload.as_slice()] {
        while !bytes.is_empty() {
            stream.set_write_timeout(Some(remaining(deadline)?))?;
            match stream.write(bytes) {
                Ok(0) => return Err(io::Error::new(io::ErrorKind::WriteZero, "socket closed")),
                Ok(count) => bytes = &bytes[count..],
                Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
                Err(error) => return Err(error),
            }
        }
    }
    Ok(())
}

fn handle_connection(mut stream: UnixStream, broker: Arc<Broker>, stopping: Arc<AtomicBool>) {
    while !stopping.load(Ordering::Acquire) {
        let frame = match read_frame(&mut stream) {
            Ok(Some(frame)) => frame,
            Ok(None) => return,
            Err(_) => {
                if !stopping.load(Ordering::Acquire) {
                    let reply = broker.reject_transport(DenialReason::InvalidRequest);
                    let _ = write_reply(&mut stream, reply, SOCKET_IO_TIMEOUT);
                }
                return;
            }
        };
        let reply = broker.execute(&frame);
        if write_reply(&mut stream, reply, SOCKET_IO_TIMEOUT).is_err() {
            return;
        }
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
struct Identity {
    device: u64,
    inode: u64,
}
impl Identity {
    fn of(metadata: &Metadata) -> Self {
        Self {
            device: metadata.dev(),
            inode: metadata.ino(),
        }
    }
}

struct Endpoint {
    directory: PathBuf,
    socket: PathBuf,
    directory_identity: Identity,
    socket_identity: Identity,
}

impl Endpoint {
    fn remove(&self) -> io::Result<()> {
        match fs::symlink_metadata(&self.socket) {
            Ok(metadata)
                if metadata.file_type().is_socket()
                    && Identity::of(&metadata) == self.socket_identity =>
            {
                fs::remove_file(&self.socket)?
            }
            Ok(_) => {
                return Err(io::Error::other(
                    "socket path identity changed; preserving replacement",
                ));
            }
            Err(error) if error.kind() == io::ErrorKind::NotFound => {}
            Err(error) => return Err(error),
        }
        match fs::symlink_metadata(&self.directory) {
            Ok(metadata)
                if metadata.is_dir() && Identity::of(&metadata) == self.directory_identity =>
            {
                fs::remove_dir(&self.directory)
            }
            Ok(_) => Err(io::Error::other(
                "socket directory identity changed; preserving replacement",
            )),
            Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
            Err(error) => Err(error),
        }
    }
}

struct Worker {
    thread: JoinHandle<()>,
    connection: UnixStream,
}

fn serve(listener: UnixListener, broker: Arc<Broker>, stopping: Arc<AtomicBool>) -> io::Result<()> {
    let mut workers: Vec<Worker> = Vec::new();
    let result = (|| {
        while !stopping.load(Ordering::Acquire) {
            if !broker.stats().is_ok_and(|stats| {
                stats.healthy && stats.execution_state == ExecutionState::Running
            }) {
                break;
            }
            let mut index = 0;
            while index < workers.len() {
                if workers[index].thread.is_finished() {
                    let worker = workers.swap_remove(index);
                    worker
                        .thread
                        .join()
                        .map_err(|_| io::Error::other("socket worker panicked"))?;
                } else {
                    index += 1;
                }
            }
            match listener.accept() {
                Ok((mut stream, _)) => {
                    // macOS accepted sockets may inherit listener nonblocking mode.
                    stream.set_nonblocking(false)?;
                    if workers.len() >= MAX_SOCKET_CONNECTIONS {
                        let reply = broker.reject_transport(DenialReason::CapacityExceeded);
                        let _ = write_reply(&mut stream, reply, Duration::from_millis(100));
                        continue;
                    }
                    let connection = stream.try_clone()?;
                    let broker = broker.clone();
                    let stopping = stopping.clone();
                    let thread = thread::Builder::new()
                        .name("invariant-socket-client".into())
                        .spawn(move || handle_connection(stream, broker, stopping))?;
                    workers.push(Worker { thread, connection });
                }
                Err(error) if error.kind() == io::ErrorKind::WouldBlock => {
                    thread::sleep(Duration::from_millis(1))
                }
                Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
                Err(error) => return Err(error),
            }
        }
        Ok(())
    })();
    stopping.store(true, Ordering::Release);
    // Even if recording termination fails, stop accepting and close channels.
    let stopped = broker.stop().map_err(io::Error::other);
    for worker in &workers {
        let _ = worker.connection.shutdown(Shutdown::Both);
    }
    let mut joined = Ok(());
    for worker in workers {
        if worker.thread.join().is_err() {
            joined = Err(io::Error::other("socket worker panicked"));
        }
    }
    result.and(stopped).and(joined)
}

/// One dedicated listener, permanently bound to one broker execution. The
/// protocol exposes effects only; trusted grant/lifecycle controls remain Rust
/// methods. Directory permissions help local use but do not isolate processes
/// running as the same user. Docker/private mounts remain a separate step.
pub struct UnixBrokerServer {
    broker: Arc<Broker>,
    endpoint: Arc<Endpoint>,
    stopping: Arc<AtomicBool>,
    thread: Option<JoinHandle<io::Result<()>>>,
}

impl UnixBrokerServer {
    pub fn start(broker: Arc<Broker>) -> io::Result<Self> {
        Self::start_in(broker, std::env::temp_dir())
    }

    /// `parent` must already exist and be selected by trusted host code. A fresh
    /// private directory is created below it. Long Unix socket paths fail at
    /// startup rather than falling back to an existing or shared endpoint.
    pub fn start_in(broker: Arc<Broker>, parent: impl AsRef<Path>) -> io::Result<Self> {
        let stats = broker.stats().map_err(io::Error::other)?;
        if !stats.healthy || stats.execution_state != ExecutionState::Running {
            return Err(io::Error::other("broker is not running"));
        }
        // A shorter name keeps common macOS temporary paths within sun_path.
        let mut name = crate::fresh_id("iv").map_err(io::Error::other)?;
        name.truncate(35); // Prefix plus 128 bits of fresh randomness.
        let directory = parent.as_ref().join(name);
        DirBuilder::new().mode(0o700).create(&directory)?;
        let socket = directory.join("s");
        let listener = match UnixListener::bind(&socket) {
            Ok(listener) => listener,
            Err(error) => {
                let _ = fs::remove_dir(&directory);
                return Err(error);
            }
        };
        let setup = (|| {
            fs::set_permissions(&socket, Permissions::from_mode(0o600))?;
            listener.set_nonblocking(true)?;
            Ok(Endpoint {
                directory: directory.clone(),
                socket: socket.clone(),
                directory_identity: Identity::of(&fs::symlink_metadata(&directory)?),
                socket_identity: Identity::of(&fs::symlink_metadata(&socket)?),
            })
        })();
        let endpoint = match setup {
            Ok(endpoint) => Arc::new(endpoint),
            Err(error) => {
                let _ = fs::remove_file(&socket);
                let _ = fs::remove_dir(&directory);
                return Err(error);
            }
        };
        let stopping = Arc::new(AtomicBool::new(false));
        let worker_endpoint = endpoint.clone();
        let worker_stopping = stopping.clone();
        let worker_broker = broker.clone();
        let thread = match thread::Builder::new()
            .name("invariant-socket-listener".into())
            .spawn(move || {
                let result = serve(listener, worker_broker, worker_stopping);
                let removed = worker_endpoint.remove();
                result.and(removed)
            }) {
            Ok(thread) => thread,
            Err(error) => {
                let _ = endpoint.remove();
                return Err(error);
            }
        };
        Ok(Self {
            broker,
            endpoint,
            stopping,
            thread: Some(thread),
        })
    }

    pub fn socket_path(&self) -> &Path {
        &self.endpoint.socket
    }

    /// Commits execution termination, closes existing channels, joins workers,
    /// and removes only this endpoint. Previously admitted HTTP calls may finish.
    pub fn shutdown(&mut self) -> io::Result<()> {
        let stopped = self.broker.stop().map_err(io::Error::other);
        self.stopping.store(true, Ordering::Release);
        let joined = match self.thread.take() {
            Some(thread) => thread
                .join()
                .map_err(|_| io::Error::other("socket listener panicked"))
                .and_then(|result| result),
            None => Ok(()),
        };
        let removed = self.endpoint.remove();
        stopped.and(joined).and(removed)
    }
}

impl Drop for UnixBrokerServer {
    fn drop(&mut self) {
        let _ = self.shutdown();
    }
}
