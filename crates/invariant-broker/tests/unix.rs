#![cfg(unix)]

mod support;

use std::fs;
use std::io::{Read, Write};
use std::net::Shutdown;
use std::os::unix::fs::PermissionsExt;
use std::os::unix::net::UnixStream;
use std::path::Path;
use std::process::{Command, Output};
use std::sync::Arc;
use std::thread;
use std::time::{Duration, Instant};

use invariant_broker::UnixBrokerServer;
use invariant_core::{ExecutionState, MAX_REQUEST_FRAME_BYTES};
use support::*;

fn connect(server: &UnixBrokerServer) -> UnixStream {
    let stream = UnixStream::connect(server.socket_path()).unwrap();
    stream
        .set_read_timeout(Some(Duration::from_secs(5)))
        .unwrap();
    stream
        .set_write_timeout(Some(Duration::from_secs(5)))
        .unwrap();
    stream
}

fn receive(stream: &mut UnixStream) -> serde_json::Value {
    let mut prefix = [0; 4];
    stream.read_exact(&mut prefix).unwrap();
    let length = u32::from_be_bytes(prefix) as usize;
    assert!(length <= invariant_broker::MAX_SOCKET_RESPONSE_BYTES);
    let mut payload = vec![0; length];
    stream.read_exact(&mut payload).unwrap();
    serde_json::from_slice(&payload).unwrap()
}

fn send(stream: &mut UnixStream, payload: &[u8]) -> serde_json::Value {
    stream
        .write_all(&(payload.len() as u32).to_be_bytes())
        .unwrap();
    stream.write_all(payload).unwrap();
    receive(stream)
}

fn python(server: &UnixBrokerServer, capability: &str, code: &str) -> Output {
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
    Command::new("python3")
        .env_clear()
        .env("PATH", std::env::var_os("PATH").unwrap_or_default())
        .env("PYTHONPATH", root.join("sdk/python"))
        .env("INVARIANT_SOCKET", server.socket_path())
        .env("INVARIANT_CAPABILITY", capability)
        .args(["-S", "-B", "-c", code])
        .output()
        .unwrap()
}

