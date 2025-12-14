"""Low-overhead, process-local API request measurements."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock
from time import monotonic


@dataclass(slots=True)
class _RouteMetric:
    count: int = 0
    errors: int = 0
    total_duration_ms: float = 0
    maximum_duration_ms: float = 0


class RuntimeMetrics:
    """Collect bounded aggregates without retaining request content."""

    def __init__(self) -> None:
        self.started_at = datetime.now(UTC)
        self._started_monotonic = monotonic()
        self._lock = Lock()
        self._in_flight = 0
        self._routes: dict[tuple[str, str], _RouteMetric] = {}

    def start_request(self) -> float:
        with self._lock:
            self._in_flight += 1
        return monotonic()

    def finish_request(
        self,
        method: str,
        route: str,
        status_code: int,
        started: float,
    ) -> None:
        duration_ms = max(0.0, (monotonic() - started) * 1_000)
        key = (method, route)
        with self._lock:
            self._in_flight = max(0, self._in_flight - 1)
            metric = self._routes.setdefault(key, _RouteMetric())
            metric.count += 1
            metric.errors += int(status_code >= 500)
            metric.total_duration_ms += duration_ms
            metric.maximum_duration_ms = max(metric.maximum_duration_ms, duration_ms)

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            routes = [
                {
                    "method": method,
                    "route": route,
                    "count": metric.count,
                    "errors": metric.errors,
                    "average_duration_ms": round(
                        metric.total_duration_ms / metric.count, 3
                    ),
                    "maximum_duration_ms": round(metric.maximum_duration_ms, 3),
                }
                for (method, route), metric in sorted(self._routes.items())
            ]
            requests_total = sum(metric.count for metric in self._routes.values())
            errors_total = sum(metric.errors for metric in self._routes.values())
            in_flight = self._in_flight
        return {
            "started_at": self.started_at,
            "uptime_seconds": round(monotonic() - self._started_monotonic, 3),
            "requests_total": requests_total,
            "requests_in_flight": in_flight,
            "errors_total": errors_total,
            "routes": routes,
        }
