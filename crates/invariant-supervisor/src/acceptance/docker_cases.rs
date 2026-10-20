use super::{
    native::{note, read_wire, validate_audit, wire},
    support::*,
};
use invariant_broker::{Broker, BrokerReply, FileJournal, UnixBrokerServer};
use invariant_core::{DenialReason as D, PrincipalId};
use invariant_supervisor::{OperatorRequest, RunSpec, Supervisor, Termination, operator_command};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    fs,
    io::Write,
    os::unix::{fs::FileTypeExt, net::UnixStream, process::CommandExt},
    path::{Path, PathBuf},
    process::{Command, Stdio},
    sync::{Arc, Mutex, atomic::AtomicBool, mpsc},
    thread,
    time::{Duration, Instant},
};

pub fn useful_programs(case: &Case) {
    let target = case.healthy("summaries");
    let programs = [
        r#"
import json
from invariant_client import BrokerClient
response=BrokerClient.from_env().request('GET','/invoices',request_id='summary-a')
assert response['status']==200
total=sum(invoice['amount'] for invoice in json.loads(response['body']))
assert total==42
print('total:',total)
"#,
        r#"
import os,socket,struct,json,base64
from decimal import Decimal
cap=open(os.environ['INVARIANT_CAPABILITY_FILE']).read()
message=json.dumps(dict(request_id='summary-b',capability=cap,method='GET',path='/invoices')).encode()
with socket.socket(socket.AF_UNIX) as stream:
 stream.connect(os.environ['INVARIANT_SOCKET'])
 stream.sendall(struct.pack('!I',len(message))+message)
 def read(n):
  b=bytearray()
  while len(b)<n:
   chunk=stream.recv(n-len(b));assert chunk;b.extend(chunk)
  return bytes(b)
 result=json.loads(read(struct.unpack('!I',read(4))[0]))
 assert result['type']=='response' and result['status']==200
 rows=json.loads(base64.b64decode(result['body_base64']))
 balance=Decimal(0)
 for row in rows: balance+=Decimal(str(row['amount']))
 assert balance==42
 print('balance:',balance)
"#,
    ];
    let mut hashes = Vec::new();
    for (i, program) in programs.iter().enumerate() {
        let report = case.success(&format!("p{i}"), &target, program, true, |_| {});
        assert_eq!(report.admissions, 1);
        assert!(report.output.contains("42"));
        hashes.push(format!("{:x}", Sha256::digest(program.as_bytes())));
    }
    assert_ne!(hashes[0], hashes[1]);
    assert_eq!(target.count(), 2);
    note(
        case,
        json!({"independent_program_sha256":hashes,"totals":[42,42],"target_receipts":2}),
    );
}

fn find_files(root: &Path, name: &str) -> Vec<PathBuf> {
    let mut paths = Vec::new();
    if let Ok(entries) = fs::read_dir(root) {
        for entry in entries {
            let entry = entry.unwrap();
            if entry.file_name() == "credentials" {
                continue;
            }
            if entry.file_type().unwrap().is_dir() {
                paths.extend(find_files(&entry.path(), name));
            } else if entry.file_name() == name {
                paths.push(entry.path());
            }
        }
    }
    paths
}
fn has_event(root: &Path, event: &str, reason: Option<&str>) -> bool {
    find_files(root, "audit.jsonl").iter().any(|p| {
        live_records(p)
            .iter()
            .any(|r| r["event"] == event && reason.is_none_or(|reason| r["reason"] == reason))
    })
}

