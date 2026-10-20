> Historical specification, preserved October 20, 2026. The current product target is [the parallel-experiment MVP](../MVP_CONTRACT.md). This document remains the specification for the existing broker acceptance cases A01–A20 and the earlier workspace extensions.

# INVARIANT MVP Contract

**Version:** 0.1  
**Date:** October 17, 2026  
**Status:** Implementation specification; controls are not established until the acceptance tests pass.

## 1. Outcome

An unfamiliar Python program can read invoices through a trusted broker and compute a local summary. Changing the program cannot expand its execution's granted authority.

The first version supports one path:

```text
Trusted operator -> supervisor -> isolated Python execution
                                       |
                               execution-bound channel
                                       |
                               trusted Rust broker
                                       |
                              mock invoice HTTP API
```

This contract defines required behavior. **MUST** means required for MVP acceptance. Examples are illustrative; the behavioral requirements take precedence over an eventual configuration or wire format.

## 2. Scope and trust boundary

The MVP MUST provide:

- One local operator, one supervisor/broker instance, and a prepared Python image.
- A restricted Docker execution profile with private scratch storage.
- One configured mock resource and one narrow HTTP adapter.
- Trusted controls to start and stop executions, issue grants, and revoke grants.
- Default denial, request accounting, execution limits, and protected audit records.
- A Python client as a convenience layer. Reimplementing or bypassing the client MUST NOT bypass enforcement.

The operator, host, container runtime, supervisor, broker, resource configuration, and audit storage are trusted. Python programs, their subprocesses, broker messages, and API response bodies are untrusted. The host and container runtime are assumed uncompromised.

The agent integration, if added, MUST use this same execution path. Tools or credentials available to an agent outside this path are outside the MVP's enforcement boundary and MUST be identified in the demonstration.

## 3. Resource and operation

The resource MUST have an operator-assigned identity containing a resource ID, configuration version, exact origin, mock account/environment, and adapter version. The origin includes scheme, host, and port. This configuration MUST remain immutable for the broker session.

The mock service MUST be reachable by the broker and unreachable directly from the workload. It MUST expose forbidden routes or operations as well as the allowed route, so tests can distinguish broker enforcement from an absent target feature. Plain HTTP is permitted only for this isolated mock connection. No real credentials or production data are in scope.

Only this operation is grantable:

| Field | Required value |
|---|---|
| Resource | The configured resource identity and version |
| Method | `GET` |
| Path | Exactly `/invoices` |
| Query | Absent, including no empty `?` suffix |
| Request body | Absent |
| Destination | Constructed exclusively from trusted resource configuration |
| Redirect handling | Never follow redirects |
| Target timeout | Five seconds total, including connection and response reading |
| Response body | At most 1,048,576 bytes; exceeding the limit aborts reading |

The broker MUST accept structured operation fields rather than an arbitrary URL or raw HTTP message. It MUST reject absolute URLs, alternate paths, encoded path variants, fragments, query strings, bodies, caller-supplied headers, and unknown fields. It MUST generate the outbound request from validated fields and trusted configuration. The MVP MUST disable ambient HTTP proxies and automatic retries. It MUST request identity encoding and reject compressed responses and protocol upgrades. Upstream headers MUST be bounded to 32 KiB in aggregate. Responses MUST be returned as bounded data, without executing or interpreting their content as instructions.

Changing an endpoint, account, environment, or adapter configuration requires a new broker session and newly issued grants. A resource name MUST NOT silently acquire a different meaning under an existing grant.

## 4. Executions and channel binding

The supervisor MUST create a fresh, non-reused execution ID, record the initial program's content digest, and bind the execution to its operator principal and broker session. The digest records provenance; it is not a permission or a guarantee about every instruction subsequently executed.

Each execution MUST receive a dedicated Unix socket endpoint exposed through a private mount. The broker MUST associate that listener with exactly one server-side execution context. Workloads MUST NOT choose or override their principal, execution ID, resource binding, or grant lineage through request fields.

No execution may mount another execution's socket or scratch directory. The shared operator/control socket MUST NOT be exposed to workloads. All processes inside one execution share its authority and limits; subprocesses do not receive fresh budgets.

For live operator issuance, an execution MAY receive its own read-only capability-delivery directory containing only its scoped handle data. It MUST NOT expose policy controls, audit records, target credentials, or another execution's files. Delivery MUST follow a durable, explicit grant issuance. The broker MUST remain authoritative; changing or copying delivery data cannot issue or renew a grant.

