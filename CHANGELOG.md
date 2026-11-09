# Changelog

## Reusable execution contexts

- Added a persistent private manager, restartable run references, revision-aware
  input synchronization and content-checked, deduplicated artifact exports.
- Share protected runtime binaries across projects; cache model metadata by
  authentication/version/TTL with a reusable metadata connection.
- Separate coding/full tool profiles from optional resource limits. Keep legacy
  saved profiles and existing continuation/evaluation/application behavior.
- Add credential-free spans and context accounting for performance investigation.

## Docker-owned agent state

- Added storage layout 2: containerized Codex/reviewer/catalog operations, private
  authenticated executor transport, named-volume settings/workspaces and a small host registry.
- Added storage inspection and verified, resumable migration of owned legacy data.
- Kept network/package installation enabled and optional quotas/deadlines disabled by default.
- Removed agent host-home/socket/device/privileged access independently of optional limits.
- Added frozen result exports, guarded automatic single-task delivery and host conflict recovery.
- Added automatic, idle-only Docker maintenance (30-day age threshold, hourly checks),
  configurable with `storage maintenance`. `storage cleanup` previews or runs the same
  reference-aware cleanup without expiring task results, logs or continuation state.


## Unreleased

- Make new persistent environments unrestricted by default, with optional
  restoration through `restrictions_enabled true`: no resource/capture/workload
  deadlines, privileged execution, host home and local Docker access, host
  networking and available devices, and no disabled agent-feature overrides.
  Preserve saved policies and frozen studies. Keep finite allocations, offline
  networking, restricted privileges and host-mount removal configurable.

- Enable command internet access by default in new persistent environments, so
  agents can install task dependencies without a manual network toggle. Preserve
  explicit offline settings and legacy profile defaults.

- Separate semantic review inputs from private integrity/accounting records;
  keep exact-plan bindings in code, use a stable minimal review profile and
  bounded frozen workspace facts and host-level blocking validation waits.
  Retain hash-bound executor command witnesses so compact evidence preserves
  operational proof as well as complete patches and test results.
- Evaluate frozen baselines directly, reuse prepared inputs and image-bound
  control runtimes, and expose a trusted worker validation tool. Durable receipts
  replace duplicate full checks only for the exact stopped candidate; red checks
  retain same-session repair and stale/unknown receipts cannot authorize delivery.
- Add a program-owned task runtime with bounded semantic review, evidence receipts
  and verified application, without an active model during preparation or waiting.
- Freeze child evaluation independently of its parent workspace and tools.
- Persist per-response usage observations and reconcile them with session totals.
- Support structured test reports, including red unittest subtest results.

- Separate frozen host-delivery policies from complete continuation snapshots.
- Add bounded task responses, coordinator waiting, review/apply and continuation
  with durable recovery and explicit concrete-plan digests.

- Add durable `invariant task run/status` coordination: ordered preparation,
  baseline checks and idempotent dispatch, with explicit recovery of uncertain
  setup and no automatic model-call replay.
- Separate retryable Debian acquisition from single offline installation;
  add typed `environment packages` and per-call preparation network overrides.
- Share exact resource contracts across Docker creation, verification and
  evidence; add `environment budget` and gated `compare` measurements.
- Route the INVARIANT skill through coordinated task submission. Correct empty
  Docker responses being classified as Windows and isolate discovery tests
  from real managed Codex installations.

## 0.3.1 — 2026-10-24

First registry release. Runtime logic and state formats are unchanged.

Registry delivery completed October 26, 2026: Python `invariant-workspace` 0.3.1
on PyPI, and `invariant-workspace-core` / `invariant-broker` 0.1.0 on crates.io.
The Rust packages use tag `rust-v0.1.0`, with updated installation documentation
and unchanged implementation. A fresh registry-only Rust 1.90 consumer and
PyPI installation passed; see [publication verification](docs/RUST_RELEASE_0.1.0.md).

- Publish the policy library as `invariant-workspace-core` because the crates.io
  name `invariant-core` belongs to an unrelated project. The Rust library/import
  name remains `invariant_core`; dependency aliases preserve broker/supervisor imports.
- Mark the internal supervisor as non-publishable.
- Automate GitHub Release builds and registry uploads with PyPI/crates.io trusted
  publishing, version checks, verified release assets and resumable uploads.
- Preserve the original `v0.3.0` tag and its verification evidence.

## 0.3.0 — 2026-10-24

Initial packaged open-source release preparation, building on the verified
parallel-experiment source milestone from October 21.

### Added

- Standalone documented Rust core/broker libraries with unchanged existing imports,
  independent archive/consumer checks and Linux/macOS Rust 1.90/stable CI.
- Guided project experiments, packaged Python/JavaScript examples, a real native
  Codex demo and interactive review/continuation/apply of saved results.
- Explicit dependency preparation without model turns and baseline test preflight.
- Allowlisted Markdown, JSON and CSV reports with separate retries and unknown values.
- Storage accounting, reference-aware retention, private image/state backups and
  recoverable same-path restore with previous-state retention.
- Safe diagnostics, host sign-in/status commands, community templates and compatibility policy.

### Fixed

- Installed legacy demos/build commands no longer require neighboring checkout files.
- Legacy workload image code has explicit readable modes after installation under a private host umask.
- User reports are written in the working project instead of the installed package.
- Deleting trial checkpoints preserves untagged parent images still needed by saved studies or prepared environments.
- Distribution checks build sdist → wheel and exercise the installed interface.

### Compatibility

- Existing experiment recipes, IDs, revision checks, retry/comparison semantics,
  reviewed apply and ordinary workspace/broker behavior are retained.
- Private SQLite schema remains 5. Report and private-backup formats are version 1.
- Codex remains pinned to 0.163.0. New native runs still use the signed-in account's
  real allowance; recovery never silently replays a model call.
- No portable backup relocation, hard disk quota, automatic merge, deterministic
  model behavior, public image push or PyPI publication is implied.

Full acceptance evidence is recorded separately for the exact tested source in
[release verification](docs/RELEASE_VERIFICATION.md).

## Container-internal execution pipeline

- Add digest-bound accepted-result inputs, frozen directly into a receiving
  study without a host export/import round trip.
- Keep one manager client connection per run context, return compact status,
  batch collection metadata, reuse verified export staging and support log cursors.
- Add opt-in validated completion with actual Codex turn interruption and
  reconciled usage; preserve the legacy final-message mode.
- Overlap baseline readiness evaluation with isolated agent preparation when
  resource budgets permit. No model turn can pass the readiness gate early.
- Publish accepted snapshots atomically and verify the stopped workspace without
  making another temporary copy. Full workspace/rootfs continuation is retained.
