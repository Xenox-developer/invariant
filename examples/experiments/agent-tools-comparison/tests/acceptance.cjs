const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const cp = require('node:child_process');
const parser = require('/opt/agent-tools/node_modules/@babel/parser');
const Module = require('node:module');
const candidate = process.env.INVARIANT_CANDIDATE_DIR || process.cwd();
const loaded = new Set();
const realLoad = Module._load;
Module._load = function (request, ...args) {
  if (request.includes('@ast-grep/napi')) loaded.add('ast-grep');
  if (request.includes('jscodeshift')) loaded.add('jscodeshift');
  return realLoad.call(this, request, ...args);
};
let migrate, loadError;
try { migrate = require(path.join(candidate, 'migrate.cjs')); }
catch (error) { loadError = error; }
const results = [];
function test(name, fn) {
  try { if (loadError) throw loadError; fn(); results.push({name, passed:true}); }
  catch (error) { results.push({name, passed:false, error:String(error.message).slice(0,1800)}); }
}
const ignored = new Set(['start','end','loc','extra','comments','leadingComments','trailingComments','innerComments']);
function clean(value) {
  if (Array.isArray(value)) return value.map(clean);
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).filter(([key]) => !ignored.has(key)).map(([key,v]) => [key,clean(v)]));
  return value;
}
function ast(source) { return clean(parser.parse(source,{sourceType:'module'})); }
const fixtures = [
  ['named import', "import { warn } from 'legacy-log'; warn('hello');", "import { warning as warn } from 'modern-log'; warn('hello');"],
  ['named alias', "import { warn as loud } from 'legacy-log'; loud('hello');", "import { warning as loud } from 'modern-log'; loud('hello');"],
  ['mixed named imports', "import { info, warn as loud } from 'legacy-log'; info('a'); loud('b');", "import { info, warning as loud } from 'modern-log'; info('a'); loud('b');"],
  ['named collision avoided', "import { warn } from 'legacy-log'; const warning=1; warn(warning);", "import { warning as warn } from 'modern-log'; const warning=1; warn(warning);"],
  ['named shadow preserved', "import { warn } from 'legacy-log'; function f(warn){warn('local')} warn('outer');", "import { warning as warn } from 'modern-log'; function f(warn){warn('local')} warn('outer');"],
  ['namespace call', "import * as log from 'legacy-log'; log.warn('x');", "import * as log from 'modern-log'; log.warning('x');"],
  ['default call', "import log from 'legacy-log'; log.warn('x');", "import log from 'modern-log'; log.warning('x');"],
  ['default plus named', "import log, { warn as loud, info } from 'legacy-log'; log.warn('x'); loud('y');", "import log, { warning as loud, info } from 'modern-log'; log.warning('x'); loud('y');"],
  ['namespace alias', "import * as audit from 'legacy-log'; audit.warn('x');", "import * as audit from 'modern-log'; audit.warning('x');"],
  ['property read', "import * as log from 'legacy-log'; const f=log.warn; f('x');", "import * as log from 'modern-log'; const f=log.warning; f('x');"],
  ['computed member', "import * as log from 'legacy-log'; log['warn']('x');", "import * as log from 'modern-log'; log['warning']('x');"],
  ['optional member', "import * as log from 'legacy-log'; log?.warn('x');", "import * as log from 'modern-log'; log?.warning('x');"],
  ['optional computed member', "import * as log from 'legacy-log'; log?.['warn']('x');", "import * as log from 'modern-log'; log?.['warning']('x');"],
  ['function parameter shadow', "import * as log from 'legacy-log'; function f(log){log.warn('local')} log.warn('outer');", "import * as log from 'modern-log'; function f(log){log.warn('local')} log.warning('outer');"],
  ['arrow parameter shadow', "import * as log from 'legacy-log'; const f=(log)=>log.warn('local'); log.warn('outer');", "import * as log from 'modern-log'; const f=(log)=>log.warn('local'); log.warning('outer');"],
  ['block lexical shadow', "import * as log from 'legacy-log'; { const log=local; log.warn('local'); } log.warn('outer');", "import * as log from 'modern-log'; { const log=local; log.warn('local'); } log.warning('outer');"],
  ['catch binding shadow', "import * as log from 'legacy-log'; try { log.warn('outer'); } catch(log) { log.warn('local'); }", "import * as log from 'modern-log'; try { log.warning('outer'); } catch(log) { log.warn('local'); }"],
  ['destructured parameter shadow', "import * as log from 'legacy-log'; function f({log}) { log.warn('local'); } log.warn('outer');", "import * as log from 'modern-log'; function f({log}) { log.warn('local'); } log.warning('outer');"],
  ['hoisted var shadow', "import * as log from 'legacy-log'; function f(){log.warn('local');var log=local;} log.warn('outer');", "import * as log from 'modern-log'; function f(){log.warn('local');var log=local;} log.warning('outer');"],
  ['nested closure shadow', "import * as log from 'legacy-log'; function f(log){return ()=>log.warn('local');} const g=()=>log.warn('outer');", "import * as log from 'modern-log'; function f(log){return ()=>log.warn('local');} const g=()=>log.warning('outer');"],
  ['nested block does not shadow parent', "import * as log from 'legacy-log'; function f(){log.warn('outer'); {let log=local; log.warn('local');}}", "import * as log from 'modern-log'; function f(){log.warning('outer'); {let log=local; log.warn('local');}}"],
  ['dynamic computed member unchanged', "import * as log from 'legacy-log'; log[key]('x');", "import * as log from 'modern-log'; log[key]('x');"],
  ['nested member unchanged', "import * as log from 'legacy-log'; log.child.warn('x'); log.info('y');", "import * as log from 'modern-log'; log.child.warn('x'); log.info('y');"],
  ['unrelated object unchanged', "import * as log from 'legacy-log'; const other={warn(){}}; other.warn(); log.warn('x');", "import * as log from 'modern-log'; const other={warn(){}}; other.warn(); log.warning('x');"],
  ['different module untouched', "import * as log from 'other-log'; log.warn('x');", null],
  ['modern module untouched', "import * as log from 'modern-log'; log.warning('x');", null],
  ['no imports untouched', "const warn = x => x; warn('legacy-log'); const log={warn(){}}; log.warn();", null],
  ['re-export untouched', "export { warn } from 'legacy-log';", null],
  ['strings preserved', "import * as log from 'legacy-log'; const text=\"log.warn('x') legacy-log\"; log.warn(text);", "import * as log from 'modern-log'; const text=\"log.warn('x') legacy-log\"; log.warning(text);"],
  ['comments preserved', "// KEEP_IMPORT_COMMENT\nimport * as log from 'legacy-log';\n/* KEEP_CALL_COMMENT */ log.warn('x');", "// KEEP_IMPORT_COMMENT\nimport * as log from 'modern-log';\n/* KEEP_CALL_COMMENT */ log.warning('x');"],
];
for (const [name,source,expected] of fixtures) {
  test(name, () => {
    assert.equal(typeof migrate,'function');
    const actual=migrate(source);assert.equal(typeof actual,'string');
    assert.deepEqual(ast(actual),ast(expected===null ? source : expected));
    if (expected===null) assert.equal(actual,source);
    for (const marker of ['KEEP_IMPORT_COMMENT','KEEP_CALL_COMMENT']) if (source.includes(marker)) assert.ok(actual.includes(marker));
  });
}
test('idempotent for every fixture',()=>{
  for(const [,source] of fixtures){const once=migrate(source);assert.equal(migrate(once),once);}
});
test('CLI preserves its input and emits migrated source',()=>{
  const tmp=fs.mkdtempSync('/scratch/tool-comparison-cli-');
  try {
    const source=fixtures[5][1],input=path.join(tmp,'input.js');fs.writeFileSync(input,source);
    const result=cp.spawnSync(process.execPath,[path.join(candidate,'migrate.cjs'),input],{encoding:'utf8'});
    assert.equal(result.status,0,result.stderr);assert.deepEqual(ast(result.stdout),ast(fixtures[5][2]));
    assert.equal(fs.readFileSync(input,'utf8'),source);
  } finally {fs.rmSync(tmp,{recursive:true,force:true});}
});
test('CLI rejects syntax errors',()=>{
  const tmp=fs.mkdtempSync('/scratch/tool-comparison-invalid-');
  try {const input=path.join(tmp,'input.js');fs.writeFileSync(input,'import {');const result=cp.spawnSync(process.execPath,[path.join(candidate,'migrate.cjs'),input],{encoding:'utf8'});assert.notEqual(result.status,0);}
  finally {fs.rmSync(tmp,{recursive:true,force:true});}
});
test('implementation loads its assigned AST library',()=>{
  if(!process.env.INVARIANT_VARIANT_FILE)return;
  const variant=JSON.parse(fs.readFileSync(process.env.INVARIANT_VARIANT_FILE,'utf8'));
  if(['ast-grep','jscodeshift'].includes(variant.id))assert.ok(loaded.has(variant.id),`Required tool not loaded: ${variant.id}`);
});
const failures=results.filter(r=>!r.passed).length;
console.log(JSON.stringify({tests:results.length,failures,errors:0,passed:failures===0,results}));
process.exitCode=failures?1:0;
