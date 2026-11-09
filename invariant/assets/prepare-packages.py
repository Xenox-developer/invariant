"""Debian preparation: retry acquisition only, then install once offline."""
import json
import re
import subprocess
import sys
import time


TRANSIENT=re.compile(r'(?m)^\s*(?:E:.*https?://\S+\s+)?(?:408|429|500|502|503|504)\s|'
                     r'Temporary failure resolving|Connection timed out|Could not connect|Network is unreachable',re.I)


def acquire(command,retries,*,run=None,sleep=None):
    run=run or subprocess.run
    sleep=sleep or time.sleep
    for attempt in range(retries+1):
        result=run(command,capture_output=True,text=True)
        output=result.stdout+result.stderr
        print(output,end='',flush=True)
        if result.returncode==0:return 0
        if attempt==retries or not TRANSIENT.search(output):return result.returncode
        print('Transient acquisition failure; retrying download only.',flush=True)
        sleep(min(2**attempt,10))


def install(packages,retries):
    base=['apt-get','-o','Acquire::Retries=0','-o','Acquire::http::Timeout=30','-o','Acquire::https::Timeout=30']
    code=acquire(base+['-o','APT::Update::Error-Mode=any','update'],retries)
    if code:return code
    code=acquire(base+['--download-only','install','-y','--no-install-recommends',*packages],retries)
    if code:return code
    # Maintainer scripts can have effects; never retry this command.
    return subprocess.run(base+['--no-download','install','-y','--no-install-recommends',*packages]).returncode


if __name__=='__main__':
    value=json.loads(sys.argv[1])
    raise SystemExit(install(value['packages'],value['retries']))
