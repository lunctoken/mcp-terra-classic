FROM python:3.12.16-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df AS builder

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY terra_classic_mcp/ ./terra_classic_mcp/
COPY server.py http_server.py ./
RUN python -m pip install --no-cache-dir uv==0.13.8 \
    && UV_CACHE_DIR=/tmp/uv-cache uv sync --frozen --no-dev --no-editable \
    && rm -rf /tmp/uv-cache

FROM python:3.12.16-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/http_server.py /app/http_server.py
COPY --from=builder /app/server.py /app/server.py
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER nobody
EXPOSE 8000
CMD ["uvicorn", "http_server:app", "--host", "0.0.0.0", "--port", "8000"]
