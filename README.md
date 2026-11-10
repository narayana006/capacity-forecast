# capacity-forecast

Forecast when disks, memory, CPU or quotas will hit their limits, from usage time series in a CSV file or Prometheus.

**Status:** early but working. Today it reads `timestamp,resource,used,limit` samples from a CSV (or stdin), fits a linear trend per resource, estimates days until the limit is reached with a confidence range, and prints a ranked report in Markdown or JSON. A read-only Prometheus adapter is included but has only been tested against a fake server, not a production Prometheus. Seasonality modelling and alert-rule generation are not built. See the [checklist](#features).

## Install

Requires Python 3.10 or newer. No third-party dependencies.

```bash
git clone https://github.com/narayana006/capacity-forecast
cd capacity-forecast
python -m pip install .
```

## Quick start

[`examples/usage.csv`](examples/usage.csv) holds 21 days of synthetic 6-hourly samples for seven resources.

```bash
capacity-forecast --input examples/usage.csv --horizon 60d
```

Output:

```markdown
# Capacity forecast

7 resource(s), horizon 60 days, 95% range on days left. **4 projected to run out within the horizon.**

| # | Resource | Status | Used / limit | Full | Growth/day | Days left (range) | Runs out |
|---:|---|---|---:|---:|---:|---|---|
| 1 | ci-cache:disk_gb | exhausted | 255 / 250 | 102% | +0.90 | now | already full |
| 2 | eu-west-1:elastic_ips | at risk | 113 / 120 | 94% | +0.43 | 15d (14d to 17d) | 2025-06-06 |
| 3 | db-primary:disk_gb | at risk | 436 / 500 | 87% | +3.08 | 21d (20d to 21d) | 2025-06-11 |
| 4 | search-data-2:disk_gb | at risk | 1,489 / 2,000 | 74% | +9.50 | 52d (49d to 55d) | 2025-07-12 |
| 5 | checkout-api:memory_gb | ok | 41.2 / 64.0 | 64% | +0.19 | 97d (65d to 175d) | 2025-08-26 |
| 6 | logs-archive:disk_gb | stable | 2,711 / 4,000 | 68% | -11.9 | never | - |
| 7 | ingest-workers:cpu_cores | stable | 43.1 / 96.0 | 45% | -0.06 | never (255d to never) | - |
```

Rendered, that is a table ranked by who runs out first. "Days left" counts from each resource's most recent sample, and the range in brackets is the 95% interval.

## Reading the report

| Status | Meaning |
|---|---|
| `exhausted` | The latest sample is already at or over the limit |
| `at risk` | The trend reaches the limit within `--horizon` |
| `watch` | The best estimate is beyond the horizon (or never), but the fast end of the range is inside it |
| `ok` | Growing, but even the fast estimate is beyond the horizon |
| `stable` | Flat or shrinking |
| `insufficient data` | Fewer than three samples, or all at one timestamp |

Resources are ordered by status in that order, then by days left, then by how full they are.

## Input: CSV

A header row is required. Column order does not matter, names are case-insensitive, extra columns are ignored.

| Column | Meaning |
|---|---|
| `timestamp` | ISO 8601 (`2025-05-01T06:00:00Z`, `2025-05-01 06:00`, `2025-05-01`) or Unix seconds. Timestamps without a zone are read as UTC |
| `resource` | Any name that identifies what is measured, e.g. `db-primary:disk_gb` |
| `used` | Amount in use |
| `limit` | Capacity, in the same unit as `used` |

```csv
timestamp,resource,used,limit
2025-05-01T00:00:00Z,db-primary:disk_gb,372.21,500
2025-05-01T00:00:00Z,search-data-2:disk_gb,1315.55,2000
2025-05-01T00:00:00Z,checkout-api:memory_gb,37.51,64
```

Samples do not need to be sorted or evenly spaced, and each resource can have its own unit. If a resource's limit changes over time, the most recent limit is the one forecast against. Use `--input -` to read from stdin.

## Input: Prometheus

The adapter issues two `GET /api/v1/query_range` requests, one for usage and one for limits, and joins the returned series by name. It never writes anything. You supply both PromQL expressions; there are no built-in defaults.

```bash
capacity-forecast --prometheus http://prom:9090 \
  --usage-query 'sum by (instance) (node_filesystem_size_bytes{mountpoint="/"} - node_filesystem_avail_bytes{mountpoint="/"})' \
  --limit-query 'sum by (instance) (node_filesystem_size_bytes{mountpoint="/"})' \
  --resource-label instance --lookback 14d --step 1h --horizon 60d
```

- Both queries must return series with the same labels, so they can be matched. Usage series with no matching limit series are dropped.
- `--resource-label` names each series by one label. Without it, the full label set is used as the name.
- `--lookback` (default `14d`) and `--step` (default `1h`) set the range and resolution fetched, ending now.
- No authentication, custom headers or TLS options yet. Point it at a URL you can reach without them.

This path is covered by tests that run a local fake of the range-query endpoint. It has not been run against a real Prometheus server, so treat it as experimental and please report what you find.

## How the estimate is made

1. For each resource, an ordinary least-squares line is fitted to `used` against time.
2. The line is extended until it crosses the latest `limit`. That gives "days left", counted from the resource's last sample.
3. The range comes from the standard error of the fitted slope. The slope's confidence interval (95% by default, normal approximation) gives a faster and a slower growth rate; lines with those slopes through the centre of the data are extended to the limit in the same way. If the slow end of the interval is flat or negative, the upper end of the range is `never`.

Use `--window 7d` to fit only the most recent part of each series when growth has changed recently.

## Options

```
capacity-forecast (--input FILE | --prometheus URL --usage-query Q --limit-query Q) [options]
```

| Option | Default | Meaning |
|---|---|---|
| `--input FILE` | | Usage CSV, or `-` for stdin |
| `--prometheus URL` | | Prometheus base URL (needs `--usage-query` and `--limit-query`) |
| `--resource-label LABEL` | all labels | Label used as the resource name |
| `--lookback DURATION` | 14d | History fetched from Prometheus |
| `--step DURATION` | 1h | Resolution fetched from Prometheus |
| `--horizon DURATION` | 60d | Resources projected to run out sooner than this are `at risk` |
| `--window DURATION` | all data | Fit the trend on only the most recent part of each series |
| `--confidence P` | 0.95 | Confidence level for the range, between 0 and 1 |
| `--top N` | all | Show only the first N rows |
| `--format {markdown,json}` | markdown | Output format |
| `--output FILE` | stdout | Write the report to a file |
| `--fail-on-risk` | off | Exit with status 2 if any resource is `exhausted` or `at risk` |

Durations are a number followed by `h`, `d` or `w` (`12h`, `60d`, `2w`); a bare number means days. Exit status is 0 on success and 1 for bad input, bad options or a Prometheus error.

JSON output carries the same ranking plus the growth-rate interval, R², and ISO dates for the estimate and both ends of the range:

```bash
capacity-forecast --input examples/usage.csv --top 1 --window 7d --format json
```

```json
{
  "horizon_days": 60.0,
  "confidence": 0.95,
  "resources_total": 7,
  "resources_at_risk": 4,
  "resources": [
    {
      "rank": 1,
      "resource": "ci-cache:disk_gb",
      "status": "exhausted",
      "samples": 29,
      "last_sample": "2025-05-22T00:00:00Z",
      "used": 254.78,
      "limit": 250.0,
      "utilisation_pct": 101.9,
      "growth_per_day": 0.8551,
      "growth_per_day_low": 0.7752,
      "growth_per_day_high": 0.9349,
      "r_squared": 0.942,
      "days_left": 0.0,
      "days_left_earliest": 0.0,
      "days_left_latest": 0.0,
      "exhaustion_date": "2025-05-22T00:00:00Z",
      "exhaustion_date_earliest": "2025-05-22T00:00:00Z",
      "exhaustion_date_latest": "2025-05-22T00:00:00Z"
    }
  ]
}
```

## Features

- [x] Pulls usage and limit metrics from Prometheus (user-supplied PromQL over the range-query API; tested against a fake server only)
- [ ] Trend and seasonality forecast per resource (linear trend works today; seasonality is not modelled)
- [x] Days-until-exhaustion estimate with a confidence range
- [x] Ranked report of the resources closest to their limits
- [ ] Optional alert rules generated from the forecast

Also working: CSV and stdin input, Markdown and JSON output.

## Limitations

- The trend is a straight line. Exponential growth, step changes and usage that saturates are all forecast poorly; `--window` helps with step changes.
- No seasonality model. Daily or weekly cycles are treated as noise, which widens the range, and a series shorter than a few cycles can produce a misleading slope.
- The range reflects uncertainty in the fitted slope only. It assumes independent, roughly normal residuals and uses a normal rather than a t quantile, so it is too narrow for short or strongly autocorrelated series. It is not a prediction interval for individual samples.
- "Days left" is where the trend line crosses the limit. A spiky resource can touch its limit earlier than its trend does.
- The Prometheus adapter has no authentication support and has not been verified against a live server.
- The example data is synthetic.

## Development

```bash
python -m pip install -e ".[dev]"
python -m pytest
python examples/generate_sample.py   # regenerates the sample CSV deterministically
```

## Contributing

Issues and pull requests are welcome. Reports from running the Prometheus adapter against a real server are especially useful, as are series where the forecast was clearly wrong. Please add a test with any code change.

Built and maintained by [Narayana Vemburaj](https://github.com/narayana006).

## License

[MIT](LICENSE)
