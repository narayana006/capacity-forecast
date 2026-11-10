"""Render ranked forecasts as Markdown or JSON."""

from __future__ import annotations

import json
from typing import Sequence

from .forecast import AT_RISK, EXHAUSTED, NO_DATA, Forecast


def _num(value: float) -> str:
    """Compact number: 1,234 / 87.5 / 0.42."""
    magnitude = abs(value)
    if magnitude >= 100:
        return f"{value:,.0f}"
    if magnitude >= 10:
        return f"{value:.1f}"
    return f"{value:.2f}"


def _days(value: float | None) -> str:
    if value is None:
        return "never"
    if value > 3650:
        return ">10y"
    return f"{value:.0f}d" if value >= 10 else f"{value:.1f}d"


def _days_cell(f: Forecast) -> str:
    if f.status == NO_DATA:
        return "n/a"
    if f.status == EXHAUSTED:
        return "now"
    if f.days_left is None and f.days_earliest is None:
        return "never"
    return f"{_days(f.days_left)} ({_days(f.days_earliest)} to {_days(f.days_latest)})"


def _date_cell(f: Forecast) -> str:
    if f.status == EXHAUSTED:
        return "already full"
    when = f.exhaustion_date
    if when is None or (f.days_left or 0) > 3650:
        return "-"
    return when.strftime("%Y-%m-%d")


def _growth_cell(f: Forecast) -> str:
    if f.growth_per_day is None:
        return "n/a"
    return f"{'+' if f.growth_per_day >= 0 else '-'}{_num(abs(f.growth_per_day))}"


def count_at_risk(forecasts: Sequence[Forecast]) -> int:
    return sum(1 for f in forecasts if f.status in (EXHAUSTED, AT_RISK))


def to_markdown(
    forecasts: Sequence[Forecast], horizon_days: float, confidence: float, top: int | None = None
) -> str:
    """Render the ranked table. ``top`` limits the rows shown, not the summary counts."""
    shown = forecasts if top is None else forecasts[:top]
    lines = [
        "# Capacity forecast",
        "",
        f"{len(forecasts)} resource(s), horizon {horizon_days:g} days, "
        f"{confidence * 100:g}% range on days left. "
        f"**{count_at_risk(forecasts)} projected to run out within the horizon.**"
        + (f" Showing the top {len(shown)}." if len(shown) < len(forecasts) else ""),
        "",
        "| # | Resource | Status | Used / limit | Full | Growth/day | Days left (range) | Runs out |",
        "|---:|---|---|---:|---:|---:|---|---|",
    ]
    for rank, f in enumerate(shown, start=1):
        lines.append(
            f"| {rank} | {f.resource} | {f.status} | {_num(f.used)} / {_num(f.limit)} "
            f"| {f.utilisation * 100:.0f}% | {_growth_cell(f)} | {_days_cell(f)} | {_date_cell(f)} |"
        )
    return "\n".join(lines) + "\n"


def _round(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(value, digits)


def _iso(f: Forecast, days: float | None) -> str | None:
    when = f.date_at(days)
    return None if when is None else when.strftime("%Y-%m-%dT%H:%M:%SZ")


def to_json(
    forecasts: Sequence[Forecast], horizon_days: float, confidence: float, top: int | None = None
) -> str:
    shown = forecasts if top is None else forecasts[:top]
    payload = {
        "horizon_days": horizon_days,
        "confidence": confidence,
        "resources_total": len(forecasts),
        "resources_at_risk": count_at_risk(forecasts),
        "resources": [
            {
                "rank": rank,
                "resource": f.resource,
                "status": f.status,
                "samples": f.samples,
                "last_sample": f.last_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "used": f.used,
                "limit": f.limit,
                "utilisation_pct": _round(f.utilisation * 100, 1),
                "growth_per_day": _round(f.growth_per_day, 4),
                "growth_per_day_low": _round(f.growth_low, 4),
                "growth_per_day_high": _round(f.growth_high, 4),
                "r_squared": _round(f.r_squared, 3),
                "days_left": _round(f.days_left),
                "days_left_earliest": _round(f.days_earliest),
                "days_left_latest": _round(f.days_latest),
                "exhaustion_date": _iso(f, f.days_left),
                "exhaustion_date_earliest": _iso(f, f.days_earliest),
                "exhaustion_date_latest": _iso(f, f.days_latest),
            }
            for rank, f in enumerate(shown, start=1)
        ],
    }
    return json.dumps(payload, indent=2) + "\n"