fn assert_python(output: Output) {
    assert!(
        output.status.success(),
        "Python failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[test]
fn python_reads_invoices_and_reconnecting_does_not_repeat_the_effect() {
    let directory = TestDirectory::new();
    let api = MockApi::healthy();
    let broker = Arc::new(broker(&api, &directory));
    let access = broker.issue_grant().unwrap();
    let mut server = UnixBrokerServer::start(broker.clone()).unwrap();
    assert_python(python(
        &server,
        &access.capability,
        r#"
import json
from invariant_client import BrokerClient
c = BrokerClient.from_env()
r = c.request('GET', '/invoices', request_id='same-id')
assert r['type'] == 'response' and r['status'] == 200
assert json.loads(r['body']) == [{'id':'invoice-1','amount':42}]
assert c.request('GET', '/invoices', request_id='same-id') == {'type':'replay','outcome':{'state':'completed','status':200}}
assert c.request('DELETE', '/invoices/123') == {'type':'denied','reason':'out_of_scope'}
assert 'cap-' not in repr(c)
"#,
    ));
    assert_eq!(broker.stats().unwrap().current_grant_admissions, 1);
    assert_eq!(api.requests().len(), 1);
    server.shutdown().unwrap();
    assert!(!server.socket_path().exists());
    assert_eq!(
        broker.stats().unwrap().execution_state,
        ExecutionState::Terminated
    );
    assert!(
        records(&directory)
            .iter()
            .any(|record| record["event"] == "response_received")
    );
}

#[test]
fn python_reconnections_share_one_twenty_request_budget() {
    let directory = TestDirectory::new();
    let api = MockApi::healthy();
    let broker = Arc::new(broker(&api, &directory));
    let access = broker.issue_grant().unwrap();
    let server = UnixBrokerServer::start(broker.clone()).unwrap();
    assert_python(python(
        &server,
        &access.capability,
        r#"
from invariant_client import BrokerClient
c = BrokerClient.from_env()
for i in range(20):
    r = c.request('GET', '/invoices', request_id=str(i))
    assert r['type'] == 'response', r
assert c.request('GET', '/invoices', request_id='21') == {'type':'denied','reason':'budget_exhausted'}
assert c.request('GET', '/invoices', request_id='0')['type'] == 'replay'
"#,
    ));
    assert_eq!(api.requests().len(), 20);
    assert_eq!(broker.stats().unwrap().current_grant_admissions, 20);
}

#[test]
fn persistent_connections_recheck_grant_revocation_for_each_frame() {
    let directory = TestDirectory::new();
    let api = MockApi::healthy();
    let broker = Arc::new(broker(&api, &directory));
    let access = broker.issue_grant().unwrap();
    let server = UnixBrokerServer::start(broker.clone()).unwrap();
    let mut connection = connect(&server);
    assert_eq!(
        send(
            &mut connection,
            &request("before", &access.capability, "GET", "/invoices")
        )["type"],
        "response"
    );
    broker.revoke_grant().unwrap();
    assert_eq!(
        send(
            &mut connection,
            &request("after", &access.capability, "GET", "/invoices")
        ),
        serde_json::json!({"type":"denied","reason":"revoked"})
    );
    assert_python(python(
        &server,
        &access.capability,
        "from invariant_client import BrokerClient; assert BrokerClient.from_env().request('GET','/invoices')['reason'] == 'revoked'",
    ));
    assert_eq!(api.requests().len(), 1);
}

#[test]
fn a_listener_never_uses_identity_or_grant_control_fields_from_messages() {
    let directory = TestDirectory::new();
    let api = MockApi::healthy();
    let broker = Arc::new(broker(&api, &directory));
    let access = broker.issue_grant().unwrap();
    let server = UnixBrokerServer::start(broker.clone()).unwrap();
    let mut connection = connect(&server);
    for field in [
        "execution_id",
        "principal_id",
        "grant",
        "allowed",
        "issue_grant",
        "revoke_grant",
    ] {
        let mut message: serde_json::Value =
            serde_json::from_slice(&request(field, &access.capability, "GET", "/invoices"))
                .unwrap();
        message[field] = serde_json::json!("forged");
        assert_eq!(
            send(&mut connection, &serde_json::to_vec(&message).unwrap()),
            serde_json::json!({"type":"denied","reason":"invalid_request"})
        );
    }
    assert_eq!(broker.stats().unwrap().current_grant_admissions, 0);
    assert!(api.requests().is_empty());
}

#[test]
fn simultaneous_listeners_cannot_exchange_capabilities() {
    let first_directory = TestDirectory::new();
    let second_directory = TestDirectory::new();
    let api = MockApi::healthy();
    let first = Arc::new(broker(&api, &first_directory));
    let second = Arc::new(broker(&api, &second_directory));
    let first_access = first.issue_grant().unwrap();
    let second_access = second.issue_grant().unwrap();
    let first_socket = UnixBrokerServer::start(first).unwrap();
    let second_socket = UnixBrokerServer::start(second).unwrap();
    assert_ne!(first_socket.socket_path(), second_socket.socket_path());
    for (server, handle) in [
        (&first_socket, &second_access.capability),
        (&second_socket, &first_access.capability),
    ] {
        assert_python(python(
            server,
            handle,
            "from invariant_client import BrokerClient; assert BrokerClient.from_env().request('GET','/invoices') == {'type':'denied','reason':'invalid_capability'}",
        ));
    }
    assert!(api.requests().is_empty());
}

#[test]
fn oversized_zero_and_truncated_frames_are_denied_without_admission() {
    let directory = TestDirectory::new();
    let api = MockApi::healthy();
    let broker = Arc::new(broker(&api, &directory));
    broker.issue_grant().unwrap();
    let server = UnixBrokerServer::start(broker.clone()).unwrap();
    for length in [0, MAX_REQUEST_FRAME_BYTES as u32 + 1, u32::MAX] {
        let mut connection = connect(&server);
        connection.write_all(&length.to_be_bytes()).unwrap();
        assert_eq!(
            receive(&mut connection),
            serde_json::json!({"type":"denied","reason":"invalid_request"})
        );
    }
    let mut truncated = connect(&server);
    truncated.write_all(&10_u32.to_be_bytes()).unwrap();
    truncated.write_all(b"{}").unwrap();
    truncated.shutdown(Shutdown::Write).unwrap();
    assert_eq!(
        receive(&mut truncated),
        serde_json::json!({"type":"denied","reason":"invalid_request"})
    );
    assert_eq!(broker.stats().unwrap().current_grant_admissions, 0);
    assert!(api.requests().is_empty());
}

#[test]
fn socket_and_parent_permissions_are_restricted_and_shutdown_preserves_replacements() {
    let directory = TestDirectory::new();
    let api = MockApi::healthy();
    let broker = Arc::new(broker(&api, &directory));
    let mut server = UnixBrokerServer::start(broker).unwrap();
    let path = server.socket_path().to_owned();
    assert_eq!(
        fs::metadata(path.parent().unwrap())
            .unwrap()
            .permissions()
            .mode()
            & 0o777,
        0o700
    );
    assert_eq!(
        fs::metadata(&path).unwrap().permissions().mode() & 0o777,
        0o600
    );
    fs::remove_file(&path).unwrap();
    fs::write(&path, b"replacement must survive cleanup").unwrap();
    assert!(server.shutdown().is_err());
    assert_eq!(
        fs::read(&path).unwrap(),
        b"replacement must survive cleanup"
    );
    // Only test-owned data is removed here; the server refused to delete it.
    fs::remove_file(&path).unwrap();
    fs::remove_dir(path.parent().unwrap()).unwrap();
}

#[test]
fn execution_stop_closes_existing_idle_channels_and_unlinks_the_socket() {
    let directory = TestDirectory::new();
    let api = MockApi::healthy();
    let broker = Arc::new(broker(&api, &directory));
    let server = UnixBrokerServer::start(broker.clone()).unwrap();
    let mut idle = connect(&server);
    assert_eq!(
        send(
            &mut idle,
            &request("prime", "unregistered", "GET", "/invoices")
        )["reason"],
        "missing_grant"
    );
    idle.write_all(&[0, 0]).unwrap();
    broker.stop().unwrap();
    match idle.read(&mut [0; 1]) {
        Ok(0) => {}
        Err(error) if error.kind() == std::io::ErrorKind::ConnectionReset => {}
        result => panic!("stopped channel remained usable: {result:?}"),
    }
    let deadline = Instant::now() + Duration::from_secs(2);
    while server.socket_path().exists() {
        assert!(Instant::now() < deadline, "socket was not removed");
        thread::sleep(Duration::from_millis(1));
    }
    assert!(UnixStream::connect(server.socket_path()).is_err());
    assert!(api.requests().is_empty());
}

#[test]
fn large_binary_response_survives_python_base64_decoding() {
    let directory = TestDirectory::new();
    let api = MockApi::new(|_| response(&vec![0xff; invariant_broker::MAX_RESPONSE_BYTES]));
    let broker = Arc::new(broker(&api, &directory));
    let access = broker.issue_grant().unwrap();
    let server = UnixBrokerServer::start(broker).unwrap();
    assert_python(python(
        &server,
        &access.capability,
        "from invariant_client import BrokerClient; r=BrokerClient.from_env().request('GET','/invoices'); assert r['body'] == bytes([255])*1048576",
    ));
    assert_eq!(api.requests().len(), 1);
}

#[test]
fn four_idle_connections_bound_the_worker_count_without_granting_more_access() {
    let directory = TestDirectory::new();
    let api = MockApi::healthy();
    let broker = Arc::new(broker(&api, &directory));
    let access = broker.issue_grant().unwrap();
    let server = UnixBrokerServer::start(broker.clone()).unwrap();
    let mut held = Vec::new();
    for index in 0..invariant_broker::MAX_SOCKET_CONNECTIONS {
        let mut connection = connect(&server);
        assert_eq!(
            send(
                &mut connection,
                &request(
                    &format!("held-{index}"),
                    &access.capability,
                    "DELETE",
                    "/invoices/123"
                )
            )["reason"],
            "out_of_scope"
        );
        held.push(connection);
    }
    let mut excess = connect(&server);
    assert_eq!(
        receive(&mut excess),
        serde_json::json!({"type":"denied","reason":"capacity_exceeded"})
    );
    assert_eq!(broker.stats().unwrap().current_grant_admissions, 0);
    assert!(api.requests().is_empty());
    drop(held);
}

#[test]
fn partial_frames_have_a_total_read_deadline_and_do_not_consume_budget() {
    let directory = TestDirectory::new();
    let api = MockApi::healthy();
    let broker = Arc::new(broker(&api, &directory));
    broker.issue_grant().unwrap();
    let server = UnixBrokerServer::start(broker.clone()).unwrap();
    let mut connection = connect(&server);
    connection.write_all(&[0, 0]).unwrap();
    assert_eq!(
        receive(&mut connection),
        serde_json::json!({"type":"denied","reason":"invalid_request"})
    );
    assert_eq!(broker.stats().unwrap().current_grant_admissions, 0);
    assert!(api.requests().is_empty());
}

#[test]
fn fragmented_and_pipelined_frames_remain_distinct_requests() {
    let directory = TestDirectory::new();
    let api = MockApi::healthy();
    let broker = Arc::new(broker(&api, &directory));
    let access = broker.issue_grant().unwrap();
    let server = UnixBrokerServer::start(broker.clone()).unwrap();
    let mut connection = connect(&server);
    for id in ["first", "second"] {
        let payload = request(id, &access.capability, "GET", "/invoices");
        for byte in (payload.len() as u32).to_be_bytes() {
            connection.write_all(&[byte]).unwrap();
        }
        for chunk in payload.chunks(3) {
            connection.write_all(chunk).unwrap();
        }
    }
    assert_eq!(receive(&mut connection)["type"], "response");
    assert_eq!(receive(&mut connection)["type"], "response");
    assert_eq!(api.requests().len(), 2);
    assert_eq!(broker.stats().unwrap().current_grant_admissions, 2);
}
