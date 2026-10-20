use std::fs;
use std::io::{self, Read, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::thread;
use std::time::{Duration, Instant, SystemTime};

use invariant_broker::{Broker, BrokerConfig, BrokerReply, FileJournal};
use invariant_core::{MockHttpOrigin, PrincipalId, ResourceId, ResourceIdentity};
use sha2::{Digest, Sha256};

fn request(id: &str, handle: &str, method: &str, path: &str) -> Vec<u8> {
    serde_json::to_vec(&serde_json::json!({
        "request_id": id, "capability": handle, "method": method, "path": path,
    }))
    .expect("fixed JSON request serializes")
}

fn serve_request(mut stream: TcpStream, observed: &AtomicUsize) -> io::Result<()> {
    stream.set_nonblocking(false)?;
    stream.set_read_timeout(Some(Duration::from_secs(5)))?;
    stream.set_write_timeout(Some(Duration::from_secs(5)))?;
    let mut header = Vec::new();
    let mut chunk = [0; 1024];
    while !header.windows(4).any(|bytes| bytes == b"\r\n\r\n") {
        let count = stream.read(&mut chunk)?;
        if count == 0 || header.len() + count > 4096 {
            return Err(io::Error::other("invalid demo target request"));
        }
        header.extend_from_slice(&chunk[..count]);
    }
    observed.fetch_add(1, Ordering::Relaxed);
    // The forbidden route exists, so unauthorized dispatch is observable.
    let (status, body): (&str, &[u8]) = if header.starts_with(b"GET /invoices HTTP/1.1\r\n") {
        ("200 OK", br#"[{"id":"invoice-1","amount":42}]"#)
    } else if header.starts_with(b"DELETE /invoices/") {
        ("200 OK", br#"{"deleted":true}"#)
    } else {
        ("404 Not Found", b"{}")
    };
    write!(
        stream,
        "HTTP/1.1 {status}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
        body.len()
    )?;
    stream.write_all(body)
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    // This is a host-side demonstration, not an isolated Python workload.
    let listener = TcpListener::bind("127.0.0.1:0")?;
    let address = listener.local_addr()?;
    listener.set_nonblocking(true)?;
    let target_requests = Arc::new(AtomicUsize::new(0));
    let observed = target_requests.clone();
    let stopping = Arc::new(AtomicBool::new(false));
    let stopped = stopping.clone();
    let target = thread::spawn(move || -> io::Result<()> {
        let deadline = Instant::now() + Duration::from_secs(10);
        while !stopped.load(Ordering::Acquire) {
            match listener.accept() {
                Ok((stream, _)) => serve_request(stream, &observed)?,
                Err(error)
                    if error.kind() == io::ErrorKind::WouldBlock && Instant::now() < deadline =>
                {
                    thread::sleep(Duration::from_millis(5))
                }
                Err(error) => return Err(error),
            }
        }
        Ok(())
    });

    let directory = std::env::current_dir()?.join(".invariant");
    fs::create_dir_all(&directory)?;
    let nonce = SystemTime::now()
        .duration_since(SystemTime::UNIX_EPOCH)?
        .as_nanos();
    let journal_path = directory.join(format!("broker-{}-{nonce}.jsonl", std::process::id()));
    let broker = Broker::start(
        BrokerConfig {
            principal_id: PrincipalId::new("demo-operator"),
            initial_artifact_digest: Sha256::digest(include_bytes!("broker.rs")).into(),
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
    )?;
    let access = broker.issue_grant()?;
    let read = request("read-1", &access.capability, "GET", "/invoices");
    match broker.execute(&read) {
        BrokerReply::Response(response) => println!(
            "Read invoices: HTTP {}, {}",
            response.status,
            String::from_utf8_lossy(&response.body)
        ),
        reply => return Err(io::Error::other(format!("unexpected demo result: {reply:?}")).into()),
    }
    println!(
        "Delete invoice: {:?}",
        broker.execute(&request(
            "delete-1",
            &access.capability,
            "DELETE",
            "/invoices/123"
        ))
    );
    println!("Duplicate read: {:?}", broker.execute(&read));
    broker.revoke_grant()?;
    println!(
        "After revocation: {:?}",
        broker.execute(&request("read-2", &access.capability, "GET", "/invoices"))
    );
    broker.stop()?;
    stopping.store(true, Ordering::Release);
    target
        .join()
        .map_err(|_| io::Error::other("demo target panicked"))??;
    println!(
        "Admissions charged: {}",
        broker.stats()?.current_grant_admissions
    );
    println!(
        "Target requests received: {}",
        target_requests.load(Ordering::Relaxed)
    );
    println!("Audit journal: {}", journal_path.display());
    Ok(())
}
