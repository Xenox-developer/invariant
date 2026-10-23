# Prepare a project without model calls

After `invariant init`, check host tools and build the shared Linux image:

```sh
invariant setup
invariant prepare --agent codex
```

Run an explicit preparation command inside this project's persistent environment:

```sh
invariant config set command_network internet
invariant environment prepare --command "apt-get update && apt-get install -y nodejs npm"
invariant environment prepare --command "npm ci" --timeout 600
invariant config set command_network none
```

Use your project's actual Linux-compatible preparation commands. Network opt-in
is explicit and remains a project setting until you change it. The command has
administrator access inside Docker and can write this project, as an ordinary
workspace session can. It has no host home, credentials or Docker socket.
Preparation uses the configured resource and duration limits, and makes no model
call or conversation. A timeout override cannot exceed the configured duration.

Installed tools, system packages and home settings are checkpointed after the
container stops, including after a failed command. Project-file changes remain
in the project; preparation is an explicit shell operation, not a transaction.
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
as ordinary workspaces and experiments. They do not infer package managers or
enable networking automatically. See [guided setup](experiments/GUIDED_SETUP.md).
