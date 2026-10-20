#!/usr/bin/env python3
"""Actual Docker/operator CLI acceptance, synchronized on audit/status records."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

from test_supervisor_lifecycle import ROOT, LAUNCHER, containers, docker, wait_for


ISOLATION_CHECKS = r'''
import errno, json, os, socket, struct, time
from invariant_client import BrokerClient
assert not os.path.exists('/state/operator.sock')
assert not os.path.exists('/var/run/docker.sock')
assert os.statvfs('/authority').f_flag & os.ST_RDONLY
try:
    open('/authority/capability','w')
except OSError as error:
    assert error.errno in (errno.EROFS,errno.EACCES)
else:
    raise AssertionError('workload can alter capability delivery')
with socket.socket(socket.AF_UNIX) as s:
    s.connect(os.environ['INVARIANT_SOCKET'])
    request=json.dumps({'action':'grant','execution_id':'forged'}).encode()
    s.sendall(struct.pack('!I',len(request))+request)
    def exact(size):
        body=b''
        while len(body)<size:
            part=s.recv(size-len(body))
            assert part
            body+=part
        return body
    prefix=exact(4)
    size=struct.unpack('!I',prefix)[0]
    body=exact(size)
    assert json.loads(body)=={'type':'denied','reason':'invalid_request'}
'''

# Kept as a runnable program so the reply/delivery boundary can also be
# reproduced deterministically against a scripted Unix peer with the real SDK.
AUTHORITY_PROBES = r'''
import json, os, time
from invariant_client import BrokerClient
client = BrokerClient.from_env()
phase='initial'
counter=0
deadline=time.monotonic()+90
while time.monotonic()<deadline:
    if phase=='finished':
        time.sleep(0.05)
        continue
    counter+=1
    rid=first_id if phase=='revoke' else 'probe-'+str(counter)
    reply=client.request('GET','/invoices',request_id=rid)
    if phase=='initial':
        if reply['type']=='denied':
            # Issuance commits before delivery. A request can already carry
            # the placeholder read before the atomic capability publication.
            assert reply['reason'] in ('missing_grant','invalid_capability')
            print('Before grant: denied ('+reply['reason']+')')
        else:
            assert reply['type']=='response' and reply['status']==200
            assert json.loads(reply['body'])[0]['amount']==42
            print('After operator grant: allowed (42)')
            old_cap=open(os.environ['INVARIANT_CAPABILITY_FILE']).read()
            first_id=rid
            phase='revoke'
    elif phase=='revoke':
        if reply=={'type':'denied','reason':'revoked'}:
            print('After operator revocation: denied (revoked)')
            phase='replacement'
        else:
            assert reply=={'type':'replay','outcome':{'state':'completed','status':200}}
    elif phase=='replacement':
        if reply['type']=='denied':
            # The same safe delivery window exists during explicit renewal.
            # The next loop iteration uses a NEW ID; never replay a denial.
            assert reply['reason'] in ('revoked','invalid_capability')
        else:
            assert reply['type']=='response' and reply['status']==200
            assert open(os.environ['INVARIANT_CAPABILITY_FILE']).read()!=old_cap
            stale=BrokerClient(os.environ['INVARIANT_SOCKET'],old_cap)
            assert stale.request('GET','/invoices')['reason']=='invalid_capability'
            assert client.request('GET','/invoices',request_id=first_id)['reason']=='request_conflict'
            for i in range(19):
                assert client.request('GET','/invoices',request_id='replacement-'+str(i))['type']=='response'
            assert client.request('GET','/invoices')['reason']=='budget_exhausted'
            print('Replacement: same client, new capability, 20 reads; old capability denied; old ID retained')
            phase='finished'
    time.sleep(0.05)
raise AssertionError('operator acceptance deadline expired')
'''

PROGRAM = ISOLATION_CHECKS + AUTHORITY_PROBES


def live_audit_records(text):
    # A live append can leave an unfinished final line in this snapshot. A
    # completed malformed line remains an error, never silently skipped.
    return [json.loads(line) for line in text.splitlines(keepends=True) if line.endswith("\n")]


def read_live_audit(control, run_name):
    # The generic Docker helper strips whitespace. Preserve journal framing so
    # the last complete record is visible even when no later record is written.
    raw = subprocess.check_output(["docker", "exec", control, "cat", "/state/" + run_name + "/audit.jsonl"],
                                  text=True, timeout=15)
    return live_audit_records(raw)


def command(action, execution=None, success=True, extra=()):
    args = [LAUNCHER,action]
    if execution:
        args.append(execution)
    result = subprocess.run(args+["--json",*extra],capture_output=True,text=True,timeout=15)
    assert (result.returncode==0)==success, result.stdout+result.stderr
    return json.loads(result.stdout) if result.stdout else None


def main():
    assert not containers("control"), "another operator is using the state directory"
    destination = ROOT/".invariant"
    destination.mkdir(exist_ok=True,mode=0o700)
    results=[]
    with tempfile.TemporaryDirectory(prefix="operator-",dir=destination) as directory:
        program=Path(directory)/"live.py"
        program.write_text(PROGRAM)
        child=subprocess.Popen([LAUNCHER,"run",str(program),"--no-grant","--duration","90"],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
        execution=None
        control=None
        try:
            control=wait_for(lambda: containers("control"))[0]
            workload=wait_for(lambda: containers("workload"))[0]
            mounts=json.loads(docker("inspect",workload))[0]["Mounts"]
            run_name=next(Path(m["Source"]).parent.name for m in mounts if m["Destination"]=="/input/program.py")
            def audit():
                return read_live_audit(control, run_name)
            def denied(reason):
                return any(r["event"]=="denied" and r.get("reason")==reason for r in audit())
            wait_for(lambda: denied("missing_grant"))
            initial=command("status")["status"]
            execution=initial["execution_id"]
            assert initial["grant"] is None and initial["total_admissions"]==0
            results.append("start without a grant and deny effects")
            wrong="run-"+"0"*64
            assert command("grant",wrong,success=False)["error"]=="wrong_execution"
            assert command("revoke",wrong,success=False)["error"]=="wrong_execution"
            command("grant",execution,success=False,extra=("--budget","1000"))
            assert command("status",execution)["status"]["grant"] is None
            results.append("wrong executions and unsupported constraints rejected")
            issued=command("grant",execution)["status"]
            assert issued["grant"]["state"]=="active" and issued["grant"]["max_requests"]==20
            assert issued["grant"]["delegation"] is False
            wait_for(lambda: command("status",execution)["status"]["total_admissions"]==1)
            assert command("grant",execution,success=False)["error"]=="active_grant_exists"
            assert command("status",execution)["status"]["grant"]["admissions"]==1
            results.append("live issuance allows one read; duplicate active grant cannot reset budget")
            exported=subprocess.run([LAUNCHER,"evidence"],capture_output=True,text=True,timeout=30)
            assert exported.returncode==0,exported.stdout+exported.stderr
            public=ROOT/".invariant/docker-evidence"/run_name
            assert (public/"audit.jsonl").is_file()
            assert not (public/"credentials").exists() and not (public/"program.py").exists()
            results.append("live evidence export excludes capability delivery and program source")
            revoked=command("revoke",execution)["status"]
            assert revoked["grant"]["state"]=="revoked"
            wait_for(lambda: denied("revoked"))
            assert command("status",execution)["status"]["total_admissions"]==1
            results.append("committed revocation denies subsequent reads in the same program")
            replacement=command("grant",execution)["status"]
            assert replacement["grant"]["grant_id"]!=issued["grant"]["grant_id"]
            wait_for(lambda: command("status",execution)["status"]["grant"]["state"]=="exhausted")
            wait_for(lambda: denied("budget_exhausted"))
            final=command("status",execution)["status"]
            assert final["total_admissions"]==21 and final["grant"]["admissions"]==20
            assert denied("invalid_capability") and denied("request_conflict") and denied("budget_exhausted")
            results.append("explicit replacement rotates delivery and preserves old request IDs; 20-read limit holds")
            final=command("stop",execution)["status"]
            assert final["running"] is False
            output,_=child.communicate(timeout=20)
            assert child.returncode==0,output
            assert "After operator grant: allowed (42)" in output and "After operator revocation: denied (revoked)" in output,output
            assert "Mock API requests: 21" in output and "Admissions: 21" in output
            assert not containers("workload")
            results.append("operator stop terminates authority and removes the workload")
        finally:
            if child.poll() is None:
                if control and control in containers("control"):
                    docker("kill","--signal=TERM",control)
                child.communicate(timeout=20)
    descriptor=os.open(destination/"operator-controls.json",os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(descriptor,"w") as file:
        json.dump({"execution_id":execution,"passed":results,"final_status":final},file,indent=2)
    for result in results:
        print("PASS:",result)


if __name__=="__main__":
    main()
