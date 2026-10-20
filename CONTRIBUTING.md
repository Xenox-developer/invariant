# Contributing

INVARIANT source is Apache-2.0. Contributions use the repository license; preserve
third-party notices. Discuss interface/schema changes with an example workflow and
include a migration/recovery plan for durable state changes.

Use Python 3.11+, Rust 1.90+ and a local Linux Docker engine. macOS uses Docker Desktop.
Native Codex tests additionally require the Codex CLI and existing host ChatGPT sign-in.
Do not put account credentials in fixtures, recipes, logs or CI artifacts.

```sh
python3 -m pip install -e .
python3 -B -m unittest discover -s scripts/tests -q
PYTHONPATH=sdk/python python3 -B -m unittest discover -s sdk/python/tests -q
cargo fmt --all -- --check
cargo clippy --locked --workspace --all-targets -- -D warnings
cargo test --locked --workspace --all-targets
```

Run Docker integration gates serially on a disposable test host, with no ordinary legacy
broker session active. Fixtures scope cleanup to their own state. Never use a global
Docker prune or daemon restart as test cleanup. Allow several GiB of free disk and
at least two trial slots. Release verification can take tens of minutes.

```sh
# Local fixtures, without provider calls. Exit 2 means release evidence is incomplete.
invariant experiment acceptance
# Full local verification: authorizes seven small real turns (two variants,
# two parent/child turns and three ordinary-chat resume turns).
invariant experiment acceptance --live MODEL_NAME --effort medium
```

See [release verification](docs/experiments/ACCEPTANCE.md),
[the quickstart](docs/experiments/QUICKSTART.md),
[schema/migrations](docs/experiments/MIGRATIONS.md) and
[the assistant interface](docs/experiments/ASSISTANT_GUIDE.md).
Report a reproducible problem with sanitized study/attempt IDs, structured error codes,
platform identity and the relevant report. Review evidence for project content before
sharing it. Private workspace state and actual provider transcripts do not belong in Git.
