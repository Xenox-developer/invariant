import base64
import json
import os
import socket
import struct
import tempfile
import threading
import unittest
from unittest.mock import patch

from invariant_client import (
    BrokerClient, MAX_BODY_BYTES, MAX_RESPONSE_BYTES, ProtocolError, TransportError,
)


def frame(payload):
    if isinstance(payload, dict):
        payload = json.dumps(payload).encode("utf-8")
    return struct.pack("!I", len(payload)) + payload


class FakeServer:
    def __init__(self, reply, fragment_size=None):
        self.directory = tempfile.TemporaryDirectory(prefix="iv-client-", dir="/tmp")
        self.path = os.path.join(self.directory.name, "s")
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener.bind(self.path)
        self.listener.listen(1)
        self.listener.settimeout(2)
        self.reply = reply
        self.fragment_size = fragment_size
        self.requests = []
        self.error = None
        self.thread = threading.Thread(target=self.serve, daemon=True)

    @staticmethod
    def read_exact(connection, length):
        data = bytearray()
        while len(data) < length:
            part = connection.recv(length - len(data))
            if not part:
                raise RuntimeError("Incomplete test request")
            data.extend(part)
        return bytes(data)

    def serve(self):
        try:
            connection, _ = self.listener.accept()
            with connection:
                connection.settimeout(2)
                length = struct.unpack("!I", self.read_exact(connection, 4))[0]
                if not 0 < length <= 16 * 1024:
                    raise RuntimeError("Client sent an oversized request")
                self.requests.append(json.loads(self.read_exact(connection, length)))
                try:
                    if self.fragment_size:
                        for offset in range(0, len(self.reply), self.fragment_size):
                            connection.sendall(self.reply[offset:offset + self.fragment_size])
                    else:
                        connection.sendall(self.reply)
                except (BrokenPipeError, ConnectionResetError):
                    pass  # Expected when a malformed response is rejected early.
        except Exception as error:
            self.error = error

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.thread.join(3)
        self.listener.close()
        self.directory.cleanup()
        if self.thread.is_alive():
            raise AssertionError("Fake server did not stop")
        if self.error:
            raise self.error


