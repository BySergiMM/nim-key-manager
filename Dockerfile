# ---------- Build stage ----------
FROM python:3.12-slim AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install --prefix=/install .

# ---------- Runtime stage ----------
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN groupadd -r app && useradd -r -g app app
WORKDIR /srv
COPY --from=builder /install /usr/local
COPY alembic.ini ./
COPY alembic ./alembic
COPY scripts/entrypoint.sh ./entrypoint.sh
RUN chmod +x ./entrypoint.sh
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
  CMD ["python", "-c", "import os,sys,urllib.request;port=os.environ.get('PORT','8000');sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{port}/health',timeout=4).status==200 else 1)"]
ENTRYPOINT ["./entrypoint.sh"]
