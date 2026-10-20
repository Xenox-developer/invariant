"""Keep a native app-server in its owner's process group until the guard pipe closes."""
import os
import signal
import subprocess
import sys
import threading


def run():
    descriptor = int(sys.argv[1])
    def guard():
        try:
            while os.read(descriptor, 1):
                pass
        finally:
            os.killpg(os.getpgrp(), signal.SIGKILL)
    threading.Thread(target=guard, daemon=True).start()
    child = subprocess.Popen(sys.argv[2:], stdin=sys.stdin.buffer, stdout=sys.stdout.buffer,
                             stderr=sys.stderr.buffer, close_fds=True)
    raise SystemExit(child.wait())