class ClientTests(unittest.TestCase):
    def test_same_client_refreshes_an_atomically_rotated_capability_file(self):
        reply = frame({"type":"denied", "reason":"revoked"})
        with tempfile.TemporaryDirectory(prefix="iv-credentials-") as directory:
            path = os.path.join(directory,"capability")
            with open(path,"w") as file:
                file.write("first-handle")
            with FakeServer(reply) as first:
                client = BrokerClient(first.path, capability_file=path)
                client.request("GET","/invoices")
            replacement = path + ".new"
            with open(replacement,"w") as file:
                file.write("second-handle")
            os.replace(replacement,path)
            with FakeServer(reply) as second:
                client.socket_path = second.path
                client.request("GET","/invoices")
            self.assertEqual(first.requests[0]["capability"],"first-handle")
            self.assertEqual(second.requests[0]["capability"],"second-handle")
            self.assertNotIn("handle",repr(client))

    def test_invalid_or_missing_capability_file_fails_before_connecting(self):
        with tempfile.TemporaryDirectory(prefix="iv-credentials-") as directory:
            path = os.path.join(directory,"capability")
            for data in (b"", b"x"*257, b"\xff"):
                with open(path,"wb") as file:
                    file.write(data)
                with self.assertRaises(ValueError):
                    BrokerClient("/unused/socket",capability_file=path)
            with open(path,"w") as file:
                file.write("valid")
            client = BrokerClient("/unused/socket",capability_file=path)
            os.unlink(path)
            with self.assertRaises(ValueError):
                client.request("GET","/invoices")

    def test_environment_file_takes_precedence_without_falling_back_on_error(self):
        with tempfile.TemporaryDirectory(prefix="iv-credentials-") as directory:
            path = os.path.join(directory,"capability")
            with open(path,"w") as file:
                file.write("from-file")
            with patch.dict(os.environ, {"INVARIANT_SOCKET":"/unused/socket", "INVARIANT_CAPABILITY":"ambient", "INVARIANT_CAPABILITY_FILE":path},clear=True):
                client = BrokerClient.from_env()
                self.assertEqual(client._current_capability(),"from-file")
                os.unlink(path)
                with self.assertRaises(ValueError):
                    BrokerClient.from_env()

    def test_binary_response_and_fragmented_frames(self):
        body = bytes([0, 255, 128, 10])
        reply = {"type": "response", "status": 200, "body_base64": base64.b64encode(body).decode("ascii")}
        with FakeServer(frame(reply), fragment_size=1) as server:
            client = BrokerClient(server.path, "private-handle")
            result = client.request("GET", "/invoices", request_id="chosen-id")
            self.assertEqual(result, {"type": "response", "status": 200, "body": body})
        self.assertEqual(server.requests, [{"request_id": "chosen-id", "capability": "private-handle", "method": "GET", "path": "/invoices"}])
        self.assertNotIn("private-handle", repr(client))

    def test_denials_replays_and_unknown_outcomes_are_preserved(self):
        for reply in [
            {"type": "denied", "reason": "revoked"},
            {"type": "replay", "outcome": {"state": "in_progress"}},
            {"type": "replay", "outcome": {"state": "completed", "status": 500}},
            {"type": "replay", "outcome": {"state": "unknown", "reason": "timeout"}},
            {"type": "unknown_outcome", "reason": None},
        ]:
            with self.subTest(reply=reply), FakeServer(frame(reply)) as server:
                self.assertEqual(BrokerClient(server.path, "handle").request("GET", "/invoices"), reply)
                self.assertEqual(len(server.requests), 1)

    def test_oversized_and_zero_length_declarations_are_rejected_without_payload(self):
        for length in [0, MAX_RESPONSE_BYTES + 1, 0xFFFFFFFF]:
            with self.subTest(length=length), FakeServer(struct.pack("!I", length)) as server:
                with self.assertRaises(ProtocolError):
                    BrokerClient(server.path, "handle").request("GET", "/invoices")

    def test_truncated_prefix_or_body_is_not_retried(self):
        for reply in [b"\x00\x00", struct.pack("!I", 10) + b"{}"]:
            with self.subTest(reply=reply), FakeServer(reply) as server:
                with self.assertRaises(ProtocolError):
                    BrokerClient(server.path, "handle").request("GET", "/invoices")
                self.assertEqual(len(server.requests), 1)

    def test_invalid_response_structures_are_rejected(self):
        payloads = [
            b'{"type":"denied","reason":"revoked","reason":"expired"}',
            b'{"type":"response","status":NaN,"body_base64":""}',
            b'{"type":"response","status":true,"body_base64":""}',
            b'{"type":"response","status":200,"body_base64":"not base64!"}',
            b'{"type":"denied","reason":"revoked","extra":true}',
            b'{"type":"allowed"}',
            b'{"type":"replay","outcome":{"state":"unknown","reason":"invented"}}',
            b'{"type":"unknown_outcome","reason":{}}',
            b'[]',
        ]
        for payload in payloads:
            with self.subTest(payload=payload), FakeServer(frame(payload)) as server:
                with self.assertRaises(ProtocolError):
                    BrokerClient(server.path, "handle").request("GET", "/invoices")

    def test_decoded_response_body_is_bounded(self):
        payload = {"type": "response", "status": 200, "body_base64": base64.b64encode(b"x" * (MAX_BODY_BYTES + 1)).decode("ascii")}
        self.assertLess(len(frame(payload)) - 4, MAX_RESPONSE_BYTES)
        with FakeServer(frame(payload)) as server:
            with self.assertRaises(ProtocolError):
                BrokerClient(server.path, "handle").request("GET", "/invoices")

    def test_request_validation_happens_before_connecting(self):
        client = BrokerClient("/unused/socket", "handle")
        for method, path, request_id in [("", "/invoices", "id"), ("GET", "/" * 2049, "id"), ("GET", "/invoices", ""), ("GET", "/invoices", "x" * 129)]:
            with self.subTest(method=method, path_length=len(path), request_id_length=len(request_id)):
                with self.assertRaises(ValueError):
                    client.request(method, path, request_id)
        for timeout in [0, -1, float("nan"), float("inf"), True]:
            with self.assertRaises(ValueError):
                BrokerClient("/unused/socket", "handle", timeout=timeout)

    def test_missing_socket_is_a_transport_error_without_a_retry(self):
        with tempfile.TemporaryDirectory(prefix="iv-missing-", dir="/tmp") as directory:
            with self.assertRaises(TransportError):
                BrokerClient(os.path.join(directory, "missing"), "handle").request("GET", "/invoices")


if __name__ == "__main__":
    unittest.main()
