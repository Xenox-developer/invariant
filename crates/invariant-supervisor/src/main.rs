use std::fs;
use std::io::{self, Read, Write};
use std::net::TcpListener;
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::Path;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::thread;
use std::time::Duration;

use invariant_core::{MockHttpOrigin, PrincipalId, ResourceId, ResourceIdentity};
use invariant_supervisor::{
    MAX_PROGRAM_BYTES, OperatorRequest, RunSpec, Supervisor, Termination, operator_command,
};

static CANCELLED: AtomicBool = AtomicBool::new(false);
extern "C" fn cancel(_: libc::c_int) {
    CANCELLED.store(true, Ordering::Release);
}

struct MockApi {
    resource: ResourceIdentity,
    requests: Arc<AtomicUsize>,
    stopping: Arc<AtomicBool>,
    thread: Option<thread::JoinHandle<()>>,
}
impl MockApi {
    fn start() -> io::Result<Self> {
        let listener = TcpListener::bind("127.0.0.1:0")?;
        let address = listener.local_addr()?;
        listener.set_nonblocking(true)?;
        let stopping = Arc::new(AtomicBool::new(false));
        let stopped = stopping.clone();
        let requests = Arc::new(AtomicUsize::new(0));
        let observed = requests.clone();
        let thread = thread::spawn(move || {
            while !stopped.load(Ordering::Acquire) {
                match listener.accept() {
                    Ok((mut stream, _)) => {
                        let _ = stream.set_nonblocking(false);
                        let _ = stream.set_read_timeout(Some(Duration::from_secs(2)));
                        let _ = stream.set_write_timeout(Some(Duration::from_secs(2)));
                        let mut request = Vec::new();
                        let mut chunk = [0; 1024];
                        while request.len() <= 4096 && !request.windows(4).any(|w| w == b"\r\n\r\n")
                        {
                            match stream.read(&mut chunk) {
                                Ok(0) | Err(_) => break,
                                Ok(n) => request.extend_from_slice(&chunk[..n]),
                            }
                        }
                        observed.fetch_add(1, Ordering::Relaxed);
                        // Forbidden operations genuinely exist at the target.
                        let body: &[u8] = if request.starts_with(b"DELETE ") {
                            br#"{"deleted":true}"#
                        } else {
                            br#"[{"id":"invoice-1","amount":42}]"#
                        };
                        let _ = write!(
                            stream,
                            "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                            body.len()
                        );
                        let _ = stream.write_all(body);
                    }
                    Err(e) if e.kind() == io::ErrorKind::WouldBlock => {
                        thread::sleep(Duration::from_millis(2))
                    }
                    Err(_) => break,
                }
            }
        });
        Ok(Self {
            resource: ResourceIdentity {
                resource_id: ResourceId::new("mock-invoice-api"),
                configuration_version: 1,
                origin: MockHttpOrigin(address),
                account: "mock-account".into(),
                environment: "test".into(),
                adapter_version: "mock-http-v1".into(),
            },
            requests,
            stopping,
            thread: Some(thread),
        })
    }
}
impl Drop for MockApi {
    fn drop(&mut self) {
        self.stopping.store(true, Ordering::Release);
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}

fn main() {
    // The handler only changes a lock-free flag. All cleanup runs in normal
    // supervisor code, including when this binary is PID 1 on Docker Desktop.
    unsafe {
        libc::signal(libc::SIGTERM, cancel as *const () as libc::sighandler_t);
        libc::signal(libc::SIGINT, cancel as *const () as libc::sighandler_t);
    }
    if let Err(error) = run() {
        eprintln!("INVARIANT: {error}");
        std::process::exit(1);
    }
}

fn run() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let value = |key: &str| -> Result<&str, io::Error> {
        args.windows(2)
            .find(|p| p[0] == key)
            .map(|p| p[1].as_str())
            .ok_or_else(|| io::Error::other(format!("missing {key}")))
    };
    let state = Path::new(value("--state")?);
    if args.iter().any(|s| s == "--export-evidence") {
        invariant_supervisor::export_evidence(state, Path::new("/export"))?;
        return Ok(());
    }
    if args.iter().any(|s| s == "--operator") {
        let execution = || value("--execution").map(str::to_string);
        let request = match value("--operator")? {
            "status" => OperatorRequest::Status {
                execution_id: execution().ok(),
            },
            "grant" => OperatorRequest::Grant {
                execution_id: execution()?,
            },
            "revoke" => OperatorRequest::Revoke {
                execution_id: execution()?,
            },
            "stop" => OperatorRequest::Stop {
                execution_id: execution()?,
            },
            _ => return Err(io::Error::other("unknown operator command").into()),
        };
        let reply = operator_command(state, &request)?;
        println!("{}", serde_json::to_string(&reply)?);
        return if reply["ok"] == true {
            Ok(())
        } else {
            Err(io::Error::other("operator command rejected").into())
        };
    }
    let daemon_state = Path::new(value("--daemon-state")?);
    let image = value("--image")?;
    let mut supervisor = Supervisor::open(state, daemon_state, image)?;
    let api = MockApi::start()?;
    if args.iter().any(|s| s == "--self-test") {
        return self_test(&mut supervisor, &api, state);
    }
    let file = fs::File::open(value("--program")?)?;
    let mut program = Vec::new();
    file.take(MAX_PROGRAM_BYTES as u64 + 1)
        .read_to_end(&mut program)?;
    let seconds = args
        .windows(2)
        .find(|p| p[0] == "--duration")
        .map(|p| p[1].parse::<u64>())
        .transpose()?
        .unwrap_or(900);
    let initial_grant = !args.iter().any(|s| s == "--no-grant");
    let report = supervisor.run_with_control(
        RunSpec {
            program: &program,
            principal: PrincipalId::new("local-operator"),
            resource: api.resource.clone(),
            duration: Duration::from_secs(seconds),
            initial_grant,
        },
        &CANCELLED,
        |broker| {
            println!(
                "Execution: {}\nOperator controls ready. Initial grant: {}",
                broker.execution_context().execution_id.as_str(),
                if initial_grant { "issued" } else { "none" }
            );
            let _ = io::stdout().flush();
        },
    )?;
    print!("{}", report.output);
    println!(
        "\nExecution: {}\nStopped: {:?}\nExit code: {:?}\nAdmissions: {}\nMock API requests: {}\nEvidence: {}",
        report.execution_id,
        report.termination,
        report.exit_code,
        report.admissions,
        api.requests.load(Ordering::Acquire),
        report.evidence_path.display()
    );
    if report.termination != Termination::Stopped
        && (report.termination != Termination::Exited || report.exit_code != Some(0))
    {
        return Err(io::Error::other("execution did not finish successfully").into());
    }
    Ok(())
}

