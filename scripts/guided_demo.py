#!/usr/bin/env python3
"""Narrated MVP demonstration through the existing supervisor/operator protocol."""
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent.parent
PROGRAM = ROOT / "examples/guided_mvp.py"
CONTROL_IMAGE = "invariant-supervisor:local"
WORKLOAD_IMAGE = "invariant-python:local"


class DemoError(RuntimeError):
    pass


def save(path, value):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as file:
            os.chmod(temporary, 0o600)
            json.dump(value, file, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def audit_snapshot(text):
    return [json.loads(line) for line in text.splitlines(keepends=True) if line.endswith("\n")]


def verify_evidence(evidence, execution, source_hash, control_output):
    """Verify exported public artifacts, not just the guide's progress messages."""
    runs = list(evidence.glob("iv-*/result.json"))
    if len(runs) != 1:
        raise DemoError("Expected exactly one demonstration execution in the evidence")
    run = runs[0].parent
    result = json.loads(runs[0].read_text())
    raw = (run / "audit.jsonl").read_text()
    if not raw.endswith("\n"):
        raise DemoError("The final audit journal is incomplete")
    journal = [json.loads(line) for line in raw.splitlines()]
    if result["execution_id"] != execution or result["termination"] != "stopped" or result["admissions"] != 20:
        raise DemoError("The final execution result does not match the demonstration")
    if any(r["execution_id"] != execution or r["initial_artifact_digest"] != source_hash for r in journal):
        raise DemoError("The audit does not match the execution and program that were started")
    if [r["sequence"] for r in journal] != list(range(1, len(journal) + 1)):
        raise DemoError("The audit sequence is incomplete")
    admitted = [r for r in journal if r["event"] == "admitted"]
    responses = [r for r in journal if r["event"] == "response_received"]
    grants = [r for r in journal if r["event"] == "grant_issued"]
    if len(grants) != 1 or grants[0]["max_requests"] != 20 or [r["ordinal"] for r in admitted] != list(range(1, 21)):
        raise DemoError("Expected one grant and exactly 20 charged reads")
    if len(responses) != 20 or any(r["status"] != 200 for r in responses):
        raise DemoError("Expected 20 successful invoice responses")
    if {r["request_id"] for r in admitted} != {r["request_id"] for r in responses}:
        raise DemoError("Response records do not match their admissions")
    required = {"demo-no-grant": "missing_grant", "demo-delete": "out_of_scope",
                "demo-over-budget": "budget_exhausted", "demo-after-revoke": "revoked"}
    denials = {}
    for request_id, reason in required.items():
        matching = [r for r in journal if r["event"] == "denied" and r.get("request_id") == request_id and r.get("reason") == reason]
        if not matching or request_id in {r["request_id"] for r in admitted}:
            raise DemoError("A required denial is missing from the audit: " + reason)
        denials[request_id] = matching[0]
    revoke = next((r for r in journal if r["event"] == "grant_revoked"), None)
    if not revoke or any(r["sequence"] > revoke["sequence"] for r in admitted):
        raise DemoError("Revocation did not prevent new admissions")
    if not (denials["demo-no-grant"]["sequence"] < grants[0]["sequence"] < admitted[0]["sequence"]
            < denials["demo-delete"]["sequence"] < admitted[-1]["sequence"]
            < denials["demo-over-budget"]["sequence"] < revoke["sequence"]
            < denials["demo-after-revoke"]["sequence"]):
        raise DemoError("The permission changes did not follow the guided sequence")
    if any(r.get("grant_id") != grants[0]["grant_id"] for r in admitted + responses):
        raise DemoError("The reads did not use the one explicitly issued grant")
    if journal[-1]["event"] != "execution_terminated":
        raise DemoError("Execution termination was not recorded")
    summaries = [json.loads(line.removeprefix("INVARIANT_DEMO_SUMMARY "))
                 for line in result["output"].splitlines() if line.startswith("INVARIANT_DEMO_SUMMARY ")]
    if summaries != [{"invoices": 1, "total": 42}]:
        raise DemoError("The program did not complete the expected computation and revocation check")
    # The final footer is written by the trusted controller AFTER captured
    # workload output. Anchor to its end; workload text cannot substitute it.
    footer = re.search(r"\nExecution: (run-[0-9a-f]{64})\nStopped: Stopped\nExit code: None\nAdmissions: (\d+)\nMock API requests: (\d+)\nEvidence: [^\n]+\n\Z", control_output)
    if not footer or footer.group(1) != execution or footer.group(2, 3) != ("20", "20"):
        raise DemoError("The independent mock API count did not match the 20 admitted reads")
    for name in ("configuration.json", "isolation.json"):
        if not (run / name).is_file():
            raise DemoError("Docker isolation evidence is missing")
    if any(p.name in ("credentials", "capability", "program.py") or p.is_symlink() for p in evidence.rglob("*")):
        raise DemoError("Private staging data appeared in the public evidence")
    milestones = {"execution_started", "grant_issued", "grant_revoked", "execution_terminated"}
    trail = [{key: r.get(key) for key in ("sequence", "event", "request_id", "reason", "ordinal")}
             for r in journal if r["event"] in milestones or
             (r["event"] == "admitted" and r["ordinal"] in (1, 20)) or
             (r["event"] == "denied" and r.get("request_id") in required)]
    return {"invoices": 1, "total": 42, "admissions": 20, "target_requests": 20,
            "audit_records": len(journal), "run_directory": run.name, "audit_trail": trail}


class Guide:
    def __init__(self, *, step=False):
        self.step = step
        self.id = uuid.uuid4().hex
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.output = ROOT / ".invariant/demo" / (stamp + "-" + self.id[:8])
        self.output.mkdir(parents=True, mode=0o700)
        self.volume = "invariant-demo-" + self.id
        self.control = "iv-demo-control-" + self.id
        self.exporter = "iv-demo-export-" + self.id
        self.volume_created = False
        self.control_created = False
        self.exporter_created = False
        self.owner = None
        self.child = None
        self.run_stream = None
        self.run_command = None
        self.execution = None
        self.run_name = None
        self.commands = []
        self.steps = []
        self.cleanup_errors = []

    def docker(self, *args, check=True, timeout=30):
        entry = {"command": ["docker", *args]}
        self.commands.append(entry)
        started = time.monotonic()
        try:
            result = subprocess.run(entry["command"], capture_output=True, text=True, timeout=timeout)
            entry["exit_code"] = result.returncode
            log = self.output / f"command-{len(self.commands):03d}.log"
            with log.open("w") as file:
                os.chmod(log, 0o600)
                file.write(result.stdout + result.stderr)
            entry["log"] = log.name
            if check and result.returncode:
                raise DemoError("A Docker command failed; details are saved in " + log.name)
            return result
        except subprocess.TimeoutExpired:
            entry["exit_code"] = None
            raise DemoError("Docker did not respond before its deadline") from None
        finally:
            entry["elapsed_seconds"] = round(time.monotonic() - started, 3)
            save(self.output / "commands.json", self.commands)

    def pause(self, instruction):
        if self.step:
            input(instruction + " Press Enter to continue (Ctrl-C to stop). ")

    def operator(self, action, *, ready=False):
        args = ["exec", self.control, "invariant-supervisor", "--state", "/state", "--operator", action]
        if self.execution:
            args += ["--execution", self.execution]
        result = self.docker(*args, check=not ready, timeout=15)
        if ready and result.returncode:
            return None
        try:
            reply = json.loads(result.stdout)
        except ValueError:
            raise DemoError("Operator controls returned an invalid reply") from None
        if reply.get("ok") is not True:
            raise DemoError("The operator command was rejected")
        return reply["status"]

    def wait(self, condition):
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            if self.child is not None and self.child.poll() is not None:
                raise DemoError("The isolated run ended before completing this step; see run.log")
            result = condition()
            if result:
                return result
            time.sleep(.05)
        raise DemoError("The demonstration did not reach its next step before the deadline")

    def audit(self):
        return audit_snapshot(self.docker("exec", self.control, "cat", "/state/" + self.run_name + "/audit.jsonl").stdout)

    def denied(self, request_id, reason):
        return any(r["event"] == "denied" and r.get("request_id") == request_id and r.get("reason") == reason for r in self.audit())

    def ensure_images(self):
        try:
            result = self.docker("info", "--format", "{{.OSType}}", check=False)
        except FileNotFoundError:
            raise DemoError("Docker was not found; the demo requires a running local Docker installation") from None
        if result.returncode or result.stdout.strip() != "linux":
            raise DemoError("Start Docker with Linux containers, then run the demo again")
        if self.docker("image", "inspect", CONTROL_IMAGE, WORKLOAD_IMAGE, check=False).returncode:
            print("Preparing the demo images. The first run may take a few minutes…", flush=True)
            with (self.output / "prepare.log").open("w") as log:
                os.chmod(self.output / "prepare.log", 0o600)
                result = subprocess.run([str(ROOT / "scripts/invariant"), "prepare"], stdout=log, stderr=subprocess.STDOUT, timeout=900)
            if result.returncode:
                raise DemoError("Could not prepare the images; see prepare.log")

    def start(self):
        if "," in str(PROGRAM) or "\n" in str(PROGRAM):
            raise DemoError("The demo cannot mount this project path; use a path without commas or newlines")
        self.volume_created = True
        self.docker("volume", "create", "--label", "io.invariant.demo=" + self.id, self.volume)
        volume = json.loads(self.docker("volume", "inspect", self.volume).stdout)[0]
        self.owner = hashlib.sha256(volume["Mountpoint"].encode()).hexdigest()
        self.control_created = True
        self.docker("create", "--name", self.control, "--pull=never", "--network=none",
                    "--label", "io.invariant.role=demo-control", "--label", "io.invariant.demo=" + self.id,
                    "--mount", "type=bind,src=/var/run/docker.sock,dst=/var/run/docker.sock",
                    "--mount", f"type=volume,src={self.volume},dst=/state",
                    "--mount", f"type=bind,src={PROGRAM},dst=/program.py,readonly",
                    CONTROL_IMAGE, "--state", "/state", "--daemon-state", volume["Mountpoint"],
                    "--image", WORKLOAD_IMAGE, "--program", "/program.py", "--duration", "900", "--no-grant")
        self.run_stream = (self.output / "run.log").open("w")
        os.chmod(self.output / "run.log", 0o600)
        self.run_command = {"command": ["docker", "start", "--attach", self.control], "log": "run.log", "exit_code": None}
        self.commands.append(self.run_command)
        # The guide alone handles terminal signals, then asks the supervisor to
        # stop. An attach client must not independently forward the same Ctrl-C.
        self.child = subprocess.Popen(self.run_command["command"], stdout=self.run_stream,
                                      stderr=subprocess.STDOUT, start_new_session=True)
        status = self.wait(lambda: self.operator("status", ready=True))
        self.execution = status["execution_id"]
        # Discover only this controller's protected run directory; never scan
        # the normal user's state volume or an unrelated running container.
        paths = self.docker("exec", self.control, "find", "/state", "-maxdepth", "2", "-name", "audit.jsonl").stdout.splitlines()
        if len(paths) != 1 or not re.fullmatch(r"/state/iv-[0-9a-f]{32}/audit.jsonl", paths[0]):
            raise DemoError("Could not identify the demonstration audit journal")
        self.run_name = paths[0].split("/")[2]

    def export(self):
        evidence = self.output / "evidence"
        evidence.mkdir(mode=0o700)
        self.exporter_created = True
        self.docker("create", "--name", self.exporter, "--network=none", "--pull=never",
                    "--label", "io.invariant.demo=" + self.id,
                    "--mount", f"type=volume,src={self.volume},dst=/state,readonly",
                    CONTROL_IMAGE, "--state", "/state", "--export-evidence")
        self.docker("start", "--attach", self.exporter)
        self.docker("cp", self.exporter + ":/export/.", str(evidence))
        return evidence

    def cleanup(self):
        def attempt(*args):
            try:
                self.docker(*args)
                return True
            except (DemoError, OSError):
                self.cleanup_errors.append("Could not complete: docker " + " ".join(args))
                return False
        if self.control_created:
            try:
                inspected = self.docker("inspect", self.control, check=False)
                if inspected.returncode == 0:
                    state = json.loads(inspected.stdout)[0]["State"]
                    if state["Running"]:
                        attempt("kill", "--signal=TERM", self.control)
                        # Wait for the CONTROLLER, not just its attach client:
                        # its shutdown owns journal completion and workload removal.
                        if not attempt("wait", self.control):
                            attempt("rm", "--force", self.control)
            except (DemoError, OSError, ValueError, KeyError):
                self.cleanup_errors.append("Could not confirm supervisor shutdown")
        if self.child is not None:
            try:
                self.child.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.child.kill()
                self.child.wait(timeout=5)
                self.cleanup_errors.append("The Docker attach client did not exit")
            self.run_command["exit_code"] = self.child.returncode
        if self.run_stream:
            self.run_stream.close()
        # Select only this unique state-owner label, and require workload role.
        if self.owner:
            try:
                workloads = self.docker("ps", "-aq", "--filter", "label=io.invariant.owner=" + self.owner,
                                        "--filter", "label=io.invariant.role=workload").stdout.split()
                for workload in workloads:
                    attempt("rm", "--force", "--volumes", workload)
            except (DemoError, OSError):
                self.cleanup_errors.append("Could not verify removal of the demonstration workload")
        if self.run_name and not (self.output / "evidence").exists() and not self.cleanup_errors:
            try:
                self.export()
            except (DemoError, OSError):
                self.cleanup_errors.append("Could not export the partial demonstration evidence")
        if self.exporter_created:
            attempt("rm", "--force", self.exporter)
        if self.control_created:
            attempt("rm", "--force", self.control)
        if self.volume_created and not self.cleanup_errors:
            attempt("volume", "rm", self.volume)
        save(self.output / "commands.json", self.commands)

    def run(self):
        error = None
        facts = None
        source_hash = None
        try:
            source_hash = hashlib.sha256(PROGRAM.read_bytes()).hexdigest()
            print("INVARIANT — guided MVP demo\nOne isolated Python program. A mock invoice API. No real credentials.\n", flush=True)
            self.ensure_images()
            print("1/5  Start without permission", flush=True)
            self.start()
            self.wait(lambda: self.denied("demo-no-grant", "missing_grant"))
            self.steps.append("No grant: invoice access denied")
            print("     BLOCKED: no grant, no invoice reads.\n", flush=True)
            self.pause("Next: issue a bounded grant.")

            print("2/5  Issue permission to the running program", flush=True)
            issued = self.operator("grant")
            if issued["grant"]["max_requests"] != 20 or issued["grant"]["delegation"]:
                raise DemoError("The issued grant does not have the fixed MVP constraints")
            self.steps.append("Operator issued GET /invoices: 20 reads, 15 minutes, no delegation")
            print("     ALLOWED: GET /invoices, up to 20 reads, 15 minutes.\n", flush=True)

            print("3/5  Use the allowance", flush=True)
            self.wait(lambda: self.denied("demo-over-budget", "budget_exhausted"))
            if not self.denied("demo-delete", "out_of_scope"):
                raise DemoError("The forbidden delete was not denied")
            status = self.operator("status")
            if status["total_admissions"] != 20 or status["grant"]["remaining_requests"] != 0:
                raise DemoError("The read allowance did not stop at 20")
            self.steps.append("20 reads used; extra read and invoice deletion denied")
            print("     BLOCKED: read 21 exceeds the allowance. Deleting invoices is also denied.\n", flush=True)
            self.pause("Next: revoke the grant and stop this execution.")

            print("4/5  Revoke permission and stop", flush=True)
            if self.operator("revoke")["grant"]["state"] != "revoked":
                raise DemoError("Revocation did not commit")
            self.wait(lambda: self.denied("demo-after-revoke", "revoked"))
            if self.operator("stop")["running"]:
                raise DemoError("The execution did not stop")
            self.child.wait(timeout=30)
            self.run_stream.close()
            self.run_stream = None
            if self.child.returncode:
                raise DemoError("The supervisor did not finish successfully; see run.log")
            self.steps.append("Revoked: later access denied; operator stopped the execution")
            print("     STOPPED: the same program observed revocation and was terminated.\n", flush=True)

            print("5/5  Verify the audit trail", flush=True)
            facts = verify_evidence(self.export(), self.execution, source_hash, (self.output / "run.log").read_text())
            self.steps.append("Audit and independent mock API count match: exactly 20 reads")
        except (DemoError, OSError, ValueError, KeyError, subprocess.SubprocessError) as exception:
            error = str(exception)
        except (KeyboardInterrupt, EOFError):
            error = "Demonstration interrupted"
        finally:
            self.cleanup()
        if self.cleanup_errors:
            error = error or "The demonstration could not confirm cleanup"
        passed = error is None and len(self.steps) == 5 and facts is not None
        report = {"passed": passed, "execution_id": self.execution, "source_sha256": source_hash,
                  "steps": self.steps, "facts": facts, "error": error, "cleanup_errors": self.cleanup_errors,
                  "evidence": str(self.output), "demo_id": self.id, "state_owner": self.owner}
        save(self.output / "demo.json", report)
        save(self.output.parent / "latest.json", report)
        lines = ["# INVARIANT guided MVP demo", "", "Result: " + ("PASS" if passed else "INCOMPLETE"), "",
                 "One isolated Python execution using the fixed mock invoice grant.", "", "| Step | Observed result |", "|---|---|"]
        lines += [f"| {i} | {step} |" for i, step in enumerate(self.steps, 1)]
        if facts:
            lines += ["", f"Invoice summary: **{facts['invoices']} invoice, total {facts['total']}**.", "",
                      f"Broker admissions: **{facts['admissions']}**. Independent mock API requests: **{facts['target_requests']}**.", "",
                      f"[Full audit journal](evidence/{facts['run_directory']}/audit.jsonl) · [Execution result](evidence/{facts['run_directory']}/result.json)"]
            lines += ["", "## Audit trail", "", "| Sequence | Event | Result |", "|---|---|---|"]
            lines += [f"| {r['sequence']} | {r['event']} | {r['reason'] or ('read ' + str(r['ordinal']) if r['ordinal'] else '')} |" for r in facts["audit_trail"]]
        if error:
            lines += ["", "Details: " + error]
        lines += ["", "The evidence contains the actual Docker configuration and effective limits. This is a guided demonstration on the recorded local profile; run the full acceptance suite for A01–A20 verification."]
        with (self.output / "REPORT.md").open("w") as file:
            os.chmod(self.output / "REPORT.md", 0o600)
            file.write("\n".join(lines) + "\n")
        if passed:
            print("     VERIFIED: 1 invoice, total 42; exactly 20 API requests.\n     Audit: denied → granted → 20 reads → exhausted → revoked → stopped.\n\nDemo complete. The isolated program has been removed.", flush=True)
        else:
            print("\nDemo incomplete: " + error, file=sys.stderr)
        print("Report: " + str(self.output / "REPORT.md"), flush=True)
        return 0 if passed else 1


def main(*, step=False):
    if step and not sys.stdin.isatty():
        print("Step mode needs an interactive terminal; use demo without --step for automatic mode.", file=sys.stderr)
        return 1
    def interrupt(_signal, _frame):
        raise KeyboardInterrupt
    previous = signal.signal(signal.SIGTERM, interrupt)
    try:
        return Guide(step=step).run()
    finally:
        signal.signal(signal.SIGTERM, previous)


if __name__ == "__main__":
    sys.exit(main())
