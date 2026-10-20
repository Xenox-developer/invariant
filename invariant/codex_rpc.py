"""Bounded stdio client for the versioned Codex app-server protocol."""
import json
import os
import queue
from pathlib import Path
import sys
import signal
import subprocess
import threading
import time
import unicodedata

MAX_FRAME = 2 * 1024 * 1024


def inert(text):
    return "".join(c if c == "\n" or not unicodedata.category(c).startswith("C")
                   else "\\u{%x}" % ord(c) for c in text)


class RpcError(RuntimeError):
    def __init__(self, message, *, code=None, data=None):
        super().__init__(message)
        self.code, self.data = code, data


class RpcCancelled(RpcError):
    pass


class CodexRpc:
    def __init__(self, command, *, env, cwd, guard_parent=False):
        self.guard = None
        options = {}
        if guard_parent:
            descriptor, self.guard = os.pipe()
            package = str(Path(__file__).resolve().parent.parent)
            bootstrap = 'import sys;sys.path.insert(0,sys.argv.pop(1));from invariant.rpc_host import run;run()'
            command = [sys.executable, '-I', '-c', bootstrap, package, str(descriptor), *command]
            options['pass_fds'] = (descriptor,)
        try:
            self.process = subprocess.Popen(command, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                        cwd=cwd, env=env, start_new_session=True, **options)
        except BaseException:
            if self.guard is not None:
                os.close(self.guard); self.guard = None
            raise
        finally:
            if guard_parent:
                os.close(descriptor)
        self.messages = queue.Queue(maxsize=64)
        self.closing = threading.Event()
        self.counter = 0
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _put(self, message):
        while not self.closing.is_set():
            try:
                self.messages.put(message, timeout=0.2)
                return
            except queue.Full:
                pass

    def _read(self):
        try:
            while not self.closing.is_set():
                line = self.process.stdout.readline(MAX_FRAME + 1)
                if not line:
                    break
                if len(line) > MAX_FRAME or not line.endswith(b"\n"):
                    raise ValueError("oversized or incomplete Codex message")
                message = json.loads(line)
                if not isinstance(message, dict):
                    raise ValueError("invalid Codex message")
                self._put(message)
        except (ValueError, OSError) as error:
            self._put(RpcError(str(error)))
        finally:
            self._put(RpcError("Codex connection closed"))

    def send(self, message):
        data = (json.dumps(message, separators=(",", ":")) + "\n").encode()
        if len(data) > MAX_FRAME:
            raise RpcError("Codex request is too large")
        try:
            self.process.stdin.write(data)
            self.process.stdin.flush()
        except (BrokenPipeError, OSError) as error:
            raise RpcError("Codex connection closed") from error

    def receive(self, timeout=0.2):
        try:
            message = self.messages.get(timeout=timeout)
        except queue.Empty:
            return None
        if isinstance(message, Exception):
            raise message
        # No server request can expand the Docker profile or enable another tool.
        if "method" in message and "id" in message:
            method = message["method"]
            if method in ("item/commandExecution/requestApproval", "item/fileChange/requestApproval"):
                self.send({"id": message["id"], "result": {"decision": "decline"}})
            elif method == "item/permissions/requestApproval":
                self.send({"id": message["id"], "result": {"permissions": {}, "scope": "turn"}})
            else:
                self.send({"id": message["id"], "error": {"code": -32601,
                           "message": "This workspace does not expose that capability"}})
            return None
        return message

    def request(self, method, params, *, timeout=60, event=None, cancel=None):
        if cancel is not None and cancel.is_set():
            raise RpcCancelled('Codex request cancelled')
        self.counter += 1
        request_id = self.counter
        self.send({"id": request_id, "method": method, "params": params})
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if cancel is not None and cancel.is_set():
                raise RpcCancelled('Codex request cancelled')
            message = self.receive()
            if message is None:
                continue
            if message.get("id") == request_id and "method" not in message:
                if "error" in message:
                    raise RpcError("Codex " + method + ": " + inert(str(message["error"].get("message", "request failed"))),
                                   code=message["error"].get("code"), data=message["error"].get("data"))
                return message.get("result", {})
            if event:
                event(message)
        raise RpcError("Codex " + method + " timed out")

    def initialize(self, *, cancel=None, timeout=60):
        self.request("initialize", {"clientInfo": {"name": "invariant", "title": "INVARIANT", "version": "0.2.0"},
                                    "capabilities": {"experimentalApi": True}}, cancel=cancel, timeout=timeout)
        self.send({"method": "initialized", "params": {}})

    def close(self):
        self.closing.set()
        if self.guard is not None:
            os.close(self.guard); self.guard = None
        try:
            self.process.stdin.close()
        except OSError:
            pass
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(self.process.pid, signal.SIGTERM)
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait(timeout=3)
        self.reader.join(timeout=2)
        self.process.stdout.close()
