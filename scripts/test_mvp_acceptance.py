#!/usr/bin/env python3
"""Rebuild and verify the complete MVP on the local Docker execution profile.

Fails closed: all A01–A20 cases AND every supporting check must pass. No Docker
socket is exposed to workloads. Use ./scripts/invariant acceptance to run it.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent.parent


def matrix_passed(report):
    if not isinstance(report, dict) or report.get("accepted") is not True:
        return False
    cases = report.get("cases")
    if not isinstance(cases, list) or len(cases) != 20 or not all(isinstance(case, dict) for case in cases):
        return False
    if {case.get("id") for case in cases} != {f"A{i:02d}" for i in range(1, 21)}:
        return False
    return all(case.get("status") == "passed" and case.get("error") is None
               and case.get("setup") and case.get("expected") and isinstance(case.get("actual"), dict)
               for case in cases)


def source_hashes():
    sources = {}
    for directory in ("crates", "sdk", "scripts", "docker", "examples", "invariant"):
        for path in sorted((ROOT / directory).rglob("*")):
            if path.is_file() and not {"__pycache__", "target"} & set(path.parts) and path.suffix != ".pyc":
                sources[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
    for name in ("Cargo.toml", "Cargo.lock", "docs/LEGACY_MVP_CONTRACT.md", "pyproject.toml"):
        sources[name] = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
    return sources


def save(path, value):
    with path.open("w", encoding="utf-8") as file:
        os.chmod(path, 0o600)
        json.dump(value, file, indent=2)
        file.write("\n")


def main():
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8]
    output = ROOT / ".invariant" / "acceptance" / run_id
    output.mkdir(parents=True, mode=0o700)
    commands = []
    volume = "invariant-acceptance-" + run_id.lower()
    container = None
    daemon_path = None
    error = None
    results = None
    sources = source_hashes()
    save(output / "source-sha256.json", sources)

    def command(arguments, timeout=60, check=True):
        label = f"{len(commands) + 1:02d}"
        log = output / (label + ".log")
        started = time.monotonic()
        entry = {"command": arguments, "cwd": str(ROOT), "log": log.name}
        commands.append(entry)
        try:
            with log.open("w", encoding="utf-8") as stream:
                os.chmod(log, 0o600)
                result = subprocess.run(arguments, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT,
                                        text=True, timeout=timeout)
            entry["exit_code"] = result.returncode
        except (OSError, subprocess.TimeoutExpired) as exc:
            entry["exit_code"] = None
            entry["error"] = str(exc)
            raise
        finally:
            entry["elapsed_seconds"] = round(time.monotonic() - started, 3)
            save(output / "commands.json", commands)
        text = log.read_text(encoding="utf-8", errors="replace")
        if check and result.returncode != 0:
            print(text[-6000:], file=sys.stderr)
            raise RuntimeError("Required check failed: " + " ".join(arguments))
        return result.returncode, text

    try:
        print("Checking Rust and Python, then rebuilding the tested Docker images…", flush=True)
        _, active = command(["docker", "ps", "-q", "--filter", "label=io.invariant.role=control", "--filter", "volume=invariant-supervisor-state-v1"])
        if active.strip():
            raise RuntimeError("The normal INVARIANT launcher is active; finish that run before running the operator regression tests")
        command(["cargo", "fmt", "--all", "--", "--check"])
        command(["cargo", "clippy", "--locked", "--workspace", "--all-targets", "--", "-D", "warnings"], 180)
        command(["cargo", "test", "--locked", "--workspace", "--all-targets"], 180)
        command(["/usr/bin/env", "PYTHONPATH=" + str(ROOT / "sdk/python"), sys.executable, "-B", "-m", "unittest", "discover", "-s", "sdk/python/tests", "-v"], 60)
        command([sys.executable, "-B", "-m", "unittest", "discover", "-s", "scripts/tests", "-v"], 60)
        command([str(ROOT / "scripts/invariant"), "prepare"], 900)
        command(["docker", "version", "--format", "{{json .}}"])
        command(["docker", "info", "--format", "{{json .}}"])
        command(["docker", "image", "inspect", "invariant-python:local", "invariant-supervisor:local"])
        command(["docker", "volume", "create", "--label", "io.invariant.acceptance=" + run_id, volume])
        _, raw = command(["docker", "volume", "inspect", volume])
        daemon_path = json.loads(raw)[0]["Mountpoint"]
        _, raw = command([
            "docker", "create", "--network=none", "--pull=never",
            "--label", "io.invariant.role=acceptance-control", "--label", "io.invariant.acceptance=" + run_id,
            "--mount", "type=bind,src=/var/run/docker.sock,dst=/var/run/docker.sock",
            "--mount", f"type=volume,src={volume},dst=/s",
            "--entrypoint", "invariant-acceptance", "invariant-supervisor:local",
            "/s/suite", daemon_path + "/suite", "invariant-python:local",
        ])
        container = raw.strip()
        print("Running all 20 acceptance cases against real isolated workloads…", flush=True)
        code, text = command(["docker", "start", "--attach", container], 600, check=False)
        print("\n".join(line for line in text.splitlines() if line.startswith(("PASS ", "FAIL "))), flush=True)
        command(["docker", "cp", container + ":/export/.", str(output)])
        results = json.loads((output / "acceptance.json").read_text())
        if code != 0 or not matrix_passed(results):
            raise RuntimeError("A required acceptance case failed; see the case reports")
        # Preserve existing boundary tests as regressions. These are serial:
        # their operator/lifecycle fixtures deliberately share the normal state
        # volume and require no other user execution to be active there.
        print("Checking the existing Docker and operator regression scenarios…", flush=True)
        command([str(ROOT / "scripts/invariant"), "test"], 240)
        command([sys.executable, str(ROOT / "scripts/test_operator_controls.py")], 180)
        command([sys.executable, str(ROOT / "scripts/test_supervisor_lifecycle.py")], 180)
        command([sys.executable, str(ROOT / "scripts/test_guided_demo_e2e.py")], 240)
        demo_checks = json.loads((ROOT / ".invariant/guided-demo-checks.json").read_text())
        demo = demo_checks["successful_demo"]
        if demo.get("passed") is not True:
            raise RuntimeError("The guided MVP demonstration did not verify its evidence")
        shutil.copytree(Path(demo["evidence"]), output / "guided-demo")
        shutil.copy2(ROOT / ".invariant/guided-demo-checks.json", output / "guided-demo-checks.json")
        for source in ("operator-controls.json", "docker-lifecycle.json"):
            shutil.copy2(ROOT / ".invariant" / source, output / source)
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as exc:
        error = str(exc)
    except KeyboardInterrupt:
        error = "Acceptance run interrupted"
    finally:
        # Remove only this invocation's controller and workloads whose verified
        # mounts point into its unique, dedicated volume. Never broad-prune.
        try:
            if container:
                command(["docker", "rm", "--force", container])
            if daemon_path:
                _, raw = command(["docker", "ps", "-aq", "--filter", "label=io.invariant.role=workload"])
                for workload in raw.split():
                    _, raw_inspect = command(["docker", "inspect", workload])
                    metadata = json.loads(raw_inspect)[0]
                    if any(m.get("Source", "").startswith(daemon_path + "/suite/") for m in metadata["Mounts"]):
                        command(["docker", "rm", "--force", "--volumes", workload])
            if daemon_path:
                command(["docker", "volume", "rm", volume])
        except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as exc:
            error = error or "Acceptance cleanup failed: " + str(exc)

    # Snapshot hashes identify the exact source and contract tested. The
    # exported bundle deliberately excludes credentials and staged programs.
    if source_hashes() != sources:
        error = error or "Source changed during verification; run acceptance again against one fixed source snapshot"
    accepted = error is None and matrix_passed(results)
    summary = {"schema_version": 1, "run_id": run_id, "accepted": accepted,
               "contract": "docs/LEGACY_MVP_CONTRACT.md section 9",
               "required_cases": 20, "passed_cases": sum(c["status"] == "passed" for c in (results or {}).get("cases", [])),
               "supporting_checks_passed": error is None, "error": error, "evidence": str(output)}
    save(output / "summary.json", summary)
    save(ROOT / ".invariant/acceptance/latest.json", summary)
    rows = ["# INVARIANT MVP acceptance", "", "Result: " + ("PASS" if accepted else "FAIL"), "",
            f"Evidence: `{output}`", "", "| Contract case | Result |", "|---|---|"]
    rows += [f"| {c['id']} | {c['status']} |" for c in (results or {}).get("cases", [])]
    if error:
        rows += ["", "Failure: " + error]
    rows += ["", "See `commands.json` and numbered logs for exact commands/results. Each case contains its setup, expectation, independent target receipts, broker journals and Docker configuration. Passing applies to the recorded execution profile and probes; it does not establish production multi-tenant security."]
    (output / "REPORT.md").write_text("\n".join(rows) + "\n")
    os.chmod(output / "REPORT.md", 0o600)
    print(("MVP acceptance PASSED" if accepted else "MVP acceptance FAILED") + "\nEvidence: " + str(output), flush=True)
    if error:
        print(error, file=sys.stderr)
    return 0 if accepted else 1


if __name__ == "__main__":
    sys.exit(main())
