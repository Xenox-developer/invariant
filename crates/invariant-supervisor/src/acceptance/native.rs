use super::support::*;
use invariant_broker::{
    Broker, BrokerReply, Clock, ClockReading, FileJournal, RecordedOutcome, UnixBrokerServer,
};
use invariant_core::{DenialReason as D, MVP_GRANT_LIFETIME};
use serde_json::{Value, json};
use std::{
    fs,
    io::{Read, Write},
    os::unix::net::UnixStream,
    path::Path,
    sync::{
        Arc, Barrier, Mutex,
        atomic::{AtomicBool, Ordering},
    },
    thread,
    time::{Duration, Instant, SystemTime},
};

pub fn wire(path: &Path, bytes: &[u8]) -> Value {
    let mut stream = UnixStream::connect(path).unwrap();
    stream
        .set_read_timeout(Some(Duration::from_secs(8)))
        .unwrap();
    stream
        .set_write_timeout(Some(Duration::from_secs(2)))
        .unwrap();
    stream
        .write_all(&(bytes.len() as u32).to_be_bytes())
        .unwrap();
    stream.write_all(bytes).unwrap();
    read_wire(&mut stream)
}
pub fn read_wire(stream: &mut UnixStream) -> Value {
    let mut length = [0; 4];
    stream.read_exact(&mut length).unwrap();
    let length = u32::from_be_bytes(length) as usize;
    assert!(length <= 1_400_000);
    let mut bytes = vec![0; length];
    stream.read_exact(&mut bytes).unwrap();
    serde_json::from_slice(&bytes).unwrap()
}
fn denied(broker: &Broker, bytes: &[u8], reason: D) {
    assert_eq!(broker.execute(bytes), BrokerReply::Denied(reason));
}
pub fn note(case: &Case, value: Value) {
    save(&case.root.join("observations.json"), &value).unwrap();
}

pub fn identity(case: &Case) {
    let target = case.healthy("identity");
    let first = case.broker("first", &target);
    let second = case.broker("second", &target);
    let mut endpoint1 = UnixBrokerServer::start_in(first.clone(), &case.root).unwrap();
    let mut endpoint2 = UnixBrokerServer::start_in(second.clone(), &case.root).unwrap();
    assert_eq!(
        wire(
            endpoint1.socket_path(),
            &request("missing", "fake", "GET", "/invoices")
        )["reason"],
        "missing_grant"
    );
    let grant1 = first.issue_grant().unwrap();
    let grant2 = second.issue_grant().unwrap();
    for (endpoint, other) in [(&endpoint1, &grant2), (&endpoint2, &grant1)] {
        for cap in [&other.capability, "forged-handle"] {
            assert_eq!(
                wire(
                    endpoint.socket_path(),
                    &request("cross", cap, "GET", "/invoices")
                )["reason"],
                "invalid_capability"
            );
        }
        for key in [
            "principal_id",
            "execution_id",
            "broker_session_id",
            "grant_id",
            "resource",
        ] {
            let mut message: Value =
                serde_json::from_slice(&request("identity", &other.capability, "GET", "/invoices"))
                    .unwrap();
            message[key] = json!("forged");
            assert_eq!(
                wire(
                    endpoint.socket_path(),
                    &serde_json::to_vec(&message).unwrap()
                )["reason"],
                "invalid_request"
            );
        }
    }
    assert_eq!(target.count(), 0);
    assert_eq!(first.stats().unwrap().current_grant_admissions, 0);
    assert_eq!(second.stats().unwrap().current_grant_admissions, 0);
    note(
        case,
        json!({"simultaneously_live_endpoints":2,"independent_execution_ids":first.execution_context().execution_id!=second.execution_context().execution_id,"target_receipts":0}),
    );
    endpoint1.shutdown().unwrap();
    endpoint2.shutdown().unwrap();
}

