mod support;

use std::fs;
use std::io;
use std::sync::{Arc, Barrier, Condvar, Mutex, mpsc};
use std::thread;
use std::time::{Duration, Instant, SystemTime};

use invariant_broker::{
    AuditEvent, AuditJournal, AuditRecord, Broker, BrokerError, BrokerReply, Clock, ClockReading,
    FileJournal, MAX_IN_FLIGHT, MAX_RESPONSE_BYTES, RecordedOutcome, UpstreamError,
};
use invariant_core::{DenialReason, MVP_GRANT_LIFETIME};

use support::*;

#[test]
fn authorized_read_reaches_the_target_after_durable_admission() {
    let directory = TestDirectory::new();
    let path = directory.journal();
    let api = MockApi::new(move |request| {
        assert!(request.starts_with("GET /invoices HTTP/1.1\r\n"));
        assert!(request.contains("Accept-Encoding: identity\r\n"));
        assert!(!request.contains("cap-"));
        let journal = fs::read_to_string(&path).unwrap();
        assert!(journal.lines().any(
            |line| serde_json::from_str::<serde_json::Value>(line).unwrap()["event"] == "admitted"
        ));
        response(b"private-invoice-body")
    });
    let broker = broker(&api, &directory);
    let access = broker.issue_grant().unwrap();
    let reply = broker.execute(&request("r1", &access.capability, "GET", "/invoices"));
    assert!(
        matches!(reply, BrokerReply::Response(ref response) if response.status == 200 && response.body == b"private-invoice-body")
    );
    assert_eq!(api.requests().len(), 1);
    assert_eq!(broker.stats().unwrap().current_grant_admissions, 1);
    let journal = fs::read_to_string(directory.journal()).unwrap();
    assert!(!journal.contains(&access.capability));
    assert!(!journal.contains("private-invoice-body"));
    let records = records(&directory);
    for (index, record) in records.iter().enumerate() {
        assert_eq!(record["sequence"], (index + 1) as u64);
        assert_eq!(
            record["execution_id"],
            broker.execution_context().execution_id.as_str()
        );
    }
    let admitted = records
        .iter()
        .find(|record| record["event"] == "admitted")
        .unwrap();
    assert_eq!(admitted["ordinal"], 1);
    assert_eq!(admitted["operation_digest"].as_str().unwrap().len(), 64);
    assert_eq!(admitted["grant_id"], access.grant_id.as_str());
}

