The upstream seccomp profile is vendored from moby/profiles commit
4b8214fc4971e06172938a1f1554ccff6647ee80, seccomp/default.json.
See seccomp-runtime-LICENSE. The exact upstream file is retained for comparison.

The environment profiles replace socket/socketpair allow rules with Unix-domain
sockets, and (for internet mode) IPv4/IPv6 TCP and UDP sockets. Other rules remain
the upstream Docker general-purpose profile, including capability conditions.
