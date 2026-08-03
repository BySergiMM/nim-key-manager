# syntax=docker/dockerfile:1
# ---------- Build stage ----------
FROM python:3.12-slim AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY pyproject.toml README.md ./
# The package carries its own Alembic migrations (app/migrations), so this is
# everything the runtime needs.
COPY app ./app
RUN pip install --prefix=/install .

# ---------- Runtime stage ----------
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 NIMKM_HOME=/data
LABEL org.opencontainers.image.title="NIM Key Manager" \
      org.opencontainers.image.description="Self-hosted manager for your own NVIDIA Build/NIM API keys, with a Claude MCP connector." \
      org.opencontainers.image.source="https://github.com/BySergiMM/nim-key-manager" \
      org.opencontainers.image.licenses="MIT"
RUN groupadd -r app && useradd -r -g app app
WORKDIR /srv
COPY --from=builder /install /usr/local
COPY scripts/entrypoint.sh ./entrypoint.sh
RUN chmod +x ./entrypoint.sh && mkdir -p /data && chown -R app:app /data
USER app
# Anonymous volume by default so a bare `docker run` does not lose the generated
# secrets (and therefore the ability to decrypt stored keys) on restart.
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
  CMD ["python", "-c", "import os,sys,urllib.request;port=os.environ.get('PORT','8000');sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{port}/health',timeout=4).status==200 else 1)"]
ENTRYPOINT ["./entrypoint.sh"]