pub fn scope(case: &Case) {
    let target = case.healthy("scope");
    // Prove the forbidden operation exists. The independent baseline receipt
    // is explicitly separated from requests made through INVARIANT.
    target.direct("DELETE", "/invoices/123");
    assert_eq!(target.count(), 1);
    let broker = case.broker("scope", &target);
    let grant = broker.issue_grant().unwrap();
    let cap = &grant.capability;
    let mut probes = 0;
    for method in ["POST", "PUT", "PATCH", "DELETE", "HEAD", "get", "CONNECT"] {
        denied(
            &broker,
            &request("method", cap, method, "/invoices"),
            D::OutOfScope,
        );
        probes += 1;
    }
    for path in [
        "/invoices/123",
        "/invoices/",
        "//invoices",
        "/%69nvoices",
        "/invoices%3f",
        "/invoices?",
        "/invoices?limit=1",
        "/invoices#fragment",
        "/./invoices",
        "/a/../invoices",
        "http://127.0.0.1/invoices",
        "/invoices\r\nX: injected",
    ] {
        denied(&broker, &request("path", cap, "GET", path), D::OutOfScope);
        probes += 1;
    }
    for (key, value) in [
        ("headers", json!({"X-Test":"forged"})),
        ("body", json!("payload")),
        ("url", json!("http://other/invoices")),
        ("budget", json!(999)),
        ("unknown", json!(true)),
    ] {
        let mut message: Value =
            serde_json::from_slice(&request("extra", cap, "GET", "/invoices")).unwrap();
        message[key] = value;
        denied(
            &broker,
            &serde_json::to_vec(&message).unwrap(),
            D::InvalidRequest,
        );
        probes += 1;
    }
    denied(&broker,format!(r#"{{"request_id":"duplicate-field","capability":"{cap}","method":"GET","method":"DELETE","path":"/invoices"}}"#).as_bytes(),D::InvalidRequest);
    assert_eq!(target.count(), 1);
    assert_eq!(broker.stats().unwrap().current_grant_admissions, 0);
    note(
        case,
        json!({"forbidden_endpoint_sanity_receipts":1,"rejected_probes":probes+1,"broker_target_receipts":0}),
    );
}
pub fn redirect(case: &Case) {
    let destination = case.healthy("destination");
    let address = destination.address;
    let source = case.target("redirect", move |_| {
        format!(
            "HTTP/1.1 302 Found\r\nLocation: http://{address}/invoices\r\nContent-Length: 0\r\n\r\n"
        )
        .into_bytes()
    });
    let broker = case.broker("redirect", &source);
    let access = broker.issue_grant().unwrap();
    assert!(
        matches!(broker.execute(&request("redirect",&access.capability,"GET","/invoices")),BrokerReply::Response(r) if r.status==302)
    );
    assert_eq!(source.count(), 1);
    assert_eq!(destination.count(), 0);
}
pub fn concurrency(case: &Case) {
    let target = case.healthy("concurrent");
    let broker = case.broker("concurrent", &target);
    let cap = broker.issue_grant().unwrap().capability;
    let barrier = Arc::new(Barrier::new(31));
    let mut workers = Vec::new();
    for n in 0..30 {
        let broker = broker.clone();
        let barrier = barrier.clone();
        let bytes = request(&format!("parallel-{n}"), &cap, "GET", "/invoices");
        workers.push(thread::spawn(move || {
            barrier.wait();
            let deadline = Instant::now() + Duration::from_secs(10);
            loop {
                let reply = broker.execute(&bytes);
                if reply != BrokerReply::Denied(D::CapacityExceeded) {
                    break reply;
                }
                assert!(Instant::now() < deadline);
                thread::yield_now();
            }
        }));
    }
    barrier.wait();
    let results: Vec<_> = workers.into_iter().map(|w| w.join().unwrap()).collect();
    assert_eq!(
        results
            .iter()
            .filter(|r| matches!(r, BrokerReply::Response(_)))
            .count(),
        20
    );
    assert_eq!(
        results
            .iter()
            .filter(|r| **r == BrokerReply::Denied(D::BudgetExhausted))
            .count(),
        10
    );
    assert_eq!(target.count(), 20);
    assert_eq!(broker.stats().unwrap().current_grant_admissions, 20);
    note(
        case,
        json!({"concurrent_requests":30,"admitted":20,"budget_exhausted":10,"retry_policy":"only explicit CapacityExceeded with identical ID"}),
    );
}
pub fn duplicates(case: &Case) {
    let gate = Arc::new(Gate::default());
    let held = gate.clone();
    let target = case.target("duplicate", move |_| {
        held.wait();
        invoices()
    });
    let broker = case.broker("duplicate", &target);
    let cap = broker.issue_grant().unwrap().capability;
    let bytes = request("same", &cap, "GET", "/invoices");
    let worker = {
        let b = broker.clone();
        let bytes = bytes.clone();
        thread::spawn(move || b.execute(&bytes))
    };
    wait_for(|| target.count() == 1);
    let barrier = Arc::new(Barrier::new(9));
    let mut duplicates = Vec::new();
    for _ in 0..8 {
        let b = broker.clone();
        let bytes = bytes.clone();
        let barrier = barrier.clone();
        duplicates.push(thread::spawn(move || {
            barrier.wait();
            b.execute(&bytes)
        }));
    }
    barrier.wait();
    let replies: Vec<_> = duplicates.into_iter().map(|w| w.join().unwrap()).collect();
    gate.release();
    let reply = worker.join().unwrap();
    assert!(
        replies
            .iter()
            .all(|r| *r == BrokerReply::Replay(RecordedOutcome::InProgress))
    );
    assert!(matches!(reply, BrokerReply::Response(_)));
    assert_eq!(
        broker.execute(&bytes),
        BrokerReply::Replay(RecordedOutcome::Completed { status: 200 })
    );
    denied(
        &broker,
        &request("same", &cap, "DELETE", "/invoices/123"),
        D::OutOfScope,
    );
    broker.revoke_grant().unwrap();
    let replacement = broker.issue_grant().unwrap();
    denied(
        &broker,
        &request("same", &replacement.capability, "GET", "/invoices"),
        D::RequestConflict,
    );
    assert_eq!(target.count(), 1);
}
pub fn revoke_before(case: &Case) {
    let target = case.healthy("before");
    let broker = case.broker("before", &target);
    let cap = broker.issue_grant().unwrap().capability;
    let gate = Arc::new(Gate::default());
    let ready = Arc::new(Barrier::new(2));
    let worker = {
        let broker = broker.clone();
        let gate = gate.clone();
        let ready = ready.clone();
        thread::spawn(move || {
            let bytes = request("paused", &cap, "GET", "/invoices");
            ready.wait();
            gate.wait();
            broker.execute(&bytes)
        })
    };
    ready.wait();
    broker.revoke_grant().unwrap();
    gate.release();
    assert_eq!(worker.join().unwrap(), BrokerReply::Denied(D::Revoked));
    assert_eq!(target.count(), 0);
    assert_eq!(broker.stats().unwrap().current_grant_admissions, 0);
}
pub fn revoke_after(case: &Case) {
    let gate = Arc::new(Gate::default());
    let held = gate.clone();
    let target = case.target("after", move |_| {
        held.wait();
        invoices()
    });
    let broker = case.broker("after", &target);
    let cap = broker.issue_grant().unwrap().capability;
    let worker = {
        let b = broker.clone();
        let cap = cap.clone();
        thread::spawn(move || b.execute(&request("in-flight", &cap, "GET", "/invoices")))
    };
    wait_for(|| target.count() == 1);
    broker.revoke_grant().unwrap();
    gate.release();
    let reply = worker.join().unwrap();
    assert!(matches!(reply, BrokerReply::Response(_)));
    denied(
        &broker,
        &request("later", &cap, "GET", "/invoices"),
        D::Revoked,
    );
    assert_eq!(target.count(), 1);
    assert_eq!(broker.stats().unwrap().current_grant_admissions, 1);
    let log = records(&case.root.join("after-audit.jsonl"));
    let index = |event| log.iter().position(|r| r["event"] == event).unwrap();
    assert!(index("admitted") < index("grant_revoked"));
    assert!(index("grant_revoked") < index("response_received"));
}
struct ManualClock(Mutex<ClockReading>);
impl Clock for ManualClock {
    fn now(&self) -> ClockReading {
        *self.0.lock().unwrap()
    }
}
pub fn expiry(case: &Case) {
    let target = case.healthy("expiry");
    for (index, wall_delta, mono_delta) in [(0, 900_i64, 899), (1, -60, 900), (2, -3600, 901)] {
        let initial = ClockReading {
            wall: SystemTime::UNIX_EPOCH + Duration::from_secs(10_000),
            monotonic: Instant::now(),
        };
        let clock = Arc::new(ManualClock(Mutex::new(initial)));
        let broker = Broker::start_with_clock(
            case.config(&target),
            FileJournal::create(case.root.join(format!("expiry-{index}-audit.jsonl"))).unwrap(),
            clock.clone(),
        )
        .unwrap();
        let cap = broker.issue_grant().unwrap().capability;
        *clock.0.lock().unwrap() = ClockReading {
            wall: if wall_delta < 0 {
                initial.wall - Duration::from_secs((-wall_delta) as u64)
            } else {
                initial.wall + Duration::from_secs(wall_delta as u64)
            },
            monotonic: initial.monotonic + Duration::from_secs(mono_delta),
        };
        denied(
            &broker,
            &request("expired", &cap, "GET", "/invoices"),
            D::Expired,
        );
        assert_eq!(broker.stats().unwrap().current_grant_admissions, 0);
    }
    assert_eq!(MVP_GRANT_LIFETIME, Duration::from_secs(900));
    assert_eq!(target.count(), 0);
}
pub fn journal_faults(case: &Case) {
    let target = case.healthy("faults");
    let mut observations = Vec::new();
    for (name, fault, expected_receipts) in [
        ("before-commit", Fault::Admission, 0),
        ("after-sync", Fault::AfterAdmission, 0),
        ("before-send", Fault::Dispatch, 0),
        ("after-receipt", Fault::Outcome, 1),
    ] {
        let before = target.count();
        let broker = Broker::start(
            case.config(&target),
            FaultJournal {
                inner: FileJournal::create(case.root.join(format!("{name}-audit.jsonl"))).unwrap(),
                fault,
            },
        )
        .unwrap();
        let cap = broker.issue_grant().unwrap().capability;
        let bytes = request(name, &cap, "GET", "/invoices");
        let reply = broker.execute(&bytes);
        assert!(matches!(
            reply,
            BrokerReply::Denied(D::StateUnavailable) | BrokerReply::UnknownOutcome { reason: None }
        ));
        denied(&broker, &bytes, D::StateUnavailable);
        assert!(!broker.stats().unwrap().healthy);
        assert_eq!(broker.stats().unwrap().current_grant_admissions, 1);
        assert_eq!(target.count() - before, expected_receipts);
        observations.push(json!({"fault":name,"target_receipts":target.count()-before,"reserved_allowance":1,"healthy":false,"retry_denied":true}));
    }
    note(case, json!(observations));
}
pub fn unavailable(case: &Case) {
    let target = case.healthy("unavailable");
    for (name, fault) in [("denial", Fault::Denial), ("revocation", Fault::Revocation)] {
        let broker = Broker::start(
            case.config(&target),
            FaultJournal {
                inner: FileJournal::create(case.root.join(format!("{name}-audit.jsonl"))).unwrap(),
                fault,
            },
        )
        .unwrap();
        let cap = broker.issue_grant().unwrap().capability;
        if matches!(fault, Fault::Revocation) {
            assert!(broker.revoke_grant().is_err());
        } else {
            denied(&broker, b"invalid", D::StateUnavailable);
        }
        denied(
            &broker,
            &request("later", &cap, "GET", "/invoices"),
            D::StateUnavailable,
        );
        assert!(!broker.stats().unwrap().healthy);
    }
    struct PoisonClock(AtomicBool);
    impl Clock for PoisonClock {
        fn now(&self) -> ClockReading {
            assert!(
                !self.0.load(Ordering::Acquire),
                "controlled policy-state poison"
            );
            ClockReading {
                wall: SystemTime::now(),
                monotonic: Instant::now(),
            }
        }
    }
    let clock = Arc::new(PoisonClock(AtomicBool::new(false)));
    let broker = Broker::start_with_clock(
        case.config(&target),
        FileJournal::create(case.root.join("poison-audit.jsonl")).unwrap(),
        clock.clone(),
    )
    .unwrap();
    let cap = broker.issue_grant().unwrap().capability;
    clock.0.store(true, Ordering::Release);
    assert!(
        std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| broker.execute(&request(
            "poison",
            &cap,
            "GET",
            "/invoices"
        ))))
        .is_err()
    );
    denied(
        &broker,
        &request("later", &cap, "GET", "/invoices"),
        D::StateUnavailable,
    );
    assert_eq!(target.count(), 0);
}
pub fn rebind(case: &Case) {
    let first = case.healthy("original");
    let changed = case.healthy("changed");
    let mut config = case.config(&first);
    let frozen = config.resource.clone();
    let broker = Broker::start(
        case.config(&first),
        FileJournal::create(case.root.join("original-audit.jsonl")).unwrap(),
    )
    .unwrap();
    let old = broker.issue_grant().unwrap();
    config.resource.origin = resource(changed.address).origin;
    config.resource.configuration_version += 1;
    config.resource.account = "changed-account".into();
    config.resource.environment = "changed-env".into();
    config.resource.adapter_version = "changed-adapter".into();
    assert_eq!(broker.operator_status().unwrap().resource, frozen);
    let mut wire: Value =
        serde_json::from_slice(&request("rebind", &old.capability, "GET", "/invoices")).unwrap();
    wire["resource"] = serde_json::to_value(&config.resource).unwrap();
    denied(
        &broker,
        &serde_json::to_vec(&wire).unwrap(),
        D::InvalidRequest,
    );
    assert!(matches!(
        broker.execute(&request("original", &old.capability, "GET", "/invoices")),
        BrokerReply::Response(_)
    ));
    let next = Broker::start(
        config,
        FileJournal::create(case.root.join("changed-audit.jsonl")).unwrap(),
    )
    .unwrap();
    let new = next.issue_grant().unwrap();
    denied(
        &next,
        &request("old", &old.capability, "GET", "/invoices"),
        D::InvalidCapability,
    );
    assert_eq!(first.count(), 1);
    assert_eq!(changed.count(), 0);
    assert!(matches!(
        next.execute(&request("new", &new.capability, "GET", "/invoices")),
        BrokerReply::Response(_)
    ));
    assert_eq!(changed.count(), 1);
}
pub fn stop(case: &Case) {
    let target = case.healthy("stop");
    let broker = case.broker("stop", &target);
    let cap = broker.issue_grant().unwrap().capability;
    let mut server = UnixBrokerServer::start_in(broker.clone(), &case.root).unwrap();
    for action in ["grant", "revoke", "renew", "delegate"] {
        assert_eq!(
            wire(
                server.socket_path(),
                &serde_json::to_vec(
                    &json!({"action":action,"execution_id":broker.execution_context().execution_id})
                )
                .unwrap()
            )["reason"],
            "invalid_request"
        );
    }
    let mut connected = UnixStream::connect(server.socket_path()).unwrap();
    connected
        .set_read_timeout(Some(Duration::from_secs(3)))
        .unwrap();
    // Wait until the worker has consumed a partial frame; it remains connected
    // across the committed stop instead of racing a fresh connect against it.
    let bytes = request("after-stop", &cap, "GET", "/invoices");
    connected
        .write_all(&(bytes.len() as u32).to_be_bytes())
        .unwrap();
    connected.write_all(&bytes[..1]).unwrap();
    broker.stop().unwrap();
    let _ = connected.write_all(&bytes[1..]);
    let mut remaining = Vec::new();
    let _ = connected.read_to_end(&mut remaining); // Close or a denial are both valid after stop.
    denied(&broker, &bytes, D::InvalidExecution);
    assert!(broker.issue_grant().is_err());
    assert_eq!(target.count(), 0);
    server.shutdown().unwrap();
}

