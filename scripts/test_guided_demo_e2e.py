#!/usr/bin/env python3
"""Real Docker checks for the guided public command and interruption cleanup."""
import json
import os
from pathlib import Path
import pty
import signal
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = str(ROOT / "scripts/invariant")


def report_from(output):
    paths = [line.removeprefix("Report: ") for line in output.splitlines() if line.startswith("Report: ")]
    assert len(paths) == 1, output
    path = Path(paths[0]).resolve()
    assert path.is_relative_to(ROOT / ".invariant/demo") and path.name == "REPORT.md"
    return json.loads(path.with_name("demo.json").read_text())


def no_resources(report):
    def docker(*args):
        return subprocess.check_output(["docker", *args], text=True, timeout=15).strip()
    assert not docker("ps", "-aq", "--filter", "label=io.invariant.demo=" + report["demo_id"])
    assert not docker("volume", "ls", "-q", "--filter", "label=io.invariant.demo=" + report["demo_id"])
    assert not docker("ps", "-aq", "--filter", "label=io.invariant.owner=" + report["state_owner"],
                      "--filter", "label=io.invariant.role=workload")


def main():
    result = subprocess.run([LAUNCHER, "demo", "--auto"], capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stdout + result.stderr
    successful = report_from(result.stdout)
    assert successful["passed"] and len(successful["steps"]) == 5
    assert successful["facts"]["target_requests"] == successful["facts"]["admissions"] == 20
    assert successful["cleanup_errors"] == []
    no_resources(successful)
    print(result.stdout, end="", flush=True)
    print("PASS: automatic guided command verifies all five stages and removes its resources", flush=True)

    with tempfile.TemporaryDirectory(prefix="guided-check-", dir=ROOT / ".invariant") as directory:
        output = Path(directory) / "output.log"
        master, slave = pty.openpty()
        with output.open("w") as log:
            child = subprocess.Popen([LAUNCHER, "demo", "--step"], stdin=slave, stdout=log,
                                     stderr=subprocess.STDOUT, start_new_session=True)
        os.close(slave)
        try:
            deadline = time.monotonic() + 45
            while "Press Enter to continue" not in output.read_text(errors="replace"):
                assert child.poll() is None, output.read_text()
                assert time.monotonic() < deadline, "step mode did not reach its first prompt"
                time.sleep(.05)
            child.send_signal(signal.SIGINT)
            assert child.wait(timeout=40) == 1
            interrupted = report_from(output.read_text())
            assert interrupted["passed"] is False and interrupted["error"] == "Demonstration interrupted"
            assert interrupted["cleanup_errors"] == []
            assert list(Path(interrupted["evidence"]).glob("evidence/iv-*/audit.jsonl"))
            no_resources(interrupted)
        finally:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=40)
            os.close(master)
    print("PASS: interrupting step mode preserves partial evidence and removes its resources", flush=True)
    destination = ROOT / ".invariant/guided-demo-checks.json"
    with destination.open("w") as file:
        os.chmod(destination, 0o600)
        json.dump({"passed": ["automatic walkthrough", "step interruption cleanup"],
                   "successful_demo": successful, "interrupted_demo": interrupted}, file, indent=2)


if __name__ == "__main__":
    main()
