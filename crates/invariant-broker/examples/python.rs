#[cfg(unix)]
mod demo {
    use std::fs;
    use std::io::{self, Read, Write};
    use std::net::TcpListener;
    use std::path::Path;
    use std::process::{Command, Stdio};
    use std::sync::Arc;
    use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
    use std::thread;
    use std::time::{Duration, SystemTime};

    use invariant_broker::{Broker, BrokerConfig, FileJournal, UnixBrokerServer};
    use invariant_core::{MockHttpOrigin, PrincipalId, ResourceId, ResourceIdentity};
    use sha2::{Digest, Sha256};

    fn python(root: &Path, server: &UnixBrokerServer, capability: &str) -> Command {
        let mut command = Command::new("python3");
        command
            .env_clear()
            .env("PATH", std::env::var_os("PATH").unwrap_or_default())
            .env("PYTHONPATH", root.join("sdk/python"))
            .env("PYTHONUTF8", "1")
            .env("INVARIANT_SOCKET", server.socket_path())
            .env("INVARIANT_CAPABILITY", capability)
            .stdin(Stdio::null())
            .args(["-S", "-B"]);
        command
    }

    fn check(command: &mut Command) -> io::Result<()> {
        if command.status()?.success() {
            Ok(())
        } else {
            Err(io::Error::other("Python example failed"))
        }
    }

    pub fn run() -> Result<(), Box<dyn std::error::Error>> {
        // The Python children run on the host. This demonstrates the transport,
        // not sandboxing or a production supervisor.
        let root = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../..")
            .canonicalize()?;
        let listener = TcpListener::bind("127.0.0.1:0")?;
        let address = listener.local_addr()?;
        listener.set_nonblocking(true)?;
        let stopping = Arc::new(AtomicBool::new(false));
        let stopped = stopping.clone();
        let requests = Arc::new(AtomicUsize::new(0));
        let observed = requests.clone();
        let target = thread::spawn(move || -> io::Result<()> {
            while !stopped.load(Ordering::Acquire) {
                let mut stream = match listener.accept() {
                    Ok((stream, _)) => stream,
                    Err(error) if error.kind() == io::ErrorKind::WouldBlock => {
                        thread::sleep(Duration::from_millis(1));
                        continue;
                    }
                    Err(error) => return Err(error),
                };
                stream.set_nonblocking(false)?;
                stream.set_read_timeout(Some(Duration::from_secs(5)))?;
                stream.set_write_timeout(Some(Duration::from_secs(5)))?;
                let mut bytes = Vec::new();
                let mut buffer = [0; 1024];
                while !bytes.windows(4).any(|bytes| bytes == b"\r\n\r\n") {
                    let count = stream.read(&mut buffer)?;
                    if count == 0 || bytes.len() + count > 4096 {
                        return Err(io::Error::other("invalid mock request"));
                    }
                    bytes.extend_from_slice(&buffer[..count]);
                }
                observed.fetch_add(1, Ordering::Relaxed);
                let body: &[u8] = if bytes.starts_with(b"DELETE ") {
                    br#"{"deleted":true}"#
                } else {
                    br#"[{"id":"invoice-1","amount":42}]"#
                };
                write!(
                    stream,
                    "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                    body.len()
                )?;
                stream.write_all(body)?;
            }
            Ok(())
        });

        let directory = root.join(".invariant");
        fs::create_dir_all(&directory)?;
        let nonce = SystemTime::now()
            .duration_since(SystemTime::UNIX_EPOCH)?
            .as_nanos();
        let journal_path = directory.join(format!("python-{}-{nonce}.jsonl", std::process::id()));
        let broker = Arc::new(Broker::start(
            BrokerConfig {
                principal_id: PrincipalId::new("demo-operator"),
                initial_artifact_digest: Sha256::digest(fs::read(
                    root.join("examples/invoice_summary.py"),
                )?)
                .into(),
                resource: ResourceIdentity {
                    resource_id: ResourceId::new("mock-invoice-api"),
                    configuration_version: 1,
                    origin: MockHttpOrigin(address),
                    account: "mock-account".into(),
                    environment: "test".into(),
                    adapter_version: "mock-http-v1".into(),
                },
            },
            FileJournal::create(&journal_path)?,
        )?);
        let access = broker.issue_grant()?;
        let mut server = UnixBrokerServer::start(broker.clone())?;
        check(
            python(&root, &server, &access.capability)
                .arg(root.join("examples/invoice_summary.py")),
        )?;
        check(
            python(&root, &server, &access.capability).arg(root.join("examples/denied_effects.py")),
        )?;
        broker.revoke_grant()?;
        check(python(&root, &server, &access.capability).args(["-c", "from invariant_client import BrokerClient; r = BrokerClient.from_env().request('GET', '/invoices'); assert r == {'type':'denied','reason':'revoked'}; print('After revocation: denied (revoked)')"]))?;
        server.shutdown()?;
        stopping.store(true, Ordering::Release);
        target
            .join()
            .map_err(|_| io::Error::other("mock target panicked"))??;
        println!(
            "Admissions charged: {}",
            broker.stats()?.current_grant_admissions
        );
        println!(
            "Target requests received: {}",
            requests.load(Ordering::Relaxed)
        );
        println!("Audit journal: {}", journal_path.display());
        Ok(())
    }
}

#[cfg(unix)]
fn main() -> Result<(), Box<dyn std::error::Error>> {
    demo::run()
}

#[cfg(not(unix))]
fn main() {
    eprintln!("This example requires Unix sockets.");
    std::process::exit(1);
}
