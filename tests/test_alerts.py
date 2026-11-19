import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from capacity_forecast.alerts import rule_expr, threshold_ratio, to_rules
from capacity_forecast.cli import main
from capacity_forecast.forecast import AT_RISK, STABLE, Forecast

yaml = pytest.importorskip("yaml")

EXAMPLE = str(Path(__file__).resolve().parent.parent / "examples" / "usage.csv")
NOW = datetime(2025, 5, 22, tzinfo=timezone.utc)
USAGE = 'sum by (instance) (node_filesystem_size_bytes - node_filesystem_avail_bytes)'
LIMIT = 'sum by (instance) (node_filesystem_size_bytes)'


def _forecast(name, growth, used=400.0, limit=500.0, status=AT_RISK):
    return Forecast(resource=name, status=status, samples=50, last_time=NOW, used=used,
                    limit=limit, utilisation=used / limit, growth_per_day=growth)


def test_threshold_leaves_lead_days_of_headroom():
    # 500 limit, +5/day, 14 days lead -> alert at 430, i.e. 86% of the limit
    assert threshold_ratio(_forecast("db", 5.0), 14) == pytest.approx(0.86)
    assert threshold_ratio(_forecast("db", 0.0), 14) is None
    assert threshold_ratio(_forecast("db", -1.0), 14) is None
    assert threshold_ratio(_forecast("db", None), 14) is None
    # growth so fast the lead covers the whole limit: clamp to 0 (always firing)
    assert threshold_ratio(_forecast("db", 100.0), 14) == 0.0


def test_expr_divides_by_limit_and_selects_one_resource():
    expr = rule_expr(USAGE, LIMIT, "instance", 'db"1', 0.86)
    assert expr == (
        f"((({USAGE}) / on(instance) ({LIMIT})) >= 0.8600)"
        ' and on(instance) label_replace(vector(1), "instance", "db\\"1", "", "")'
    )


def test_rules_file_is_valid_yaml_with_one_rule_per_growing_resource():
    forecasts = [_forecast("db-1", 5.0), _forecast("cache", -2.0, status=STABLE),
                 _forecast("search", 1.0, used=100, limit=1000)]
    text, count = to_rules(forecasts, USAGE, LIMIT, "instance", 14, "30m")
    assert count == 2
    assert "# No rule (flat, shrinking or too little data): cache" in text
    doc = yaml.safe_load(text)
    (group,) = doc["groups"]
    assert group["name"] == "capacity-forecast"
    db, search = group["rules"]
    assert db["alert"] == "CapacityForecastLowHeadroom"
    assert db["for"] == "30m"
    assert db["labels"] == {"severity": "warning", "instance": "db-1"}
    assert db["expr"].endswith('label_replace(vector(1), "instance", "db-1", "", "")')
    assert ">= 0.8600)" in db["expr"]
    assert db["annotations"]["forecast_threshold_ratio"] == "0.8600"
    assert "{{ $value | humanizePercentage }}" in db["annotations"]["description"]
    assert ">= 0.9860)" in search["expr"]


def test_no_growing_resources_gives_empty_rule_list():
    text, count = to_rules([_forecast("cache", -2.0, status=STABLE)], USAGE, LIMIT, "instance", 14)
    assert count == 0
    assert yaml.safe_load(text)["groups"][0]["rules"] == []


@pytest.mark.parametrize("kwargs, message", [
    ({"lead_days": 0}, "lead time"),
    ({"label": ""}, "--resource-label"),
    ({"label": "bad-label"}, "cannot be used"),
    ({"label": "severity"}, "cannot be used"),
    ({"for_duration": "1 hour"}, "for"),
])
def test_bad_rule_options_are_rejected(kwargs, message):
    args = {"usage_query": USAGE, "limit_query": LIMIT, "label": "instance",
            "lead_days": 14, "for_duration": "1h"} | kwargs
    with pytest.raises(ValueError, match=message):
        to_rules([_forecast("db", 1.0)], **args)


def test_cli_writes_rules_from_csv(tmp_path, capsys):
    rules = tmp_path / "rules.yml"
    code = main(["--input", EXAMPLE, "--format", "json", "--alert-rules", str(rules),
                 "--usage-query", USAGE, "--limit-query", LIMIT, "--resource-label", "instance"])
    assert code == 0
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    growing = [r for r in report["resources"] if r["growth_per_day"] > 0]
    assert f"wrote {len(growing)} alert rule(s)" in captured.err
    names = [r["labels"]["instance"] for r in yaml.safe_load(rules.read_text())["groups"][0]["rules"]]
    assert names == [r["resource"] for r in growing]


def test_cli_alert_rules_need_queries_and_label(tmp_path, capsys):
    code = main(["--input", EXAMPLE, "--alert-rules", str(tmp_path / "r.yml"),
                 "--usage-query", USAGE, "--limit-query", LIMIT])
    assert code == 1
    assert "--alert-rules needs" in capsys.readouterr().err
    assert not (tmp_path / "r.yml").exists()