pub fn isolation(case: &Case) {
    let target = case.healthy("bypass");
    let canary = case.root.join("host-canary");
    fs::write(
        &canary,
        b"acceptance-only host file; must remain outside workload mounts",
    )
    .unwrap();
    let (ready, received) = mpsc::channel();
    thread::scope(|scope| {
        let first = scope.spawn(|| {
            case.success(
                "a",
                &target,
                r#"
import time
from invariant_client import BrokerClient
c=BrokerClient.from_env()
while True:
 r=c.request('DELETE','/invoices/123')
 assert r['type']=='denied'
 if r['reason']=='revoked': break
 assert r['reason']=='out_of_scope'
 time.sleep(.01)
print('first isolated execution stopped after trusted revocation')
"#,
                true,
                |broker| ready.send(broker).unwrap(),
            )
        });
        let first_broker = received.recv_timeout(Duration::from_secs(20)).unwrap();
        let socket = find_files(&case.root.join("a"), "s")
            .into_iter()
            .find(|p| fs::symlink_metadata(p).unwrap().file_type().is_socket())
            .unwrap();
        let first_profile: Value = serde_json::from_slice(
            &fs::read(find_files(&case.root.join("a"), "isolation.json")[0].clone()).unwrap(),
        )
        .unwrap();
        let program = format!(
            r#"
import os,socket,json
from invariant_client import BrokerClient
assert os.getuid()==65532
for family in (socket.AF_INET,socket.AF_INET6,socket.AF_NETLINK,socket.AF_PACKET):
 try: s=socket.socket(family)
 except PermissionError: pass
 else: s.close();raise AssertionError('direct socket was permitted')
for host in ('127.0.0.1','host.docker.internal','169.254.169.254','example.invalid'):
 try: socket.create_connection((host,80),timeout=.2)
 except OSError: pass
 else: raise AssertionError('direct host access was permitted')
try: socket.getaddrinfo('must-not-resolve.invalid',443)
except OSError: pass
else: raise AssertionError('DNS available')
for path in ('/state/audit.jsonl','/var/run/docker.sock','/proc/1/root/state','/Users/nikchin', '/input/other.py', {host_canary}, {other_socket}):
 try:
  if path=={other_socket}:
   s=socket.socket(socket.AF_UNIX);s.connect(path);s.close()
  else: open(path,'rb').read(1)
 except OSError: pass
 else: raise AssertionError('host/other-run resource accessible')
for path in ('/escape','/dev/escape','/etc/hosts','/input/program.py','/authority/capability'):
 try: open(path,'wb')
 except OSError: pass
 else: raise AssertionError('immutable mount writable')
assert BrokerClient.from_env().request('DELETE','/invoices/123')['reason']=='out_of_scope'
print('network, DNS, host files and other live socket blocked')
"#,
            host_canary = serde_json::to_string(&canary.to_string_lossy()).unwrap(),
            other_socket = serde_json::to_string(&socket.to_string_lossy()).unwrap()
        );
        let second = case.success("b", &target, &program, true, |b| {
            assert_ne!(
                b.execution_context().execution_id,
                first_broker.execution_context().execution_id
            )
        });
        first_broker.revoke_grant().unwrap();
        let first = first.join().unwrap();
        assert_eq!(first.admissions + second.admissions, 0);
        let second_profile: Value = serde_json::from_slice(
            &fs::read(find_files(&case.root.join("b"), "isolation.json")[0].clone()).unwrap(),
        )
        .unwrap();
        assert_ne!(
            first_profile["effective"]["namespaces"],
            second_profile["effective"]["namespaces"]
        );
    });
    assert_eq!(target.count(), 0);
}