A capability handle MUST be looked up within the channel's execution context. A forged handle or a handle copied from another execution MUST fail. Knowing another run's ID MUST convey no authority. The socket and existing connections MUST become unusable for new admissions when the execution terminates. Old execution IDs, endpoints, and handles MUST NOT be reassigned after restart.

## 5. Grant rules

A trusted operator can issue one active grant per execution. The grant MUST contain:

- A unique grant ID and the bound principal, execution ID, and broker session.
- The immutable resource identity and version.
- The operation scope in Section 3.
- An issuance time and an absolute expiration time, exactly 15 minutes apart.
- A total admission budget of 20 requests.
- `delegation: false` and an explicit active/revoked state.

Expiration MUST be checked at admission. Timekeeping MUST prevent a backward wall-clock adjustment from extending the 15-minute lifetime; a monotonic deadline may enforce this within the non-resumable broker session.

Missing, expired, revoked, exhausted, mismatched, or malformed grants MUST deny access. Unknown policy fields or unsupported constraints MUST cause grant issuance to fail. The workload cannot issue, modify, extend, reactivate, or delegate a grant.

Multiple-grant composition is outside this version. Issuing another grant while one is active MUST fail. Replacing a revoked, expired, or exhausted grant requires an explicit new operator action and a new grant ID. Only that action can authorize a new budget; reconnecting or changing code cannot reset one.

## 6. Admission, budgets, and revocation

**Admission** is the broker's committed local decision to consume one request allowance and permit one outbound attempt. It is the precise revocation and expiration boundary. Admission is not proof that the target received or completed the request.

Before admission, the broker MUST:

1. Validate a bounded message and obtain execution identity from its channel.
2. Resolve and validate the exact operation against the fixed resource.
3. Obtain an available outbound worker slot, without holding a previously admitted request in a worker queue.
4. Check that the execution is running and the grant is active, unexpired, correctly bound, and has remaining budget.
5. Commit one admission record containing the request identity, normalized operation digest, grant ID, and consumed budget ordinal.

The final authority check, budget consumption, durable admission record, and ordering against revocation MUST form one serialized admission operation. The implementation MUST NOT authorize first and consume budget later without rechecking authority. No outbound I/O may occur without a successfully committed admission record.

An execution's grant permits at most 20 admissions across all connections, threads, and subprocesses. Each admission permits at most one outbound attempt. Admissions consume budget even if connection establishment fails, the target returns an error, a response exceeds its limit, the workload disconnects, or the outcome becomes unknown. The MVP has no budget refunds or automatic retries.

Each request MUST carry a bounded request ID scoped to the execution. An identical ID and operation MUST NOT create another admission or target attempt. It may return its existing bounded result or an in-progress/unknown outcome. Reusing an ID with different operation fields MUST fail. Admission identities MUST remain available for deduplication until the execution terminates.

Revocation MUST use the same serialization boundary as admission. The operator receives success only after a durable revocation record is committed. Any admission ordered after that commit MUST fail. An operation admitted before the commit may still be sent or finish afterward; revocation does not recall it. The broker MUST record admission ordering so tests can distinguish these cases. Expiration and execution termination likewise prevent subsequent admissions without promising rollback of admitted operations.

If state or journal persistence fails, the broker MUST stop new admissions until it can establish authoritative state. It MUST NOT dispatch an operation whose admission commit is uncertain. After broker restart, every previous execution and grant MUST be invalidated, remaining workloads stopped before new work is accepted, and old channels rejected. The MVP MUST NOT resume runs, reactivate old grants, replay pending operations, or restore an exhausted budget.

## 7. Isolation and bounded execution

The supervisor MUST enforce the following initial profile, recorded in the test evidence:

| Limit or boundary | MVP requirement |
|---|---|
| Direct networking | Disabled, including IPv4, IPv6, DNS, host services, and metadata endpoints |
| Filesystem | Read-only prepared root; private writable scratch; only the dedicated broker socket and run-specific read-only program/capability inputs exposed |
| Host access | No host credentials, host Docker socket, host namespaces, privileged mode, or host workspace mounts |
| Process identity | Non-root; no additional Linux capabilities; no privilege escalation |
| System calls | An explicit restricted syscall profile |
| CPU | At most one CPU of execution capacity |
| Memory | 256 MiB; no additional swap allowance |
| Process count | At most 32 tasks, including threads |
| Scratch storage | Hard limit of 64 MiB |
| Execution duration | At most 15 minutes |
| Broker frame | At most 16 KiB, checked before unbounded buffering or parsing |
| Outstanding requests | At most four per execution, with bounded connection handling |
| Program output | At most 1 MiB combined stdout/stderr; terminate on overflow |

