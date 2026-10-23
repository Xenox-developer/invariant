# Ready-to-use example projects

Generate either example from an installed package, without cloning this repository:

```sh
invariant experiment example python ~/invariant-python-example
invariant experiment example javascript ~/invariant-javascript-example
```

Both are intentionally unfinished slug utilities with fixed test suites and
complete setup/run instructions. Python uses the standard library; JavaScript
uses Node's test runner with pinned dependency metadata and no npm dependencies.
Generation uses no Docker, sign-in or model calls. Their documented real guided
runs each authorize three initial model turns.

Canonical sources are packaged under
[Python](../../invariant/assets/examples/python/README.md) and
[JavaScript](../../invariant/assets/examples/javascript/README.md).
