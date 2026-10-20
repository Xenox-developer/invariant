use std::fs::{self, OpenOptions};
use std::io::{self, Read, Write};
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::os::unix::fs::OpenOptionsExt;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant, SystemTime};

use invariant_broker::{AuditJournal, AuditRecord, Broker, BrokerConfig, FileJournal};
use invariant_core::{MockHttpOrigin, PrincipalId, ResourceId, ResourceIdentity};
use invariant_supervisor::{RunReport, RunSpec, Supervisor, Termination};
use serde_json::{Value, json};

pub type Result<T = ()> = std::result::Result<T, Box<dyn std::error::Error>>;

pub fn save(path: &Path, value: &impl serde::Serialize) -> io::Result<()> {
    let mut file = OpenOptions::new()
        .create(true)
        .truncate(true)
        .write(true)
        .mode(0o600)
        .open(path)?;
    file.write_all(&serde_json::to_vec_pretty(value)?)?;
    file.sync_all()
}
pub fn records(path: &Path) -> Vec<Value> {
    let bytes = fs::read(path).unwrap();
    assert!(
        bytes.is_empty() || bytes.ends_with(b"\n"),
        "final audit contains an unterminated record"
    );
    parse_lines(&bytes)
}
fn parse_lines(bytes: &[u8]) -> Vec<Value> {
    std::str::from_utf8(bytes)
        .unwrap()
        .lines()
        .map(|line| serde_json::from_str(line).unwrap())
        .collect()
}
pub fn live_records(path: &Path) -> Vec<Value> {
    // A concurrent append can expose a partial trailing record. Only polling
    // may defer that tail; final evidence uses the strict reader above.
    let bytes = fs::read(path).unwrap();
    let complete = bytes
        .iter()
        .rposition(|byte| *byte == b'\n')
        .map_or(0, |end| end + 1);
    parse_lines(&bytes[..complete])
}
pub fn request(id: &str, cap: &str, method: &str, path: &str) -> Vec<u8> {
    serde_json::to_vec(&json!({"request_id":id,"capability":cap,"method":method,"path":path}))
        .unwrap()
}
pub fn response(body: &[u8]) -> Vec<u8> {
    let mut bytes = format!(
        "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
        body.len()
    )
    .into_bytes();
    bytes.extend_from_slice(body);
    bytes
}
pub fn invoices() -> Vec<u8> {
    response(br#"[{"id":"inv-1","amount":42}]"#)
}

#[derive(Default)]
pub struct Gate {
    open: Mutex<bool>,
    changed: Condvar,
}
impl Gate {
    pub fn release(&self) {
        *self.open.lock().unwrap() = true;
        self.changed.notify_all();
    }
    pub fn wait(&self) {
        let (open, _) = self
            .changed
            .wait_timeout_while(self.open.lock().unwrap(), Duration::from_secs(20), |open| {
                !*open
            })
            .unwrap();
        assert!(*open, "acceptance synchronization gate timed out");
    }
}

pub struct Target {
    pub address: SocketAddr,
    pub path: PathBuf,
    received: Arc<Mutex<Vec<Value>>>,
    stopped: Arc<AtomicBool>,
    thread: Option<JoinHandle<()>>,
}
impl Target {
    pub fn start(
        path: PathBuf,
        handler: impl Fn(&Value) -> Vec<u8> + Send + Sync + 'static,
    ) -> Self {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        listener.set_nonblocking(true).unwrap();
        let file = OpenOptions::new()
            .create_new(true)
            .write(true)
            .mode(0o600)
            .open(&path)
            .unwrap();
        file.sync_all().unwrap();
        let received = Arc::new(Mutex::new(Vec::new()));
        let observed = received.clone();
        let stopped = Arc::new(AtomicBool::new(false));
        let stopping = stopped.clone();
        let journal = Arc::new(Mutex::new(file));
        let handler = Arc::new(handler);
        let thread = thread::spawn(move || {
            let mut workers = Vec::new();
            while !stopping.load(Ordering::Acquire) {
                match listener.accept() {
                    Ok((mut stream, _)) => {
                        let observed = observed.clone();
                        let journal = journal.clone();
                        let handler = handler.clone();
                        workers.push(thread::spawn(move || {
                            stream.set_nonblocking(false).unwrap();
                            stream.set_read_timeout(Some(Duration::from_secs(5))).unwrap();
                            stream.set_write_timeout(Some(Duration::from_secs(5))).unwrap();
                            let mut raw=Vec::new(); let mut buffer=[0;1024];
                            while raw.len()<=4096 && !raw.windows(4).any(|b|b==b"\r\n\r\n") {
                                let n=match stream.read(&mut buffer) { Ok(0)|Err(_)=>return,Ok(n)=>n };
                                raw.extend_from_slice(&buffer[..n]);
                            }
                            let text=String::from_utf8_lossy(&raw);
                            let line=text.lines().next().unwrap_or("");
                            let parts:Vec<_>=line.split_whitespace().collect();
                            let mut entries=observed.lock().unwrap();
                            let record=json!({"ordinal":entries.len()+1,"method":parts.first().copied().unwrap_or(""),"path":parts.get(1).copied().unwrap_or(""),"origin":address.to_string(),"received_at_ms":SystemTime::now().duration_since(SystemTime::UNIX_EPOCH).unwrap().as_millis()});
                            // Independent target-side receipt is durable before
                            // its response, including for deliberately forbidden routes.
                            let mut log=journal.lock().unwrap();
                            log.write_all(serde_json::to_string(&record).unwrap().as_bytes()).unwrap(); log.write_all(b"\n").unwrap(); log.sync_all().unwrap();
                            entries.push(record.clone()); drop(log); drop(entries);
                            let bytes=handler(&record);
                            let _=stream.write_all(&bytes);
                        }));
                    }
                    Err(error) if error.kind() == io::ErrorKind::WouldBlock => {
                        thread::sleep(Duration::from_millis(1))
                    }
                    Err(error) => panic!("acceptance mock failed: {error}"),
                }
                let mut index = 0;
                while index < workers.len() {
                    if workers[index].is_finished() {
                        workers.swap_remove(index).join().unwrap();
                    } else {
                        index += 1;
                    }
                }
            }
            for worker in workers {
                worker.join().unwrap();
            }
        });
        Self {
            address,
            path,
            received,
            stopped,
            thread: Some(thread),
        }
    }
    pub fn count(&self) -> usize {
        self.received.lock().unwrap().len()
    }
    pub fn direct(&self, method: &str, path: &str) {
        let mut stream = TcpStream::connect(self.address).unwrap();
        stream
            .set_read_timeout(Some(Duration::from_secs(5)))
            .unwrap();
        write!(
            stream,
            "{method} {path} HTTP/1.1\r\nHost: mock\r\nConnection: close\r\n\r\n"
        )
        .unwrap();
        let mut response = Vec::new();
        stream.read_to_end(&mut response).unwrap();
        assert!(response.starts_with(b"HTTP/1.1 200"));
    }
}
impl Drop for Target {
    fn drop(&mut self) {
        self.stopped.store(true, Ordering::Release);
        if let Some(t) = self.thread.take() {
            t.join().unwrap();
        }
    }
}

#[derive(Clone)]
pub struct Case {
    pub root: PathBuf,
    pub daemon: PathBuf,
    pub image: String,
    pub targets: Arc<Mutex<Vec<Value>>>,
    pub runs: Arc<Mutex<Vec<Value>>>,
}
impl Case {
    pub fn target(
        &self,
        name: &str,
        handler: impl Fn(&Value) -> Vec<u8> + Send + Sync + 'static,
    ) -> Target {
        let target = Target::start(self.root.join(format!("{name}-target.jsonl")), handler);
        self.targets
            .lock()
            .unwrap()
            .push(json!({"journal":target.path,"origin":target.address.to_string()}));
        target
    }
    pub fn healthy(&self, name: &str) -> Target {
        self.target(name, |r| {
            if r["method"] == "DELETE" {
                response(br#"{"deleted":true}"#)
            } else {
                invoices()
            }
        })
    }
    pub fn resource(&self, target: &Target) -> ResourceIdentity {
        resource(target.address)
    }
    pub fn config(&self, target: &Target) -> BrokerConfig {
        BrokerConfig {
            principal_id: PrincipalId::new("acceptance-operator"),
            initial_artifact_digest: [42; 32],
            resource: self.resource(target),
        }
    }
    pub fn broker(&self, name: &str, target: &Target) -> Arc<Broker> {
        Arc::new(
            Broker::start(
                self.config(target),
                FileJournal::create(self.root.join(format!("{name}-audit.jsonl"))).unwrap(),
            )
            .unwrap(),
        )
    }
    pub fn supervisor(&self, name: &str) -> Supervisor {
        Supervisor::open(&self.root.join(name), &self.daemon.join(name), &self.image).unwrap()
    }
    pub fn run(
        &self,
        name: &str,
        target: &Target,
        program: &str,
        initial_grant: bool,
        duration: Duration,
        control: impl FnOnce(Arc<Broker>),
    ) -> RunReport {
        let mut supervisor = self.supervisor(name);
        let report = supervisor
            .run_with_control(
                RunSpec {
                    program: program.as_bytes(),
                    principal: PrincipalId::new("acceptance-operator"),
                    resource: self.resource(target),
                    duration,
                    initial_grant,
                },
                &AtomicBool::new(false),
                control,
            )
            .unwrap();
        self.runs
            .lock()
            .unwrap()
            .push(serde_json::to_value(&report).unwrap());
        report
    }
    pub fn success(
        &self,
        name: &str,
        target: &Target,
        program: &str,
        initial_grant: bool,
        control: impl FnOnce(Arc<Broker>),
    ) -> RunReport {
        let report = self.run(
            name,
            target,
            program,
            initial_grant,
            Duration::from_secs(30),
            control,
        );
        assert_eq!(report.termination, Termination::Exited, "{}", report.output);
        assert_eq!(report.exit_code, Some(0), "{}", report.output);
        report
    }
    pub fn observation(&self) -> Value {
        let target_files = self.targets.lock().unwrap().clone();
        let targets: Vec<_> = target_files
            .iter()
            .map(|target| {
                let path = Path::new(target["journal"].as_str().unwrap());
                json!({"journal":path,"origin":target["origin"],"received_requests":records(path)})
            })
            .collect();
        let mut audits = Vec::new();
        let mut isolation = Vec::new();
        collect(&self.root, &mut audits, &mut isolation);
        let notes = fs::read(self.root.join("observations.json"))
            .ok()
            .map(|bytes| serde_json::from_slice::<Value>(&bytes).unwrap());
        json!({"targets":targets,"broker_audits":audits,"docker_runs":*self.runs.lock().unwrap(),"isolation_evidence":isolation,"observations":notes})
    }
}
pub fn resource(address: SocketAddr) -> ResourceIdentity {
    ResourceIdentity {
        resource_id: ResourceId::new("mock-invoice-api"),
        configuration_version: 1,
        origin: MockHttpOrigin(address),
        account: "mock-account".into(),
        environment: "test".into(),
        adapter_version: "mock-http-v1".into(),
    }
}
fn collect(directory: &Path, audits: &mut Vec<Value>, isolation: &mut Vec<Value>) {
    for entry in fs::read_dir(directory).unwrap() {
        let entry = entry.unwrap();
        let path = entry.path();
        if entry.file_type().unwrap().is_dir() {
            if entry.file_name() != "credentials" {
                collect(&path, audits, isolation);
            }
        } else if path
            .file_name()
            .is_some_and(|s| s == "audit.jsonl" || s.to_string_lossy().ends_with("-audit.jsonl"))
        {
            let records = records(&path);
            audits.push(json!({"path":path,"records":records}));
        } else if path
            .file_name()
            .is_some_and(|s| s == "isolation.json" || s == "configuration.json")
        {
            isolation.push(json!({"path":path,"configuration":serde_json::from_slice::<Value>(&fs::read(&path).unwrap()).unwrap()}));
        }
    }
}

pub fn wait_for(mut condition: impl FnMut() -> bool) {
    let deadline = Instant::now() + Duration::from_secs(15);
    while !condition() {
        assert!(Instant::now() < deadline, "acceptance condition timed out");
        thread::sleep(Duration::from_millis(2));
    }
}
pub fn docker(args: &[&str]) -> String {
    let output = Command::new("docker")
        .arg("--host=unix:///var/run/docker.sock")
        .args(args)
        .output()
        .unwrap();
    assert!(output.status.success(), "Docker acceptance command failed");
    String::from_utf8(output.stdout).unwrap()
}

#[derive(Clone, Copy)]
pub enum Fault {
    Admission,
    AfterAdmission,
    Dispatch,
    Outcome,
    Denial,
    Revocation,
}
pub struct FaultJournal {
    pub inner: FileJournal,
    pub fault: Fault,
}
impl AuditJournal for FaultJournal {
    fn append(&mut self, record: &AuditRecord) -> io::Result<()> {
        use invariant_broker::AuditEvent;
        let fail = match self.fault {
            Fault::Admission | Fault::AfterAdmission => {
                matches!(record.event, AuditEvent::Admitted { .. })
            }
            Fault::Dispatch => matches!(record.event, AuditEvent::DispatchIntent),
            Fault::Outcome => matches!(record.event, AuditEvent::ResponseReceived { .. }),
            Fault::Denial => matches!(record.event, AuditEvent::Denied { .. }),
            Fault::Revocation => matches!(record.event, AuditEvent::GrantRevoked),
        };
        if fail {
            if matches!(self.fault, Fault::AfterAdmission) {
                self.inner.append(record)?;
            }
            return Err(io::Error::other("acceptance fault injection"));
        }
        self.inner.append(record)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    struct SnapshotFile(PathBuf);
    impl SnapshotFile {
        fn new() -> Self {
            let mut nonce = [0; 8];
            getrandom::fill(&mut nonce).unwrap();
            let path = std::env::temp_dir()
                .join(format!("iv-audit-observer-{:x}", u64::from_ne_bytes(nonce)));
            OpenOptions::new()
                .write(true)
                .create_new(true)
                .mode(0o600)
                .open(&path)
                .unwrap();
            Self(path)
        }
    }
    impl Drop for SnapshotFile {
        fn drop(&mut self) {
            let _ = fs::remove_file(&self.0);
        }
    }

    #[test]
    fn live_audit_snapshot_waits_for_a_complete_appended_line() {
        let file = SnapshotFile::new();
        fs::write(
            &file.0,
            b"{\"event\":\"admitted\"}\n{\"event\":\"response_receiv",
        )
        .unwrap();
        assert_eq!(live_records(&file.0), vec![json!({"event":"admitted"})]);
        let mut writer = OpenOptions::new().append(true).open(&file.0).unwrap();
        writer.write_all(b"ed\"}\n").unwrap();
        writer.sync_all().unwrap();
        assert_eq!(
            live_records(&file.0),
            vec![
                json!({"event":"admitted"}),
                json!({"event":"response_received"})
            ]
        );
        assert_eq!(records(&file.0).len(), 2);
    }

    #[test]
    fn final_audit_validation_rejects_truncation_and_complete_corruption() {
        let file = SnapshotFile::new();
        for bytes in [b"{}".as_slice(), b"{\"event\":"] {
            fs::write(&file.0, bytes).unwrap();
            assert!(live_records(&file.0).is_empty());
            assert!(std::panic::catch_unwind(|| records(&file.0)).is_err());
        }
        fs::write(&file.0, b"{}\n{not-json}\n").unwrap();
        assert!(std::panic::catch_unwind(|| records(&file.0)).is_err());
        assert!(std::panic::catch_unwind(|| live_records(&file.0)).is_err());
    }
}