pub fn shared_budget(case: &Case) {
    let target = case.healthy("shared");
    let report=case.success("run",&target,r#"
import subprocess,sys
from invariant_client import BrokerClient
for i in range(20):
 assert BrokerClient.from_env().request('GET','/invoices',request_id='spend-'+str(i))['type']=='response'
for i in range(3):
 assert BrokerClient.from_env().request('GET','/invoices',request_id='reconnect-'+str(i))['reason']=='budget_exhausted'
children=[]
for i in range(4):
 code="from invariant_client import BrokerClient; c=BrokerClient.from_env(); assert c.request('GET','/invoices',request_id='child-%s')['reason']=='budget_exhausted'; print('child exhausted')"%i
 children.append(subprocess.Popen([sys.executable,'-S','-B','-c',code]))
for child in children: assert child.wait(timeout=10)==0
print('all processes share the exhausted budget')
"#,true,|_|{});
    assert_eq!(report.admissions, 20);
    assert_eq!(target.count(), 20);
}

pub fn limits(case: &Case) {
    let target = case.healthy("limits");
    let broker = case.broker("frames", &target);
    broker.issue_grant().unwrap();
    let mut server = UnixBrokerServer::start_in(broker.clone(), &case.root).unwrap();
    for length in [0, 16_385, u32::MAX] {
        let mut stream = UnixStream::connect(server.socket_path()).unwrap();
        stream
            .set_read_timeout(Some(Duration::from_secs(3)))
            .unwrap();
        stream.write_all(&length.to_be_bytes()).unwrap();
        assert_eq!(read_wire(&mut stream)["reason"], "invalid_request");
    }
    assert_eq!(broker.stats().unwrap().current_grant_admissions, 0);
    server.shutdown().unwrap();
    let mut headers = b"HTTP/1.1 200 OK\r\nX-Padding: ".to_vec();
    headers.extend(vec![b'x'; 32769]);
    headers.extend_from_slice(b"\r\n\r\n");
    for (name, response_bytes, reason) in [
        (
            "body",
            b"HTTP/1.1 200 OK\r\nContent-Length: 1048577\r\n\r\n".to_vec(),
            invariant_broker::UpstreamError::BodyTooLarge,
        ),
        (
            "headers",
            headers,
            invariant_broker::UpstreamError::HeadersTooLarge,
        ),
    ] {
        let oversized = case.target(name, move |_| response_bytes.clone());
        let b = case.broker(name, &oversized);
        let cap = b.issue_grant().unwrap().capability;
        let bytes = request("oversized", &cap, "GET", "/invoices");
        assert_eq!(
            b.execute(&bytes),
            BrokerReply::UnknownOutcome {
                reason: Some(reason)
            }
        );
        assert!(matches!(b.execute(&bytes), BrokerReply::Replay(_)));
        assert_eq!(oversized.count(), 1);
    }
    // Occupy all four admitted outbound slots using independently observed
    // target receipts, not a sleep intended to guess their state.
    let gate = Arc::new(Gate::default());
    let held = gate.clone();
    let slow = case.target("slots", move |_| {
        held.wait();
        invoices()
    });
    let b = case.broker("slots", &slow);
    let cap = b.issue_grant().unwrap().capability;
    let mut workers = Vec::new();
    for n in 0..4 {
        let b = b.clone();
        let bytes = request(&format!("slot-{n}"), &cap, "GET", "/invoices");
        workers.push(thread::spawn(move || b.execute(&bytes)));
    }
    wait_for(|| slow.count() == 4);
    let fifth = b.execute(&request("fifth", &cap, "GET", "/invoices"));
    gate.release();
    for w in workers {
        assert!(matches!(w.join().unwrap(), BrokerReply::Response(_)));
    }
    assert_eq!(fifth, BrokerReply::Denied(D::CapacityExceeded));
    assert_eq!(slow.count(), 4);
    let checks: [(&str, &str, Duration, Termination, Option<i64>); 7] = [
        (
            "disk",
            "import errno\ntry:\n f=open('/scratch/full','wb',buffering=0)\n for _ in range(70): f.write(b'x'*1048576)\n raise AssertionError('disk limit absent')\nexcept OSError as e: assert e.errno==errno.ENOSPC\nprint('64 MiB scratch enforced')",
            Duration::from_secs(15),
            Termination::Exited,
            Some(0),
        ),
        (
            "pids",
            "import os,errno,time\npids=[]\ntry:\n for _ in range(40):\n  p=os.fork()\n  if p==0: time.sleep(30);os._exit(0)\n  pids.append(p)\n raise AssertionError('PID limit absent')\nexcept OSError as e: assert e.errno==errno.EAGAIN and 1<=len(pids)<=31\nfinally:\n for p in pids: os.kill(p,9)\n for p in pids: os.waitpid(p,0)",
            Duration::from_secs(15),
            Termination::Exited,
            Some(0),
        ),
        (
            "memory",
            "data=bytearray(400*1024*1024)",
            Duration::from_secs(15),
            Termination::Exited,
            Some(137),
        ),
        (
            "output",
            "import os\nwhile True:\n os.write(1,b'x'*4096);os.write(2,b'y'*4096)",
            Duration::from_secs(15),
            Termination::OutputLimit,
            None,
        ),
        (
            "time",
            "import subprocess,time\nsubprocess.Popen(['python3','-c','import time;time.sleep(60)'])\ntime.sleep(60)",
            Duration::from_millis(250),
            Termination::Timeout,
            None,
        ),
        (
            "files",
            "import os,errno\nf=[]\ntry:\n for _ in range(150): f.append(open('/dev/null','rb'))\n raise AssertionError('FD limit absent')\nexcept OSError as e: assert e.errno==errno.EMFILE and len(f)<128",
            Duration::from_secs(15),
            Termination::Exited,
            Some(0),
        ),
        (
            "fresh",
            "import os\nassert os.listdir('/scratch')==[]",
            Duration::from_secs(15),
            Termination::Exited,
            Some(0),
        ),
    ];
    for (name, program, duration, termination, code) in checks {
        let report = case.run(name, &target, program, true, duration, |_| {});
        assert_eq!(report.termination, termination, "{}", report.output);
        assert_eq!(report.exit_code, code, "{}", report.output);
        assert_eq!(report.admissions, 0);
        assert!(report.output_bytes <= 1_048_576);
    }
    assert_eq!(target.count(), 0);
    note(
        case,
        json!({"frame_limit_bytes":16384,"response_body_limit_bytes":1048576,"header_limit_bytes":32768,"outbound_slots":4,"memory_bytes":268435456,"pids":32,"scratch_bytes":67108864,"output_bytes":1048576,"file_descriptors":128,"duration_override_ms":250}),
    );
}

pub fn audit(case: &Case) {
    let target = case.healthy("audit");
    let broker = case.broker("audit", &target);
    let cap = broker.issue_grant().unwrap().capability;
    assert!(matches!(
        broker.execute(&request("allowed", &cap, "GET", "/invoices")),
        BrokerReply::Response(_)
    ));
    assert_eq!(
        broker.execute(&request("denied", &cap, "DELETE", "/invoices/123")),
        BrokerReply::Denied(D::OutOfScope)
    );
    let malformed = case.target("malformed", |_| b"not HTTP".to_vec());
    let failure = case.broker("failed", &malformed);
    let access = failure.issue_grant().unwrap();
    assert!(matches!(
        failure.execute(&request("failed", &access.capability, "GET", "/invoices")),
        BrokerReply::UnknownOutcome { .. }
    ));
    let unknown = Broker::start(
        case.config(&target),
        FaultJournal {
            inner: FileJournal::create(case.root.join("unknown-audit.jsonl")).unwrap(),
            fault: Fault::Outcome,
        },
    )
    .unwrap();
    let access = unknown.issue_grant().unwrap();
    assert_eq!(
        unknown.execute(&request("unknown", &access.capability, "GET", "/invoices")),
        BrokerReply::UnknownOutcome { reason: None }
    );
    for name in [
        "audit-audit.jsonl",
        "failed-audit.jsonl",
        "unknown-audit.jsonl",
    ] {
        validate_audit(&case.root.join(name));
    }
    let source = format!(
        r#"
import os
from invariant_client import BrokerClient
for path in ({root},'/state','/audit.jsonl','/proc/1/root/state'):
 try: os.listdir(path)
 except OSError: pass
 else: raise AssertionError('trusted journal directory visible')
c=BrokerClient.from_env()
assert c.request('GET','/invoices',request_id='docker-audit')['type']=='response'
assert c.request('GET','/invoices',request_id='docker-audit')['type']=='replay'
print('untrusted \x1b[31m\r output \u202e')
"#,
        root = serde_json::to_string(&case.root.to_string_lossy()).unwrap()
    );
    let report = case.success("run", &target, &source, true, |_| {});
    assert!(!report.output.contains('\x1b'));
    assert!(!report.output.contains('\r'));
    assert!(!report.output.contains('\u{202e}'));
    for audit in find_files(&case.root.join("run"), "audit.jsonl") {
        validate_audit(&audit);
    }
    assert_eq!(target.count(), 3);
    assert_eq!(malformed.count(), 1);
    note(
        case,
        json!({"unknown_definition":"admitted without terminal outcome is derivably unknown and never restored/replayed","correlation_checked":["sequence","principal","execution","session","artifact digest","resource","grant","request ID","operation digest","ordinal","outcome"],"target_receipts":4}),
    );
}

pub fn live_operator(case: &Case) {
    let target = case.healthy("operator");
    let controller = Arc::new(Mutex::new(None));
    let holder = controller.clone();
    let root = case.root.join("run");
    let observe = root.clone();
    let report = case.success(
        "run",
        &target,
        r#"
import time
from invariant_client import BrokerClient
c=BrokerClient.from_env(); denied_ids=set();index=0
while True:
 request_id='phase-'+str(index);index+=1
 r=c.request('GET','/invoices',request_id=request_id)
 if r['type']=='response':
  assert denied_ids and request_id not in denied_ids
  break
 assert r['type']=='denied' and r['reason'] in ('missing_grant','invalid_capability')
 denied_ids.add(request_id);time.sleep(.01)
while True:
 r=c.request('GET','/invoices',request_id=request_id)
 if r.get('reason')=='revoked': break
 assert r['type']=='replay'
 time.sleep(.01)
print('missing grant -> allowed -> revoked; denied IDs never replayed')
"#,
        false,
        move |broker| {
            *holder.lock().unwrap() = Some(thread::spawn(move || {
                wait_for(|| has_event(&observe, "denied", Some("missing_grant")));
                let id = broker.execution_context().execution_id.as_str().to_owned();
                let issued = operator_command(
                    &root,
                    &OperatorRequest::Grant {
                        execution_id: id.clone(),
                    },
                )
                .unwrap();
                assert_eq!(issued["ok"], true);
                wait_for(|| has_event(&observe, "response_received", None));
                let revoked =
                    operator_command(&root, &OperatorRequest::Revoke { execution_id: id }).unwrap();
                assert_eq!(revoked["ok"], true);
            }));
        },
    );
    controller.lock().unwrap().take().unwrap().join().unwrap();
    assert_eq!(report.admissions, 1);
    assert_eq!(target.count(), 1);
    let log = records(&find_files(&case.root.join("run"), "audit.jsonl")[0]);
    let denied: Vec<_> = log
        .iter()
        .filter(|r| r["event"] == "denied" && r["reason"] == "missing_grant")
        .map(|r| r["request_id"].clone())
        .collect();
    for admitted in log.iter().filter(|r| r["event"] == "admitted") {
        assert!(!denied.contains(&admitted["request_id"]));
    }
}
pub fn grant_constraints(case: &Case) {
    let target = case.healthy("constraints");
    let root = case.root.join("run");
    let proofs = Arc::new(Mutex::new(Vec::new()));
    let observations = proofs.clone();
    let report=case.success("run",&target,"from invariant_client import BrokerClient\nassert BrokerClient.from_env().request('GET','/invoices')['type']=='response'",true,move |broker|{
        let id=broker.execution_context().execution_id.as_str().to_owned();let before=broker.operator_status().unwrap();
        for (key,value) in [("budget",json!(21)),("max_requests",json!(21)),("method",json!("DELETE")),("path",json!("/")),("delegation",json!(true)),("expires_in",json!(901)),("resource",json!("other")),("unknown",json!(true))] {
            let mut message=json!({"action":"grant","execution_id":id});message[key]=value;
            let reply=wire(&root.join("operator.sock"),&serde_json::to_vec(&message).unwrap());assert_eq!(reply["error"],"invalid_request");observations.lock().unwrap().push(json!({"unsupported_field":key,"result":"invalid_request"}));
        }
        let reply=operator_command(&root,&OperatorRequest::Grant{execution_id:id.clone()}).unwrap();assert_eq!(reply["error"],"active_grant_exists");
        let wrong=operator_command(&root,&OperatorRequest::Revoke{execution_id:"wrong-execution".into()}).unwrap();assert_eq!(wrong["ok"],false);
        let after=broker.operator_status().unwrap();assert_eq!(serde_json::to_value(&before.grant).unwrap(),serde_json::to_value(&after.grant).unwrap());assert_eq!(before.total_admissions,after.total_admissions);
    });
    assert_eq!(report.admissions, 1);
    assert_eq!(target.count(), 1);
    note(
        case,
        json!({"invalid_grants":*proofs.lock().unwrap(),"second_active_grant":"rejected","original_grant_unchanged":true}),
    );
}

/// Private fixture mode: it is intentionally killed while its request is in
/// flight. Production binaries contain no recovery/fault-injection switch.
pub fn crash_child(args: &[String]) -> Result {
    if args.len() != 4 {
        return Err("invalid crash fixture arguments".into());
    }
    let mut supervisor = Supervisor::open(Path::new(&args[0]), Path::new(&args[1]), &args[2])?;
    let address = args[3].parse()?;
    supervisor.run_with_control(
        RunSpec {
            program: br#"
import time
from invariant_client import BrokerClient
try:
 BrokerClient.from_env().request('GET','/invoices',request_id='unfinished')
except Exception:
 # Deliberately survive broker loss: startup must remove this orphan rather
 # than relying on a cooperative program to exit after its socket closes.
 time.sleep(60)
"#,
            principal: PrincipalId::new("acceptance-operator"),
            resource: resource(address),
            duration: Duration::from_secs(60),
            initial_grant: true,
        },
        &AtomicBool::new(false),
        |_| {},
    )?;
    Err("crash fixture unexpectedly finished".into())
}
pub fn restart(case: &Case) {
    let gate = Arc::new(Gate::default());
    let held = gate.clone();
    let target = case.target("restart", move |_| {
        held.wait();
        invoices()
    });
    let state = case.root.join("run");
    let daemon = case.daemon.join("run");
    let mut command = Command::new(std::env::current_exe().unwrap());
    command
        .arg("--crash-child")
        .arg(&state)
        .arg(&daemon)
        .arg(&case.image)
        .arg(target.address.to_string())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    // Own process group includes the Docker attach client. Killing it leaves
    // only the actual workload container for startup orphan cleanup to stop.
    command.process_group(0);
    let mut child = command.spawn().unwrap();
    let deadline = Instant::now() + Duration::from_secs(20);
    while target.count() != 1 {
        if child.try_wait().unwrap().is_some() {
            panic!("crash fixture exited before target receipt");
        }
        assert!(
            Instant::now() < deadline,
            "crash fixture never reached target"
        );
        thread::sleep(Duration::from_millis(2));
    }
    let audit = find_files(&state, "audit.jsonl")[0].clone();
    let before = records(&audit);
    let old_id = before[0]["execution_id"].as_str().unwrap().to_owned();
    let profile: Value =
        serde_json::from_slice(&fs::read(find_files(&state, "isolation.json")[0].clone()).unwrap())
            .unwrap();
    let container = profile["container"].as_str().unwrap().to_owned();
    let old_socket = find_files(&state, "s")[0].clone();
    let old_cap =
        fs::read_to_string(audit.parent().unwrap().join("credentials/capability")).unwrap();
    assert!(before.iter().any(|r| r["event"] == "admitted"));
    assert!(!before.iter().any(|r| r["event"] == "response_received"));
    unsafe {
        assert_eq!(libc::kill(-(child.id() as i32), libc::SIGKILL), 0);
    }
    child.wait().unwrap();
    gate.release();
    assert_eq!(
        docker(&["inspect", "--format", "{{.State.Running}}", &container]).trim(),
        "true"
    );
    let mut supervisor = case.supervisor("run");
    assert!(
        !docker(&["ps", "-aq", "--filter", &format!("name=^{container}$")])
            .split_whitespace()
            .any(|_| true)
    );
    assert!(UnixStream::connect(old_socket).is_err());
    let source = format!(
        "from invariant_client import BrokerClient\nc=BrokerClient.from_env()\nc._capability_file=None\nc._capability={}\nassert c.request('GET','/invoices',request_id='unfinished')['reason']=='invalid_capability'\nprint('old handle rejected')",
        serde_json::to_string(&old_cap).unwrap()
    );
    let report = supervisor
        .run(
            RunSpec {
                program: source.as_bytes(),
                principal: PrincipalId::new("acceptance-operator"),
                resource: case.resource(&target),
                duration: Duration::from_secs(15),
                initial_grant: true,
            },
            &AtomicBool::new(false),
        )
        .unwrap();
    assert_eq!(report.termination, Termination::Exited, "{}", report.output);
    assert_eq!(report.exit_code, Some(0));
    assert_eq!(report.admissions, 0);
    assert_ne!(report.execution_id, old_id);
    case.runs
        .lock()
        .unwrap()
        .push(serde_json::to_value(&report).unwrap());
    assert_eq!(target.count(), 1);
    validate_audit(&audit);
    let log = records(&audit);
    assert!(!log.iter().any(|r| r["event"] == "response_received"));
    note(
        case,
        json!({"old_execution":old_id,"new_execution":report.execution_id,"old_container_removed_before_new_execution":true,"old_channel_closed":true,"old_handle_denied":true,"target_receipts":1,"unfinished_outcome":"unknown; no replay or allowance recovery"}),
    );
}
