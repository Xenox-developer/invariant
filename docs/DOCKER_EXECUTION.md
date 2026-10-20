# Docker execution

The trusted Rust supervisor owns program staging, broker/grant creation,
container admission, output capture, cancellation, termination and cleanup.
The prepared Python image contains the client and a trusted isolation probe.

## Run

With Docker running, from the project directory:

```sh
./scripts/invariant prepare
./scripts/invariant run examples/invoice_summary.py
./scripts/invariant test
python3 -B scripts/test_supervisor_lifecycle.py
./scripts/invariant evidence
```

`prepare` builds trusted images before execution. It may download base images and
Rust dependencies. `run` never pulls images or installs dependencies. It resolves
the prepared image to its immutable image ID. Starting a run explicitly authorizes
one initial mock-invoice grant: 20 reads, 15 minutes, no delegation. Each invocation
creates a fresh broker and execution. The CLI uses a built-in mock API.

Use `--duration 10` after the program to shorten execution. The maximum is 900
seconds. Ctrl-C or termination signals request cancellation. The Rust interface
also accepts a cancellation flag and exposes trusted broker controls through
`run_with_control`. The [operator commands](OPERATOR_CONTROLS.md) expose status,
grant issuance, revocation and stop through a separate protected socket.
Use `run PROGRAM --no-grant` to start without access.

## Placement

```text
Operator -> host launcher -> trusted Linux control container
                              Rust supervisor + broker -> mock API
                                       |
                              exact private Unix socket
                                       |
                              isolated Python container
                              read-only image + program
                              private scratch
```

On Docker Desktop the supervisor runs inside the Linux VM. This avoids assuming
that a native macOS Unix socket works from a Linux container. Both containers use
the same daemon. The control container mounts the Docker control socket and a
dedicated state volume; **the workload mounts neither**. The control container is
trusted and has host-equivalent Docker authority. No workload code executes there.

Native supervision also supports a rootful local Linux daemon. Build the Rust
binary and pass `--state PATH --daemon-state PATH --image IMAGE --program FILE`.
The two paths must name the same directory from the supervisor and daemon
filesystems. The launcher obtains this mapping from its dedicated Docker volume.
Root privilege is needed to assign the socket to workload UID 65532. Rootless and
remote daemons, cgroup v1 and 32-bit architectures are unsupported.

## Fixed profile

| Control | Implementation |
|---|---|
| Identity | UID/GID 65532, all capabilities dropped, no-new-privileges |
| Networking | Network `none`; seccomp permits socket/socketpair only for AF_UNIX |
| Namespaces | Private PID, IPC, network, mount, UTS and cgroup namespaces |
| Root filesystem | Read-only prepared image |
| Program | Snapshot at most 1 MiB; read-only exact-file mount; SHA-256 in broker audit |
| Host mounts | Own effect socket, source snapshot, read-only capability directory and empty read-only resolver/host files |
| Scratch | Private 64 MiB tmpfs at `/scratch`, noexec/nosuid/nodev |
| Other writable areas | `/dev` read-only; shm disabled; host/resolver files read-only |
| CPU | One CPU quota |
| Memory | 256 MiB memory; memory+swap also 256 MiB, no extra swap allowance |
| Processes | 32 tasks, including threads; 128 file descriptors; no core dumps |
| System calls | Explicit default-deny allowlist; no new namespaces, mounts, ptrace, BPF or keyring operations |
| Output | 1 MiB stdout/stderr combined; bounded reader queue; Docker logging disabled |
| Time | Monotonic deadline, at most 15 minutes of untrusted execution |
| Lifecycle | No automatic restart; broker stopped before forced container removal |

The profile requires 64-bit arm64/amd64 Linux with cgroup v2, seccomp and memory,
swap, CPU and PID controllers. Unsupported settings fail startup. The supervisor
inspects the container before starting it. A trusted bootstrap then checks
effective cgroup values, identity, security flags, read-only mounts, scratch size,
denied network sockets and broker socket reachability. The program waits until
the supervisor durably saves evidence before execution. Its stdin becomes
`/dev/null`. All subprocesses share the container's grant and limits.

