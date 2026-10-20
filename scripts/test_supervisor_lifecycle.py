#!/usr/bin/env python3
"""Real Docker lifecycle tests; run after scripts/invariant prepare and test."""
import json
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = str(ROOT / "scripts/invariant")


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True, timeout=15).strip()


def containers(role):
    args = ["ps", "-q", "--filter", f"label=io.invariant.role={role}"]
    if role == "control":
        args += ["--filter", "volume=invariant-supervisor-state-v1"]
    else:
        mountpoint = json.loads(docker("volume", "inspect", "invariant-supervisor-state-v1"))[0]["Mountpoint"]
        owner = hashlib.sha256(mountpoint.encode()).hexdigest()
        args += ["--filter", "label=io.invariant.owner=" + owner]
    return docker(*args).split()


def wait_for(predicate):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.05)
    raise AssertionError("Docker lifecycle condition timed out")


def launch(program):
    return subprocess.Popen([LAUNCHER, "run", str(program), "--duration", "60"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def controls_ready():
    result = subprocess.run([LAUNCHER,"status","--json"],capture_output=True,text=True,timeout=15)
    return result.returncode == 0


def main():
    assert not containers("control"), "another operator is using the supervisor state"
    destination = ROOT / ".invariant"
    destination.mkdir(exist_ok=True, mode=0o700)
    results = []
    with tempfile.TemporaryDirectory(prefix="lifecycle-", dir=destination) as directory:
        program = Path(directory) / "wait.py"
        program.write_text("import time\ntime.sleep(60)\n")
        child = launch(program)
        control = None
        try:
            control = wait_for(lambda: containers("control"))[0]
            workload = wait_for(lambda: containers("workload"))[0]
            wait_for(controls_ready)  # Crash with a live operator socket too.
            # The persistent state lock must reject simultaneous controllers.
            second = subprocess.run([LAUNCHER, "run", str(program)], capture_output=True,
                                    text=True, timeout=20)
            assert second.returncode != 0 and "another supervisor owns" in second.stderr
            results.append("exclusive supervisor lock")
            # SIGKILL deliberately bypasses cleanup. Only our identified control
            # container is killed; the orphan is then recovered by normal startup.
            docker("kill", "--signal=KILL", control)
            child.communicate(timeout=20)
            assert workload in containers("workload"), "test did not leave a live orphan"
            recovered = subprocess.run([LAUNCHER, "run", str(ROOT / "examples/invoice_summary.py")],
                                       capture_output=True, text=True, timeout=45)
            assert recovered.returncode == 0, recovered.stderr + recovered.stdout
            assert "Invoices read: 1" in recovered.stdout
            assert workload not in docker("ps", "-aq").split()
            assert not containers("workload")
            results.append("restart removes orphan before new successful execution")
        finally:
            if control and control in containers("control"):
                docker("kill", "--signal=TERM", control)
            child.communicate(timeout=20)

        child = launch(program)
        control = None
        try:
            control = wait_for(lambda: containers("control"))[0]
            wait_for(lambda: containers("workload"))
            wait_for(controls_ready)
            docker("kill", "--signal=TERM", control)
            output, _ = child.communicate(timeout=20)
            assert child.returncode != 0 and "Cancelled" in output, output
            assert not containers("workload")
            results.append("termination signal stops authority and container")
        finally:
            if control and control in containers("control"):
                docker("kill", "--signal=TERM", control)
            child.communicate(timeout=20)
    evidence = destination / "docker-lifecycle.json"
    descriptor = os.open(evidence, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as file:
        json.dump({"passed": results}, file, indent=2)
    for result in results:
        print("PASS:", result)


if __name__ == "__main__":
    main()
