use std::fs;
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpListener};
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::thread::{self, JoinHandle};
use std::time::{Duration, SystemTime};

use invariant_broker::{Broker, BrokerConfig, FileJournal};
use invariant_core::{MockHttpOrigin, PrincipalId, ResourceId, ResourceIdentity};

static DIRECTORY_ID: AtomicU64 = AtomicU64::new(0);

pub struct TestDirectory(pub PathBuf);

impl TestDirectory {
    pub fn new() -> Self {
        let nonce = SystemTime::now()
            .duration_since(SystemTime::UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let path = std::env::temp_dir().join(format!(
            "invariant-test-{}-{nonce}-{}",
            std::process::id(),
            DIRECTORY_ID.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&path).unwrap();
        Self(path)
    }
    pub fn journal(&self) -> PathBuf {
        self.0.join("audit.jsonl")
    }
}

impl Drop for TestDirectory {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

pub struct MockApi {
    pub address: SocketAddr,
    requests: Arc<Mutex<Vec<String>>>,
    stopping: Arc<AtomicBool>,
    thread: Option<JoinHandle<()>>,
}

pub fn response(body: &[u8]) -> Vec<u8> {
    let mut response = format!(
        "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
        body.len()
    )
    .into_bytes();
    response.extend_from_slice(body);
    response
}

impl MockApi {
    pub fn new(handler: impl Fn(&str) -> Vec<u8> + Send + Sync + 'static) -> Self {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        listener.set_nonblocking(true).unwrap();
        let address = listener.local_addr().unwrap();
        let requests = Arc::new(Mutex::new(Vec::new()));
        let observed = requests.clone();
        let stopping = Arc::new(AtomicBool::new(false));
        let stopped = stopping.clone();
        let handler = Arc::new(handler);
        let thread = thread::spawn(move || {
            let mut children = Vec::new();
            while !stopped.load(Ordering::Acquire) {
                match listener.accept() {
                    Ok((mut stream, _)) => {
                        let observed = observed.clone();
                        let handler = handler.clone();
                        children.push(thread::spawn(move || {
                            // Accepted sockets can inherit nonblocking mode on
                            // macOS. Handlers use blocking I/O with deadlines.
                            stream.set_nonblocking(false).unwrap();
                            stream
                                .set_read_timeout(Some(Duration::from_secs(3)))
                                .unwrap();
                            stream
                                .set_write_timeout(Some(Duration::from_secs(3)))
                                .unwrap();
                            let mut bytes = Vec::new();
                            let mut buffer = [0; 1024];
                            while !bytes.windows(4).any(|bytes| bytes == b"\r\n\r\n") {
                                let count = stream.read(&mut buffer).unwrap_or_else(|error| {
                                    eprintln!("mock request read failed: {:?}", error.kind());
                                    0
                                });
                                if count == 0 {
                                    return;
                                }
                                bytes.extend_from_slice(&buffer[..count]);
                                assert!(bytes.len() < 4096);
                            }
                            let request = String::from_utf8(bytes).unwrap();
                            observed.lock().unwrap().push(request.clone());
                            if let Err(error) = stream.write_all(&handler(&request)) {
                                eprintln!("mock response write failed: {:?}", error.kind());
                            }
                        }));
                    }
                    Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                        thread::sleep(Duration::from_millis(1))
                    }
                    Err(error) => panic!("mock accept failed: {error}"),
                }
            }
            for child in children {
                child.join().unwrap();
            }
        });
        Self {
            address,
            requests,
            stopping,
            thread: Some(thread),
        }
    }
    pub fn healthy() -> Self {
        Self::new(|_| response(br#"[{"id":"invoice-1","amount":42}]"#))
    }
    pub fn requests(&self) -> Vec<String> {
        self.requests.lock().unwrap().clone()
    }
}

impl Drop for MockApi {
    fn drop(&mut self) {
        self.stopping.store(true, Ordering::Release);
        if let Some(thread) = self.thread.take() {
            thread.join().unwrap();
        }
    }
}

pub fn config(address: SocketAddr) -> BrokerConfig {
    BrokerConfig {
        principal_id: PrincipalId::new("operator-1"),
        initial_artifact_digest: [42; 32],
        resource: ResourceIdentity {
            resource_id: ResourceId::new("mock-invoice-api"),
            configuration_version: 1,
            origin: MockHttpOrigin(address),
            account: "mock-account".into(),
            environment: "test".into(),
            adapter_version: "mock-http-v1".into(),
        },
    }
}

pub fn broker(api: &MockApi, directory: &TestDirectory) -> Broker {
    Broker::start(
        config(api.address),
        FileJournal::create(directory.journal()).unwrap(),
    )
    .unwrap()
}

pub fn request(id: &str, handle: &str, method: &str, path: &str) -> Vec<u8> {
    serde_json::to_vec(&serde_json::json!({ "request_id": id, "capability": handle, "method": method, "path": path })).unwrap()
}

pub fn records(directory: &TestDirectory) -> Vec<serde_json::Value> {
    fs::read_to_string(directory.journal())
        .unwrap()
        .lines()
        .map(|line| serde_json::from_str(line).unwrap())
        .collect()
}
