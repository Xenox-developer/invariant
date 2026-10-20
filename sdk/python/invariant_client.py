"""Standard-library client for the INVARIANT Unix socket protocol.

No automatic retries: a disconnected call may already have reached its target.
This client is a convenience; all authorization remains in the Rust broker.
"""

import base64
import binascii
import json
import math
import os
import socket
import struct
import time
import uuid


MAX_REQUEST_BYTES = 16 * 1024
MAX_BODY_BYTES = 1_048_576
MAX_RESPONSE_BYTES = ((MAX_BODY_BYTES + 2) // 3) * 4 + 1024

DENIAL_REASONS = frozenset({
    "invalid_request", "invalid_execution", "invalid_capability", "missing_grant",
    "wrong_resource", "out_of_scope", "expired", "revoked", "budget_exhausted",
    "state_unavailable", "request_conflict", "capacity_exceeded",
})
UPSTREAM_REASONS = frozenset({
    "transport", "timeout", "invalid_response", "headers_too_large", "body_too_large",
    "unsupported_encoding", "unsupported_transfer_encoding",
})


class ProtocolError(RuntimeError):
    """An invalid or oversized broker response; never retried automatically."""


class TransportError(RuntimeError):
    """A failed connection whose remote outcome may be unknown."""


def _text(value, name, limit):
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > limit:
        raise ValueError("Invalid length or type for " + name)
    return value


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Broker deadline expired")
    return remaining


def _read_exact(connection, length, deadline):
    data = bytearray()
    while len(data) < length:
        connection.settimeout(_remaining(deadline))
        chunk = connection.recv(min(length - len(data), 64 * 1024))
        if not chunk:
            raise ProtocolError("Broker closed an incomplete response; outcome may be unknown")
        data.extend(chunk)
    return bytes(data)


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError("Duplicate response field")
        result[key] = value
    return result


def _invalid_constant(_value):
    raise ProtocolError("Nonstandard JSON value")


def _keys(value, expected):
    if type(value) is not dict or set(value) != set(expected):
        raise ProtocolError("Unexpected response structure")


def _status(value):
    if type(value) is not int or not 200 <= value <= 599:
        raise ProtocolError("Invalid HTTP status")


def _upstream_reason(value):
    if value is not None and (type(value) is not str or value not in UPSTREAM_REASONS):
        raise ProtocolError("Invalid upstream reason")


def _decode(payload):
    try:
        reply = json.loads(payload.decode("utf-8"), object_pairs_hook=_object,
                           parse_constant=_invalid_constant)
        if type(reply) is not dict:
            raise ProtocolError("Expected a response object")
        kind = reply.get("type")
        if kind == "response":
            _keys(reply, {"type", "status", "body_base64"})
            _status(reply["status"])
            if type(reply["body_base64"]) is not str:
                raise ProtocolError("Invalid response body")
            body = base64.b64decode(reply["body_base64"], validate=True)
            if len(body) > MAX_BODY_BYTES:
                raise ProtocolError("Response body exceeds 1 MiB")
            return {"type": kind, "status": reply["status"], "body": body}
        if kind == "denied":
            _keys(reply, {"type", "reason"})
            if type(reply["reason"]) is not str or reply["reason"] not in DENIAL_REASONS:
                raise ProtocolError("Invalid denial reason")
        elif kind == "unknown_outcome":
            _keys(reply, {"type", "reason"})
            _upstream_reason(reply["reason"])
        elif kind == "replay":
            _keys(reply, {"type", "outcome"})
            outcome = reply["outcome"]
            if type(outcome) is not dict:
                raise ProtocolError("Invalid replay outcome")
            if outcome.get("state") == "in_progress":
                _keys(outcome, {"state"})
            elif outcome.get("state") == "completed":
                _keys(outcome, {"state", "status"})
                _status(outcome["status"])
            elif outcome.get("state") == "unknown":
                _keys(outcome, {"state", "reason"})
                _upstream_reason(outcome["reason"])
            else:
                raise ProtocolError("Invalid replay state")
        else:
            raise ProtocolError("Unknown response type")
        return reply
    except (UnicodeError, json.JSONDecodeError, binascii.Error, ValueError, RecursionError) as error:
        raise ProtocolError("Malformed broker response") from error


class BrokerClient:
    """One execution's endpoint and capability; each request opens a connection.

    Reconnecting preserves the broker's shared budget and request-ID ledger.
    A duplicate returns recorded status, not another copy of the response body.
    """

    def __init__(self, socket_path, capability=None, timeout=10.0, *, capability_file=None):
        if not isinstance(socket_path, str) or not socket_path or "\x00" in socket_path:
            raise ValueError("Invalid socket path")
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Timeout must be positive and finite")
        self.socket_path = socket_path
        if capability_file is not None:
            if capability is not None or not isinstance(capability_file, str) or not capability_file or "\x00" in capability_file:
                raise ValueError("Provide a capability or a capability file")
            self._capability_file = capability_file
            self._capability = None
            self._current_capability()  # Fail before connecting if delivery is invalid.
        else:
            self._capability_file = None
            self._capability = _text(capability, "capability", 256)
        self.timeout = timeout

    @classmethod
    def from_env(cls):
        try:
            if "INVARIANT_CAPABILITY_FILE" in os.environ:
                return cls(os.environ["INVARIANT_SOCKET"], capability_file=os.environ["INVARIANT_CAPABILITY_FILE"])
            return cls(os.environ["INVARIANT_SOCKET"], os.environ["INVARIANT_CAPABILITY"])
        except KeyError:
            raise ValueError("INVARIANT_SOCKET and a capability source are required") from None

    def _current_capability(self):
        if self._capability_file is None:
            return self._capability
        try:
            with open(self._capability_file, "rb") as file:
                data = file.read(257)
            return _text(data.decode("utf-8"), "capability", 256)
        except (OSError, UnicodeError, ValueError):
            raise ValueError("Capability file is unavailable or invalid") from None

    def __repr__(self):
        return "BrokerClient(socket_path={!r}, capability='[redacted]')".format(self.socket_path)

    def request(self, method, path, request_id=None):
        request_id = uuid.uuid4().hex if request_id is None else request_id
        message = {
            "request_id": _text(request_id, "request_id", 128),
            "capability": self._current_capability(),
            "method": _text(method, "method", 16),
            "path": _text(path, "path", 2048),
        }
        payload = json.dumps(message, separators=(",", ":")).encode("utf-8")
        if len(payload) > MAX_REQUEST_BYTES:
            raise ValueError("Request exceeds 16 KiB")
        deadline = time.monotonic() + self.timeout
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(_remaining(deadline))
                connection.connect(self.socket_path)
                connection.settimeout(_remaining(deadline))
                connection.sendall(struct.pack("!I", len(payload)) + payload)
                length = struct.unpack("!I", _read_exact(connection, 4, deadline))[0]
                if length == 0 or length > MAX_RESPONSE_BYTES:
                    raise ProtocolError("Invalid response frame length")
                return _decode(_read_exact(connection, length, deadline))
        except OSError:
            raise TransportError("Broker connection failed; outcome may be unknown. No retry was attempted.") from None
