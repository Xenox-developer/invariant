# ast-grep vs jscodeshift through INVARIANT

One coding agent implements the same JavaScript migration twice. Only its
assigned AST tool differs:

- [ast-grep](https://github.com/ast-grep/ast-grep), `@ast-grep/napi@0.51.1`.
- [jscodeshift](https://github.com/meta/jscodeshift), `jscodeshift@17.4.0`.

Both use the same account-default model, medium effort, frozen workspace,
prepared Linux image, and 34 fixed acceptance checks. Tools are installed
together so setup is shared. Published package commits and package-integrity
metadata are recorded in `tool-provenance.json`.

See `TASK.md` for the exact behavior. Checks cover import aliases, property
access, optional chaining, lexical shadowing, comments/strings, idempotence,
CLI behavior and loading the assigned library. Babel 7.28.5 is used solely by
the evaluator to compare output syntax trees.

Run from this directory with the INVARIANT checkout available:

```sh
python3 run_comparison.py
```

The runner freezes one study, checks its baseline without model calls, dispatches
two native variants concurrently, waits in code and exports the results. Stable
request IDs reopen the same study without replaying completed agent calls.
The baseline implementation is intentionally incomplete and should fail positive
migration tests. No result is automatically selected or applied.

The environment is prepared with Node.js/npm and the three pinned packages under
`/opt/agent-tools`. The current user's optional resource/network defaults are
preserved; agent and executor storage remain inside Docker. Package installation and image creation are outside agent timings.

The current layout-2 run writes reports under `results/storage-v2/`.
The original `REPORT.md` and `results.json` remain historical evidence.
Both layout-2 variants passed the same 34 checks; see [the report](results/storage-v2/REPORT.md). Saved implementations, logs and provider
usage remain attached to their INVARIANT attempts.
