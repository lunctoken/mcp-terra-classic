FROM python:3.15.0-slim-bookworm@sha256:0e019bbdfb5d9874cb2389f1c2884b64aa2ac97c99960b5bc521cef978c0617d AS builder

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY terra_classic_mcp/ ./terra_classic_mcp/
COPY server.py http_server.py ./
RUN python -m pip install --no-cache-dir uv==0.13.8 \
    && UV_CACHE_DIR=/tmp/uv-cache uv sync --frozen --no-dev --no-editable \
    && rm -rf /tmp/uv-cache

FROM python:3.15.0-slim-bookworm@sha256:0e019bbdfb5d9874cb2389f1c2884b64aa2ac97c99960b5bc521cef978c0617d
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
