# Run an experiment on your project without writing configuration

Inside your project, initialize INVARIANT once, then open guided setup:

```sh
invariant init
invariant experiment init
```

Answer the prompts for a coding task, a Linux test command, test files/folders to keep
fixed, a model, reasoning efforts and an experiment name. Defaults compare low, medium
and high effort on your account's default Codex model. The summary shows the generated
files and fixed tests. Choose whether to start the real Codex runs now. Setup alone
uses no Docker, authentication or model calls. Existing project files are preserved.

After running, a comparison shows each configuration's shared-test verdict, agent time,
reported token usage and attempt ID. Choose a result to inspect its diff, continue with
a new task, or review/apply. Enter leaves everything saved. Return at any time with:

```sh
invariant experiment list
invariant experiment review STUDY_ID
```

Closing a viewer leaves detached execution running. Use `logs STUDY_ID --follow` to
watch it, `stop STUDY_ID` to stop owned work, or `recover STUDY_ID` after a controller
crash. Failed/interrupted turns are never replayed automatically; retries are explicit.

## Use flags or an AI assistant

These flags create and run the same experiment without prompts or authored files:

```sh
invariant experiment init --name caching \
  --task "Implement caching while preserving existing behavior" \
  --test-command "npm test" \
  --test-path tests --test-path package.json \
  --model gpt-6.1-sol --efforts low medium high \
  --request-id caching-v1 --run --json
```

Replace the command and test paths with your project's actual ones. Quote paths with
spaces and repeat `--test-path` for more roots. Include test configuration/manifests if
they must stay fixed. For different models, replace `--model`/`--efforts` with repeated
`--variant MODEL EFFORT` pairs. `--max-parallel N` requests concurrency; available Docker
CPU/memory may reduce it. `--test-timeout 120` controls the shared command's deadline.

Omit `--run` to save setup only. A provided filename remains supported; without guided
flags, `init FILE --runner command|codex` still creates the existing editable starter.
All JSON operations return the usual versioned envelope and never prompt, including
on a terminal. Missing required inputs return a structured error without creating files.

Reusing identical flags and a setup request key reopens the same generated files. With
`--run`, it finds the same study without repeating completed turns. Different settings
with the same key conflict. Choose a new name/path and key for another experiment.
For a lost acknowledgement, repeat the same command/key; `data.study_id` identifies
the launched study. The setup key itself identifies the creation operation in `status`.

An assistant and a person can switch control at every step:

```sh
invariant experiment review STUDY_ID --json
invariant experiment select ATTEMPT_ID --if-revision REVISION --json
invariant experiment continue STUDY_ID --task "Add useful documentation" \
  --request-id caching-followup-1 --wait --json
invariant experiment select CHILD_ATTEMPT_ID --if-revision 0 --json
invariant experiment diff STUDY_ID --json
invariant experiment apply STUDY_ID --dry-run --json
invariant experiment apply --plan PARENT_PLAN_ID --json
invariant experiment apply CHILD_STUDY_ID --dry-run --json
invariant experiment apply --plan CHILD_PLAN_ID --json
```

Selection changes no project files. Continuation uses the selected files/environment
and a fresh conversation; the captured evaluator stays the same. The terminal review
asks for the next task directly. Apply uses concrete reviewed plans; for continued
results, apply ancestors before children. The terminal flow handles this ordering and
preserves conflicts. Piped output and JSON never select, continue or apply automatically.

## What is evaluated

The test command and selected test files are captured with the study. INVARIANT restores
those test roots in a separate evaluator copy of each candidate before running the
command inside Docker. Candidate edits and added files inside a fixed test directory
cannot replace that original suite. The saved candidate itself is preserved.

Exit 0 means pass; other ordinary exit codes mean fail. Missing executables (126/127),
timeouts and excessive output produce unknown quality. Output is limited to 1 MiB and
saved with the exit status/reason as evaluator artifacts. Test case counts are not
inferred from framework output. This is normal code evaluation, not an attestation
against adversarial candidate code or a modified test runtime.

Test-file selection is generated at setup (up to 250 files); contents are frozen when
the study is submitted. New test files require a new setup. Paths must be regular
project files/directories, without symlinks, overlapping roots or protected credentials.
Generated files are excluded from candidate workspaces and apply changes. You can
inspect/share them, but do not need to write or edit their JSON.

The architecture accepts any stack with Linux-compatible commands. Tools/dependencies
must exist in the project's prepared image or captured files. INVARIANT does not guess
a language or install packages during setup. If needed, prepare dependencies in an
ordinary `invariant run` session first, then exit it before capturing an experiment.
Project networking and resource limits continue to apply; see `invariant config` and
`invariant guardrails`. Prerequisites: running Docker, host Codex ChatGPT sign-in and
`invariant prepare --agent codex`. Unsupported model/effort settings fail without fallback.

Maintainers can verify the complete path with
`python3 -B scripts/test_experiments_guided.py --live MODEL`. This opt-in check uses
three initial Codex turns and one inline continuation, exercises manual/JSON handoffs
and reviewed parent/child apply, and records evidence under
`.invariant/experiment-guided-checks/`. The package-install gate also checks offline
generation from a wheel, without model calls.
