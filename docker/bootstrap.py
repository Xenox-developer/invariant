"""Trusted image entry point. Check the effective profile before executing code."""
import json
import os
import socket
import sys


def check():
    assert os.getuid() == os.geteuid() == 65532
    status = dict(line.split(":", 1) for line in open("/proc/self/status") if ":" in line)
    assert int(status["CapEff"].strip(), 16) == 0
    assert status["NoNewPrivs"].strip() == "1"
    assert status["Seccomp"].strip() == "2"
    for domain in (socket.AF_INET, socket.AF_INET6, socket.AF_NETLINK, socket.AF_PACKET):
        try:
            s = socket.socket(domain, socket.SOCK_DGRAM)
        except PermissionError:
            continue
        s.close()
        raise RuntimeError("network socket unexpectedly permitted")
    limits = {name: open("/sys/fs/cgroup/" + name).read().strip() for name in (
        "memory.max", "memory.swap.max", "pids.max", "cpu.max")}
    assert limits["memory.max"] == "268435456"
    assert limits["memory.swap.max"] == "0"
    assert limits["pids.max"] == "32"
    quota, period = map(int, limits["cpu.max"].split())
    assert quota > 0 and quota <= period
    for path in ("/", "/dev", "/etc/hosts", "/etc/hostname", "/etc/resolv.conf", "/input/program.py", "/authority"):
        assert os.statvfs(path).f_flag & os.ST_RDONLY, path
    scratch = os.statvfs("/scratch")
    assert scratch.f_blocks * scratch.f_frsize <= 67108864
    assert not scratch.f_flag & os.ST_RDONLY
    assert not os.path.exists("/dev/shm")
    with open("/scratch/probe", "xb") as f:
        f.write(b"ok")
    os.unlink("/scratch/probe")
    s = socket.socket(socket.AF_UNIX)
    s.connect(os.environ["INVARIANT_SOCKET"])
    s.close()
    from invariant_client import BrokerClient
    BrokerClient.from_env()  # The capability file must be bounded and readable.
    return {"limits": limits, "scratch_bytes": scratch.f_blocks * scratch.f_frsize,
            "uid": os.getuid(), "seccomp": 2, "cap_effective": status["CapEff"].strip(),
            "no_new_privileges": 1, "network_sockets": "denied",
            "namespaces": {n: os.readlink("/proc/self/ns/" + n) for n in (
                "mnt", "pid", "net", "ipc", "uts", "cgroup")}}


try:
    evidence = check()
    print(json.dumps({"preflight": evidence}, separators=(",", ":")), flush=True)
    # The supervisor saves evidence before releasing the untrusted program.
    if sys.stdin.buffer.read(1) != b"G":
        raise RuntimeError("supervisor did not release execution")
    null = os.open("/dev/null", os.O_RDONLY)
    os.dup2(null, 0)
    os.close(null)
    os.execv(sys.executable, [sys.executable, "-S", "-B", "/input/program.py"])
except Exception as error:
    print(f"INVARIANT isolation preflight failed: {type(error).__name__}: {error}", file=sys.stderr, flush=True)
    sys.exit(125)
