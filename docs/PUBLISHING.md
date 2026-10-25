# Registry publishing

The Python application and Rust libraries have separate versions:
`invariant-workspace` 0.3.1, `invariant-workspace-core` 0.1.0 and
`invariant-broker` 0.1.0. The crates.io name `invariant-core` belongs to an
unrelated project. Our library keeps `invariant_core` as its Rust import name;
use `package = "invariant-workspace-core"` with the `invariant-core` dependency alias.
The internal `invariant-supervisor` is not published.

`v0.3.0` and its original release assets remain historical verification evidence.
`v0.3.1` records the package-name correction and publication tooling. Runtime code
and saved-state formats are unchanged. Registry uploads require owner credentials;
GitHub releases alone do not establish that a registry upload succeeded.

## Published versions

The first registry delivery is complete (October 26, 2026):

| Package | Registry version | Source tag |
|---|---|---|
| `invariant-workspace` | [PyPI 0.3.1](https://pypi.org/project/invariant-workspace/0.3.1/) | `v0.3.1` |
| `invariant-workspace-core` | [crates.io 0.1.0](https://crates.io/crates/invariant-workspace-core/0.1.0) | `rust-v0.1.0` |
| `invariant-broker` | [crates.io 0.1.0](https://crates.io/crates/invariant-broker/0.1.0) | `rust-v0.1.0` |

The Rust tag adds registry installation instructions to the library READMEs;
implementation code and Cargo dependency declarations match `v0.3.1`.
A consumer with an empty Cargo home passed on Rust 1.90 using only registry
dependencies. Downloaded archives match the uploaded bytes and registry hashes.
See [Rust publication verification](RUST_RELEASE_0.1.0.md).

The following first-upload steps describe the completed owner setup. Existing
versions must not be uploaded again. Increase the relevant package version for
future releases and verify the new artifacts before publishing.

## First crates.io upload

Sign into crates.io using the owner's GitHub account, verify its email and create
a narrowly scoped, short-lived publish token for both public packages. Save it
locally with `cargo login`; never commit it or paste it into an issue or chat.
From the clean release checkout:

```sh
cargo publish --locked -p invariant-workspace-core
# Wait until Cargo confirms that the new version is available in the index.
cargo publish --locked -p invariant-broker
```

Publish the core first: the broker's normalized registry dependency uses its
public package name. Re-query both versions and run a fresh registry-only
consumer before marking publication complete. Upload versions cannot be replaced.
For later releases, configure crates.io trusted publishing after the first upload.

## First PyPI upload without a long-lived token

In the owner's [PyPI account publishing settings](https://pypi.org/manage/account/publishing/),
register a pending GitHub publisher with these exact fields:

| Field | Value |
|---|---|
| PyPI project name | `invariant-workspace` |
| Owner | `Xenox-developer` |
| Repository | `invariant` |
| Workflow filename | `publish.yml` |
| Environment | `pypi` |

The account needs a verified email and PyPI's required two-factor authentication.
A pending publisher creates the project on its first successful upload; it does
not reserve the name. No PyPI API token is stored in this repository.

After uploading the checked wheel, sdist and `SHA256SUMS` to the GitHub release,
run the **Publish verified Python release** workflow from `main` with its tag:

```sh
gh workflow run publish.yml --ref main -f tag=v0.3.1
```

The verification job downloads only the named distributions, checks SHA-256 and
package/version metadata, and runs strict Twine validation. A separate upload job
uses the `pypi` environment and GitHub's short-lived identity token. It publishes
those exact bytes and PyPI provenance attestations. Re-running an already uploaded
version fails instead of silently skipping or replacing it.

Download the published wheel from PyPI, compare the registry SHA-256 with the
release and install it into a fresh environment outside the checkout. Only then
update the release notes and default installation instructions to registry commands.

See the official [Cargo publishing guide](https://doc.rust-lang.org/cargo/reference/publishing.html)
and [PyPI pending-publisher guide](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/).
