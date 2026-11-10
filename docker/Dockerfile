FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY terra_classic_mcp/ ./terra_classic_mcp/
COPY server.py http_server.py ./
RUN pip install --no-cache-dir .

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
USER nobody
EXPOSE 8000
CMD ["uvicorn", "http_server:app", "--host", "0.0.0.0", "--port", "8000"]
