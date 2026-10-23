# Compatibility policy

INVARIANT 0.3.x is beta. Changes are recorded in [CHANGELOG](../CHANGELOG.md).
Patch releases retain documented behavior. Incompatible documented interfaces
require a minor version increase before 1.0, migration notes and explicit failure
for unsupported state. This is not a promise of binary ABI or remote reproducibility.

## CLI and JSON

The documented CLI is the supported automation interface. Experiment `--json`
uses version-1 `{schema_version, ok, data, error}` envelopes, never prompts and
shares state/IDs with readable commands. Additive fields and new error codes
may appear; clients should ignore unknown fields and preserve error recovery
information. Breaking envelope meanings need a schema-version change.

`ok: true` means the operation completed, not that candidate quality passed.
Inspect `passed`, readiness, cleanup and attempt state. Missing measurements are
unknown, not zero. Trials retain explicit retries; comparison uses first attempts.
`--wait`/viewer exit does not cancel detached execution. Apply requires a reviewed
current plan; selection uses revisions. Existing command/native recipes keep
their behavior. Guided submission adds a baseline evaluator check before new
model trials; ordinary `run FILE` uses explicit `preflight FILE` when desired.

## Durable state and exports

Recipes use schema 1; private SQLite stores use schema 5. Known versions 1–4
migrate transactionally on write. Unknown schemas fail explicitly. Frozen inputs
and completed evidence are not rewritten to claim newer provenance. Restore and
apply journals block incompatible mutations until recovery is resolved.

Comparison report JSON is schema 1, kind `invariant-comparison`. Private backup
archives use schema 1, kind `invariant-private-backup`; they are same-path local
archives with exact images, not portable reports. Raw SQLite, private paths,
Python module internals and native transcripts are not a public interchange format.
See [migrations](experiments/MIGRATIONS.md) and [backups](STORAGE_AND_RECOVERY.md).

Historical acceptance bundles remain verifiable under their recorded profile;
they establish acceptance only for their exact source. New release profiles may
require additional checks without treating older profiles as evidence for a
new release. Evidence hashes provide integrity, not third-party attestation.

## Codex and runtime

The adapter is pinned to Codex CLI 0.163.0, including its accepted prereleases.
This is intentional protocol compatibility, not a claim that any newer CLI works.
Setup can maintain a private supported copy alongside an incompatible global CLI.

A Codex update needs adapter/schema review, catalog/account/error/usage tests,
Docker image rebuilds, regression checks and small real-provider experiments.
There is no automatic model fallback, trial replay or background update of a
frozen image. Existing trials/continuations require their exact saved images.
Provider availability and account catalogs can change independently of INVARIANT.

Rust crate root imports, signatures, wire/audit values and current grant limits
were preserved during library extraction. Each crate remains in the 0.1.x series;
its incompatible public changes require a minor version and migration notes.
