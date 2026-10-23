//! A standalone Rust consumer: one real HTTP request, replay, revocation and audit.
use std::fs;
use std::io::{self, Read, Write};
use std::net::TcpListener;
use std::sync::Arc;
use std::thread;
use std::time::{Duration, Instant, SystemTime};

use invariant_broker::core::{
    DenialReason, MockHttpOrigin, PrincipalId, ResourceId, ResourceIdentity,
};
use invariant_broker::{Broker, BrokerConfig, BrokerReply, FileJournal, RecordedOutcome};

fn request(id: &str, capability: &str, method: &str, path: &str) -> Vec<u8> {
    // These arguments are fixed strings or broker-generated hexadecimal handles.
    format!(
        r#"{{"request_id":"{id}","capability":"{capability}","method":"{method}","path":"{path}"}}"#,
    )
    .into_bytes()
}

fn serve_one(listener: TcpListener) -> io::Result<usize> {
    listener.set_nonblocking(true)?;
    let deadline = Instant::now() + Duration::from_secs(10);
    let mut stream = loop {
        match listener.accept() {
            Ok((stream, _)) => break stream,
            Err(error)
                if error.kind() == io::ErrorKind::WouldBlock && Instant::now() < deadline =>
            {
                thread::sleep(Duration::from_millis(5));
            }
            Err(error) => return Err(error),
        }
    };
    stream.set_nonblocking(false)?;
    stream.set_write_timeout(Some(Duration::from_secs(2)))?;
    let mut header = Vec::new();
    while !header.ends_with(b"\r\n\r\n") {
        let remaining = deadline.saturating_duration_since(Instant::now());
        if remaining.is_zero() || header.len() >= 4096 {
            return Err(io::Error::other("mock request exceeded its limits"));
        }
        stream.set_read_timeout(Some(remaining))?;
        let mut byte = [0];
        stream.read_exact(&mut byte)?;
        header.push(byte[0]);
    }
    if !header.starts_with(b"GET /invoices HTTP/1.1\r\n") {
        return Err(io::Error::other("unexpected target request"));
    }
    stream.write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n[]")?;
    Ok(1)
}

#[cfg(unix)]
fn check_socket(broker: Arc<Broker>, frame: &[u8]) -> io::Result<()> {
    use invariant_broker::UnixBrokerServer;
    use std::os::unix::net::UnixStream;

    let mut server = UnixBrokerServer::start(broker)?;
    let mut connection = UnixStream::connect(server.socket_path())?;
    connection.set_read_timeout(Some(Duration::from_secs(3)))?;
    connection.set_write_timeout(Some(Duration::from_secs(3)))?;
    connection.write_all(&(frame.len() as u32).to_be_bytes())?;
    connection.write_all(frame)?;
    let mut prefix = [0; 4];
    connection.read_exact(&mut prefix)?;
    let length = u32::from_be_bytes(prefix) as usize;
    if length > 1024 {
        return Err(io::Error::other("unexpected socket response size"));
    }
    let mut reply = vec![0; length];
    connection.read_exact(&mut reply)?;
    assert_eq!(reply, br#"{"type":"denied","reason":"revoked"}"#);
    server.shutdown()?;
    assert!(!server.socket_path().exists());
    Ok(())
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let nonce = SystemTime::now()
        .duration_since(SystemTime::UNIX_EPOCH)?
        .as_nanos();
    let state = std::env::temp_dir().join(format!("iv-embed-{}-{nonce}", std::process::id()));
    let mut directory = fs::DirBuilder::new();
    #[cfg(unix)]
    {
        use std::os::unix::fs::DirBuilderExt;
        directory.mode(0o700);
    }
    directory.create(&state)?;
    let result = run(&state);
    fs::remove_dir_all(&state)?;
    result
}

fn run(state: &std::path::Path) -> Result<(), Box<dyn std::error::Error>> {
    let listener = TcpListener::bind("127.0.0.1:0")?;
    let address = listener.local_addr()?;
    let target = thread::spawn(move || serve_one(listener));
    let journal = state.join("audit.jsonl");
    let broker = Arc::new(Broker::start(
        BrokerConfig {
            principal_id: PrincipalId::new("embedding-example"),
            // Illustrative trusted digest; a production host hashes its actual artifact.
            initial_artifact_digest: [42; 32],
            resource: ResourceIdentity {
                resource_id: ResourceId::new("mock-invoices"),
                configuration_version: 1,
                origin: MockHttpOrigin(address),
                account: "example-account".into(),
                environment: "test".into(),
                adapter_version: "mock-http-v1".into(),
            },
        },
        FileJournal::create(&journal)?,
    )?);
    // Compile-time compatibility: direct core imports and the broker use identical types.
    let context: &invariant_core::ExecutionContext = broker.execution_context();
    assert!(!context.execution_id.as_str().is_empty());
    assert_eq!(
        broker.execute(&request("before-grant", "unissued", "GET", "/invoices")),
        BrokerReply::Denied(DenialReason::MissingGrant),
    );
    let access = broker.issue_grant()?;
    let read = request("read-1", &access.capability, "GET", "/invoices");
    match broker.execute(&read) {
        BrokerReply::Response(response) => {
            assert_eq!(response.status, 200);
            assert_eq!(response.body, b"[]");
        }
        reply => return Err(io::Error::other(format!("unexpected reply: {reply:?}")).into()),
    }
    assert_eq!(
        broker.execute(&read),
        BrokerReply::Replay(RecordedOutcome::Completed { status: 200 })
    );
    assert_eq!(
        broker.execute(&request(
            "delete-1",
            &access.capability,
            "DELETE",
            "/invoices/123"
        )),
        BrokerReply::Denied(DenialReason::OutOfScope),
    );
    broker.revoke_grant()?;
    let after = request("read-2", &access.capability, "GET", "/invoices");
    assert_eq!(
        broker.execute(&after),
        BrokerReply::Denied(DenialReason::Revoked)
    );
    assert_eq!(broker.stats()?.current_grant_admissions, 1);
    #[cfg(unix)]
    check_socket(broker.clone(), &after)?;
    broker.stop()?;
    assert_eq!(
        broker.stats()?.execution_state,
        invariant_core::ExecutionState::Terminated
    );
    let received = target
        .join()
        .map_err(|_| io::Error::other("mock target panicked"))??;
    assert_eq!(received, 1);
    let audit = fs::read_to_string(journal)?;
    assert!(!audit.contains(&access.capability));
    assert_eq!(
        audit
            .lines()
            .filter(|line| line.contains(r#""event":"admitted""#))
            .count(),
        1
    );
    println!(
        "PASS: one HTTP call, replay without another admission, scope denial, revocation and private audit"
    );
    Ok(())
}