An unsupported isolation setting MUST cause startup to fail rather than silently weaken the profile. The test environment MUST record the host/VM platform, image digest, runtime version, actual mounts, namespaces, and effective limits.

Captured stdout/stderr is an explicitly permitted output channel to the initiating operator, stored outside the workload with restricted access. It MUST be displayed as inert text without active terminal control sequences. It MUST NOT automatically trigger another tool or be forwarded to a model provider. Scratch contents MUST be discarded at termination; exporting a file to the host is outside this version.

Prepared images are built before workload execution. Package acquisition, dependency installation requiring network access, and reuse of previous executions' writable state are outside scope.

## 8. Denials, failures, and audit

The broker MUST deny by default. Recognized denials MUST have stable reason codes covering invalid requests, invalid execution, invalid capabilities, missing grants, wrong resource/scope, expiry, revocation, exhausted budget, and unavailable authoritative state. Errors MUST NOT disclose host paths, credentials, another execution's data, or unrestricted upstream diagnostics.

Protected audit records MUST include execution start/termination, grant issuance/rejection/revocation, request denials, admissions, outbound attempts, and outcomes. Each admitted request MUST be traceable to its principal, execution, initial artifact digest, resource version, grant, request ID, operation digest, consumed ordinal, ordering sequence, and outcome. A malformed frame may be correlated by connection rather than an unavailable request ID.

The workload MUST have no write access to audit records or policy state. Ordinary records MUST exclude unrestricted payloads and secrets. The MVP uses a protected append-only local journal; it makes no claim of integrity against a compromised host administrator. Admission records MUST be durable before outbound attempts. A recorded denial cannot consume budget or contact the target. If a denial cannot be logged, access MUST still be denied and new admissions MUST stop.

Crashes and timeouts can leave remote outcomes unknown. Such outcomes MUST be reported as unknown, retain their consumed allowance, and never trigger automatic replay. A completed outbound operation followed by a failed outcome write MUST remain reconstructable as an admitted operation with an uncertain outcome.

## 9. Required acceptance evidence

Each test MUST identify its contract requirement, setup, expected result, actual result, and relevant broker and independent mock-target observations. Target-side logs MUST count all received requests, including forbidden operations. A denial reported by the client alone is insufficient evidence that the target was protected.

| ID | Test | Required result |
|---|---|---|
| A01 | Two independently authored invoice-summary programs, each under an explicitly issued equivalent grant | Both complete useful computation through the same unchanged broker and adapter |
| A02 | Missing grant; forged handle; forged identity; another run's handle | No admission and zero target requests |
| A03 | Wrong methods/routes; encoded paths; query variants; absolute URLs; caller headers; body; unknown fields | All rejected; zero target requests |
| A04 | Allowed route returns a redirect to another service | Initial request counted once; no request to redirect destination |
| A05 | Workload attempts direct IPv4/IPv6 sockets, DNS, host/metadata access, another run's socket, and host-file access | Defined bypass probes fail; no unauthorized target effects |
| A06 | Thirty distinct concurrent valid requests under a fresh 20-request grant, retrying only local capacity rejections with the same IDs | Exactly 20 admissions and 20 target requests against a healthy target; remaining requests denied for exhaustion |
| A07 | Reconnect and launch subprocesses after budget exhaustion | Budget remains exhausted; no additional target requests |
| A08 | Concurrent duplicate request IDs; same ID with changed fields | At most one admission/attempt for the original operation; changed fields rejected |
| A09 | Pause a request before final admission, commit revocation, then release it | Request denied; no target request |
| A10 | Admit a request before committing revocation; let it complete afterward | Its completion is permitted and correlated to the earlier admission; subsequent admissions denied |
| A11 | Reach expiry; simulate backward wall-clock adjustment | No admissions after the lifetime deadline and no extension from clock rollback |
| A12 | Simulate failures before admission commit, after commit/before send, and after target receipt/before outcome recording | No attempt without committed admission; consumed allowance never refunded; ambiguous outcomes never replayed |
| A13 | Make policy state or the audit journal unavailable | New admissions stop; no unrecorded outbound attempts |
| A14 | Restart broker with existing runs, handles, and incomplete requests | Old runs stopped and old grants/channels rejected; no automatic replay or budget restoration |
| A15 | Attempt resource rebinding during a session; start a new session with a changed target | Live configuration change rejected; previous grants invalid in the new session |
| A16 | Oversized frames, responses, output, process/memory/disk usage, and execution duration | Recorded limits hold; overruns never confer additional access |
| A17 | Stop a run while its socket remains connected; attempt grant replacement without operator authorization | No new admissions; no workload-driven authority renewal |
| A18 | Inspect journal correlation for allowed, denied, failed, and unknown requests | Every admitted request has a durable admission and a result or explicit/derivable unknown outcome; audit data inaccessible to workloads |
| A19 | Keep one program running while the operator issues and then revokes its grant | Access changes from denied to allowed to denied without a code change or automatic replay of a denied request |
| A20 | Issue grants with unknown fields, unsupported constraints, or a second active grant | Issuance fails; existing authority and accounting remain unchanged |

