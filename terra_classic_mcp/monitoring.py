"""Low-cardinality, payload-free process metrics."""

from __future__ import annotations

from collections import Counter
import logging
import sys
from threading import Lock


logger = logging.getLogger("terra_classic_mcp")
if not logger.handlers:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter('%(message)s'))
    logger.addHandler(handler)
logger.setLevel(logging.WARNING)
logger.propagate = False


class Metrics:
    def __init__(self):
        self._lock = Lock()
        self._http = Counter()
        self._tool_errors = Counter()
        self._duration_sum = 0.0

    def http_request(self, status: int, duration: float) -> None:
        with self._lock:
            self._http[status] += 1
            self._duration_sum += max(0.0, duration)

    def tool_error(self, category: str) -> None:
        if category not in {"invalid_or_lcd", "unexpected"}:
            raise ValueError("Unknown metric category")
        with self._lock:
            self._tool_errors[category] += 1

    def prometheus(self) -> bytes:
        with self._lock:
            http = dict(self._http)
            errors = dict(self._tool_errors)
            duration = self._duration_sum
        lines = [
            "# TYPE terra_mcp_http_requests_total counter",
            *(f'terra_mcp_http_requests_total{{status="{status}"}} {count}' for status, count in sorted(http.items())),
            "# TYPE terra_mcp_http_request_duration_seconds_sum counter",
            f"terra_mcp_http_request_duration_seconds_sum {duration:.6f}",
            "# TYPE terra_mcp_tool_errors_total counter",
            *(f'terra_mcp_tool_errors_total{{category="{category}"}} {count}' for category, count in sorted(errors.items())),
        ]
        return ("\n".join(lines) + "\n").encode()


METRICS = Metrics()
