# Codex workspace sessions

**New projects now use `codex-environment-v2`: a persistent general-purpose Linux
environment.** The agent installs any Linux-compatible tools normally; OS files,
home, toolchains and configuration persist together. See
[the current architecture, migration and limits](ENVIRONMENT_ARCHITECTURE.md).
Use `invariant config set profile codex-environment-v2` to switch an existing
project explicitly. The fixed Python-oriented profile described below remains
available for compatibility; its package paths and read-only system restrictions
do not apply to the new general environment.

The `codex-workspace-v1` profile is an interactive agent environment. The
original invoice/Python profile remains a separate execution path and keeps its
original grants and acceptance matrix.

## Use

Install with `python3 -m pip install -e /path/to/invariant`. Python 3.11+, a
running Linux-container Docker engine, and Codex CLI 0.163.0 are required. Run
`invariant setup` to detect existing installations and offer missing downloads.
See [guided setup](SETUP.md). `run` and `resume` offer the same guided flow in an
interactive terminal; piped input never becomes permission to install software.

This adapter accepts 0.163.0 prereleases as well; other versions fail closed.
An explicitly installed INVARIANT copy under `~/.invariant/tools/codex-0.163.0`
takes precedence. Otherwise, it uses `codex` from PATH, then the bundled CLI
inside `ChatGPT.app` in `/Applications` or `~/Applications` on macOS. Docker is
detected on PATH, in `~/.docker/bin`, or inside either system/user `Docker.app`.
No shell configuration changes are needed. Detection checks both tools and
whether Docker's engine is reachable; missing, stopped, inaccessible and
incompatible installations are reported separately.

In an empty project directory:

```sh
invariant init
invariant run
```

To choose the model's reasoning effort:

```sh
invariant run --model gpt-6.1-sol --effort high
```

`--effort LEVEL` applies to `run` (including the original `--agent codex MODEL`
form) and `resume`. Levels such as `low`,
`medium`, `high`, `xhigh`, `max` and `ultra` depend on the selected model and
account. INVARIANT queries Codex's model catalog, including all pages and hidden
models, and rejects unsupported levels with the advertised choices. It passes
the choice into thread configuration and every chat turn, checks the thread's
confirmed setting, and displays it before chat begins. A different confirmed
effort stops startup rather than silently substituting a value. Omit the flag
to reuse this project's confirmed effort on a bare run. An explicit new model
uses its own default effort if you omit the flag; this option does not change
the workspace permissions.

`init` exclusively creates `.invariant/config.json`. It never overwrites a
configuration. The policy allows project writes and disclosure of project
content/tool results to the selected OpenAI model, and denies command
networking by default. Networking, resource limits, session duration and project access can be changed
per project as described below. Unknown or unsupported settings fail closed.

The first `run` builds the pinned Codex executor image automatically. Run
`invariant prepare --agent codex` to build it beforehand. The image contains
Python 3.12, bash, git, ripgrep and the official Codex 0.163.0 Linux executable.
Both supported architectures have SHA-512-pinned package downloads. Package
downloads during a chat require the per-project internet opt-in described below.

Chat at `You>`. Messages share one thread until `/exit`, `/quit`, EOF, stop or
the session deadline. Ctrl+C interrupts an active turn; another Ctrl+C stops
the session. Ctrl+C at the prompt stops it. New projects default to 3600
seconds; existing policies keep their old 900-second default or chosen duration; change `duration_seconds` per project or override one session with
`--duration SECONDS` (1–86400). Allowed file edits persist immediately.

### Continue a saved chat

```sh
cd /path/to/your/project
invariant resume                  # latest saved chat; same model and effort
invariant chats                   # list this project's saved chat IDs
invariant chats --json
invariant resume CHAT_ID          # choose an older chat
invariant resume --effort high    # change reasoning effort
invariant resume --duration 3600  # new one-hour execution session
```

Each `run --agent codex MODEL` starts a new conversation. Native Codex stores
its history after the first accepted message; closing an empty new chat does
not replace your last saved conversation. The startup screen prints the chat
ID and resume command. `/exit`, EOF and terminal hangup keep the saved chat.
A resumed chat uses its saved model and confirmed effort; explicit `--effort`
is validated against that model's current catalog. An unavailable history,
wrong project, different returned chat ID or changed environment fails startup
without creating a replacement chat. Existing pre-persistence conversations
were ephemeral and cannot be recovered.

