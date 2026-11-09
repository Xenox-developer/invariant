# Migrate the legacy logging API safely

Implement `migrate(source)` in `migrate.cjs` and export it as a CommonJS function.
It accepts a JavaScript ES-module source string and returns migrated source.
The CLI `node migrate.cjs INPUT.js` must print migrated source without changing
the input file. Parsing errors must produce a nonzero exit code.

Migration contract:

1. Change import sources exactly equal to `legacy-log` to `modern-log`.
2. Change named imports of `warn` to imports of `warning`, preserving each local
   binding name. For example, `import { warn } from 'legacy-log'` becomes
   `import { warning as warn } from 'modern-log'`. Preserve all other specifiers.
3. For namespace and default imports from `legacy-log`, change accesses to their
   direct `warn` member to `warning`. Cover property reads, calls, optional
   chaining, and statically quoted computed properties. Preserve dot/computed
   notation. Dynamic computed properties and nested members are unchanged.
4. Respect lexical binding: function/arrow parameters, block locals, catch
   bindings, destructuring parameters and hoisted function-local `var` bindings
   shadow imported namespace/default names. Preserve those local member accesses.
   A declaration in a nested block does not shadow its surrounding block.
5. Preserve comments and string contents. Imports from other modules, unrelated
   objects, local functions called `warn`, re-export declarations, and files
   without a legacy import must remain unchanged. Already migrated input must be
   returned byte-for-byte unchanged. The migration must be idempotent.

Use the AST library assigned in your variant instructions as the actual parsing
and transformation mechanism. Do not implement the migration with regex/text
matching, and do not use the other variant's library or Babel as a fallback AST.
The evaluator uses Babel only to compare output semantics; it is not an
implementation dependency. Do not change the acceptance suite or evaluator.

Both libraries are installed in the same prepared environment under
`/opt/agent-tools/node_modules`. Require your assigned package by its absolute
path, or set NODE_PATH explicitly when developing. The model and effort,
starting workspace, installed versions and acceptance tests are the same in
both variants. Work in `/workspace`; do not inspect other attempts or host files.

Before finishing, call `invariant_validate` to run the protected suite. If it
passes, send your final message without more commands or changes. If it fails,
repair your implementation and validate again in the same attempt.
