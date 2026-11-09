Use jscodeshift version 17.4.0 from https://github.com/meta/jscodeshift
(published git head e71f63dba4f60e39944f5ef1e74a36fbdd317e1e).
Load `/opt/agent-tools/node_modules/jscodeshift`. Use its AST collections,
scope/binding information and printer to implement the task. The installed
package includes sources and API examples you can inspect. Do not use ast-grep
or a separate Babel parser in your implementation. Use the same TASK.md
contract and protected tests.