Concurrency and lifecycle tests MUST use synchronization barriers or controlled fault points rather than timing assumptions alone. Socket-binding tests MUST use two simultaneous executions with different grants. The evidence bundle MUST include the actual isolation configuration and test commands/results. A failed required test blocks MVP acceptance.

## 10. Completion and exclusions

The MVP is accepted when all tests in Section 9 pass reproducibly on the documented execution profile. The demonstration MUST show useful authorized computation, blocked unauthorized operations, a shared concurrency budget, and the defined revocation boundary. It MUST label unimplemented features and residual risks accurately.

Passing establishes evidence for the tested profile and probes. It does not prove the absence of sandbox escapes, complete information-flow control, safe behavior within every valid grant, or a match between an action and human intent.

The following remain outside the invoice/Python profile: real enterprise credentials, production data, multiple-grant composition, child-agent delegation, distributed brokers, run recovery after restart, arbitrary HTTP proxying, new protocols, semantic authorization for real ERP systems, model-provider integrations, a graphical console, and production multi-tenant security. The separately scoped Codex workspace extension below adds one model-provider integration. Any other expansion MUST update this contract and its acceptance evidence before stronger guarantees are claimed.

## 11. Codex workspace extension (`codex-workspace-v1`)

This extension provides `invariant init`, `invariant run --agent codex MODEL`,
`invariant chats` and `invariant resume [CHAT_ID]`.
It is a separate profile; passing A01–A20 does not establish acceptance of this
extension. Its detailed placement, limits and verification are specified in
[Codex workspace sessions](CODEX_WORKSPACE.md).

- Guided setup MUST report Codex version and Docker engine readiness before
  agent startup. Downloads, host installation and starting Docker MUST require
  explicit operator consent. Non-interactive launches and read-only checks MUST
  NOT install software or start services. Codex downloads MUST use pinned
  integrity and preserve the supported version; existing global installations
  MUST NOT be overwritten. Docker Desktop downloads MUST pass macOS signature
  and Gatekeeper checks before installation. First-launch terms and permission
  decisions MUST remain with the operator in Docker's own UI.
- Initialization MUST create a project policy without replacing an existing
  policy. Unknown or unsupported policy changes MUST fail closed.
- Per-project configuration MUST validate CPU, memory, additional swap, task,
  file-descriptor and scratch limits, session duration and editable/read-only
  project access and none/internet command networking. Legacy policies MUST keep their previous defaults. Supported
  ranges and units MUST be documented; invalid terminal edits MUST NOT replace
  the last valid file. Arbitrary host mounts, privileges and local execution
  MUST NOT become configurable authority.
- New projects MUST support plain `init`, `run` and `resume`, with finite automatic
  resource budgets selected from Docker capacity and a one-hour default deadline.
  Existing project policies and explicit manual limits MUST NOT be migrated or
  resized implicitly. Unknown capacity MUST use conservative finite limits.
- Automatic memory advice MUST be based on confirmed OOM evidence, remain private
  and project-bound, and be clamped to the newly measured next-session ceiling.
  The live session MUST keep its budget. Interrupted turns/commands MUST NOT be
  replayed automatically. Guardrails MUST separate selected session resources
  from the original project configuration and unselected inactive auto values.
