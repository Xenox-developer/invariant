# Docker state storage (layout 2)

The host runs the INVARIANT interface and Docker client. Codex, its Code Mode
companion, the reviewer, model-catalog queries, task coordinators and execution
environments run in Docker. No managed host Codex installation is required.

The trusted manager alone receives the Docker socket. It mounts named volumes
for state, project inputs and immutable coordinator code. Agent runtimes receive
only their private Codex home and read-only runtime code. Executors receive their
working copy, protected inputs/control and Docker scratch storage. Neither receives
the host home, socket, device passthrough, privileged mode or a published port.
Historical Docker Desktop mount hints are cleared before creating containers;
the actual mounts are checked before a model can use an executor.

Codex connects to a separate executor over an authenticated WebSocket on an
internal Docker network. The capability is stored in Docker and sent in the
Authorization header, never as a plaintext command argument. Validation can pause
the executor without pausing the agent's connection to its coordinator. Internet
access uses an additional bridge interface. Optional quotas and workload deadlines
are disabled by default; disabling restrictions never grants host access.

## Storage and authentication

- `invariant-state-v2`: settings, private snapshots, databases, Codex homes, chats,
  account state, journals and migration recovery data.
- `invariant-project-PROJECT_HASH-v2`: the project working tree inside Docker.
- `invariant-code-v2-DIGEST`: immutable runtime source for a manager version.
- Images: Linux packages, installed system tools and environment checkpoints.
- Host `~/.invariant/registry.json`: layout version, project IDs, volume pointers
  and minimal migration phases. The adjacent lock coordinates registry changes.
- Project `.invariant/results/` and `.invariant/logs/`: exported artifacts.

Logical absolute paths retained in historical records refer to paths *inside*
the manager. API workspace locations use `workspace_ref` containing a volume name
and an internal path. Other private locations use `docker-volume://` references.
Project identity is represented by `project_id` and `project_ref`; local result/log
destinations are returned only after export.

An existing file-based ChatGPT sign-in is copied once into Docker. The original
personal auth/configuration files are not modified. Subsequent login and token
refresh state is stored by Codex in its private volume and synchronized to the
Docker account store. Credentials are not built into images or exported artifacts.
Without an existing sign-in, `invariant login` uses the container's device login.

## Results and source changes

Project inputs are streamed into Docker; caches, dependencies and internal state
are excluded. A three-way source manifest prevents a later import from silently
overwriting divergent Docker work.

Task acceptance and the semantic review bind an immutable candidate to its frozen
delivery rules. Dependencies, virtual environments, caches and agent state cannot
be exported by broadening worker ignore rules. Exports contain selected result
files and a manifest, rather than the entire working directory.

Single `task execute` jobs apply by default after passing configured checks and
the existing semantic review; use `--no-apply` to retain an exported result only.
Comparisons export each successful variant independently. They never select or
apply a winner automatically. Failed/unknown checks do not authorize application.
Ordinary chats export their changes without claiming acceptance or auto-applying.

Before host writes, INVARIANT re-verifies the saved result and checks original
source entries, protected tests and directory contents against the reviewed plan.
It journals each write in Docker and verifies delivered files. External edits
cause a conflict report and leave the candidate in Docker. Recovery checks intent,
before/after content and external edits; it never replays a model request.

## Migration and inspection

```sh
invariant storage status --json
invariant storage migrate --all
```

Migration requires inactive legacy jobs. It copies owned state, checks file hashes,
file/directory modes and symlink targets, verifies the installed copy and records
the volume pointer before deleting host copies. Linux symlink permissions are
fixed by the platform, so target identity rather than macOS symlink mode is checked.
The verified staging copy remains until host deletion finishes. An interrupted
deletion verifies remaining file content against that copy before continuing.
Afterward, redundant staging may be released only after rechecking the installed
Docker copy; the migration journal and installed state remain available. No model calls are part of migration.

All private legacy workspaces and INVARIANT-managed tools are migrated. Saved
project configuration and recognized runtime/dependency directories in historical
INVARIANT evidence folders are migrated too. Source projects, historical reports
and logs remain on the host; personal Codex installations/configuration outside
INVARIANT ownership are left alone. Historical run records retain their IDs and
content. New continuations use layout 2 even when their parent was created earlier.

`storage status` reports registered volumes, remaining legacy host state and actual
mounts of active agents/executors. Docker itself, its engine storage and the
installed INVARIANT interface remain prerequisites on the host. This is a local
Docker storage boundary, not a multi-tenant or adversarial-evaluator attestation.

## Automatic maintenance

Every trusted manager runs a maintenance scheduler inside Docker. It checks once
an hour and, when no manager operation or executor is active, expires disposable
objects older than 30 days. Managers restart with Docker unless explicitly stopped;
no host scheduler, installation or cache is added. Multiple managers share a
volume lock and last-run journal, so they do not clean concurrently.

Maintenance removes only stopped owned agents, stopped executors with a verified
saved checkpoint of that exact session, untagged owned images with no durable
references, unused old runtime-code volumes, and old npm/pip download-cache files
in mutable agent homes. Referenced checkpoints, tagged images, project/state
volumes, installed packages, virtual environments, workspaces, chats, authentication,
results, logs and recovery/migration data have no automatic expiry. Immutable
snapshots are never rewritten. If metadata cannot be read, cleanup fails closed.
A shared Docker build cache is not pruned: it may belong to other projects.

```sh
invariant storage maintenance --json
invariant storage maintenance --older-than-days 30 --interval-hours 1
invariant storage maintenance --enabled false
invariant storage maintenance --enabled true
invariant storage cleanup --json                 # preview, no deletion
invariant storage cleanup --confirm --json       # run now, with the same guards
```

Policy, maintenance history and journals live in the Docker state volume. A busy
run defers cleanup until a later check. An unfinished migration also blocks it.
`experiment cleanup --confirm` is a separate, explicit deletion of experiment
history and continuation data; automatic maintenance never calls that command.
