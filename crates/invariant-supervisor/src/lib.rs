//! Trusted Docker execution supervisor. Run on Linux alongside the Docker
//! daemon, or in the supplied control container on Docker Desktop.
mod operator;
mod process;
pub use operator::{OperatorRequest, operator_command};

use std::fs::{self, File, OpenOptions, Permissions};
use std::io::{self, Write};
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant};

use invariant_broker::{Broker, BrokerConfig, FileJournal, UnixBrokerServer};
use invariant_core::{ExecutionState, PrincipalId, ResourceIdentity};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};

pub const MAX_OUTPUT_BYTES: usize = 1_048_576;
pub const MAX_PROGRAM_BYTES: usize = 1_048_576;
pub const MAX_DURATION: Duration = Duration::from_secs(900);
const COMMAND_TIMEOUT: Duration = Duration::from_secs(30);
const SECCOMP: &str = include_str!("../../../docker/seccomp.json");

fn failure(message: &str) -> io::Error {
    io::Error::other(message)
}
fn random_name() -> io::Result<String> {
    let mut bytes = [0; 16];
    getrandom::fill(&mut bytes).map_err(|_| failure("secure randomness unavailable"))?;
    Ok(format!("iv-{:032x}", u128::from_be_bytes(bytes)))
}
fn write_new(path: &Path, bytes: &[u8], mode: u32) -> io::Result<()> {
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(mode)
        .open(path)?;
    file.write_all(bytes)?;
    file.sync_all()?;
    File::open(path.parent().unwrap())?.sync_all()
}

/// Export known evidence only. Never copy capability delivery or staged source,
/// including when an operator exports while an execution is still running.
pub fn export_evidence(state: &Path, destination: &Path) -> io::Result<()> {
    fs::create_dir(destination)?;
    fs::set_permissions(destination, Permissions::from_mode(0o700))?;
    let copy = |source: PathBuf, target: PathBuf| -> io::Result<()> {
        match fs::symlink_metadata(&source) {
            Ok(metadata) if metadata.is_file() => {
                fs::copy(source, &target)?;
                fs::set_permissions(target, Permissions::from_mode(0o600))
            }
            Ok(_) => Err(failure("unexpected evidence file")),
            Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
            Err(error) => Err(error),
        }
    };
    copy(
        state.join("acceptance.json"),
        destination.join("acceptance.json"),
    )?;
    for entry in fs::read_dir(state)? {
        let entry = entry?;
        let name = entry.file_name();
        let Some(name) = name.to_str().filter(|n| {
            n.len() == 35 && n.starts_with("iv-") && n[3..].bytes().all(|b| b.is_ascii_hexdigit())
        }) else {
            continue;
        };
        if !entry.file_type()?.is_dir() {
            continue;
        }
        let target = destination.join(name);
        fs::create_dir(&target)?;
        fs::set_permissions(&target, Permissions::from_mode(0o700))?;
        for file in [
            "audit.jsonl",
            "configuration.json",
            "isolation.json",
            "result.json",
        ] {
            copy(entry.path().join(file), target.join(file))?;
        }
    }
    Ok(())
}

struct Docker;
impl Docker {
    fn command(&self, args: &[String]) -> Command {
        let mut command = Command::new("docker");
        command
            .env_clear()
            .env("PATH", "/usr/local/bin:/usr/bin:/bin")
            .arg("--host=unix:///var/run/docker.sock")
            .args(args);
        command
    }
    fn call(&self, args: &[&str]) -> io::Result<Vec<u8>> {
        process::bounded(
            &mut self.command(&args.iter().map(|s| s.to_string()).collect::<Vec<_>>()),
            262_144,
            COMMAND_TIMEOUT,
        )
    }
    fn json(&self, args: &[&str]) -> io::Result<Value> {
        serde_json::from_slice(&self.call(args)?).map_err(|_| failure("invalid runtime metadata"))
    }
    fn remove(&self, name: &str) -> io::Result<()> {
        self.call(&["rm", "--force", "--volumes", name])?;
        Ok(())
    }
}