- A first plain run MUST use a valid account-advertised model/effort default;
  later plain runs MUST reuse verified project-scoped preferences. Explicit
  model choices MUST NOT inherit another model's effort implicitly. Failed
  choices MUST NOT overwrite working preferences or change saved conversation
  identity. Normal stops, missing history and failed startup/turns MUST provide
  actionable recovery messages without expanding permissions.
- Each launch MUST take one policy snapshot and verify Docker against it.
  Project changes apply to the next run/resume, not a running session. Read-only
  project access MUST deny actual filesystem writes while retaining private
  writable scratch. An explicit duration override MUST NOT rewrite the project
  default. Resume MUST use the current project policy for its new executor.
- Launching the session permits project writes only in configured editable mode,
  and permits disclosure of project contents and command results to the selected OpenAI model. This is the
  sole exception to Section 7's prohibition on model feedback, and applies only
  to this profile. Invoice/Python output remains inert and operator-only.
- Host Codex and the workspace launcher are trusted. Authentication and model
  communication MUST stay outside the command container. The container MUST
  NOT receive provider credentials, host home or Docker control access.
- Codex filesystem and command execution MUST select the isolated executor with
  local execution disabled. A failed connection MUST NOT select a host fallback.
  Host extensions and alternate effect tools MUST be disabled for this profile.
- Only the project may be mounted writable. Configuration MUST be overlaid
  read-only. Existing symlinks, hard links and special files MUST be rejected.
  Arbitrary host-filesystem access MUST fail in real probes.
- Command networking MUST default to none, with IPv4/IPv6 sockets denied. The
  operator MAY explicitly select internet per project. That mode MUST use an
  isolated Docker bridge and the exact managed seccomp profile allowing IPv4/IPv6
  TCP/UDP; host/shared networking, additional network attachments, raw/packet
  sockets, published ports and added capabilities MUST remain unavailable.
  This broad outbound capability includes reachable local/host services; it MUST
  NOT be described as a domain allowlist or isolation from host network services.
- In editable projects, ordinary Python pip installs and explicit `--user`
  installs MUST persist in project-local dependency storage, with imports and
  installed command-line tools automatically available in fresh run/resume
  executors, including offline and login shells. No activation or custom
  PYTHONPATH MUST be required. Separate projects MUST NOT share installed packages.
  Read-only sessions MUST allow using saved dependencies while denying their
  modification. Temporary installs MUST remain available through an explicit
  scratch user-base override. Dependency storage MUST NOT modify host Python or
  require additional mounts, root privileges or automatic network permission.
  Python package installs MUST work in writable session scratch with an explicit
  scratch user base. Existing explicit project `--target` installs MUST remain
  usable, and no removed scratch packages MUST be claimed as recoverable. The
  root filesystem and configuration MUST stay read-only; enabling internet MUST
  NOT grant root/system-install authority or expose host credentials. Resume MUST
  apply the current network policy, preserving project packages and dropping old
  scratch. Real evidence MUST cover a package download/install/import, default
  offline denial, changes across sessions and continued filesystem restrictions.
- The documented container profile MUST be verified before use. Commands and
  subprocesses MUST share the same container limits and lifetime.
- The operator MUST be able to end the session by exit, EOF, interruption,
  deadline or an exact-session stop command. Cleanup MUST remove the owned
  container. Failed cleanup MUST be reported and retain recovery information.
- `invariant guardrails` MUST distinguish a configured inactive policy from live
  observed enforcement. Live verification MUST inspect the owning container and
  executor kernel and compare them to the recorded launch policy. Pending project
  configuration changes MUST be reported separately from live mismatches. It MUST show actual changed
  limits, flag mismatches/unavailable inspection, and never label defaults as live
  measurements. Inspection MUST NOT alter policy, start/stop an executor, read
  credentials/history or submit a model request. Launcher metadata/configuration
  and kernel measurements MUST be labeled separately. Successful socket checks
  MUST NOT be presented as proof of internet or registry reachability.
- Protected session records MUST identify the model, policy, image, lifecycle
  and cleanup outcome. They MUST NOT record authentication tokens. These records
  MUST NOT be described as complete command or model-request admission logs.
