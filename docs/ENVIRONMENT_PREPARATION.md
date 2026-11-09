# Prepare a project without model calls

For an assistant submitting a coding task, use the
[durable task interface](TASK_AUTOMATION.md) to coordinate preparation, checks
and dispatch. For standalone setup, the interfaces below remain available.

After `invariant init`, check host tools and build the shared Linux image:

```sh
invariant setup
invariant prepare --agent codex
```

Run an explicit preparation command inside this project's persistent environment:

```sh
invariant environment prepare --command "apt-get update && apt-get install -y nodejs npm"
invariant environment prepare --command "npm ci" --timeout 600
```

New environments enable internet access by default. Existing projects retain
their saved policy. For an offline project, per-call `--allow-network` permits
downloads during preparation while leaving the saved policy untouched.
The current checkout also supports typed `environment packages --package NAME`
with acquisition-only retries. Prefer those for new automation; see
[task automation](TASK_AUTOMATION.md#safe-acquisition-retries).

Use your project's actual Linux-compatible preparation commands. Set
`command_network none` to explicitly disable networking for future sessions.
The command has
administrator access inside Docker and can write its volume-backed project
copy. It has no host home, Docker socket, device passthrough or privileged mode.
Preparation uses any explicitly configured resource and duration limits; new
environments impose none. It makes no model
call or conversation. A timeout override cannot exceed the configured duration.

Installed tools, system packages and home settings are checkpointed after the
container stops, including after a failed command. Project-file changes remain
in the Docker project copy; preparation is an explicit shell operation, not a transaction.
Review the exit status and private log. Partial installs are not retried.
After an interrupted host process, use `invariant environment recover` before
explicitly issuing another command. Recovery never replays that command.

## Test readiness before model calls

```sh
invariant experiment preflight experiments/comparison/compare.json --json
```

This validates the configured native account/model/effort without starting a
model turn, then runs the captured evaluator against a private, untouched
baseline in Docker. The original project's files and chosen result do not change.
The saved preflight study has a single baseline configuration and one attempt
per case; inspect its logs and results like any other study.

`ready: true` means the evaluation ran, not that the original implementation
already passes its tests. Red baseline tests can be exactly what the task should
fix. A missing test command, timeout, output limit, malformed evaluator or failed
runner blocks readiness. For custom evaluators, an intentional unknown quality
verdict can still be a successfully completed evaluation.

Guided `experiment init ... --run` performs this check automatically before a
new native submission. Reopening a completed keyed submission does not repeat
model turns or the preflight. Use a new request key to check a changed setup.
Advanced `experiment run FILE` retains its existing semantics; run preflight
explicitly when adopting an evaluator. Recipes without an evaluator need one
before preflight can assess readiness.

Preparation and preflight honor the same project locks and recovery requirements
as ordinary workspaces and experiments. They use the saved project network policy
and do not infer package managers. See [guided setup](experiments/GUIDED_SETUP.md).
