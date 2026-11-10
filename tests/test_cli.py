import io
import json
from pathlib import Path

import pytest

from capacity_forecast.cli import main

EXAMPLE = str(Path(__file__).resolve().parent.parent / "examples" / "usage.csv")


def test_markdown_report_on_example(capsys):
    assert main(["--input", EXAMPLE, "--horizon", "60d"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("# Capacity forecast")
    assert "**4 projected to run out within the horizon.**" in out
    rows = [line for line in out.splitlines() if line.startswith("| ") and "Resource" not in line]
    assert [row.split(" | ")[1] for row in rows] == [
        "ci-cache:disk_gb",
        "eu-west-1:elastic_ips",
        "db-primary:disk_gb",
        "search-data-2:disk_gb",
        "checkout-api:memory_gb",
        "logs-archive:disk_gb",
        "ingest-workers:cpu_cores",
    ]
    assert "| 1 | ci-cache:disk_gb | exhausted |" in out


def test_json_report_matches_planted_growth(capsys):
    assert main(["--input", EXAMPLE, "--format", "json"]) == 0
    data = json.loads(capsys.readouterr().out)
    by_name = {r["resource"]: r for r in data["resources"]}
    db = by_name["db-primary:disk_gb"]
    assert db["growth_per_day"] == pytest.approx(3.1, abs=0.1)
    assert db["days_left_earliest"] <= db["days_left"] <= db["days_left_latest"]
    assert db["exhaustion_date"].startswith("2025-06-")
    assert by_name["logs-archive:disk_gb"]["days_left"] is None
    assert by_name["logs-archive:disk_gb"]["exhaustion_date"] is None


def test_shorter_horizon_changes_status_and_top_limits_rows(capsys):
    main(["--input", EXAMPLE, "--horizon", "2w", "--top", "3", "--format", "json"])
    resources = json.loads(capsys.readouterr().out)["resources"]
    assert len(resources) == 3
    assert [r["status"] for r in resources] == ["exhausted", "watch", "ok"]


def test_fail_on_risk_exit_code(tmp_path):
    assert main(["--input", EXAMPLE, "--fail-on-risk", "--output", str(tmp_path / "r.md")]) == 2
    quiet = tmp_path / "quiet.csv"
    quiet.write_text(
        "timestamp,resource,used,limit\n"
        + "".join(f"2025-01-{d:02d},cpu,10,100\n" for d in range(1, 11))
    )
    assert main(["--input", str(quiet), "--fail-on-risk", "--output", str(tmp_path / "q.md")]) == 0
    assert "| 1 | cpu | stable |" in (tmp_path / "q.md").read_text()


def test_reads_stdin(monkeypatch, capsys):
    rows = "timestamp,resource,used,limit\n" + "".join(
        f"2025-01-{d:02d},disk,{40 + 2 * d},100\n" for d in range(1, 11)
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(rows))
    assert main(["--input", "-", "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["resources"][0]["days_left"] == pytest.approx(20.0)


@pytest.mark.parametrize(
    "args, message",
    [
        ([], "exactly one of"),
        (["--input", EXAMPLE, "--prometheus", "http://x"], "exactly one of"),
        (["--input", "/no/such/file.csv"], "No such file"),
        (["--input", EXAMPLE, "--horizon", "soon"], "duration"),
        (["--input", EXAMPLE, "--confidence", "1.5"], "confidence"),
    ],
)
def test_errors_exit_1_with_message(args, message, capsys):
    assert main(args) == 1
    assert message in capsys.readouterr().err
