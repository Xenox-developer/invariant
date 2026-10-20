FROM python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
ARG TARGETARCH
RUN apt-get update && apt-get install -y --no-install-recommends bash git ca-certificates ripgrep && rm -rf /var/lib/apt/lists/*
COPY install_codex.py codex-package-LICENSE /tmp/
RUN python3 /tmp/install_codex.py "$TARGETARCH" && rm /tmp/install_codex.py
COPY project-dependencies.sh /etc/profile.d/invariant-dependencies.sh
RUN mkdir -p /workspace/.invariant /scratch /etc/codex && printf 'allowed_approval_policies = ["never"]\nallowed_sandbox_modes = ["danger-full-access"]\n[mcp_servers]\n[plugins]\n[marketplaces]\nrestrict_to_allowed_sources = true\n[marketplaces.allowed_sources]\n' > /etc/codex/requirements.toml
ENV HOME=/scratch CODEX_HOME=/scratch TMPDIR=/scratch PATH=/usr/local/bin:/usr/bin:/bin
USER 65532:65532
WORKDIR /workspace
ENTRYPOINT ["codex", "exec-server", "--listen", "stdio://"]