/// `daemon_state` is the same directory as `state`, expressed in the daemon's
/// Linux filesystem. The Desktop launcher obtains it from its dedicated volume.
pub struct Supervisor {
    state: PathBuf,
    daemon_state: PathBuf,
    owner: String,
    image: String,
    platform: Value,
    docker: Docker,
    _lock: File,
    healthy: bool,
}

pub struct RunSpec<'a> {
    pub program: &'a [u8],
    pub principal: PrincipalId,
    pub resource: ResourceIdentity,
    /// Trusted callers may shorten, never extend, the fixed maximum.
    pub duration: Duration,
    /// Issuing at launch is an explicit trusted choice. False starts with no grant.
    pub initial_grant: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Termination {
    Exited,
    Timeout,
    OutputLimit,
    Cancelled,
    BrokerUnavailable,
    StartupFailed,
    RuntimeFailed,
    Stopped,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct RunReport {
    pub execution_id: String,
    pub termination: Termination,
    pub exit_code: Option<i64>,
    pub output: String,
    /// Captured bytes before escaping; never exceeds MAX_OUTPUT_BYTES.
    pub output_bytes: usize,
    pub admissions: u32,
    pub evidence_path: PathBuf,
}

impl Supervisor {
    pub fn open(state: &Path, daemon_state: &Path, image: &str) -> io::Result<Self> {
        if !cfg!(target_os = "linux") {
            return Err(failure(
                "use scripts/invariant on Docker Desktop; native supervision requires Linux",
            ));
        }
        fs::create_dir_all(state)?;
        fs::set_permissions(state, Permissions::from_mode(0o700))?;
        let state = state.canonicalize()?;
        if !daemon_state.is_absolute() || daemon_state.to_string_lossy().contains([',', '\n']) {
            return Err(failure("invalid daemon state directory"));
        }
        let lock = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .mode(0o600)
            .open(state.join("supervisor.lock"))?;
        lock.try_lock()
            .map_err(|_| failure("another supervisor owns this state directory"))?;
        let docker = Docker;
        let info = docker.json(&["info", "--format", "{{json .}}"])?;
        validate_platform(&info)?;
        let owner = format!(
            "{:x}",
            Sha256::digest(daemon_state.as_os_str().as_encoded_bytes())
        );
        // Recovery is completed while holding the persistent exclusive lock,
        // before creating a broker or issuing any new authority.
        let leftover = docker.call(&[
            "ps",
            "-aq",
            "--filter",
            &format!("label=io.invariant.owner={owner}"),
            "--filter",
            "label=io.invariant.role=workload",
        ])?;
        for id in String::from_utf8(leftover)
            .map_err(|_| failure("invalid container IDs"))?
            .split_whitespace()
        {
            if id.len() > 64 || !id.bytes().all(|b| b.is_ascii_hexdigit()) {
                return Err(failure("invalid container ID"));
            }
            docker.remove(id)?;
        }
        operator::remove_stale(&state)?;
        // No old broker state or grants are read. Old staging files are inert;
        // only newly created endpoints can be mounted by a new execution.
        let images = docker.json(&["image", "inspect", image])?;
        let metadata = images
            .get(0)
            .ok_or_else(|| failure("missing prepared image"))?;
        let image = metadata["Id"]
            .as_str()
            .filter(|s| s.starts_with("sha256:"))
            .ok_or_else(|| failure("missing image digest"))?
            .to_string();
        if metadata["Os"] != "linux"
            || metadata["Config"]["Volumes"]
                .as_object()
                .is_some_and(|v| !v.is_empty())
        {
            return Err(failure(
                "prepared image must be Linux with no declared volumes",
            ));
        }
        let platform = json!({"os":info["OperatingSystem"],"kernel":info["KernelVersion"],"architecture":info["Architecture"],"docker":info["ServerVersion"],"docker_cli":info["ClientInfo"]["Version"],"runtime":info["DefaultRuntime"],"runc":info["RuncCommit"],"containerd":info["ContainerdCommit"],"cgroups":info["CgroupVersion"],"image":image});
        Ok(Self {
            state,
            daemon_state: daemon_state.to_path_buf(),
            owner,
            image,
            platform,
            docker,
            _lock: lock,
            healthy: true,
        })
    }

    pub fn run(&mut self, spec: RunSpec<'_>, cancelled: &AtomicBool) -> io::Result<RunReport> {
        self.run_with_control(spec, cancelled, |_| {})
    }

    /// Only trusted operator code receives the broker control interface.
    /// The callback must return promptly (it may spawn an operator thread).
    pub fn run_with_control(
        &mut self,
        spec: RunSpec<'_>,
        cancelled: &AtomicBool,
        control: impl FnOnce(Arc<Broker>),
    ) -> io::Result<RunReport> {
        if !self.healthy {
            return Err(failure("supervisor requires restart after cleanup failure"));
        }
        if spec.program.len() > MAX_PROGRAM_BYTES
            || spec.duration.is_zero()
            || spec.duration > MAX_DURATION
        {
            return Err(failure("program or duration exceeds the execution profile"));
        }
        let name = random_name()?;
        let directory = self.state.join(&name);
        fs::create_dir(&directory)?;
        fs::set_permissions(&directory, Permissions::from_mode(0o700))?;
        let result = self.run_in(&name, &directory, spec, cancelled, control);
        // The Docker create reply can be lost: always query by the preselected
        // random name, including when creation appeared to fail.
        let remaining = self
            .docker
            .call(&["ps", "-aq", "--filter", &format!("name=^/{name}$")]);
        let cleanup = remaining.and_then(|ids| {
            if ids.is_empty() {
                Ok(())
            } else {
                self.docker.remove(&name)
            }
        });
        if cleanup.is_err() {
            self.healthy = false;
            return Err(failure(
                "container cleanup failed; new executions are disabled",
            ));
        }
        // Only supervisor-owned staging files; never recursively delete paths.
        for file in ["program.py", "empty", "seccomp.json"] {
            match fs::remove_file(directory.join(file)) {
                Ok(()) => {}
                Err(e) if e.kind() == io::ErrorKind::NotFound => {}
                Err(e) => return Err(e),
            }
        }
        if directory.join("credentials").exists() {
            fs::remove_file(directory.join("credentials/capability"))?;
            fs::remove_dir(directory.join("credentials"))?;
        }
        result
    }

    fn run_in(
        &self,
        name: &str,
        directory: &Path,
        spec: RunSpec<'_>,
        cancelled: &AtomicBool,
        control: impl FnOnce(Arc<Broker>),
    ) -> io::Result<RunReport> {
        write_new(&directory.join("program.py"), spec.program, 0o444)?;
        write_new(&directory.join("empty"), b"", 0o444)?;
        write_new(&directory.join("seccomp.json"), SECCOMP.as_bytes(), 0o600)?;
        let broker = Arc::new(
            Broker::start(
                BrokerConfig {
                    principal_id: spec.principal,
                    resource: spec.resource,
                    initial_artifact_digest: Sha256::digest(spec.program).into(),
                },
                FileJournal::create(directory.join("audit.jsonl"))?,
            )
            .map_err(io::Error::other)?,
        );
        let mut server = UnixBrokerServer::start_in(broker.clone(), directory)?;
        let execution_id = broker.execution_context().execution_id.as_str().to_string();
        let mut operator_server = None;
        let result = (|| {
            // The host parent remains 0700. Only this exact socket inode is
            // mounted, read-only; no world-readable directory is introduced.
            std::os::unix::fs::chown(server.socket_path(), Some(65532), Some(65532))?;
            let credentials = directory.join("credentials");
            fs::create_dir(&credentials)?;
            std::os::unix::fs::chown(&credentials, Some(65532), Some(65532))?;
            fs::set_permissions(&credentials, Permissions::from_mode(0o500))?;
            let capability = if spec.initial_grant {
                broker.issue_grant().map_err(io::Error::other)?.capability
            } else {
                random_name()?
            };
            operator::publish(&credentials, &capability)?;
            let daemon_dir = self.daemon_state.join(name);
            let daemon_socket = self.daemon_state.join(
                server
                    .socket_path()
                    .strip_prefix(&self.state)
                    .map_err(io::Error::other)?,
            );
            let args = create_args(
                name,
                &self.owner,
                &self.image,
                &daemon_dir,
                &daemon_socket,
                &directory.join("seccomp.json"),
            )?;
            process::bounded(&mut self.docker.command(&args), 4096, COMMAND_TIMEOUT)?;
            let inspection = self.docker.json(&["inspect", name])?;
            validate_container(&inspection[0], &self.image, &daemon_dir, &daemon_socket)?;
            let evidence_path = directory.join("isolation.json");
            let context = broker.execution_context();
            let mut evidence = json!({"execution":{"execution_id":context.execution_id,"principal_id":context.principal_id,"broker_session_id":context.broker_session_id,"initial_artifact_digest":context.initial_artifact_digest},"platform":self.platform,"container":name,"host_config":inspection[0]["HostConfig"],"mounts":inspection[0]["Mounts"],"seccomp_sha256":format!("{:x}",Sha256::digest(SECCOMP.as_bytes())),"maximum_duration_seconds":spec.duration.as_secs_f64()});
            write_new(
                &directory.join("configuration.json"),
                &serde_json::to_vec_pretty(&evidence)?,
                0o600,
            )?;
            let (mut child, receiver) = process::capture(
                self.docker
                    .command(&[
                        "start".into(),
                        "--attach".into(),
                        "--interactive".into(),
                        name.into(),
                    ])
                    .stdin(Stdio::piped()),
            )?;
            let mut input = child
                .stdin
                .take()
                .ok_or_else(|| failure("container input unavailable"))?;
            let monitor: io::Result<_> = (|| {
                let startup_deadline = Instant::now() + COMMAND_TIMEOUT;
                let mut run_deadline = None;
                let mut probe = Vec::new();
                let mut output = Vec::new();
                let mut output_bytes = 0;
                let mut closed = 0;
                let mut status = None;
                let mut control = Some(control);
                let termination = loop {
                    if cancelled.load(Ordering::Acquire) {
                        break Termination::Cancelled;
                    }
                    if operator_server
                        .as_ref()
                        .is_some_and(|server: &operator::OperatorServer| !server.healthy())
                    {
                        break Termination::BrokerUnavailable;
                    }
                    match broker.stats() {
                        Ok(s) if s.healthy && s.execution_state == ExecutionState::Running => {}
                        Ok(s) if s.healthy && s.execution_state == ExecutionState::Terminated => {
                            break Termination::Stopped;
                        }
                        _ => break Termination::BrokerUnavailable,
                    }
                    let now = Instant::now();
                    if run_deadline.is_some_and(|deadline| now >= deadline) {
                        break Termination::Timeout;
                    }
                    if run_deadline.is_none() && now >= startup_deadline {
                        break Termination::StartupFailed;
                    }
                    match receiver.recv_timeout(Duration::from_millis(10)) {
                        Ok(process::Event::Data { stdout, bytes }) => {
                            if run_deadline.is_none() && stdout {
                                probe.extend(bytes);
                                if probe.len() > 8192 {
                                    break Termination::StartupFailed;
                                }
                                if probe.last() == Some(&b'\n') {
                                    let data: Value = serde_json::from_slice(&probe)
                                        .map_err(|_| failure("invalid isolation probe"))?;
                                    validate_probe(&data)?;
                                    evidence["effective"] = data["preflight"].clone();
                                    write_new(
                                        &evidence_path,
                                        &serde_json::to_vec_pretty(&evidence)?,
                                        0o600,
                                    )?;
                                    operator_server = Some(operator::OperatorServer::start(
                                        &self.state,
                                        &credentials,
                                        broker.clone(),
                                    )?);
                                    control.take().unwrap()(broker.clone());
                                    input.write_all(b"G")?;
                                    run_deadline = Some(Instant::now() + spec.duration);
                                }
                            } else {
                                output_bytes += bytes.len();
                                let keep = (MAX_OUTPUT_BYTES - output.len()).min(bytes.len());
                                output.extend_from_slice(&bytes[..keep]);
                                if output_bytes > MAX_OUTPUT_BYTES {
                                    break Termination::OutputLimit;
                                }
                            }
                        }
                        Ok(process::Event::Closed) => closed += 1,
                        Ok(process::Event::Failed) => break Termination::RuntimeFailed,
                        Err(_) => {}
                    }
                    if status.is_none() {
                        status = child.try_wait()?;
                    }
                    if closed == 2 && status.is_some() {
                        break if run_deadline.is_some() {
                            Termination::Exited
                        } else {
                            Termination::StartupFailed
                        };
                    }
                };
                Ok((termination, output))
            })();
            // Termination is committed before killing the process tree. Stop
            // authority even on monitoring/persistence errors.
            let stopped = broker.stop().map_err(io::Error::other);
            drop(input);
            // Release backpressure before asking the daemon to remove a noisy
            // container. Otherwise daemon stream teardown can wait for an
            // attach client whose output queue we have stopped draining.
            drop(receiver);
            let _ = child.kill();
            let _ = child.wait();
            let removed = self.docker.remove(name);
            stopped?;
            removed?;
            let (termination, output) = monitor?;
            // Exit code is captured before removal below via the Docker attach
            // status; a nonzero program exit is still a completed execution.
            let exit_code = if termination == Termination::Exited {
                child.try_wait()?.and_then(|s| s.code()).map(i64::from)
            } else {
                None
            };
            let report = RunReport {
                execution_id,
                termination,
                exit_code,
                output: process::inert(&output),
                output_bytes: output.len(),
                admissions: u32::try_from(
                    broker
                        .operator_status()
                        .map_err(io::Error::other)?
                        .total_admissions,
                )
                .map_err(io::Error::other)?,
                evidence_path: if evidence_path.exists() {
                    evidence_path
                } else {
                    directory.join("configuration.json")
                },
            };
            write_new(
                &directory.join("result.json"),
                &serde_json::to_vec_pretty(&report)?,
                0o600,
            )?;
            Ok(report)
        })();
        if let Some(mut controls) = operator_server {
            // Stop admission before joining the control listener. A request
            // already being read cannot renew authority during shutdown.
            let _ = broker.stop();
            if let Err(error) = controls.shutdown() {
                let _ = server.shutdown();
                return Err(error);
            }
        }
        let stopped = server.shutdown();
        match (result, stopped) {
            (Ok(report), Ok(())) => Ok(report),
            (Err(e), _) | (_, Err(e)) => Err(e),
        }
    }
}

fn validate_platform(info: &Value) -> io::Result<()> {
    if info["OSType"] != "linux"
        || info["CgroupVersion"] != "2"
        || !["aarch64", "x86_64"]
            .iter()
            .any(|a| info["Architecture"] == *a)
        || ["MemoryLimit", "SwapLimit", "CpuCfsQuota", "PidsLimit"]
            .iter()
            .any(|k| info[*k] != true)
        || !info["SecurityOptions"].as_array().is_some_and(|a| {
            a.iter()
                .any(|v| v.as_str().is_some_and(|s| s.starts_with("name=seccomp")))
        })
    {
        return Err(failure(
            "Docker requires Linux arm64/amd64, cgroup v2, seccomp and all resource controllers",
        ));
    }
    Ok(())
}

fn create_args(
    name: &str,
    owner: &str,
    image: &str,
    directory: &Path,
    socket: &Path,
    seccomp: &Path,
) -> io::Result<Vec<String>> {
    let mut args: Vec<String> = [
        "create",
        "--pull=never",
        "--name",
        name,
        "--label",
        &format!("io.invariant.owner={owner}"),
        "--label",
        "io.invariant.role=workload",
        "--network=none",
        "--ipc=none",
        "--cgroupns=private",
        "--read-only",
        "--user=65532:65532",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges=true",
        "--cpus=1",
        "--memory=268435456",
        "--memory-swap=268435456",
        "--pids-limit=32",
        "--ulimit=nofile=128:128",
        "--ulimit=core=0:0",
        "--restart=no",
        "--log-driver=none",
        "--workdir=/scratch",
        "--interactive",
        "--env=INVARIANT_SOCKET=/broker/s",
        "--env=INVARIANT_CAPABILITY_FILE=/authority/capability",
        "--entrypoint=python3",
    ]
    .into_iter()
    .map(str::to_string)
    .collect();
    args.extend([
        format!("--security-opt=seccomp={}", seccomp.display()),
        "--tmpfs=/scratch:rw,noexec,nosuid,nodev,size=67108864,mode=700,uid=65532,gid=65532".into(),
        "--tmpfs=/dev:ro,noexec,nosuid,dev,size=65536,mode=755".into(),
    ]);
    for (source, target) in [
        (directory.join("program.py"), "/input/program.py"),
        (socket.to_path_buf(), "/broker/s"),
        (directory.join("empty"), "/etc/hosts"),
        (directory.join("empty"), "/etc/hostname"),
        (directory.join("empty"), "/etc/resolv.conf"),
        (directory.join("credentials"), "/authority"),
    ] {
        let source = source
            .to_str()
            .filter(|s| !s.contains([',', '\n']))
            .ok_or_else(|| failure("invalid mount source"))?;
        args.push(format!(
            "--mount=type=bind,src={source},dst={target},readonly"
        ));
    }
    args.extend([
        image.into(),
        "-S".into(),
        "-B".into(),
        "/opt/invariant/bootstrap.py".into(),
    ]);
    Ok(args)
}

fn validate_container(
    info: &Value,
    image: &str,
    directory: &Path,
    socket: &Path,
) -> io::Result<()> {
    let host = &info["HostConfig"];
    let security = host["SecurityOpt"]
        .as_array()
        .ok_or_else(|| failure("missing security options"))?;
    let valid = info["Image"] == image
        && info["Config"]["User"] == "65532:65532"
        && host["NetworkMode"] == "none"
        && host["IpcMode"] == "none"
        && host["PidMode"] == ""
        && host["CgroupnsMode"] == "private"
        && host["ReadonlyRootfs"] == true
        && host["Privileged"] == false
        && host["NanoCpus"] == 1_000_000_000u64
        && host["Memory"] == 268435456
        && host["MemorySwap"] == 268435456
        && host["PidsLimit"] == 32
        && host["CapDrop"] == json!(["ALL"])
        && host["CapAdd"].as_array().is_none_or(Vec::is_empty)
        && host["Devices"].as_array().is_none_or(Vec::is_empty)
        && host["RestartPolicy"]["Name"] == "no"
        && host["LogConfig"]["Type"] == "none"
        && security.iter().any(|v| v == "no-new-privileges=true")
        && security
            .iter()
            .any(|v| v.as_str().is_some_and(|s| s.starts_with("seccomp={")));
    if !valid {
        return Err(failure(
            "Docker did not apply the required isolation profile",
        ));
    }
    let mounts = info["Mounts"]
        .as_array()
        .ok_or_else(|| failure("missing runtime mounts"))?;
    let expected = [
        (directory.join("program.py"), "/input/program.py"),
        (socket.to_path_buf(), "/broker/s"),
        (directory.join("empty"), "/etc/hosts"),
        (directory.join("empty"), "/etc/hostname"),
        (directory.join("empty"), "/etc/resolv.conf"),
        (directory.join("credentials"), "/authority"),
    ];
    let binds: Vec<_> = mounts.iter().filter(|m| m["Type"] == "bind").collect();
    if binds.len() != expected.len()
        || expected.iter().any(|(source, target)| {
            !binds.iter().any(|m| {
                m["Source"] == source.to_string_lossy().as_ref()
                    && m["Destination"] == *target
                    && m["RW"] == false
            })
        })
    {
        return Err(failure("unexpected execution mounts"));
    }
    Ok(())
}

fn validate_probe(data: &Value) -> io::Result<()> {
    let p = &data["preflight"];
    if p["uid"] != 65532
        || p["seccomp"] != 2
        || p["network_sockets"] != "denied"
        || p["scratch_bytes"] != 67108864
        || p["limits"]["memory.max"] != "268435456"
        || p["limits"]["memory.swap.max"] != "0"
        || p["limits"]["pids.max"] != "32"
    {
        return Err(failure("effective isolation checks failed"));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn evidence_export_excludes_live_capabilities_source_and_symlinks() {
        let root = std::env::temp_dir().join(random_name().unwrap());
        fs::create_dir(&root).unwrap();
        let state = root.join("state");
        let run = state.join(random_name().unwrap());
        fs::create_dir_all(run.join("credentials")).unwrap();
        fs::write(run.join("audit.jsonl"), b"audit").unwrap();
        fs::write(run.join("credentials/capability"), b"private-capability").unwrap();
        fs::write(run.join("program.py"), b"private-source").unwrap();
        let destination = root.join("export");
        export_evidence(&state, &destination).unwrap();
        let exported = destination.join(run.file_name().unwrap());
        assert_eq!(fs::read(exported.join("audit.jsonl")).unwrap(), b"audit");
        assert!(!exported.join("credentials").exists());
        assert!(!exported.join("program.py").exists());
        std::os::unix::fs::symlink(run.join("program.py"), run.join("result.json")).unwrap();
        assert!(export_evidence(&state, &root.join("unsafe-export")).is_err());
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn unsupported_platforms_and_missing_controllers_fail_closed() {
        let supported = json!({"OSType":"linux","Architecture":"aarch64","CgroupVersion":"2","MemoryLimit":true,"SwapLimit":true,"CpuCfsQuota":true,"PidsLimit":true,"SecurityOptions":["name=seccomp,profile=unconfined"]});
        assert!(validate_platform(&supported).is_ok());
        for key in ["MemoryLimit", "SwapLimit", "CpuCfsQuota", "PidsLimit"] {
            let mut info = supported.clone();
            info[key] = json!(false);
            assert!(validate_platform(&info).is_err(), "{key}");
        }
        for (key, value) in [
            ("OSType", "windows"),
            ("CgroupVersion", "1"),
            ("Architecture", "armv7l"),
        ] {
            let mut info = supported.clone();
            info[key] = json!(value);
            assert!(validate_platform(&info).is_err(), "{key}");
        }
        assert!(validate_platform(&json!({})).is_err());
    }

    #[test]
    fn weakened_effective_limits_are_rejected() {
        let valid = json!({"preflight":{"uid":65532,"seccomp":2,"network_sockets":"denied","scratch_bytes":67108864,"limits":{"memory.max":"268435456","memory.swap.max":"0","pids.max":"32"}}});
        assert!(validate_probe(&valid).is_ok());
        for key in [
            "uid",
            "seccomp",
            "scratch_bytes",
            "network_sockets",
            "limits",
        ] {
            let mut changed = valid.clone();
            changed["preflight"][key] = Value::Null;
            assert!(validate_probe(&changed).is_err(), "{key}");
        }
    }

    #[test]
    fn mount_arguments_cannot_inject_an_extra_host_mount() {
        assert!(
            create_args(
                "run",
                "owner",
                "sha256:test",
                Path::new("/state/bad,dst=/host"),
                Path::new("/socket"),
                Path::new("/profile"),
            )
            .is_err()
        );
    }
}
