"""Command-line entry point."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from . import __version__
from .forecast import forecast_all, parse_duration
from .loader import read_usage, read_usage_file
from .prometheus import PrometheusError, fetch_samples
from .report import count_at_risk, to_json, to_markdown


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="capacity-forecast",
        description="Forecast when resources will hit their limits and rank the ones closest to running out.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    source = parser.add_argument_group("input (choose one)")
    source.add_argument("--input", metavar="FILE",
                        help="usage CSV (timestamp,resource,used,limit); '-' reads stdin")
    source.add_argument("--prometheus", metavar="URL",
                        help="Prometheus base URL, e.g. http://prom:9090")

    prom = parser.add_argument_group("Prometheus options")
    prom.add_argument("--usage-query", metavar="PROMQL", help="query returning one usage series per resource")
    prom.add_argument("--limit-query", metavar="PROMQL",
                      help="query returning the limit for each resource, with the same labels")
    prom.add_argument("--resource-label", metavar="LABEL",
                      help="label to use as the resource name (default: all labels)")
    prom.add_argument("--lookback", default="14d", help="how much history to fetch (default: 14d)")
    prom.add_argument("--step", default="1h", help="resolution of the fetched series (default: 1h)")

    parser.add_argument("--horizon", default="60d",
                        help="flag resources projected to run out within this time (default: 60d)")
    parser.add_argument("--window", metavar="DURATION",
                        help="fit the trend on only the most recent part of each series, e.g. 7d")
    parser.add_argument("--confidence", type=float, default=0.95,
                        help="confidence level for the days-left range (default: 0.95)")
    parser.add_argument("--top", type=int, metavar="N", help="show only the N highest-ranked resources")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown",
                        help="output format (default: markdown)")
    parser.add_argument("--output", metavar="FILE", help="write the report here instead of stdout")
    parser.add_argument("--fail-on-risk", action="store_true",
                        help="exit with status 2 if any resource runs out within the horizon")
    return parser


def _load(args: argparse.Namespace):
    if bool(args.input) == bool(args.prometheus):
        raise ValueError("give exactly one of --input or --prometheus")
    if args.input:
        return read_usage(sys.stdin) if args.input == "-" else read_usage_file(args.input)
    if not args.usage_query or not args.limit_query:
        raise ValueError("--prometheus needs both --usage-query and --limit-query")
    return fetch_samples(
        args.prometheus, args.usage_query, args.limit_query,
        end=datetime.now(timezone.utc),
        lookback_days=parse_duration(args.lookback),
        step_seconds=parse_duration(args.step) * 86400.0,
        label=args.resource_label,
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        horizon = parse_duration(args.horizon)
        window = parse_duration(args.window) if args.window else None
        samples = _load(args)
        if not samples:
            raise ValueError("no usage samples found")
        forecasts = forecast_all(samples, horizon, args.confidence, window)
    except (OSError, ValueError, PrometheusError) as exc:
        print(f"capacity-forecast: error: {exc}", file=sys.stderr)
        return 1

    render = to_json if args.format == "json" else to_markdown
    top = None if args.top is None else max(args.top, 0)
    text = render(forecasts, horizon, args.confidence, top)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(text)
    else:
        sys.stdout.write(text)
    return 2 if args.fail_on_risk and count_at_risk(forecasts) else 0


if __name__ == "__main__":
    sys.exit(main())