pub fn validate_audit(path: &Path) {
    let entries = records(path);
    assert!(!entries.is_empty());
    for (i, entry) in entries.iter().enumerate() {
        assert_eq!(entry["sequence"].as_u64(), Some(i as u64 + 1));
        for key in [
            "principal_id",
            "execution_id",
            "broker_session_id",
            "initial_artifact_digest",
            "resource",
        ] {
            assert!(!entry[key].is_null(), "missing correlation field {key}");
        }
        if entry["event"] == "admitted" {
            for key in ["grant_id", "request_id", "operation_digest", "ordinal"] {
                assert!(!entry[key].is_null(), "missing admission field {key}");
            }
            assert_eq!(entry["operation_digest"].as_str().unwrap().len(), 64);
            let outcomes: Vec<_> = entries
                .iter()
                .filter(|r| {
                    r["request_id"] == entry["request_id"]
                        && (r["event"] == "response_received" || r["event"] == "upstream_failed")
                })
                .collect();
            // Missing terminal outcome explicitly means unknown, including a
            // durable dispatch intent followed by a controller crash.
            assert!(outcomes.len() <= 1);
            for outcome in outcomes {
                for key in [
                    "principal_id",
                    "execution_id",
                    "broker_session_id",
                    "initial_artifact_digest",
                    "resource",
                    "grant_id",
                    "request_id",
                    "operation_digest",
                ] {
                    assert_eq!(outcome[key], entry[key]);
                }
            }
        }
    }
    assert!(!fs::read_to_string(path).unwrap().contains("cap-"));
}
