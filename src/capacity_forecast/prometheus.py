"""Fetch usage and limit series from the Prometheus HTTP API.

Uses only ``GET /api/v1/query_range`` as documented at
https://prometheus.io/docs/prometheus/latest/querying/api/#range-queries.
Nothing is ever written to Prometheus.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from .loader import Sample


class PrometheusError(RuntimeError):
    """Prometheus could not be reached or returned an unusable answer."""


def _resource_name(metric: dict[str, str], label: str | None) -> str:
    if label:
        if label not in metric:
            raise PrometheusError(f"series {metric} has no label {label!r}")
        return metric[label]
    parts = [f'{k}="{v}"' for k, v in sorted(metric.items()) if k != "__name__"]
    return "{" + ",".join(parts) + "}" if parts else metric.get("__name__", "{}")


def query_range(
    base_url: str, query: str, start: float, end: float, step_seconds: float,
    label: str | None = None, timeout: float = 30.0,
) -> dict[str, list[tuple[float, float]]]:
    """Run one range query and return ``{resource name: [(unix time, value), ...]}``."""
    params = urllib.parse.urlencode(
        {"query": query, "start": f"{start:.3f}", "end": f"{end:.3f}", "step": f"{step_seconds:g}"}
    )
    url = base_url.rstrip("/") + "/api/v1/query_range?" + params
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = json.load(exc).get("error", "")
        except (ValueError, AttributeError):
            pass
        raise PrometheusError(f"Prometheus returned HTTP {exc.code} for {query!r}: {detail}".rstrip(": ")) from None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise PrometheusError(f"could not query {base_url}: {exc}") from None

    if payload.get("status") != "success":
        raise PrometheusError(f"query {query!r} failed: {payload.get('error', 'unknown error')}")
    data = payload.get("data", {})
    if data.get("resultType") != "matrix":
        raise PrometheusError(f"expected a matrix result, got {data.get('resultType')!r}")

    series: dict[str, list[tuple[float, float]]] = {}
    for item in data.get("result", []):
        name = _resource_name(item.get("metric", {}), label)
        if name in series:
            raise PrometheusError(
                f"query {query!r} returned more than one series named {name!r}; "
                "aggregate in the query or choose a different --resource-label"
            )
        points = []
        for stamp, value in item.get("values", []):
            number = float(value)
            if number == number and number not in (float("inf"), float("-inf")):
                points.append((float(stamp), number))
        series[name] = points
    return series


def fetch_samples(
    base_url: str, usage_query: str, limit_query: str, end: datetime,
    lookback_days: float, step_seconds: float, label: str | None = None, timeout: float = 30.0,
) -> list[Sample]:
    """Join a usage query and a limit query into samples.

    Series are matched by resource name. The limit at each usage timestamp is
    the limit series' value at the same step; if that step is missing, the
    most recent earlier limit is used.
    """
    end_ts = end.timestamp()
    start_ts = end_ts - lookback_days * 86400.0
    usage = query_range(base_url, usage_query, start_ts, end_ts, step_seconds, label, timeout)
    limits = query_range(base_url, limit_query, start_ts, end_ts, step_seconds, label, timeout)

    samples: list[Sample] = []
    for name, points in usage.items():
        limit_points = sorted(limits.get(name, []))
        if not limit_points:
            continue  # no limit known, nothing to forecast against
        index = 0
        current = limit_points[0][1]
        for stamp, used in sorted(points):
            while index < len(limit_points) and limit_points[index][0] <= stamp:
                current = limit_points[index][1]
                index += 1
            samples.append(
                Sample(name, datetime.fromtimestamp(stamp, tz=timezone.utc), used, current)
            )
    return samples