fn self_test(
    supervisor: &mut Supervisor,
    api: &MockApi,
    state: &Path,
) -> Result<(), Box<dyn std::error::Error>> {
    let cases: &[(&str, &str, Duration, Termination, Option<i64>)] = &[
        (
            "broker, identity, filesystem and network",
            r#"
import os, socket, json
from invariant_client import BrokerClient
c=BrokerClient.from_env()
r=c.request('GET','/invoices',request_id='one')
assert json.loads(r['body'])[0]['amount']==42
assert c.request('GET','/invoices',request_id='one')['type']=='replay'
assert c.request('DELETE','/invoices/123')['reason']=='out_of_scope'
assert not os.path.exists('/var/run/docker.sock')
assert not os.path.exists('/state')
for family in (socket.AF_INET,socket.AF_INET6):
    try: socket.socket(family)
    except PermissionError: pass
    else: raise AssertionError('network available')
for path in ('/escape','/dev/escape','/etc/hosts','/input/program.py'):
    try: open(path,'wb')
    except OSError: pass
    else: raise AssertionError('writable outside scratch')
assert os.getuid()==65532
print('approved read, duplicate, forbidden delete, network and mounts: passed')
"#,
            Duration::from_secs(10),
            Termination::Exited,
            Some(0),
        ),
        (
            "scratch hard limit",
            "import errno\ntry:\n f=open('/scratch/full','wb',buffering=0)\n for _ in range(70): f.write(b'x'*1048576)\n raise AssertionError('scratch limit missing')\nexcept OSError as e:\n assert e.errno==errno.ENOSPC\nprint('scratch limit: passed')",
            Duration::from_secs(10),
            Termination::Exited,
            Some(0),
        ),
        (
            "process hard limit",
            "import os,errno,time\npids=[]\ntry:\n for _ in range(40):\n  p=os.fork()\n  if p==0: time.sleep(20);os._exit(0)\n  pids.append(p)\n raise AssertionError('process limit missing')\nexcept OSError as e:\n assert e.errno==errno.EAGAIN\n assert 1<=len(pids)<=31\nfinally:\n for p in pids: os.kill(p,9)\n for p in pids: os.waitpid(p,0)\nprint('process limit: passed')",
            Duration::from_secs(10),
            Termination::Exited,
            Some(0),
        ),
        (
            "memory hard limit",
            "data=bytearray(400*1024*1024)",
            Duration::from_secs(10),
            Termination::Exited,
            Some(137),
        ),
        (
            "combined output hard limit",
            "import os\nwhile True:\n os.write(1,b'x'*4096)\n os.write(2,b'y'*4096)",
            Duration::from_secs(10),
            Termination::OutputLimit,
            None,
        ),
        (
            "time limit and subprocess cleanup",
            "import subprocess,time\nsubprocess.Popen(['python3','-c','import time;time.sleep(60)'])\ntime.sleep(60)",
            Duration::from_millis(200),
            Termination::Timeout,
            None,
        ),
        (
            "inert output",
            "print('hello\\x1b[31m\\rworld')",
            Duration::from_secs(10),
            Termination::Exited,
            Some(0),
        ),
        (
            "fresh scratch",
            "import os\nassert os.listdir('/scratch')==[]\nprint('fresh scratch: passed')",
            Duration::from_secs(10),
            Termination::Exited,
            Some(0),
        ),
    ];
    let mut evidence = Vec::new();
    for (label, program, duration, termination, exit_code) in cases {
        let report = supervisor.run(
            RunSpec {
                program: program.as_bytes(),
                principal: PrincipalId::new("acceptance-operator"),
                resource: api.resource.clone(),
                duration: *duration,
                initial_grant: true,
            },
            &CANCELLED,
        )?;
        if report.termination != *termination || report.exit_code != *exit_code {
            return Err(io::Error::other(format!(
                "{label}: unexpected result {:?}, exit {:?}; {}",
                report.termination, report.exit_code, report.output
            ))
            .into());
        }
        if report.output.contains('\x1b')
            || report.output_bytes > 1_048_576
            || report.output.len() > 12 * 1_048_576
        {
            return Err(io::Error::other("unsafe output").into());
        }
        println!("PASS: {label}");
        evidence.push(report);
    }
    let cancelled = AtomicBool::new(true);
    let report = supervisor.run(
        RunSpec {
            program: b"raise AssertionError('cancelled program ran')",
            principal: PrincipalId::new("acceptance-operator"),
            resource: api.resource.clone(),
            duration: Duration::from_secs(10),
            initial_grant: true,
        },
        &cancelled,
    )?;
    if report.termination != Termination::Cancelled {
        return Err(io::Error::other("cancellation failed").into());
    }
    evidence.push(report);
    println!("PASS: operator cancellation");
    if api.requests.load(Ordering::Acquire) != 1 {
        return Err(io::Error::other("unexpected mock target calls").into());
    }
    let path = state.join("acceptance.json");
    let mut file = fs::OpenOptions::new()
        .write(true)
        .create(true)
        .truncate(true)
        .mode(0o600)
        .open(&path)?;
    fs::set_permissions(&path, fs::Permissions::from_mode(0o600))?;
    file.write_all(&serde_json::to_vec_pretty(&evidence)?)?;
    file.sync_all()?;
    println!("Docker acceptance checks passed; evidence saved to /state/acceptance.json");
    Ok(())
}
