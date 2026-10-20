# Release licensing

The project owner selected **Apache-2.0 on October 21, 2026** for INVARIANT's original
source. See [LICENSE](../LICENSE), [NOTICE](../NOTICE), Python metadata and the Rust
workspace metadata. Third-party components retain their original terms.

`scripts/release_inventory.py --report PATH` records the complete locked Rust graph
and declared license expressions, the Python runtime dependency list (standard library),
build inputs, tested image IDs, installed OS package versions and original copyright
files. It saves the notice archives beside its report. The release gate hashes these
artifacts. Inspect this inventory for each binary/image distribution: an inventory pass
means the inventory is present, not that every form of redistribution is approved.

The Codex installer verifies the pinned official package's integrity and license metadata,
preserves its package metadata and included notice/license resources, and adds the
Apache-2.0 license text. These files are copied into the general environment image under
`/usr/local/share/invariant/codex-notices`. The seccomp profile keeps its existing license
and provenance files. Debian package notices remain in `/usr/share/doc`.

Before publishing images or binaries, review the generated inventories, preserve the
upstream notices and satisfy any source/offer requirements for the components actually
distributed. Docker Desktop and a ChatGPT account are separate user prerequisites; the
INVARIANT source license does not grant rights to those products or provider access.