- New chats MUST use native Codex persistence. Saved history, including prompts,
  responses and tool results, MUST remain in the private host Codex home outside
  the mounted project. Authentication tokens MUST NOT enter chat metadata or
  lifecycle journals. An empty new chat MUST NOT replace the latest saved chat.
- Resume MUST select only a saved chat belonging to the resolved project path,
  preserve its native chat ID and model, and restore its confirmed effort unless
  the operator explicitly changes it. It MUST verify a fresh executor and the
  restored environment before accepting new input. Missing history or a changed
  identity/environment MUST fail without silently creating a new conversation.
- Exit, EOF and terminal hangup MUST keep saved history and clean the executor.
  Resume after a killed launcher MUST recover only its stale owned executor.
  Resume MUST NOT automatically replay a prompt, command or interrupted turn.
  Existing files remain current; scratch and running commands are not restored.

Acceptance requires the workspace integration probes and a live authenticated
multi-turn session that creates and executes a project file, plus the original
profile's regression suite. Persistence acceptance additionally requires a live
conversation that remembers a random phrase across terminal hangup and forced
launcher termination, retains its chat ID/model/effort, and executes commands in
the verified workspace after resume. Empty-chat, EOF, project binding and
missing-history failure behavior MUST be checked. Chats created before this
extension were ephemeral and cannot be recovered. The selected model MUST be
preserved without silent substitution. Full project disk quotas, arbitrary
network grants, other agents and production multi-tenant guarantees remain
excluded.

## 12. General persistent Linux environment

The original invoice MVP and the legacy `codex-workspace-v1` profile above retain
their isolation requirements. New `invariant init` projects MUST instead select
`codex-environment-v2`. Existing projects MUST retain their profile unless the
operator explicitly changes it with `invariant config set profile codex-environment-v2`.

- The general environment MUST have no language/runtime-profile selection or
  package-manager-specific installation paths. Normal Linux package managers,
  toolchain installers, virtual environments, symlinks, project-contained hard links and home/shell configuration
  MUST be usable within declared network/resource/kernel limits. Temporary scratch
  MUST allow execution of build outputs while retaining nosuid/nodev. Hard links
  MUST be rejected if any inode link is unaccounted for outside the project tree.
- Its filesystem and home MUST persist as project-bound Docker image checkpoints.
  Every run/resume MUST create a new executor from the exact recorded image,
  reapplying current policy rather than restoring old runtime authority. Processes,
  memory, connections and scratch MUST NOT be restored or automatically replayed.
- Writable sessions MAY run as container administrator with only CHOWN,
  DAC_OVERRIDE, FOWNER, FSETID, SETGID, SETUID, SYS_CHROOT and SETFCAP. Host access,
  privileged mode, SYS_ADMIN/SYS_PTRACE/NET_ADMIN/NET_RAW, host namespaces/devices,
  Docker control and published ports MUST remain unavailable. The exact general
  Docker seccomp profile, resource limits and socket policy MUST be verified.
- The project policy and trusted executor/static inspector MUST be read-only.
  Guardrail and memory-pressure checks MUST NOT run the environment's mutable
  interpreter or shared libraries. Authentication/history and checkpoint metadata
  MUST remain outside writable agent state. Checkpoint filesystems MUST be treated
  as project-owned data, never as a source of trusted control executables.
- Saving MUST stop processes, checkpoint, atomically record a project-bound image
  identity and only then remove the owned container. Failures MUST keep the prior
  checkpoint, stopped container and recovery metadata. Recovery MUST preserve the
  exact stale owned environment before starting another executor. A missing or
  foreign image MUST fail without silently substituting a fresh environment.
- Read-only sessions MUST freeze both project and environment filesystems while
  retaining temporary scratch and use of existing tools. They MUST NOT overwrite
  a saved writable environment. Independent projects MUST NOT share installed state.
- Resource sizing MUST NOT predict a technology stack from project manifests.
  Linux/Docker kernel/device limitations, finite resources, disk consumption,
  temporary processes and the absence of application-aware backups MUST be stated.

Acceptance additionally requires real toolchain/system-package installation,
multiple languages executing before and after native offline chat resume, normal
symlink/virtual-environment and shell-profile behavior, interrupted-launcher
recovery, read-only mode, immutable inspection despite a replaced environment
interpreter, separate-project isolation and the legacy regression/acceptance suites.
