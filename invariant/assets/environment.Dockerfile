FROM rust:1.90-bookworm@sha256:3914072ca0c3b8aad871db9169a651ccfce30cf58303e5d6f2db16d1d8a7e58f AS inspector
COPY inspector.rs /build/inspector.rs
RUN rustc --edition=2021 -O -C target-feature=+crt-static /build/inspector.rs -o /build/inspect
FROM python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258 AS codex
ARG TARGETARCH
COPY install_codex.py codex-package-LICENSE /tmp/
RUN python3 /tmp/install_codex.py "$TARGETARCH"
FROM debian:bookworm-slim@sha256:7c7b2c966bc9ee8cedfeef67e0e279108992c77681fa595db4a9d65c06ccc587
RUN apt-get update && apt-get install -y --no-install-recommends bash ca-certificates curl git python3 python3-venv python3-pip xz-utils unzip && rm -rf /var/lib/apt/lists/*
COPY --from=codex /usr/local/bin/codex /__invariant/codex
COPY --from=codex /usr/local/share/invariant/codex-notices /usr/local/share/invariant/codex-notices
COPY --from=inspector /build/inspect /__invariant/inspect
RUN mkdir -p /workspace/.invariant /scratch /etc/codex
ENV HOME=/root CODEX_HOME=/scratch/codex TMPDIR=/scratch PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
USER 0:0
WORKDIR /workspace
ENTRYPOINT ["/__invariant/codex", "exec-server", "--listen", "stdio://"]
