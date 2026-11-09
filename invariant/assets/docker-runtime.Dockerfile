FROM docker:27.4-cli@sha256:af0bcc63244e5110ac6499e4a9160a67101bf61abda35c2503ffc40038e8ee0a AS dockercli
FROM node:24.21.0-bookworm-slim@sha256:d6aa754f16b3197301076f047b5def2f02ea1dbbc2ca920407d46d7ec7f87b20 AS node
FROM invariant-codex-environment:0.163.0-v1
ARG TARGETARCH
COPY --from=dockercli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=node /usr/local/ /usr/local/
COPY package/assets/install_codex.py package/assets/codex-package-LICENSE /tmp/
RUN python3 /tmp/install_codex.py "$TARGETARCH" && cp /usr/local/bin/codex /__invariant/codex && cp /usr/local/bin/codex-code-mode-host /__invariant/codex-code-mode-host
RUN python3 -m venv /opt/agent-runtime && /opt/agent-runtime/bin/pip install --no-cache-dir websocket-client==1.8.0
COPY package/ /opt/invariant/invariant/
COPY legacy/ /opt/invariant/invariant/_legacy/
ENV PYTHONPATH=/opt/invariant PYTHONDONTWRITEBYTECODE=1 PATH=/opt/agent-runtime/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ENTRYPOINT ["python3", "-B", "-m", "invariant.storage_worker"]
