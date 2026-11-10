# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df

ARG VERSION=dev
ARG REVISION=unknown
ARG PACKAGE_VERSION=0.3.0
LABEL org.opencontainers.image.title="Rehearse" \
      org.opencontainers.image.description="Local Project Brain and recipe validation CLI" \
      org.opencontainers.image.source="https://github.com/Someone006/Restore-Rehearsal" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.version="$VERSION" \
      org.opencontainers.image.revision="$REVISION"

COPY dist/restore_rehearsal-${PACKAGE_VERSION}-py3-none-any.whl /tmp/restore_rehearsal-${PACKAGE_VERSION}-py3-none-any.whl
RUN --mount=type=secret,id=proxy_ca \
    set -eu; \
    if [ -f /run/secrets/proxy_ca ]; then export PIP_CERT=/run/secrets/proxy_ca; fi; \
    python -m pip install --no-cache-dir /tmp/restore_rehearsal-${PACKAGE_VERSION}-py3-none-any.whl; \
    rm -f /tmp/restore_rehearsal-${PACKAGE_VERSION}-py3-none-any.whl; \
    mkdir /work; \
    chown 65532:65532 /work

ENV HOME=/tmp \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /work
USER 65532:65532
ENTRYPOINT ["rehearse"]
CMD ["--help"]
