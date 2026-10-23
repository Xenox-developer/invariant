# Storage, private backups and recovery

## Inspect and clean up

```sh
invariant experiment storage
invariant experiment storage --json
invariant experiment cleanup --older-than-days 30
invariant experiment cleanup --older-than-days 30 --confirm
```

Storage reports distinguish per-study files, shared frozen baselines, total
project private state and Docker image logical sizes. Docker layers are shared;
adding image logical sizes does not measure physical space or reclaimable bytes.
Docker daemon free space remains unknown. Hard disk quotas are not provided.

Cleanup previews eligible unselected terminal studies. `--confirm` rechecks each
study before deleting it. Active, selected, referenced and unrecovered work is
retained. Children are considered before parents. Each deletion uses the existing
ownership and reference checks; there is no global Docker prune. Inspect returned
`skipped` and `errors` instead of assuming everything was removed.

## Back up stopped experiments and their images

```sh
invariant experiment backup --output ~/invariant-study-backup.tar.gz
```

Finish or stop/recover every attempt first, and exit ordinary sessions. The
destination must be a new file outside the project and its live private state.
The command takes project/controller/mutation leases, makes a consistent SQLite
backup and saves the exact referenced Docker images by ID. The private archive
contains captured inputs, workspaces, results, selections, lineage and apply
records. It can contain proprietary code or workload-generated secrets. It has
mode 0600, is not encrypted and is not a report to upload to an issue.

Host authentication, native Codex conversation homes, credential-file references
and regenerable runtime mounts are omitted. Frozen files and installed tools are
preserved; continuing a result uses a fresh conversation, as it normally does.
Supply secret references again before retry/continuation if required. The archive
does not back up the current original project, ordinary chat history or the whole
host. Keep your project files/configuration in a separate project backup or Git.

## Restore

Restore your own intact archive at the **same absolute project and private state
paths**, with matching project configuration and a compatible Linux Docker engine:

```sh
invariant experiment restore ~/invariant-study-backup.tar.gz
# If there is existing saved experiment state:
invariant experiment restore ~/invariant-study-backup.tar.gz --replace
```

Checksums, archive paths/types, database schema/identity, stopped attempts and
image identities are checked before publishing restored state. Both classic
Docker config IDs and containerd/OCI manifest IDs are supported. Images are
loaded without mutable tags; identity is verified again afterward. Cross-machine
relocation or conversion between Docker image stores is not promised.

`--replace` retains the previous experiment directory in a private
`restore-before-...` sibling. It never restores over project source files, starts
workloads or replays a model turn. Storage reports list retained restore copies;
ordinary retention cleanup leaves them alone. Preserve your archive before
removing an obsolete previous copy with your file manager. Recreate/review apply
plans after restoration rather than relying on an old destination preview.

An interrupted directory swap leaves a durable journal. Normal experiment
commands block until you reconcile it:

```sh
invariant experiment restore --recover
```

Recovery retains a published state or rolls back to the old copy. If an external
writer makes the state ambiguous, it preserves all copies and reports a blocked
recovery. This is local integrity verification, not a signed authenticity claim;
restore only private archives you trust. A failed load may leave untagged image
data in Docker, while the live experiment state is kept.

## Support diagnostics

```sh
invariant --version
invariant doctor --json
invariant doctor --output diagnostics.json
invariant login --check --json
```

Doctor is read-only and can run outside a project. It reports supported host,
tool readiness, versions and project recovery codes. It omits private paths,
environment values, source, prompts, auth and raw logs. Account status checks do
not start sign-in; `invariant login` explicitly starts host Codex ChatGPT sign-in.
Native preflight separately verifies the required account and model settings.

For errors, include the diagnostic report, command, stable error code and a small
reproduction. Inspect any separately attached logs or detailed exports first.
See [Security](../SECURITY.md) for private vulnerability reporting.
