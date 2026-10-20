"""Drive the real workload probe and SDK through controlled wire replies.

This reproduces a request carrying a capability read before a grant change;
safe InvalidCapability denials must not crash the acceptance workload.
"""
import base64
import json
import os
from pathlib import Path
import queue
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
try:
    from test_operator_controls import AUTHORITY_PROBES, live_audit_records, read_live_audit
finally:
    sys.path.pop(0)


def exact(stream, size):
    data = bytearray()
    while len(data) < size:
        chunk = stream.recv(size - len(data))
        if not chunk:
            raise AssertionError("incomplete workload request")
        data.extend(chunk)
    return bytes(data)


class DeliveryWindowTests(unittest.TestCase):
    def test_guided_demo_workload_observes_grant_budget_and_revocation(self):
        denied = lambda reason: {"type": "denied", "reason": reason}
        allowed = {"type": "response", "status": 200,
                   "body_base64": base64.b64encode(b'[{"amount":42}]').decode()}
        script = [
            ("placeholder", denied("missing_grant"), None),
            ("placeholder", denied("invalid_capability"), "first"),
            ("first", allowed, None),
            ("first", denied("out_of_scope"), None),
            *[("first", allowed, None) for _ in range(19)],
            ("first", denied("budget_exhausted"), None),
            ("first", {"type": "replay", "outcome": {"state": "completed", "status": 200}}, None),
            ("first", denied("revoked"), None),
            ("first", denied("revoked"), None),
        ]
        program = (ROOT / "examples/guided_mvp.py").read_text()
        requests = self.drive_program(program, script, "INVARIANT_DEMO_REVOKED", expect_exit=False)
        self.assertEqual(requests[-1]["request_id"], "demo-after-revoke")
        self.assertEqual(requests[0]["request_id"], "demo-no-grant")

    def test_docker_audit_reader_preserves_the_last_complete_record(self):
        # Mock only the external CLI boundary, retaining the real adapter and
        # parser. Trimming this newline hides the last budget-exhausted event.
        with patch("subprocess.check_output", return_value='{"event":"denied","reason":"budget_exhausted"}\n'):
            self.assertEqual(read_live_audit("fixture-control", "fixture-run"),
                             [{"event": "denied", "reason": "budget_exhausted"}])

    def test_live_audit_observer_waits_for_complete_record_and_rejects_corruption(self):
        self.assertEqual(live_audit_records('{"event":"admitted"}\n{"event":"response'), [{"event": "admitted"}])
        self.assertEqual(live_audit_records('{"event":"admitted"}\n{"event":"response_received"}\n'),
                         [{"event": "admitted"}, {"event": "response_received"}])
        with self.assertRaises(ValueError):
            live_audit_records('{}\n{not-json}\n')

    def test_live_probe_survives_first_grant_and_replacement_delivery_windows(self):
        denied = lambda reason: {"type": "denied", "reason": reason}
        allowed = {"type": "response", "status": 200,
                   "body_base64": base64.b64encode(b'[{"amount":42}]').decode()}
        script = [
            ("placeholder", denied("missing_grant"), None),
            ("placeholder", denied("invalid_capability"), "first"),
            ("first", allowed, None),
            ("first", {"type": "replay", "outcome": {"state": "completed", "status": 200}}, None),
            ("first", denied("revoked"), None),
            ("first", denied("revoked"), None),
            ("first", denied("invalid_capability"), "second"),
            ("second", allowed, None),
            ("first", denied("invalid_capability"), None),
            ("second", denied("request_conflict"), None),
            *[("second", allowed, None) for _ in range(19)],
            ("second", denied("budget_exhausted"), None),
        ]
        received = self.drive_program(AUTHORITY_PROBES, script, "Replacement:", expect_exit=False)
        self.assertNotEqual(received[1]["request_id"], received[2]["request_id"])
        self.assertNotEqual(received[6]["request_id"], received[7]["request_id"])

    def test_documented_live_grant_demo_survives_capability_delivery(self):
        script = [
            ("placeholder", {"type": "denied", "reason": "missing_grant"}, None),
            ("placeholder", {"type": "denied", "reason": "invalid_capability"}, "first"),
            ("first", {"type": "response", "status": 200,
                       "body_base64": base64.b64encode(b'[{"amount":42}]').decode()}, None),
            ("first", {"type": "denied", "reason": "revoked"}, None),
        ]
        program = (ROOT / "examples/live_grants.py").read_text()
        received = self.drive_program(program, script, "After revocation:", expect_exit=True)
        self.assertEqual(len({r["request_id"] for r in received}), 4)

    def drive_program(self, program, script, marker, *, expect_exit):
        errors = queue.Queue()
        output = queue.Queue()
        stopped = threading.Event()
        received = []
        with tempfile.TemporaryDirectory(prefix="iv-delivery-", dir="/tmp") as directory:
            endpoint = str(Path(directory) / "s")
            capability = Path(directory) / "capability"
            capability.write_text("placeholder")
            listener = socket.socket(socket.AF_UNIX)
            listener.bind(endpoint)
            listener.listen(4)
            listener.settimeout(.05)

            def serve():
                try:
                    for expected_cap, reply, publish in script:
                        while not stopped.is_set():
                            try:
                                connection, _ = listener.accept()
                                break
                            except TimeoutError:
                                continue
                        else:
                            return
                        with connection:
                            connection.settimeout(2)
                            length = struct.unpack("!I", exact(connection, 4))[0]
                            self.assertLessEqual(length, 16384)
                            request = json.loads(exact(connection, length))
                            self.assertEqual(request["capability"], expected_cap)
                            received.append(request)
                            if publish:
                                capability.write_text(publish)
                            payload = json.dumps(reply).encode()
                            connection.sendall(struct.pack("!I", len(payload)) + payload)
                except BaseException as error:
                    errors.put(error)

            server = threading.Thread(target=serve, daemon=True)
            server.start()
            environment = dict(os.environ, PYTHONPATH=str(ROOT / "sdk/python"),
                               INVARIANT_SOCKET=endpoint, INVARIANT_CAPABILITY_FILE=str(capability))
            child = subprocess.Popen([sys.executable, "-u", "-B", "-c", program],
                                     env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            reader = threading.Thread(target=lambda: [output.put(line) for line in child.stdout], daemon=True)
            reader.start()
            lines = []
            try:
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    try:
                        lines.append(output.get(timeout=.05))
                    except queue.Empty:
                        pass
                    if any(line.startswith(marker) for line in lines):
                        break
                    if child.poll() is not None:
                        reader.join(timeout=1)
                        while not output.empty():
                            lines.append(output.get())
                        break
                self.assertTrue(errors.empty(), list(errors.queue))
                self.assertTrue(any(line.startswith(marker) for line in lines), "".join(lines))
                if expect_exit:
                    self.assertEqual(child.wait(timeout=3), 0, "".join(lines))
                else:
                    self.assertIsNone(child.poll(), "probe crashed instead of awaiting operator stop")
                self.assertEqual(len(received), len(script))
            finally:
                stopped.set()
                child.terminate()
                child.wait(timeout=3)
                server.join(timeout=2)
                reader.join(timeout=2)
                child.stdout.close()
                listener.close()
        return received


if __name__ == "__main__":
    unittest.main()