#[test]
fn missing_grants_forged_handles_and_forbidden_operations_never_reach_target() {
    let directory = TestDirectory::new();
    let api = MockApi::healthy();
    let broker = broker(&api, &directory);
    assert_eq!(
        broker.execute(&request("missing", "forged", "GET", "/invoices")),
        BrokerReply::Denied(DenialReason::MissingGrant)
    );
    let access = broker.issue_grant().unwrap();
    assert_eq!(
        broker.execute(&request("fake", "forged", "GET", "/invoices")),
        BrokerReply::Denied(DenialReason::InvalidCapability)
    );
    for (id, method, path) in [
        ("delete", "DELETE", "/invoices/123"),
        ("query", "GET", "/invoices?"),
        ("encoded", "GET", "/%69nvoices"),
        ("url", "GET", "http://example.invalid/invoices"),
    ] {
        assert_eq!(
            broker.execute(&request(id, &access.capability, method, path)),
            BrokerReply::Denied(DenialReason::OutOfScope)
        );
    }
    assert_eq!(
        broker.execute(br#"{"allowed":true}"#),
        BrokerReply::Denied(DenialReason::InvalidRequest)
    );
    assert!(api.requests().is_empty());
    assert_eq!(broker.stats().unwrap().current_grant_admissions, 0);
    assert_eq!(
        records(&directory)
            .iter()
            .filter(|record| record["event"] == "denied")
            .count(),
        7
    );
}

#[test]
fn thirty_concurrent_requests_produce_exactly_twenty_admissions_and_target_requests() {
    let directory = TestDirectory::new();
    let api = MockApi::healthy();
    let broker = Arc::new(broker(&api, &directory));
    let access = broker.issue_grant().unwrap();
    let barrier = Arc::new(Barrier::new(31));
    let mut threads = Vec::new();
    for index in 0..30 {
        let broker = broker.clone();
        let barrier = barrier.clone();
        let bytes = request(&format!("r{index}"), &access.capability, "GET", "/invoices");
        threads.push(thread::spawn(move || {
            barrier.wait();
            let deadline = Instant::now() + Duration::from_secs(15);
            loop {
                let reply = broker.execute(&bytes);
                if reply != BrokerReply::Denied(DenialReason::CapacityExceeded) {
                    return reply;
                }
                assert!(Instant::now() < deadline, "broker stayed busy");
                thread::sleep(Duration::from_millis(1));
            }
        }));
    }
    barrier.wait();
    let replies: Vec<_> = threads
        .into_iter()
        .map(|thread| thread.join().unwrap())
        .collect();
    assert_eq!(
        replies
            .iter()
            .filter(|reply| matches!(reply, BrokerReply::Response(_)))
            .count(),
        20,
        "all replies: {replies:?}"
    );
    assert_eq!(
        replies
            .iter()
            .filter(|reply| **reply == BrokerReply::Denied(DenialReason::BudgetExhausted))
            .count(),
        10
    );
    assert_eq!(api.requests().len(), 20);
    assert_eq!(broker.stats().unwrap().current_grant_admissions, 20);
    let ordinals: Vec<_> = records(&directory)
        .iter()
        .filter(|record| record["event"] == "admitted")
        .map(|record| record["ordinal"].as_u64().unwrap())
        .collect();
    assert_eq!(ordinals, (1..=20).collect::<Vec<_>>());
    assert_eq!(
        broker.execute(&request("later", &access.capability, "GET", "/invoices")),
        BrokerReply::Denied(DenialReason::BudgetExhausted)
    );
    // Duplicates do not require a new allowance even when the budget is full.
    // Concurrent scheduling does not guarantee that r0 was one of the winners.
    let admitted_id = records(&directory)
        .into_iter()
        .find(|record| record["event"] == "admitted")
        .unwrap()["request_id"]
        .as_str()
        .unwrap()
        .to_owned();
    assert_eq!(
        broker.execute(&request(
            &admitted_id,
            &access.capability,
            "GET",
            "/invoices"
        )),
        BrokerReply::Replay(RecordedOutcome::Completed { status: 200 })
    );
    assert_eq!(api.requests().len(), 20);
}

fn gate() -> Arc<(Mutex<bool>, Condvar)> {
    Arc::new((Mutex::new(false), Condvar::new()))
}
fn release(gate: &Arc<(Mutex<bool>, Condvar)>) {
    *gate.0.lock().unwrap() = true;
    gate.1.notify_all();
}
fn wait(gate: &Arc<(Mutex<bool>, Condvar)>) {
    let (open, _) = gate
        .1
        .wait_timeout_while(gate.0.lock().unwrap(), Duration::from_secs(3), |open| {
            !*open
        })
        .unwrap();
    assert!(*open, "test gate was never released");
}

#[test]
fn duplicates_cannot_create_a_second_admission_while_the_first_is_in_flight() {
    let directory = TestDirectory::new();
    let gate = gate();
    let paused = gate.clone();
    let (seen, received) = mpsc::channel();
    let api = MockApi::new(move |_| {
        seen.send(()).unwrap();
        wait(&paused);
        response(b"ok")
    });
    let broker = Arc::new(broker(&api, &directory));
    let access = broker.issue_grant().unwrap();
    let bytes = request("duplicate", &access.capability, "GET", "/invoices");
    let worker = {
        let broker = broker.clone();
        let bytes = bytes.clone();
        thread::spawn(move || broker.execute(&bytes))
    };
    received.recv_timeout(Duration::from_secs(2)).unwrap();
    assert_eq!(
        broker.execute(&bytes),
        BrokerReply::Replay(RecordedOutcome::InProgress)
    );
    assert_eq!(
        broker.execute(&request(
            "duplicate",
            &access.capability,
            "DELETE",
            "/invoices/123"
        )),
        BrokerReply::Denied(DenialReason::OutOfScope)
    );
    release(&gate);
    assert!(matches!(worker.join().unwrap(), BrokerReply::Response(_)));
    assert_eq!(
        broker.execute(&bytes),
        BrokerReply::Replay(RecordedOutcome::Completed { status: 200 })
    );
    assert_eq!(broker.stats().unwrap().current_grant_admissions, 1);
    assert_eq!(api.requests().len(), 1);
}

#[test]
fn four_worker_slots_are_shared_and_capacity_denials_do_not_consume_budget() {
    let directory = TestDirectory::new();
    let gate = gate();
    let paused = gate.clone();
    let (seen, received) = mpsc::channel();
    let api = MockApi::new(move |_| {
        seen.send(()).unwrap();
        wait(&paused);
        response(b"ok")
    });
    let broker = Arc::new(broker(&api, &directory));
    let access = broker.issue_grant().unwrap();
    let workers: Vec<_> = (0..MAX_IN_FLIGHT)
        .map(|index| {
            let broker = broker.clone();
            let bytes = request(&format!("r{index}"), &access.capability, "GET", "/invoices");
            thread::spawn(move || broker.execute(&bytes))
        })
        .collect();
    for _ in 0..MAX_IN_FLIGHT {
        received.recv_timeout(Duration::from_secs(2)).unwrap();
    }
    assert_eq!(broker.stats().unwrap().in_flight, 4);
    assert_eq!(
        broker.execute(&request("fifth", &access.capability, "GET", "/invoices")),
        BrokerReply::Denied(DenialReason::CapacityExceeded)
    );
    assert_eq!(broker.stats().unwrap().current_grant_admissions, 4);
    release(&gate);
    for worker in workers {
        assert!(matches!(worker.join().unwrap(), BrokerReply::Response(_)));
    }
    assert_eq!(api.requests().len(), 4);
}

#[test]
fn revocation_orders_new_admissions_but_allows_an_already_admitted_response() {
    let directory = TestDirectory::new();
    let gate = gate();
    let paused = gate.clone();
    let (seen, received) = mpsc::channel();
    let api = MockApi::new(move |_| {
        seen.send(()).unwrap();
        wait(&paused);
        response(b"ok")
    });
    let broker = Arc::new(broker(&api, &directory));
    let access = broker.issue_grant().unwrap();
    let worker = {
        let broker = broker.clone();
        let bytes = request("before", &access.capability, "GET", "/invoices");
        thread::spawn(move || broker.execute(&bytes))
    };
    received.recv_timeout(Duration::from_secs(2)).unwrap();
    broker.revoke_grant().unwrap();
    assert_eq!(
        broker.execute(&request("after", &access.capability, "GET", "/invoices")),
        BrokerReply::Denied(DenialReason::Revoked)
    );
    release(&gate);
    assert!(matches!(worker.join().unwrap(), BrokerReply::Response(_)));
    let records = records(&directory);
    let position = |event| {
        records
            .iter()
            .position(|record| record["event"] == event)
            .unwrap()
    };
    assert!(position("admitted") < position("grant_revoked"));
    assert!(position("grant_revoked") < position("response_received"));
    assert_eq!(api.requests().len(), 1);
}

#[test]
fn revocation_committed_before_a_waiting_call_prevents_its_admission() {
    let directory = TestDirectory::new();
    let api = MockApi::healthy();
    let broker = Arc::new(broker(&api, &directory));
    let access = broker.issue_grant().unwrap();
    let barrier = Arc::new(Barrier::new(2));
    let worker = {
        let broker = broker.clone();
        let barrier = barrier.clone();
        let bytes = request("waiting", &access.capability, "GET", "/invoices");
        thread::spawn(move || {
            barrier.wait();
            broker.execute(&bytes)
        })
    };
    broker.revoke_grant().unwrap();
    barrier.wait();
    assert_eq!(
        worker.join().unwrap(),
        BrokerReply::Denied(DenialReason::Revoked)
    );
    assert!(api.requests().is_empty());
}

#[test]
fn replacement_requires_operator_action_and_never_reuses_old_handles_or_request_ids() {
    let directory = TestDirectory::new();
    let api = MockApi::healthy();
    let broker = broker(&api, &directory);
    let old = broker.issue_grant().unwrap();
    assert!(matches!(
        broker.issue_grant(),
        Err(BrokerError::ActiveGrant)
    ));
    assert!(matches!(
        broker.execute(&request("same-id", &old.capability, "GET", "/invoices")),
        BrokerReply::Response(_)
    ));
    broker.revoke_grant().unwrap();
    let new = broker.issue_grant().unwrap();
    assert_ne!(old.grant_id, new.grant_id);
    assert_ne!(old.capability, new.capability);
    assert_eq!(
        broker.execute(&request("old-handle", &old.capability, "GET", "/invoices")),
        BrokerReply::Denied(DenialReason::InvalidCapability)
    );
    assert_eq!(
        broker.execute(&request("same-id", &new.capability, "GET", "/invoices")),
        BrokerReply::Denied(DenialReason::RequestConflict)
    );
    assert!(matches!(
        broker.execute(&request("new-id", &new.capability, "GET", "/invoices")),
        BrokerReply::Response(_)
    ));
    assert_eq!(api.requests().len(), 2);
    assert_eq!(broker.stats().unwrap().current_grant_admissions, 1);
}

#[test]
fn stopping_prevents_further_admissions_and_grant_issuance() {
    let directory = TestDirectory::new();
    let api = MockApi::healthy();
    let broker = broker(&api, &directory);
    let access = broker.issue_grant().unwrap();
    broker.stop().unwrap();
    assert_eq!(
        broker.execute(&request("stopped", &access.capability, "GET", "/invoices")),
        BrokerReply::Denied(DenialReason::InvalidExecution)
    );
    assert!(matches!(
        broker.issue_grant(),
        Err(BrokerError::ExecutionTerminated)
    ));
    assert!(api.requests().is_empty());
}

struct ManualClock(Mutex<ClockReading>);
impl Clock for ManualClock {
    fn now(&self) -> ClockReading {
        *self.0.lock().unwrap()
    }
}

#[test]
fn expiry_uses_fresh_trusted_clocks_at_admission() {
    let directory = TestDirectory::new();
    let api = MockApi::healthy();
    let initial = ClockReading {
        wall: SystemTime::UNIX_EPOCH + Duration::from_secs(10_000),
        monotonic: Instant::now(),
    };
    let clock = Arc::new(ManualClock(Mutex::new(initial)));
    let broker = Broker::start_with_clock(
        config(api.address),
        FileJournal::create(directory.journal()).unwrap(),
        clock.clone(),
    )
    .unwrap();
    let access = broker.issue_grant().unwrap();
    *clock.0.lock().unwrap() = ClockReading {
        wall: initial.wall - Duration::from_secs(60),
        monotonic: initial.monotonic + MVP_GRANT_LIFETIME,
    };
    assert_eq!(
        broker.execute(&request("expired", &access.capability, "GET", "/invoices")),
        BrokerReply::Denied(DenialReason::Expired)
    );
    assert!(api.requests().is_empty());
    assert_eq!(broker.stats().unwrap().current_grant_admissions, 0);
}

#[test]
fn a_fresh_session_cannot_resume_old_grants_and_a_journal_is_never_overwritten() {
    let directory = TestDirectory::new();
    let api = MockApi::healthy();
    let old = broker(&api, &directory);
    let access = old.issue_grant().unwrap();
    let old_context = old.execution_context().clone();
    drop(old);
    assert!(FileJournal::create(directory.journal()).is_err());
    let next_directory = TestDirectory::new();
    let new = broker(&api, &next_directory);
    assert_ne!(
        new.execution_context().execution_id,
        old_context.execution_id
    );
    assert_ne!(
        new.execution_context().broker_session_id,
        old_context.broker_session_id
    );
    assert_eq!(
        new.execute(&request("old", &access.capability, "GET", "/invoices")),
        BrokerReply::Denied(DenialReason::MissingGrant)
    );
    new.issue_grant().unwrap();
    assert_eq!(
        new.execute(&request("old", &access.capability, "GET", "/invoices")),
        BrokerReply::Denied(DenialReason::InvalidCapability)
    );
    assert!(api.requests().is_empty());
}

#[derive(Clone, Copy)]
enum Fault {
    Admission,
    AfterAdmissionSync,
    Dispatch,
    Outcome,
    Denial,
    Revocation,
}
struct FailingJournal {
    inner: FileJournal,
    fault: Fault,
}
impl AuditJournal for FailingJournal {
    fn append(&mut self, record: &AuditRecord) -> io::Result<()> {
        let fail = match self.fault {
            Fault::Admission | Fault::AfterAdmissionSync => {
                matches!(record.event, AuditEvent::Admitted { .. })
            }
            Fault::Dispatch => matches!(record.event, AuditEvent::DispatchIntent),
            Fault::Outcome => matches!(record.event, AuditEvent::ResponseReceived { .. }),
            Fault::Denial => matches!(record.event, AuditEvent::Denied { .. }),
            Fault::Revocation => matches!(record.event, AuditEvent::GrantRevoked),
        };
        if fail {
            if matches!(self.fault, Fault::AfterAdmissionSync) {
                self.inner.append(record)?;
            }
            return Err(io::Error::other("injected journal failure"));
        }
        self.inner.append(record)
    }
}

#[test]
fn journal_failures_before_dispatch_prevent_all_target_io() {
    for fault in [Fault::Admission, Fault::AfterAdmissionSync, Fault::Dispatch] {
        let directory = TestDirectory::new();
        let api = MockApi::healthy();
        let journal = FailingJournal {
            inner: FileJournal::create(directory.journal()).unwrap(),
            fault,
        };
        let broker = Broker::start(config(api.address), journal).unwrap();
        let access = broker.issue_grant().unwrap();
        let reply = broker.execute(&request("first", &access.capability, "GET", "/invoices"));
        assert!(matches!(
            reply,
            BrokerReply::Denied(DenialReason::StateUnavailable)
                | BrokerReply::UnknownOutcome { reason: None }
        ));
        assert!(!broker.stats().unwrap().healthy);
        assert_eq!(
            broker.execute(&request("second", &access.capability, "GET", "/invoices")),
            BrokerReply::Denied(DenialReason::StateUnavailable)
        );
        assert!(api.requests().is_empty());
        assert_eq!(broker.stats().unwrap().current_grant_admissions, 1);
    }
}

#[test]
fn loss_of_outcome_recording_never_refunds_or_retries_a_received_request() {
    let directory = TestDirectory::new();
    let api = MockApi::healthy();
    let broker = Broker::start(
        config(api.address),
        FailingJournal {
            inner: FileJournal::create(directory.journal()).unwrap(),
            fault: Fault::Outcome,
        },
    )
    .unwrap();
    let access = broker.issue_grant().unwrap();
    let bytes = request("uncertain", &access.capability, "GET", "/invoices");
    assert_eq!(
        broker.execute(&bytes),
        BrokerReply::UnknownOutcome { reason: None }
    );
    assert_eq!(
        broker.execute(&bytes),
        BrokerReply::Denied(DenialReason::StateUnavailable)
    );
    assert_eq!(broker.stats().unwrap().current_grant_admissions, 1);
    assert_eq!(api.requests().len(), 1);
    assert!(
        records(&directory)
            .iter()
            .any(|record| record["event"] == "admitted")
    );
    assert!(
        !records(&directory)
            .iter()
            .any(|record| record["event"] == "response_received")
    );
}

#[test]
fn denial_or_revocation_recording_failure_also_fails_closed() {
    for fault in [Fault::Denial, Fault::Revocation] {
        let directory = TestDirectory::new();
        let api = MockApi::healthy();
        let broker = Broker::start(
            config(api.address),
            FailingJournal {
                inner: FileJournal::create(directory.journal()).unwrap(),
                fault,
            },
        )
        .unwrap();
        let access = broker.issue_grant().unwrap();
        if matches!(fault, Fault::Revocation) {
            assert!(broker.revoke_grant().is_err());
        } else {
            assert_eq!(
                broker.execute(b"invalid JSON"),
                BrokerReply::Denied(DenialReason::StateUnavailable)
            );
        }
        assert_eq!(
            broker.execute(&request("later", &access.capability, "GET", "/invoices")),
            BrokerReply::Denied(DenialReason::StateUnavailable)
        );
        assert!(api.requests().is_empty());
    }
}

#[test]
fn redirects_are_returned_without_contacting_the_second_target() {
    let directory = TestDirectory::new();
    let other = MockApi::healthy();
    let redirect = format!(
        "HTTP/1.1 302 Found\r\nLocation: http://{}/elsewhere\r\nContent-Length: 0\r\n\r\n",
        other.address
    )
    .into_bytes();
    let api = MockApi::new(move |_| redirect.clone());
    let broker = broker(&api, &directory);
    let access = broker.issue_grant().unwrap();
    assert!(
        matches!(broker.execute(&request("redirect", &access.capability, "GET", "/invoices")), BrokerReply::Response(ref response) if response.status == 302)
    );
    assert_eq!(api.requests().len(), 1);
    assert!(other.requests().is_empty());
}

#[test]
fn unsupported_and_oversized_responses_consume_budget_without_automatic_retry() {
    let mut oversized_headers = b"HTTP/1.1 200 OK\r\nX-Padding: ".to_vec();
    oversized_headers.extend(vec![b'a'; 32 * 1024]);
    oversized_headers.extend_from_slice(b"\r\n\r\n");
    for (bytes, expected) in [
        (
            b"HTTP/1.1 200 OK\r\nContent-Length: 9\r\n\r\nshort".to_vec(),
            UpstreamError::InvalidResponse,
        ),
        (
            b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\nContent-Length: 0\r\n\r\n".to_vec(),
            UpstreamError::InvalidResponse,
        ),
        (
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n0\r\n\r\n".to_vec(),
            UpstreamError::UnsupportedTransferEncoding,
        ),
        (
            b"HTTP/1.1 200 OK\r\nContent-Encoding: gzip\r\nContent-Length: 0\r\n\r\n".to_vec(),
            UpstreamError::UnsupportedEncoding,
        ),
        (
            b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n\r\n".to_vec(),
            UpstreamError::InvalidResponse,
        ),
        (
            format!(
                "HTTP/1.1 200 OK\r\nContent-Length: {}\r\n\r\n",
                MAX_RESPONSE_BYTES + 1
            )
            .into_bytes(),
            UpstreamError::BodyTooLarge,
        ),
        (oversized_headers, UpstreamError::HeadersTooLarge),
    ] {
        let directory = TestDirectory::new();
        let api = MockApi::new(move |_| bytes.clone());
        let broker = broker(&api, &directory);
        let access = broker.issue_grant().unwrap();
        let bytes = request("response", &access.capability, "GET", "/invoices");
        assert_eq!(
            broker.execute(&bytes),
            BrokerReply::UnknownOutcome {
                reason: Some(expected)
            }
        );
        assert_eq!(
            broker.execute(&bytes),
            BrokerReply::Replay(RecordedOutcome::Unknown {
                reason: Some(expected)
            })
        );
        assert_eq!(broker.stats().unwrap().current_grant_admissions, 1);
        assert_eq!(api.requests().len(), 1);
    }
}

#[test]
fn connection_failure_is_charged_once_and_never_retried_automatically() {
    let directory = TestDirectory::new();
    let address = {
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        listener.local_addr().unwrap()
    };
    let broker = Broker::start(
        config(address),
        FileJournal::create(directory.journal()).unwrap(),
    )
    .unwrap();
    let access = broker.issue_grant().unwrap();
    let bytes = request("connection", &access.capability, "GET", "/invoices");
    assert_eq!(
        broker.execute(&bytes),
        BrokerReply::UnknownOutcome {
            reason: Some(UpstreamError::Transport)
        }
    );
    assert_eq!(
        broker.execute(&bytes),
        BrokerReply::Replay(RecordedOutcome::Unknown {
            reason: Some(UpstreamError::Transport)
        })
    );
    assert_eq!(broker.stats().unwrap().current_grant_admissions, 1);
}

#[test]
fn close_delimited_body_is_bounded_and_truncated_at_no_more_than_the_cap() {
    for length in [MAX_RESPONSE_BYTES, MAX_RESPONSE_BYTES + 1] {
        let directory = TestDirectory::new();
        let api = MockApi::new(move |_| {
            let mut bytes = b"HTTP/1.1 200 OK\r\nConnection: close\r\n\r\n".to_vec();
            bytes.extend(vec![b'x'; length]);
            bytes
        });
        let broker = broker(&api, &directory);
        let access = broker.issue_grant().unwrap();
        let reply = broker.execute(&request("body", &access.capability, "GET", "/invoices"));
        if length == MAX_RESPONSE_BYTES {
            assert!(
                matches!(reply, BrokerReply::Response(ref response) if response.body.len() == MAX_RESPONSE_BYTES),
                "reply for a body of {length} bytes: {reply:?}"
            );
        } else {
            assert_eq!(
                reply,
                BrokerReply::UnknownOutcome {
                    reason: Some(UpstreamError::BodyTooLarge)
                }
            );
        }
    }
}

#[test]
fn two_live_executions_cannot_use_each_others_capabilities() {
    let first_directory = TestDirectory::new();
    let second_directory = TestDirectory::new();
    let api = MockApi::healthy();
    let first = broker(&api, &first_directory);
    let second = broker(&api, &second_directory);
    let first_access = first.issue_grant().unwrap();
    let second_access = second.issue_grant().unwrap();
    assert_eq!(
        first.execute(&request(
            "cross-run",
            &second_access.capability,
            "GET",
            "/invoices"
        )),
        BrokerReply::Denied(DenialReason::InvalidCapability)
    );
    assert_eq!(
        second.execute(&request(
            "cross-run",
            &first_access.capability,
            "GET",
            "/invoices"
        )),
        BrokerReply::Denied(DenialReason::InvalidCapability)
    );
    assert!(api.requests().is_empty());
    assert_eq!(first.stats().unwrap().current_grant_admissions, 0);
    assert_eq!(second.stats().unwrap().current_grant_admissions, 0);
}

#[test]
fn target_error_responses_are_charged_and_replays_preserve_the_status() {
    let directory = TestDirectory::new();
    let api = MockApi::new(|_| {
        b"HTTP/1.1 500 Internal Server Error\r\nContent-Length: 0\r\n\r\n".to_vec()
    });
    let broker = broker(&api, &directory);
    let access = broker.issue_grant().unwrap();
    let bytes = request("server-error", &access.capability, "GET", "/invoices");
    assert!(
        matches!(broker.execute(&bytes), BrokerReply::Response(ref response) if response.status == 500)
    );
    assert_eq!(
        broker.execute(&bytes),
        BrokerReply::Replay(RecordedOutcome::Completed { status: 500 })
    );
    assert_eq!(broker.stats().unwrap().current_grant_admissions, 1);
    assert_eq!(api.requests().len(), 1);
}

#[test]
fn a_stalled_target_times_out_without_refunding_or_replaying() {
    use std::io::Read;
    let directory = TestDirectory::new();
    let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    let address = listener.local_addr().unwrap();
    let (seen, received) = mpsc::channel();
    let (release, released) = mpsc::channel();
    let target = thread::spawn(move || {
        let (mut stream, _) = listener.accept().unwrap();
        stream
            .set_read_timeout(Some(Duration::from_secs(2)))
            .unwrap();
        let mut bytes = Vec::new();
        let mut chunk = [0; 1024];
        while !bytes.windows(4).any(|bytes| bytes == b"\r\n\r\n") {
            let count = stream.read(&mut chunk).unwrap();
            assert_ne!(count, 0);
            bytes.extend_from_slice(&chunk[..count]);
        }
        seen.send(()).unwrap();
        // Keep the connection open without sending any response. Release it
        // only after the broker's real five-second I/O deadline has expired.
        released.recv_timeout(Duration::from_secs(8)).unwrap();
    });
    let broker = Broker::start(
        config(address),
        FileJournal::create(directory.journal()).unwrap(),
    )
    .unwrap();
    let access = broker.issue_grant().unwrap();
    let bytes = request("timeout", &access.capability, "GET", "/invoices");
    let started = Instant::now();
    let reply = broker.execute(&bytes);
    let elapsed = started.elapsed();
    release.send(()).unwrap();
    target.join().unwrap();
    received.try_recv().unwrap();
    assert_eq!(
        reply,
        BrokerReply::UnknownOutcome {
            reason: Some(UpstreamError::Timeout)
        }
    );
    assert!(elapsed >= invariant_broker::TARGET_TIMEOUT);
    assert!(elapsed < Duration::from_secs(8));
    assert_eq!(
        broker.execute(&bytes),
        BrokerReply::Replay(RecordedOutcome::Unknown {
            reason: Some(UpstreamError::Timeout)
        })
    );
    assert_eq!(broker.stats().unwrap().current_grant_admissions, 1);
}

#[cfg(unix)]
#[test]
fn file_journals_are_owner_only() {
    use std::os::unix::fs::PermissionsExt;
    let directory = TestDirectory::new();
    let _journal = FileJournal::create(directory.journal()).unwrap();
    assert_eq!(
        fs::metadata(directory.journal())
            .unwrap()
            .permissions()
            .mode()
            & 0o777,
        0o600
    );
}

#[test]
fn operator_status_tracks_grants_and_never_exposes_capabilities() {
    let directory = TestDirectory::new();
    let api = MockApi::healthy();
    let broker = broker(&api, &directory);
    let status = broker.operator_status().unwrap();
    assert!(status.grant.is_none());
    assert!(status.running && status.healthy);
    let first = broker.issue_grant().unwrap();
    broker.execute(&request("one", &first.capability, "GET", "/invoices"));
    let status = broker.operator_status().unwrap();
    let grant = status.grant.as_ref().unwrap();
    assert_eq!(grant.state, "active");
    assert_eq!(grant.remaining_requests, 19);
    assert_eq!(grant.admissions, 1);
    assert_eq!(status.total_admissions, 1);
    assert!(
        !serde_json::to_string(&status)
            .unwrap()
            .contains(&first.capability)
    );
    broker.revoke_grant().unwrap();
    assert_eq!(
        broker.operator_status().unwrap().grant.unwrap().state,
        "revoked"
    );
    let second = broker.issue_grant().unwrap();
    assert_ne!(first.grant_id, second.grant_id);
    let status = broker.operator_status().unwrap();
    assert_eq!(status.grant.unwrap().remaining_requests, 20);
    assert_eq!(status.total_admissions, 1);
    broker.execute(&request("two", &second.capability, "GET", "/invoices"));
    assert_eq!(broker.operator_status().unwrap().total_admissions, 2);
    broker.stop().unwrap();
    assert!(!broker.operator_status().unwrap().running);
    assert!(matches!(
        broker.issue_grant(),
        Err(BrokerError::ExecutionTerminated)
    ));
    assert!(matches!(
        broker.revoke_grant(),
        Err(BrokerError::ExecutionTerminated)
    ));
}
