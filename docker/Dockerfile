FROM python:3.15.2-slim-bookworm@sha256:96599df822d0e1c6bb700014a05c7f3c8cabc6e0f7ff3c4f9f69cee033b4b5d9 AS builder

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY terra_classic_mcp/ ./terra_classic_mcp/
COPY server.py http_server.py ./
RUN python -m pip install --no-cache-dir uv==0.13.8 \
    && UV_CACHE_DIR=/tmp/uv-cache uv sync --frozen --no-dev --no-editable \
    && rm -rf /tmp/uv-cache

FROM python:3.15.2-slim-bookworm@sha256:96599df822d0e1c6bb700014a05c7f3c8cabc6e0f7ff3c4f9f69cee033b4b5d9
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
