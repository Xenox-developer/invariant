"""Run one captured shell test command against candidate code inside Docker only."""
import json
import hashlib
import os
from pathlib import Path
import select
import shutil
import signal
import subprocess
import sys
import time

LIMIT = 1024 * 1024


def remove(path):
    if path.is_symlink() or path.is_file(): path.unlink()
    elif path.exists(): shutil.rmtree(path)


def destination(root, relative):
    parts = Path(relative).parts
    if not parts or Path(relative).is_absolute() or any(p in ('.','..') for p in parts):
        raise ValueError('Invalid captured test path')
    parent = root
    for part in parts[:-1]:
        parent = parent/part
        if parent.is_symlink() or (parent.exists() and not parent.is_dir()): remove(parent)
        parent.mkdir(exist_ok=True)
    return parent/parts[-1]


def run(command, cwd, log, timeout, output_limit=LIMIT):
    start = time.monotonic(); process = None; reason = None; size = 0
    try:
        process = subprocess.Popen(['/bin/sh','-c',command], cwd=cwd, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        with log.open('wb') as output:
            while True:
                remaining = timeout-(time.monotonic()-start) if timeout else float('inf')
                if remaining<=0: reason='timeout'; break
                if not select.select([process.stdout],[],[],min(.1,remaining))[0]: continue
                data = os.read(process.stdout.fileno(),65536)
                if not data: break
                output.write(data if output_limit is None else data[:max(0,output_limit-size)]); size+=len(data)
                if output_limit is not None and size>output_limit: reason='output_limit'; break
            if reason is None:
                try: process.wait(timeout=max(.01,timeout-(time.monotonic()-start)) if timeout else None)
                except subprocess.TimeoutExpired: reason='timeout'
        code = process.returncode if reason is None else None
        if code in (126,127): reason='command_unavailable'
        return code, reason, time.monotonic()-start
    finally:
        if process:
            try: os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError: pass
            process.wait(); process.stdout.close()


def evaluate(spec, source, candidate, output):
    output.mkdir(parents=True,exist_ok=True)
    code = None; reason = None; duration = 0
    try:
        for root in spec['roots']: remove(destination(candidate,root))
        for row in spec['files']:
            target = destination(candidate,row['path'])
            remove(target); target.write_bytes((source/row['path']).read_bytes()); target.chmod(row['mode'])
        if spec.get('report'):remove(destination(candidate,spec['report']))
        code, reason, duration = run(spec['command'],candidate,output/'test-output.txt',spec['timeout_seconds'],spec.get('output_limit',LIMIT))
    except Exception as error:
        reason='test_preparation_failed'
        (output/'test-output.txt').write_text(type(error).__name__+': '+str(error))
    checks=None;report_digest=None
    if spec.get('report') and reason is None:
        try:
            path=destination(candidate,spec['report'])
            if path.is_symlink() or path.stat().st_size>1024*1024:raise ValueError('Invalid report file')
            raw=path.read_bytes();report=json.loads(raw)
            counts={key:report.get(key) for key in ('tests','failures','errors')}
            if any(type(v) is not int or v<0 for v in counts.values()):raise ValueError('Invalid counts')
            # Failure events may exceed test methods (for example unittest subtests).
            if type(report.get('passed')) is not bool or report['passed']!=(code==0) or report['passed']!=(counts['failures']==0 and counts['errors']==0):raise ValueError('Report disagrees with command')
            checks={**counts,'passed':report['passed']}
            report_digest=hashlib.sha256(raw).hexdigest()
        except (OSError,ValueError,TypeError):reason='invalid_test_report'
    details = {'command':spec['command'],'exit_code':code,'reason':reason,'duration_seconds':duration,'checks':checks,'report_sha256':report_digest}
    (output/'test-result.json').write_text(json.dumps(details,indent=2)+'\n')
    metrics = {'test_command_seconds':duration}
    if code is not None: metrics['test_exit_code']=code
    if checks is not None:metrics.update({'tests_total':checks['tests'],'tests_failures':checks['failures'],'tests_errors':checks['errors']})
    return {'schema_version':1,'passed':None if reason else code==0,'metrics':metrics,
            'artifacts':['test-output.txt','test-result.json']}


if __name__=='__main__':
    print(json.dumps(evaluate(json.loads(Path(sys.argv[1]).read_text()),Path.cwd(),
                              Path(os.environ['INVARIANT_CANDIDATE_DIR']),
                              Path(os.environ['INVARIANT_EVALUATION_OUTPUT_DIR']))))
