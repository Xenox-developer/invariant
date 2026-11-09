---
name: invariant
description: "Use INVARIANT to prepare persistent Linux environments, run coding tasks or compare agent configurations, inspect saved results, and apply reviewed changes. Use when the user requests INVARIANT or this isolated execution workflow."
---

# INVARIANT

Use the installed CLI in the **target project**, responding in the user's language.
Check `invariant task --help` once. This workflow needs `task execute`; report a
required update if missing. Top-level `run`/`resume` are interactive user chats.

For a coding task, inspect the requested behavior, dependencies and real fixed
tests, then use [the job protocol](references/experiments.md). If no acceptance
checks exist, agree on them before execution; never substitute a no-op.

- Prefer **one `task execute --input JOB.json --request-id KEY --json`**. Add
  `--apply` when implementation/application is authorized. The coordinator owns
  setup, validation, baseline preflight, dispatch, waiting, evidence, semantic
  review and guarded application. It uses one worker and a bounded reviewer;
  no model decides polling, hashes, container flags or metadata bookkeeping.
- The worker uses `invariant_validate` for the full fixed acceptance suite.
  The coordinator runs it in a disposable evaluator, durably freezes the
  candidate, and reuses its bound receipt. Focused development checks are fine;
  avoid asking the worker to run the same full suite separately. After a passing
  validation it sends its final message; further changes invalidate acceptance.
- Workers receive a bounded frozen file/tool inventory and keep the model's
  supported tool transport; `invariant_validate` waits for its final result.
  The independent reviewer receives complete requirements/patches, verified
  check scope and executor command witnesses in a minimal profile. Preserve
  operational proof when compacting evidence. Integrity, accounting and
  exact-plan binding stay in code.
- Default worker effort is **medium**; omit model for saved/account default.
  Explicit model/settings win without fallback. The reviewer uses the selected
  worker model/effort and its usage is included. A comparison request alone does
  not authorize application or automatic winner selection.
- When the shell exposes a running process, await that process. A tool host can
  await `Coordinator.execute` directly to suspend its own model loop. Avoid full
  status/log polling, unchanged-state commentary and reading private state to
  reconstruct evidence already present in execution/application receipts.
- Follow-ups inherit the complete saved workspace/tools, with a fresh worker
  conversation. Specify a **new frozen evaluation** when acceptance criteria
  change. Parent-before-child application and host conflict guards remain active.
- A large/binary diff or conflicts returns to explicit review. Inspect complete
  required content before approving its exact digest; never bypass this gate.

Snapshots and delivery are separate. Baseline `.gitignore` rules exclude caches
from host application while preserving them for continuation. Explicit delivery
rules handle intended generated outputs. Worker ignore edits cannot expand scope.

Read [environment preparation](references/environments.md) only for setup,
configuration or environment recovery. Preparation may change files; temporary
network access must fit the task and preserves saved policy. Existing user
application authorization carries forward without extra confirmation steps.

Report IDs, evaluation scope/verdict, application verification and full available
usage. Counts come from structured evidence, never guessed from exit zero.
Missing usage/verdict stays unknown. Per-response observations must reconcile with
session totals; cached input and reasoning output are subsets, not extra tokens.
