"""Read usage samples from CSV.

Expected columns (header row required, order free, extra columns ignored):

    timestamp  ISO 8601 (2025-05-01T12:00:00Z, 2025-05-01 12:00, 2025-05-01)
               or Unix seconds
    resource   any name that identifies the thing being measured
    used       amount in use, in any unit
    limit      capacity in the same unit as ``used``
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import IO

REQUIRED_COLUMNS = ("timestamp", "resource", "used", "limit")


class UsageFileError(ValueError):
    """The usage file is missing columns or contains a row that cannot be parsed."""


@dataclass(frozen=True)
class Sample:
    resource: str
    time: datetime  # timezone-aware, UTC
    used: float
    limit: float


def parse_timestamp(text: str) -> datetime:
    """Parse ISO 8601 or Unix seconds. Naive timestamps are taken as UTC."""
    text = text.strip()
    try:
        return datetime.fromtimestamp(float(text), tz=timezone.utc)
    except (ValueError, OverflowError, OSError):
        pass
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _number(raw: str, name: str, line_no: int) -> float:
    try:
        value = float(raw.strip())
    except ValueError:
        raise UsageFileError(f"line {line_no}: {name} {raw!r} is not a number") from None
    if value != value or value in (float("inf"), float("-inf")):
        raise UsageFileError(f"line {line_no}: {name} must be a finite number")
    return value


def read_usage(handle: IO[str]) -> list[Sample]:
    reader = csv.DictReader(handle)
    if reader.fieldnames is None:
        raise UsageFileError("usage file is empty (no header row)")
    header = {name.strip().lower(): name for name in reader.fieldnames if name}
    missing = [col for col in REQUIRED_COLUMNS if col not in header]
    if missing:
        raise UsageFileError("usage file is missing required column(s): " + ", ".join(missing))

    samples: list[Sample] = []
    for line_no, raw in enumerate(reader, start=2):
        if not any((value or "").strip() for value in raw.values() if isinstance(value, str)):
            continue
        stamp = raw[header["timestamp"]] or ""
        try:
            when = parse_timestamp(stamp)
        except ValueError:
            raise UsageFileError(
                f"line {line_no}: timestamp {stamp!r} is not ISO 8601 or Unix seconds"
            ) from None
        resource = (raw[header["resource"]] or "").strip()
        if not resource:
            raise UsageFileError(f"line {line_no}: resource is empty")
        samples.append(
            Sample(
                resource=resource,
                time=when,
                used=_number(raw[header["used"]] or "", "used", line_no),
                limit=_number(raw[header["limit"]] or "", "limit", line_no),
            )
        )
    return samples


def read_usage_file(path: str) -> list[Sample]:
    with open(path, newline="", encoding="utf-8-sig") as handle:
        return read_usage(handle)
