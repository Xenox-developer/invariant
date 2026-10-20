# A first comparison

Clone this repository, enter it and install the local package:

```sh
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -e .
```

Docker must be installed and running. Native trials also need Codex CLI and existing
host ChatGPT sign-in; the command example below makes no model calls. The base image
contains general Linux tools. INVARIANT does not require a particular agent framework.

Create a new project directory outside this checkout. Copy `compare.json`, `case.json`,
`agent.py`, `judge.py` and `quickstart.sh` from `examples/experiments/` into it. Then run:

```sh
sh quickstart.sh
```

This exact script is exercised by the release gate from an isolated wheel installation.
It initializes the project, prepares the shared Docker image, compares two multipliers,
shows one correct and one incorrect result, selects the correct result and explicitly
applies its reviewed changes. It repeats with the manual and JSON clients reversed.
The final `result.txt` contains `42`. Run in a fresh directory: request IDs and starter
paths are intentionally stable, so rerunning in the same directory is not a new experiment.

For a real agent harness, replace `runner.argv`. It receives JSON file paths through
`INVARIANT_VARIANT_FILE` and `INVARIANT_CASE_FILE`; outputs belong in the private workspace.
The evaluator sees a separate copy after the runner stops. Exit zero is execution success;
an evaluator's `passed` and metrics establish quality. Keep evaluator output to one JSON
object with finite numbers. Declare its source files explicitly.

For native Codex, use `invariant experiment init experiments/codex.json --runner codex`.
Edit each variant's model/effort and the captured prompt, then validate and run the file.
Omitted model/effort settings resolve to available account defaults at capture time.
You can compare any supported settings; an unavailable setting fails before dispatch.

Need installed tools in the baseline? Open an ordinary `invariant run`, ask it to install
the tools with project networking enabled if needed, and exit cleanly. After its saved
environment is complete and its project lock is released, create the study. Each trial
starts from that pinned image; further installs stay private and persist in its result.
The requested parallelism can be reduced by available CPU/memory; status explains the
effective allocation. No hard per-trial disk quota is promised.

Use `logs ID --follow` to watch, reconnect with its saved `--after` cursor, `stop ID` to
cancel and `recover ID` after a crash. Recovery does not retry a model call. Use
`retry TRIAL_ID` explicitly; first-attempt comparisons continue to show the original failure.
Use `continue ID --prompt-file FILE` to create a child from the saved tools/files with
a fresh conversation. Review `diff ID`, then `apply ID --dry-run` and apply the returned
plan explicitly. Conflicts preserve existing work; interrupted apply rolls back or reports
a blocked recovery. `delete ID` previews storage cleanup; `delete ID --confirm` refuses
active, selected or referenced state.

Host credentials are not copied into captured source or trials. External service versions,
mutable data and model behavior must be recorded as external dependencies; a frozen
environment does not make remote APIs or LLM responses deterministic. See
[the full interface](README.md) for networking, secret references and limits.
