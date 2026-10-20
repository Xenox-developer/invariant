# Schema and durable-state compatibility

Recipes and JSON envelopes use version **1**. The shareable recipe shape is published in
[schema-v1.json](schema-v1.json); the runtime validator additionally enforces unique IDs,
finite JSON, bounded expansion, path/link rules, model catalog compatibility and available
resources. A JSON Schema validator alone cannot validate the runtime environment.

The private SQLite store currently uses schema **5** (`PRAGMA user_version`):

| Version | Addition |
|---|---|
| 1 | Studies, captured baselines, trials, attempts, request IDs and events |
| 2 | Durable attempt evidence records |
| 3 | Detached dispatch, stop/recovery/retry and operation records |
| 4 | Selection revision/history and additional operation metadata |
| 5 | Immutable child lineage and durable apply-plan references |

Read paths accept known versions; the next write migrates known older stores under the
database lease. Frozen definitions and completed attempt evidence are never rewritten to
pretend they were produced by a newer version. Unknown versions fail with
`UNSUPPORTED_STATE_SCHEMA`; unsafe state permissions fail before SQLite opens it.

Before upgrading, finish or stop/recover active work, preserve the whole private project
state directory and its referenced images, and keep the previous INVARIANT version.
Never manually change `user_version`, remove an apply journal or edit a saved manifest.
Private state is outside the project under `~/.invariant/workspaces/`; copying only a recipe
or a database does not copy its workspaces/images. There is no promised portable export
format yet. Downgrading a migrated database is unsupported; restore a consistent backup.

New schema changes must retain explicit failure/recovery behavior, tests for the previous
version and corruption handling, and a clear migration note. The M6 gate tests M1-style
store migration and apply-journal corruption/crash handling.