Resume reloads native Codex history, including prior user messages and tool
results, rather than replaying prompts. The terminal starts at a fresh prompt;
it does not reprint the previous transcript. It starts a fresh, verified Docker
executor for the current project files and recovers a stale owned executor
first if the previous launcher was killed. File edits are not rolled back,
private scratch and running commands are not restored, and interrupted work
is not automatically resubmitted. The last in-flight response may be incomplete
after a crash; completed turns are preserved by Codex's native history store.

In another terminal in the same project:

```sh
invariant status
invariant status --json
invariant stop ws-THE_EXACT_SESSION_ID
```

## Automatic resources and recovery

Ordinary use is `invariant init`, `invariant run`, and `invariant resume`.
A fresh project uses `resource_mode: "auto"`. No resource questions are required.
`init` never rewrites an existing policy, including its duration or manual limits.

At the first run, INVARIANT queries the signed-in account's paginated model
catalog and selects its single visible, text-capable `isDefault` model and valid
`defaultReasoningEffort`, as described in the official
[model catalog](https://learn.chatgpt.com/docs/app-server#list-models-modellist).
It does not hard-code a model. Ambiguous/missing defaults fail with a command to
select one explicitly: `invariant run --model MODEL_NAME`.

Successful launch settings are saved as a private mode-0600 `launch.json` in
this project's trusted state directory, outside the project mount. Every future
bare run reuses the confirmed model and effort. Explicit `--model` or the legacy
positional model replaces them after verification; `--effort` overrides the
chosen model's effort and is remembered. An explicit model without an effort
uses that model's default, rather than carrying effort over from another model.
An existing project without launch preferences can use its latest saved chat's
model/effort for a new run. A run still starts a new conversation; resume keeps
the selected saved chat's identity/model/effort. Empty chats remember launch
preferences without replacing the latest durable conversation. Failed settings
never overwrite working preferences. Invalid launch preferences can be replaced
with an explicit model; saved history remains separate.

Automatic sizing reads Docker's reported CPU count and total memory. These are
engine inventory, not a measurement of currently free resources or all parent
cgroup constraints. It does not inspect technology manifests, guess the stack,
or install tools to choose a budget. Confirmed memory pressure can inform the
next launch, independently of the technologies in use.

| Resource | Automatic selection and hard ceiling |
| --- | --- |
| CPU | Up to 2; never more than half Docker's CPU count, with a minimum of 1 |
| Memory | Starts up to 1024 MiB; ceiling is half Docker memory rounded down to 64 MiB, at most 4096 MiB |
| Extra swap | 0 |
| Tasks | 256 with measured memory, otherwise 128 |
| File descriptors | 1024 with measured memory, otherwise 256 |
| Scratch | One quarter selected memory, 16–512 MiB |

Unknown capacity uses finite conservative fallback limits (1 CPU when CPU is
unknown; 512 MiB memory when memory is unknown), and the startup screen says so.
Tiny engines below the minimum supported workspace budget stop with guidance to
increase Docker memory. These budgets are per executor, not reservations or a
global scheduler across concurrent projects, and do not constrain host Codex or
model tokens. Detection does not prove that the chosen budget is currently free.

The actual selected values form an immutable session snapshot. `guardrails`
checks Docker and cgroups against that snapshot, while separately comparing the
original project policy for pending changes. Inactive auto projects show
unselected limits (`null` in JSON) and hard ceilings, not guessed live values.

After a turn and before cleanup, INVARIANT reads cgroup `memory.events` or a
stopped container's OOM state. A confirmed memory kill in auto mode records
bounded advice in private `resources.json`; the next session can double memory
up to its newly measured ceiling. The current container is not resized. Unknown
failures, disk-full errors and ordinary process exits are not assumed to be OOM.
There is no automatic model-turn or command replay: prior file changes may
already exist. At the ceiling, the message points to Docker capacity and advanced
settings. This version does not automatically resize CPU/tasks/scratch during a
session or auto-retry a build; those limits are selected for each new executor.

Opt an existing project in, or return to manual control:

```sh
invariant config set resource_mode auto    # resets numeric fallback values
invariant config set resource_mode manual
invariant config set limits.memory_mib 768 # also selects manual mode
```

Direct JSON edits combining auto mode with custom numeric limits are rejected.
This prevents a manually edited limit from being silently ignored.

Normal exits explain whether a chat was saved and print `invariant resume`.
Deadline/operator stops explain the reason. Crash recovery removes only the
recorded owned executor and explains that files/history remain while running
commands and scratch are not restored. A failed model turn leaves the chat open
for another message; a broken connection closes it with recovery instructions.
Missing chats point to `invariant run`; unsupported settings point to explicit
model/effort selection. No recovery path silently opens a host executor, enables
internet, installs dependencies or replays interrupted work. Cached-image lookups
are retried once before preparing an image; successful preparation uses compact
progress output and failures include a diagnostic and retry command.

## Change project settings

From the initialized project folder:

```sh
invariant config                                      # show effective settings and file path
invariant config --json
invariant config set resource_mode auto                   # automatic sizing
invariant config set limits.cpus 2
invariant config set limits.memory_mib 1024
invariant config set limits.extra_swap_mib 0
invariant config set limits.processes 256
invariant config set limits.file_descriptors 512
invariant config set limits.scratch_mib 256
invariant config set duration_seconds 3600
invariant config set workspace_access read-only        # inspect without editing project files
invariant config set workspace_access read-write       # allow project edits again
invariant config set command_network internet          # permit command internet access
invariant config set command_network none              # disable it for the next launch
```

You can also edit `.invariant/config.json` directly. `config` displays the full
normalized JSON, including defaults for omitted optional settings. Values in
MiB use 1 MiB = 1,048,576 bytes. Numeric defaults below apply in manual mode
or are fallback values in an auto configuration file; live auto values are
selected separately at launch. Supported settings:

| Setting | Initial default | Allowed values |
| --- | --- | --- |
| `resource_mode` | `auto` for new projects; `manual` for legacy | `auto`, `manual` |
| `command_network` | `none` | `none`, `internet` |
| `workspace_access` | `read-write` | `read-write`, `read-only` |
| `duration_seconds` | 3600 for new projects; 900 for legacy | Integer 1–86400 |
| `limits.cpus` | 1 | 0.1–64, at most two decimal places |
| `limits.memory_mib` | 512 | Integer 64–65536 |
| `limits.extra_swap_mib` | 0 | Integer 0–65536 (additional to memory) |
| `limits.processes` | 128 | Integer 32–4096 (tasks, including threads) |
| `limits.file_descriptors` | 256 | Integer 64–65536 (soft and hard per process) |
| `limits.scratch_mib` | 128 | Integer 16–16384 |

Each change is validated and atomically saved. Invalid values do not replace the
previous policy. Unknown keys, duplicate JSON keys, booleans used as numbers,
unlimited values and unsupported authority changes are rejected. Concurrent
terminal edits use a project lock. Existing five-field fixed-profile files
remain valid and use the same defaults; no migration is required. `init` never
resets an existing policy. Projects keep independent settings.

Changes apply only on the next `run` or `resume`. A running session retains its
launch snapshot and deadline. `guardrails` verifies against that recorded policy
and reports pending configuration separately; editing the file does not resize
or grant new access to an existing container. `--duration` overrides the project
default for one launch without rewriting the file. Resume keeps the native chat
and model while creating an executor under the current project policy.

In read-only mode, Docker mounts the entire project read-only, and startup and
live inspection verify that flag. Temporary files can still be written in
`/scratch`; project file edits and deletion fail at the filesystem. Tools that
need to write build output in the project must use `/scratch` or editable mode.
`.invariant` always remains read-only inside either kind of executor.

Host mounts, privileges, root filesystem access, model provider and local
execution fallback are not configurable in this profile. Host credentials
remain outside the executor, and
project content/tool results may still be sent to the selected OpenAI model.
Limits apply to the command container; they are not host Codex limits or a model
token budget. The project has no disk quota. Docker must support and have enough
capacity for the selected limits; unsupported or unapplied settings stop startup.
Very small limits can prevent tools from running even within the supported range.

## Internet access and installing packages

Enable networking explicitly inside the project, then start or resume a chat:

```sh
invariant config set command_network internet
invariant run --agent codex gpt-6.1-sol --effort medium
# Or: invariant resume
```

Ask Codex to install the package and use it. Python 3.12 and pip are included.
Install normally inside the chat; no activation or special import path is needed:

```sh
python3 -m pip install requests
python3 -c "import requests; print(requests.__version__)"
```

Both ordinary installs and `pip install --user` use the project-local
`PYTHONUSERBASE=/workspace/.invariant-deps/python`. Python automatically finds its
versioned user site-packages; installed command-line tools are on `PATH`, including
in login shells. Immutable system commands take precedence over package scripts.
The pip download cache is in `.invariant-deps/cache/pip`. These are normal project
files: they survive `/exit`, terminal closure, forced launcher termination, a new
run and native chat resume. A fresh executor uses them without reinstalling,
including when the next session is offline. No package directory is mounted
outside the project, and the host Python environment is not modified.

Read-only sessions can import saved packages and execute their command-line tools,
but cannot install, upgrade, uninstall or change project dependencies. For an
explicitly temporary install (also usable with a read-only project), override
the user base for installation and use:

```sh
PYTHONUSERBASE=/scratch/.local python3 -m pip install --user --no-cache-dir requests
PYTHONUSERBASE=/scratch/.local python3 your_script.py
# Temporary command-line tools: /scratch/.local/bin/TOOL
```

The temporary override disappears when the executor is removed. Return to the
default user base to use the saved project packages. Explicit `--target /workspace/.packages`
installs from earlier versions continue to work with `PYTHONPATH=/workspace/.packages`;
INVARIANT does not move, delete or reinstall them automatically. Old session-only
packages removed with a previous executor cannot be recovered.

Add `.invariant-deps/` to your project's ignore file. Dependency storage has no
disk quota, just like other project files; scratch and memory limits still apply
to unpacking and build processes. Persistent packages are Linux dependencies for
the executor's Python version and architecture, not a portable host environment
or a dependency lockfile. Keep your requirements/lockfile to rebuild when changing
Python versions or architecture. To reset, exit the chat and remove the project's
`.invariant-deps/` folder; this does not erase saved conversations. To uninstall
one dependency, ask Codex to run `python3 -m pip uninstall PACKAGE` in an editable
session. Separate projects have separate package directories.

The existing startup rule still rejects project symlinks and hard links: virtual
environments and editable installs can create those links, so use ordinary
non-editable Python packages. Packages that require system libraries,
compilers or missing tools may need a prepared image. Internet access does not
add npm, Cargo or other toolchains. System package installs (`apt`, `sudo`, writes
to `/usr`) remain unavailable because the system filesystem is read-only and
commands run without root privileges.

`internet` selects Docker's isolated bridge network and a seccomp profile that
additionally permits IPv4/IPv6 TCP and UDP sockets, including DNS. It also allows
read-only extended-attribute queries needed by pip when copying files from
scratch into the project; extended-attribute writes remain blocked. Raw/packet
sockets and namespace creation remain denied. No host networking, extra mounts,
capabilities or published ports are enabled. Startup verifies the exact socket
profile, network mode and sole network attachment. The same filesystem and
resource checks apply in both network modes.

This is broad outbound access, not a package-registry or domain allowlist.
Commands can contact reachable internet, local-network and Docker-host services
and transmit data available inside the container. It does not provide network
isolation from host services or other containers reachable on the bridge.
Download failures (DNS, proxy, TLS, registry availability) are reported by the
package manager; network permission does not guarantee a working internet route.
Host proxy settings and credentials are not copied into commands. Explicit
empty proxy variables prevent Docker CLI from automatically injecting its saved
proxy URLs (including credentials); startup rejects nonempty inherited proxy
variables. Configure any required project-specific proxy explicitly inside the
container without exposing host credentials.
The host Codex web-search feature remains disabled in both modes.

Disable networking for the next launch:

```sh
invariant config set command_network none
# Exit the existing chat, then:
invariant resume
```

An existing chat executor keeps its recorded network policy until stopped.
`guardrails` shows pending changes separately from its live socket checks. It
checks socket permission and Docker settings without contacting the internet;
it does not verify DNS, TLS, registry reachability or an outbound allowlist.

## Show guardrails in the terminal

From the initialized project folder:

```sh
invariant guardrails
invariant guardrails --json
```

For live settings, leave the chat running and use another terminal in the same
project. The command reads the current session identity and operator readiness,
checks Docker ownership/mounts and its recorded immutable image, then reads the
executor's cgroup limits, file-descriptor limits, UID, capability masks,
seccomp/no-new-privileges state and filesystem read-only flags. It also attempts
IPv4/IPv6 socket creation without making a network connection. The probe uses
the immutable container ID and isolated Python mode so project modules cannot
replace its standard-library imports. Docker/session identity is checked again
at the end to reject a changed/stopped session during inspection.

The output includes CPU, memory, additional swap, process/task count, file
descriptors, scratch capacity, network, privileges, writable mounts and image ID.
Session duration and local-executor/web-search configuration come from trusted
launcher metadata/configuration and are labeled separately from kernel values.
Older sessions lacking duration metadata show it as unavailable. A shorter or
longer `--duration` is displayed as selected; the default is not substituted.

`ACTIVE` with `verified: true` means the observed Docker/kernel snapshot matched
the session’s recorded project policy. Changed live limits remain visible at their actual values and
produce `mismatch`, warnings and exit status 1. A stale control socket, missing
container, unavailable Docker, bad metadata or failed probe produces `unverified`
and exit status 1. With no current session, exit status 0 returns `inactive`,
`verified: false` and explicitly labeled configured values without calling Docker.
For automation requiring live verification, check the JSON `verified` field.

This read-only command does not install/start/stop anything, change project or
policy files, read authentication/history, submit model requests, or enforce new
settings. It briefly runs a bounded inspection process in the existing executor.
Configured values and the launcher use the same validated project policy. The
printed scope also explains the selected project access and permitted disclosure
to the OpenAI model, that the project has no disk quota, and that host Codex/model
communication are outside the command container's resource limits. Launcher
settings are configuration evidence, not a fresh attestation of every host tool.
A successful snapshot does not prove the absence of every possible sandbox escape.

## Placement and enforcement

```text
Terminal chat → trusted host Codex app-server → OpenAI model
                         │
                  stdio execution protocol
                         │
                 isolated Codex exec-server
                         │
                  files and commands
                         │
                  /workspace project
```

INVARIANT maintains a private Codex home outside the project, configures
`environments.toml` with `include_local = false`, and selects only the Docker
executor. No network listener is published. It verifies Docker's profile before
connecting, requires the executor's working directory to be `/workspace`, and
pins the thread to that environment. A missing executor never falls back to a
host shell.

Host Codex, the INVARIANT launcher, host OS, Docker engine and prepared image
are trusted. Model instructions, project files, commands and their subprocesses
are untrusted. The model connection and authentication lifecycle remain in the
trusted host process. A file-based existing Codex auth cache is linked into the
private home without the launcher reading tokens. Keyring-only accounts may
need to sign in once using the displayed browser link. Native Codex handles
refresh. API-key accounts are rejected by this subscription-oriented profile;
ambient API keys and other credentials are not inherited.

Host hooks, plugins, apps, browser/computer tools, image generation, delegation,
worktrees and dependency helpers are disabled by startup overrides. Codex's Code
Mode runner remains part of the trusted host runtime for tool orchestration;
its shell/file tools use the isolated executor. Host Codex and its orchestration
processes are not covered by the command container's resource limits.
The executor's read-only managed requirements deny MCP/plugin configuration.
Server requests cannot grant wider permissions. Web search is disabled.

The executor has a read-only root, non-root UID, no capabilities, no new
privileges and a seccomp filter. By default it permits AF_UNIX only and has no
network; the internet opt-in adds IPv4/IPv6 TCP/UDP over Docker bridge. Initial resource
manual defaults are one CPU, 512 MiB memory with no additional swap, 128 tasks, 256 file
descriptors and 128 MiB private scratch; per-project settings replace these
defaults. The project can be mounted writable or read-only; `.invariant` is
always overlaid read-only. No host home, authentication store or Docker socket is mounted.
Existing project symlinks, hard-linked regular files, sockets, devices and FIFOs
are rejected before mounting. Only one session can own a project at a time.

The project itself has no disk quota. In editable mode, project files can be
changed or deleted; use version control or backups when needed. In read-only
mode, project writes and deletion are denied by the mount. Container system files
and its private scratch are readable; unrelated *host* files are not exposed.
Model disclosure is a permitted output channel, not an information-flow proof.
The host model connection is operated by trusted Codex; this profile does not
proxy or audit individual model HTTP requests.

## Lifecycle and evidence

Trusted state lives at `~/.invariant/workspaces/PROJECT_HASH/`, outside the
project. Each session has a private JSONL journal recording its policy, model,
immutable image ID, executor connection, turn/tool completion, operator stop
and cleanup result. The saved chat ID and resume status link separate execution
sessions to the same conversation. The requested effort, confirmed thread effort
and per-turn effort are recorded as session metadata. Raw prompts, responses and authentication tokens are not
written to that journal. These are session lifecycle records, not the invoice
broker's per-request admission journal.

Stopping closes the Codex connection and removes exactly the owned executor
container, including its subprocesses and scratch. Shutdown failure is reported
and keeps recovery metadata. On the next launch, after obtaining the exclusive
project lock, a stale owned container is removed before a new session starts.
Conversation history is stored by native Codex in the private `codex/` home.
This includes prompts, responses and tool results. The `chats/CHAT_ID.json`
metadata and `latest-chat.json` pointer are atomically written with mode 0600;
the containing directories are mode 0700 and never mounted into the executor.
Chat metadata is scoped to the resolved project path, so copying or moving a
project does not import its chats. It is not stored in project version control.
Normal `run`/`resume`/`chats` commands do not delete saved conversations.
Domain-scoped network grants, system package installation, other agents
and a full terminal UI are outside this profile.

## Verification

```sh
python3 -m unittest discover -s scripts/tests -p test_easy_workflow.py -v
python3 scripts/test_easy_workflow_e2e.py --live --oom
python3 -m unittest discover -s scripts/tests -p test_configuration.py -v
python3 scripts/test_configuration_e2e.py
python3 scripts/test_internet_e2e.py
python3 scripts/test_internet_e2e.py --live gpt-6.1-sol --effort medium
python3 -m unittest discover -s scripts/tests -p test_workspace.py -v
python3 scripts/test_codex_workspace.py
python3 scripts/test_codex_workspace.py --live gpt-6.1-sol --effort high
python3 -m unittest discover -s scripts/tests -p test_resume.py -v
python3 scripts/test_codex_resume.py --live gpt-6.1-sol --effort medium
```

The internet check verifies real DNS/HTTPS package downloads, temporary and
persistent pip installs, network changes across launches, read-only projects and
offline defaults. `--live` uses two model turns to install a package through Codex
and import it again after resuming the same chat with networking disabled.

The configuration integration check exercises custom kernel limits, read-only
write denial, immutable active settings, pending changes and the next editable
launch without model turns.

The workspace integration check uses the real installed app-server and isolated executor
to exercise file edits, process execution, host-file/credential isolation,
network denial, policy immutability, protocol failure, and cleanup without model
charges. `--live MODEL_NAME` additionally runs a real authenticated two-turn
conversation to create and execute a Python file; it consumes Codex allowance.
Adding `--effort LEVEL` also checks the confirmed effort and its persistence
across both turns. Effort parsing, model capability validation, forwarding,
default preservation and fallback rejection are covered in `test_effort.py`.
The resume integration check uses three short model turns to remember a random
phrase across terminal hangup and forced launcher termination, execute commands
after recovery, and verify latest/explicit resume, EOF and empty-chat behavior.
It consumes Codex allowance.
The original `./scripts/invariant acceptance` separately verifies the invoice
profile and shared CLI regressions.

The adapter follows [Codex app-server](https://learn.chatgpt.com/docs/app-server)
and [authentication](https://learn.chatgpt.com/docs/auth). Executor configuration
is version-pinned against the official 0.163.0 implementation. Passing the
probes establishes evidence for this profile, rather than proof against every
container/runtime vulnerability or unintended action inside the project.