Scratch is destroyed with the container. Workload files are never exported.
Captured output escapes terminal controls, carriage-return overwriting and bidi
formatting controls and is returned only to the operator.

## State and recovery

The `invariant-supervisor-state-v1` volume holds protected audit journals, output
and evidence. Each run gets a fresh private directory and socket. Audit/result
files use mode 0600; state/run directories use mode 0700. The exposed socket inode
is assigned to workload UID 65532, mode 0600; its parent remains private to the
supervisor. Normal cleanup deletes staged program and profile files after container
removal. A controller crash may leave private staging files; a later run never
mounts or executes those files.

A persistent exclusive lock prevents simultaneous supervisors using this state.
Before accepting work, a new supervisor removes containers carrying both its
state-owner label and workload-role label. Other projects' containers are not
selected. Old grants are never loaded or requests replayed. Ambiguous container
creation failures still trigger cleanup using the fresh preselected name. A
cleanup failure disables further executions.

The host and runtime remain trusted. A supervisor crash can leave a workload
alive until restart recovery; broker authority disappears with the broker. An
unresponsive Docker daemon can prevent immediate removal. That failure is reported
instead of claiming cleanup. No crash-resume or production multi-tenant guarantee
is provided.

## Evidence and checks

`configuration.json` records inspected mounts, limits, platform, image ID, seccomp
digest and execution provenance. `isolation.json` adds effective limits and
namespace identifiers. `audit.jsonl` records broker decisions. `result.json`
records termination, exit status and bounded inert output. Capabilities and
response bodies are excluded from isolation evidence and broker audit. A workload
can print data into its own captured output.

`./scripts/invariant test` runs actual containers to check allowed/denied broker
operations, duplicates, network/filesystem restrictions, scratch/process/memory
limits, output overflow, duration, cancellation, inert output and fresh scratch.
`evidence` explicitly copies the protected volume to `.invariant/docker-evidence/`.
`scripts/test_supervisor_lifecycle.py` additionally checks the exclusive state lock,
restart cleanup after killing the controller, and termination-signal cleanup.
The complete acceptance matrix in the MVP contract remains a separate gate.

## Verified on October 17, 2026

The supplied profile was exercised on this Mac through Docker Desktop 27.4.0,
Linux `6.10.14-linuxkit`, aarch64, cgroup v2 and runc `1.1.13`.
All 73 Rust tests, 11 Python client tests, 9 real-container execution checks,
7 operator-control checks and 3 Docker lifecycle checks passed. Formatting and
Clippy checks also passed.
The amd64 profile is included but was not exercised on this arm64 machine.

The invoice example returned one invoice totaling 42, with exactly one admission
and one target request after duplicate detection. The probes observed UID 65532,
zero effective capabilities, no-new-privileges, seccomp filtering, CPU quota
`100000 100000`, memory limit `268435456`, swap allowance `0`, PID limit `32`
and scratch size `67108864`. The memory probe was killed with exit 137;
output overflow and timeout terminated their containers. A forced controller
crash left an orphan which the next supervisor removed before a successful run.

The latest execution evidence index is exported to
`.invariant/docker-evidence/acceptance.json`; lifecycle results are in
`.invariant/docker-lifecycle.json`. Each index entry identifies its protected
per-run configuration, effective-isolation evidence, audit and result files.
Live operator results are saved in `.invariant/operator-controls.json`. The same
Python client observed missing-grant, allowed and revoked states. Explicit
replacement rotated the capability, retained the old request-ID ledger and
admitted exactly 20 new reads, for 21 total target calls. Evidence exports omit
capability-delivery directories and staged program source.

Docker references: [resource constraints](https://docs.docker.com/engine/containers/resource_constraints/),
[seccomp](https://docs.docker.com/engine/security/seccomp/),
[tmpfs](https://docs.docker.com/engine/storage/tmpfs/) and
[bind mounts](https://docs.docker.com/engine/storage/bind-mounts/).
