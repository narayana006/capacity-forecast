"""Linear trend fit and time-to-exhaustion estimate.

For each resource an ordinary least-squares line is fitted to ``used`` over
time. The line is extended until it crosses the resource's current limit. The
range comes from the uncertainty in the fitted growth rate: the slope's
confidence interval gives a faster and a slower line, both pivoting on the
centre of the data, and each is extended to the limit the same way.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import NormalDist
from typing import Sequence

from .loader import Sample

SECONDS_PER_DAY = 86400.0

# Status values, in the order they are ranked.
EXHAUSTED = "exhausted"  # already at or over the limit
AT_RISK = "at risk"  # projected to hit the limit within the horizon
WATCH = "watch"  # not projected within the horizon, but the fast estimate is
OK = "ok"  # growing, but the limit is beyond the horizon
STABLE = "stable"  # flat or shrinking
NO_DATA = "insufficient data"


@dataclass(frozen=True)
class Forecast:
    resource: str
    status: str
    samples: int
    last_time: datetime
    used: float  # most recent sample
    limit: float  # most recent limit
    utilisation: float  # used / limit, 0-1 (can exceed 1)
    growth_per_day: float | None = None
    growth_low: float | None = None  # slower end of the confidence interval
    growth_high: float | None = None  # faster end
    days_left: float | None = None  # None means the trend never reaches the limit
    days_earliest: float | None = None
    days_latest: float | None = None  # None means no upper bound
    r_squared: float | None = None

    def date_at(self, days: float | None) -> datetime | None:
        if days is None:
            return None
        try:
            return self.last_time + timedelta(days=days)
        except OverflowError:
            return None

    @property
    def exhaustion_date(self) -> datetime | None:
        return self.date_at(self.days_left)


@dataclass(frozen=True)
class Fit:
    slope: float  # units per day
    slope_stderr: float
    mean_x: float  # days since the first sample
    mean_y: float
    r_squared: float


def fit_line(xs: Sequence[float], ys: Sequence[float]) -> Fit:
    """Least-squares line through (xs, ys). Needs three points and some spread in x."""
    n = len(xs)
    if n < 3:
        raise ValueError("need at least three points to fit a trend")
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    sxx = sum((x - mean_x) ** 2 for x in xs)
    if sxx == 0:
        raise ValueError("all samples share one timestamp")
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    syy = sum((y - mean_y) ** 2 for y in ys)
    slope = sxy / sxx
    residual = max(syy - slope * sxy, 0.0)
    stderr = math.sqrt(residual / (n - 2) / sxx)
    r_squared = 1.0 if syy == 0 else max(0.0, 1.0 - residual / syy)
    return Fit(slope, stderr, mean_x, mean_y, r_squared)


def _days_until(limit: float, slope: float, fit: Fit, last_x: float) -> float | None:
    """Days after the last sample at which a line of this slope reaches the limit."""
    if slope <= 0:
        return None
    crossing = fit.mean_x + (limit - fit.mean_y) / slope
    return max(crossing - last_x, 0.0)


def forecast_resource(
    resource: str,
    samples: Sequence[Sample],
    horizon_days: float,
    confidence: float = 0.95,
    window_days: float | None = None,
) -> Forecast:
    ordered = sorted(samples, key=lambda s: s.time)
    last = ordered[-1]
    if window_days is not None:
        cutoff = last.time - timedelta(days=window_days)
        ordered = [s for s in ordered if s.time >= cutoff]
    utilisation = last.used / last.limit if last.limit > 0 else float("inf")
    base = dict(
        resource=resource, samples=len(ordered), last_time=last.time,
        used=last.used, limit=last.limit, utilisation=utilisation,
    )

    origin = ordered[0].time
    xs = [(s.time - origin).total_seconds() / SECONDS_PER_DAY for s in ordered]
    ys = [s.used for s in ordered]
    try:
        fit = fit_line(xs, ys)
    except ValueError:
        status = EXHAUSTED if last.used >= last.limit else NO_DATA
        return Forecast(status=status, **base)

    z = NormalDist().inv_cdf(0.5 + confidence / 2.0)
    low = fit.slope - z * fit.slope_stderr
    high = fit.slope + z * fit.slope_stderr
    trend = dict(growth_per_day=fit.slope, growth_low=low, growth_high=high, r_squared=fit.r_squared)

    if last.used >= last.limit:
        return Forecast(status=EXHAUSTED, days_left=0.0, days_earliest=0.0, days_latest=0.0,
                        **base, **trend)

    days = _days_until(last.limit, fit.slope, fit, xs[-1])
    earliest = _days_until(last.limit, high, fit, xs[-1])
    latest = _days_until(last.limit, low, fit, xs[-1])
    if days is None:
        status = WATCH if earliest is not None and earliest <= horizon_days else STABLE
    elif days <= horizon_days:
        status = AT_RISK
    elif earliest is not None and earliest <= horizon_days:
        status = WATCH
    else:
        status = OK
    return Forecast(status=status, days_left=days, days_earliest=earliest, days_latest=latest,
                    **base, **trend)


_STATUS_RANK = {EXHAUSTED: 0, AT_RISK: 1, WATCH: 2, OK: 3, STABLE: 4, NO_DATA: 5}


def forecast_all(
    samples: Sequence[Sample],
    horizon_days: float,
    confidence: float = 0.95,
    window_days: float | None = None,
) -> list[Forecast]:
    """Forecast every resource and rank them, soonest to run out first."""
    if not 0 < confidence < 1:
        raise ValueError("confidence must be between 0 and 1 (exclusive)")
    if horizon_days <= 0:
        raise ValueError("horizon must be positive")
    if window_days is not None and window_days <= 0:
        raise ValueError("window must be positive")

    by_resource: dict[str, list[Sample]] = defaultdict(list)
    for sample in samples:
        by_resource[sample.resource].append(sample)

    results = [
        forecast_resource(name, group, horizon_days, confidence, window_days)
        for name, group in by_resource.items()
    ]
    results.sort(
        key=lambda f: (
            _STATUS_RANK[f.status],
            f.days_left if f.days_left is not None else math.inf,
            -f.utilisation,
            f.resource,
        )
    )
    return results


_UNITS = {"h": 1 / 24.0, "d": 1.0, "w": 7.0}


def parse_duration(text: str) -> float:
    """Turn '60d', '12h' or '2w' into days. A bare number means days."""
    text = text.strip().lower()
    unit = 1.0
    if text and text[-1] in _UNITS:
        unit = _UNITS[text[-1]]
        text = text[:-1]
    try:
        value = float(text)
    except ValueError:
        raise ValueError("duration must look like 60d, 12h or 2w") from None
    if value <= 0 or not math.isfinite(value):
        raise ValueError("duration must be positive")
    return value * unit
