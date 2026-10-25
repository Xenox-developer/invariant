# Registry publishing

[Publish release packages](../.github/workflows/publish.yml) runs when a GitHub
Release is **published**, including a prerelease. Drafts do not upload packages.
The workflow builds from the release tag, verifies the distributions, attaches
packages and SHA-256 evidence to the release, then uploads new versions.
There is no manual package-building or file-upload step.

## Published versions

| Package | First registry version | Source tag |
|---|---|---|
| `invariant-workspace` | [PyPI 0.3.1](https://pypi.org/project/invariant-workspace/0.3.1/) | `v0.3.1` |
| `invariant-workspace-core` | [crates.io 0.1.0](https://crates.io/crates/invariant-workspace-core/0.1.0) | `rust-v0.1.0` |
| `invariant-broker` | [crates.io 0.1.0](https://crates.io/crates/invariant-broker/0.1.0) | `rust-v0.1.0` |

The Python application and Rust libraries have separate versions. The core's
registry package is `invariant-workspace-core`; its import remains `invariant_core`.
The internal supervisor is never published. Historical release tags/assets remain
unchanged; see [first Rust publication evidence](RUST_RELEASE_0.1.0.md).

## One-time registry configuration

Both registries use GitHub OIDC trusted publishing. No permanent registry token
is stored in GitHub secrets. Publishing jobs use environments with the exact
names below, and have the identity-token permission only for those jobs.

### PyPI — already configured

The existing publisher remains valid because the workflow filename and environment
are unchanged. In the project's publishing settings, its identity is:

| Field | Value |
|---|---|
| Project | `invariant-workspace` |
| Repository owner | `Xenox-developer` |
| Repository | `invariant` |
| Workflow filename | `publish.yml` |
| Environment | `pypi` |

### crates.io — both crates configured

Both publishers were configured and verified on October 26, 2026. For a new
crate or repository, use **Settings → Trusted Publishing** with the same fields:

- [invariant-workspace-core settings](https://crates.io/crates/invariant-workspace-core/settings)
- [invariant-broker settings](https://crates.io/crates/invariant-broker/settings)

| Field | Value |
|---|---|
| Repository owner | `Xenox-developer` |
| Repository name | `invariant` |
| Workflow filename | `publish.yml` |
| Environment | `crates-io` |

This one-time owner setting is now complete for both packages. New publish
tokens are short-lived and are revoked by the official Rust authentication
action when its job finishes. The local first-publication token is not used
by GitHub Actions.

## Publish a new version

1. Update `pyproject.toml` and `invariant/__init__.py` for a new Python version.
   For a Rust release, update `[workspace.package].version` in `Cargo.toml`, the
   broker's `invariant-core` dependency version and the workspace lockfile.
   Bump the Rust version whenever the Rust libraries change; existing registry
   versions are skipped, not replaced.
2. Run the documented application/library checks, update the changelog, commit
   and push. The release commit must include the new `publish.yml` workflow.
3. Create and publish a GitHub Release using one of these tag formats:

| Tag | Packages considered |
|---|---|
| `vMAJOR.MINOR.PATCH` | Matching Python version and any new Rust workspace version |
| `rust-vMAJOR.MINOR.PATCH` | Matching Rust workspace version, core then broker; no Python upload |

For example, publishing `v0.3.2` requires Python version `0.3.2`. Rust can stay at
`0.1.0` if unchanged: it will be reported as already published and skipped.
Versions such as `0.x.y` can still be marked as a GitHub prerelease; the workflow
handles both ordinary releases and prereleases. Tags currently use three numeric
components; other version/tag formats fail validation before uploading anything.

## Checks and delivery

The unprivileged preparation job validates the tag, CLI version and broker/core
version alignment. Registry errors other than an explicit 404 stop the run.
It checks standalone Rust packages and an independent consumer on Rust 1.90, or
builds a Python sdist → wheel and exercises the installed CLI outside a checkout.
Python descriptions also pass strict Twine checks. Only packages needing upload
are built during normal runs.

The assets job attaches the checked archives, `SHA256SUMS` and
`workflow-publication.json` before uploading to registries. A separate Rust job
uses `rust-lang/crates-io-auth-action`; core is uploaded and indexed before
broker. Cargo repackaging must reproduce the verified archive hash before an
upload. The Python job uses `pypa/gh-action-pypi-publish` and publishes the checked
wheel/sdist with provenance attestations. The registries publish independently;
a partial failure can be resumed without uploading successful versions again.

Preparation has no publishing credentials. Only the assets job can write GitHub
release files. Registry jobs have OIDC permissions; verification never executes
with a permanent registry token.

## Verify setup without publishing

After the one-time crates.io configuration, run from `main`:

```sh
gh workflow run publish.yml --ref main -f tag=v0.3.1 -F dry_run=true
```

This builds and validates the selected packages and checks both registries'
OIDC authentication. It does not attach assets or upload package versions.
For a Rust-only check use `-f tag=rust-v0.1.0`. A dry run is the default when
starting the workflow manually. Check the job summary for selected versions and
already-published packages.

## Verified setup

On October 26, 2026 the [GitHub dry run](https://github.com/Xenox-developer/invariant/actions/runs/40242604882)
passed preparation and both registry authentication jobs after the crates.io
publishers were configured. The assets job was skipped and no packages were
uploaded. The Python CI matrix also passed for the workflow implementation.

Local verification passed 288 application/maintainer tests (including 17 release
regressions), 11 SDK tests, actionlint and both release-tag build modes. A separate
local-only fixture changed Python to 0.3.2 and Rust to 0.1.1: all four distributions
built and the installed CLI reported the new fixture version. Those fixture
versions were not uploaded. Cargo's separate repackaging reproduced the checked
archive hashes. Tests cover partial-upload recovery, mismatched hashes, moved
commits, unsafe manifest paths, registry errors and preservation of release files.

## Retry a failed publication

Use **Re-run failed jobs** on the original Actions run. A manual recovery can also
use the same release tag with `-F dry_run=false`. Previously checked assets are
reused only when the recorded commit and every SHA-256 value match. A moved tag
or conflicting release asset fails instead of overwriting files.

For a partial PyPI upload, existing filenames must match their checked hashes
before the action is allowed to skip them and upload the remaining file. Rust
uploads likewise compare registry hashes if a version appeared during a retry.
Published versions skipped during planning still require the maintainer to bump
the version when code changes. Never reuse a version for different code.

The original `v0.3.1` assets are historical preparation evidence, with a different
manifest format. They are preserved; new releases use the automatic manifest.

Official references: [GitHub release events](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#release),
[PyPI trusted publishing](https://docs.pypi.org/trusted-publishers/using-a-publisher/),
[crates.io trusted publishing](https://crates.io/docs/trusted-publishing).
