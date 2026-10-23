FROM python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
COPY sdk/python/invariant_client.py /opt/invariant/invariant_client.py
COPY docker/bootstrap.py /opt/invariant/bootstrap.py
RUN chmod 0444 /opt/invariant/invariant_client.py /opt/invariant/bootstrap.py && mkdir -p /input /broker /scratch /authority && touch /input/program.py /broker/s
ENV PYTHONPATH=/opt/invariant PYTHONUTF8=1 PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/scratch TMPDIR=/scratch
USER 65532:65532
WORKDIR /scratch
ENTRYPOINT ["python3", "-S", "-B", "/opt/invariant/bootstrap.py"]
