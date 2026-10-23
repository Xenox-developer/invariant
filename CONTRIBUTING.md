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
cargo test --locked --workspace --doc
```

For Rust library changes, also run the independent package/consumer check:

```sh
python3 -B scripts/test_rust_library.py
```

It packages both crates from a source export, verifies their LICENSE/NOTICE and
README files, and runs Rust tests and an external embedding application against
the extracted archives. No Docker, Codex or model calls are needed. Add
`--toolchain 1.90.0 --offline` to use the installed minimum toolchain and cached
dependencies. See [the Rust libraries guide](docs/RUST_LIBRARY.md).

Run Docker integration gates serially on a disposable test host, with no ordinary legacy
broker session active. Fixtures scope cleanup to their own state. Never use a global
Docker prune or daemon restart as test cleanup. Allow several GiB of free disk and
at least two trial slots. Release verification can take tens of minutes.

```sh
# Local fixtures, without provider calls. Exit 2 means release evidence is incomplete.
invariant experiment acceptance
# Full local verification: authorizes fifteen small real turns, including guided/demo.
invariant experiment acceptance --live MODEL_NAME --effort medium
```

See [release verification](docs/experiments/ACCEPTANCE.md),
[the quickstart](docs/experiments/QUICKSTART.md),
[schema/migrations](docs/experiments/MIGRATIONS.md) and
[the assistant interface](docs/experiments/ASSISTANT_GUIDE.md).
Report a reproducible problem with sanitized study/attempt IDs, structured error codes,
platform identity and the relevant report. Review evidence for project content before
sharing it. Private workspace state and actual provider transcripts do not belong in Git.

The [Python workflow](.github/workflows/python.yml) checks Python 3.11–3.15 on
Linux/macOS, builds sdist → wheel and exercises an installation outside the
checkout. Run `python3 -B scripts/test_distribution.py` locally; `--docker`
additionally verifies the installed legacy demo against real containers.

Open-source release checks also cover dependency preparation, baseline preflight,
reports, backup/restore and retention. The full release profile's explicit live
option additionally runs the guided setup and demo checks; see acceptance docs
for the exact model-turn count. No provider calls run in ordinary CI fixtures.

For a contribution, explain the concrete problem and resulting workflow, include
appropriate regression evidence, and use the PR template. Additive JSON fields
are preferable to changing existing meanings. Keep frozen evidence immutable and
document migration/recovery for state changes. The [compatibility policy](docs/COMPATIBILITY.md)
and [security policy](SECURITY.md) apply. Use issues for small reproducible feature
proposals and private reporting for vulnerabilities. No response-time SLA is promised.

Maintainers: see [registry publishing](docs/PUBLISHING.md) for the first-upload
account setup, package names, release-asset verification and PyPI trusted publishing.
