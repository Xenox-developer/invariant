# Compare three real Codex configurations

Run one calendar utility task with three reasoning efforts, evaluate every result with
the same 23 checks, then choose what to keep. The demo uses a separate project folder.
Each trial starts from the same frozen `solution.py` stub and shared Docker image.

After installing INVARIANT, make sure Docker is running and Codex is signed in with
ChatGPT on the host:

```sh
invariant setup
invariant prepare --agent codex
invariant experiment demo ~/invariant-codex-demo --run
```

The first run authorizes three real Codex turns using your account default model with
`low`, `medium` and `high` effort. A terminal then lets you choose a result, inspect its
diff, continue it, or review/apply changes. Nothing is selected or applied automatically.
Continuation authorizes one additional turn with the selected model/effort and saved
files/environment, in a fresh conversation. The same captured checks evaluate it again.
Apply writes only project files, after reviewing a destination-bound plan.

To choose a model or different supported efforts:

```sh
invariant experiment demo ~/another-codex-demo --model gpt-6.1-sol \
  --efforts low medium high --run
```

Unsupported settings fail before dispatch; no model or effort fallback is substituted.
Actual concurrency can be lower on a small Docker engine and is shown in the output.
No packages or command networking are needed. Python is this example's task language;
experiment runners remain language- and framework-independent.

## Inspect before spending model turns

Omit `--run` to create files without Docker, authentication or provider calls:

```sh
invariant experiment demo ~/invariant-codex-demo
```

The folder contains `solution.py`, `task.md`, `followup.md`, `compare.json`, `verify.py`,
`suite.json` and a guide. The task is sorting, validating and merging calendar intervals,
including touching intervals, negative endpoints, invalid input and input immutability.
The evaluator and case data are frozen separately from writable candidate code.
Individual checks have two-second deadlines; detailed outcomes are saved as a
`tests.json` evaluator artifact. Candidate code executes inside the trial environment.

The comparison shows test counts, observed agent runtime and reported tokens. Missing
quality/usage stays unknown. A completed model turn is not necessarily a correct solution.
All three configurations may pass; one task and one attempt are illustrative, not a
statistical ranking of models or efforts. No synthetic winner is inserted.

## Come back later or use an assistant

The initial request key is stable within this demo project. Rerunning the same command
reopens its saved results without replaying completed model turns. Ctrl+C closes only
the viewer; the detached study continues. An occupied non-demo directory is never replaced.

```sh
cd ~/invariant-codex-demo
invariant experiment demo . --run --json
invariant experiment status --request-id guided-codex-demo-v1 --json
invariant experiment results STUDY_ID
invariant experiment logs STUDY_ID --follow
invariant experiment select ATTEMPT_ID --if-revision REVISION --json
invariant experiment diff STUDY_ID
```

`--json` and piped input never prompt or select/apply a result. An assistant can use
these same IDs, revisions and commands, and hand control back to a person at any point.

```sh
invariant experiment continue STUDY_ID --prompt-file followup.md --wait --json
invariant experiment select CHILD_ATTEMPT_ID --if-revision 0
invariant experiment apply STUDY_ID --dry-run
invariant experiment apply --plan PARENT_PLAN_ID
invariant experiment apply CHILD_STUDY_ID --dry-run
invariant experiment apply --plan CHILD_PLAN_ID
```

The follow-up asks for useful documentation/examples while preserving behavior. Edit it
for your own next task. Child diffs are relative to their parents: apply an ancestor's
reviewed plan before its child. The guided terminal flow handles this ordering, skips
ancestors whose changed files already match, and asks about each concrete plan.
Existing conflicting edits are preserved; the demo never silently merges them.

Use `stop STUDY_ID` to stop owned work, `recover STUDY_ID` after a controller crash,
and explicit `retry TRIAL_ID` for an interrupted/failed attempt. The comparison continues
to use first attempts. To change frozen settings or run a new experiment, edit the
recipe and use ordinary `run compare.json --request-id A_NEW_KEY`. Preview cleanup with
`delete STUDY_ID`; clear selections and remove dependent child studies before parents.

See [the full interface](README.md) and [assistant guide](ASSISTANT_GUIDE.md).

## Verify the complete example

Maintainers can run `python3 -B scripts/test_experiments_demo.py --live MODEL`.
This opt-in check uses four real turns: three initial efforts and one continuation.
It verifies shared evaluation, overlapping execution, reopening, selection and reviewed
parent/child apply. It then restores the generated stub and clears selections, retaining
the results for a person to choose. Use `--project /absolute/new/demo` to choose that
new folder. A private evidence report is saved under `.invariant/experiment-demo-checks/`.
