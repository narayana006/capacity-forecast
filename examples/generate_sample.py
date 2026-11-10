"""Regenerate examples/usage.csv.

Synthetic data: 21 days of 6-hourly samples for seven resources that behave
differently (steady growth, noisy growth, daily cycle, flat, shrinking, a
quota that is already nearly used up, and one that is over its limit).

Run from the repository root:  python examples/generate_sample.py
"""

import csv
import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

START = datetime(2025, 5, 1, tzinfo=timezone.utc)
STEP_HOURS = 6
DAYS = 21

# resource -> (limit, start value, growth per day, noise std dev, daily swing)
RESOURCES = {
    "db-primary:disk_gb": (500.0, 372.0, 3.1, 0.6, 0.0),
    "search-data-2:disk_gb": (2000.0, 1310.0, 9.5, 14.0, 0.0),
    "checkout-api:memory_gb": (64.0, 41.0, 0.22, 0.8, 3.0),
    "ingest-workers:cpu_cores": (96.0, 52.0, 0.0, 2.5, 9.0),
    "logs-archive:disk_gb": (4000.0, 2950.0, -12.0, 9.0, 0.0),
    "eu-west-1:elastic_ips": (120.0, 104.0, 0.45, 0.7, 0.0),
    "ci-cache:disk_gb": (250.0, 236.0, 0.9, 0.5, 0.0),
}


def main() -> None:
    rng = random.Random(20250501)
    out = Path(__file__).with_name("usage.csv")
    with out.open("w", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["timestamp", "resource", "used", "limit"])
        for step in range(DAYS * 24 // STEP_HOURS + 1):
            when = START + timedelta(hours=step * STEP_HOURS)
            day = step * STEP_HOURS / 24.0
            for name, (limit, start, growth, noise, swing) in RESOURCES.items():
                value = start + growth * day + rng.gauss(0, noise)
                value += swing * math.sin(2 * math.pi * (when.hour - 6) / 24.0)
                if name.endswith("elastic_ips"):
                    value = round(value)
                writer.writerow([when.strftime("%Y-%m-%dT%H:%M:%SZ"), name, f"{max(value, 0):.2f}", f"{limit:g}"])
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
