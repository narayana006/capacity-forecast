"""Exercise the Prometheus adapter against a local fake of /api/v1/query_range."""

import json
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

from capacity_forecast.cli import main
from capacity_forecast.prometheus import PrometheusError, fetch_samples, query_range

END = datetime(2025, 5, 11, tzinfo=timezone.utc)
DAY = 86400


def matrix(series):
    return {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [{"metric": metric, "values": values} for metric, values in series],
        },
    }


class FakePrometheus(HTTPServer):
    def __init__(self):
        super().__init__(("127.0.0.1", 0), Handler)
        self.responses = {}  # query -> (http status, body dict)
        self.requests = []

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server_address[1]}"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        self.server.requests.append((parsed.path, params))
        status, body = self.server.responses.get(
            params.get("query"), (400, {"status": "error", "error": "unknown query"})
        )
        if callable(body):
            body = body(params)
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        pass


@pytest.fixture
def prom():
    server = FakePrometheus()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


def daily(start_value, per_day, days=10, end=END.timestamp()):
    first = end - days * DAY
    return [[first + i * DAY, str(start_value + per_day * i)] for i in range(days + 1)]


def test_query_range_sends_documented_parameters(prom):
    prom.responses["up"] = (200, matrix([({"__name__": "up", "job": "a"}, [[100, "1"], [160, "1"]])]))
    result = query_range(prom.url + "/", "up", 100, 160, 60)
    path, params = prom.requests[0]
    assert path == "/api/v1/query_range"
    assert params == {"query": "up", "start": "100.000", "end": "160.000", "step": "60"}
    assert result == {'{job="a"}': [(100.0, 1.0), (160.0, 1.0)]}


def test_resource_label_names_series_and_nan_points_are_dropped(prom):
    prom.responses["used"] = (
        200,
        matrix([({"instance": "db1", "mount": "/"}, [[1, "5"], [2, "NaN"], [3, "+Inf"], [4, "7"]])]),
    )
    assert query_range(prom.url, "used", 1, 4, 1, label="instance") == {"db1": [(1.0, 5.0), (4.0, 7.0)]}
    with pytest.raises(PrometheusError, match="no label"):
        query_range(prom.url, "used", 1, 4, 1, label="pod")


def test_duplicate_resource_names_are_rejected(prom):
    prom.responses["used"] = (
        200,
        matrix([({"instance": "db1", "mount": "/"}, [[1, "5"]]), ({"instance": "db1", "mount": "/data"}, [[1, "6"]])]),
    )
    with pytest.raises(PrometheusError, match="more than one series"):
        query_range(prom.url, "used", 1, 4, 1, label="instance")


def test_fetch_samples_joins_usage_with_limits(prom):
    prom.responses["used"] = (
        200,
        matrix([
            ({"instance": "db1"}, daily(50, 2)),
            ({"instance": "no-limit"}, daily(1, 1)),
        ]),
    )
    # The limit is raised from 100 to 200 half way through and has a gap at the end.
    limit_values = [[t, "100" if i < 5 else "200"] for i, (t, _) in enumerate(daily(0, 0))][:-1]
    prom.responses["limit"] = (200, matrix([({"instance": "db1"}, limit_values)]))

    samples = fetch_samples(prom.url, "used", "limit", END, lookback_days=10, step_seconds=DAY, label="instance")
    assert {s.resource for s in samples} == {"db1"}
    assert len(samples) == 11
    assert [s.limit for s in samples] == [100.0] * 5 + [200.0] * 6
    assert samples[0].used == 50.0 and samples[-1].used == 70.0
    assert samples[-1].time == END
    _, params = prom.requests[0]
    assert float(params["end"]) - float(params["start"]) == pytest.approx(10 * DAY)


def test_error_responses_become_prometheus_errors(prom):
    prom.responses["bad"] = (400, {"status": "error", "errorType": "bad_data", "error": "parse error"})
    with pytest.raises(PrometheusError, match="HTTP 400.*parse error"):
        query_range(prom.url, "bad", 1, 2, 1)
    prom.responses["soft"] = (200, {"status": "error", "error": "timed out"})
    with pytest.raises(PrometheusError, match="timed out"):
        query_range(prom.url, "soft", 1, 2, 1)
    prom.responses["vector"] = (200, {"status": "success", "data": {"resultType": "vector", "result": []}})
    with pytest.raises(PrometheusError, match="matrix"):
        query_range(prom.url, "vector", 1, 2, 1)


def test_unreachable_server():
    with pytest.raises(PrometheusError, match="could not query"):
        query_range("http://127.0.0.1:1", "up", 1, 2, 1, timeout=2)


def test_cli_end_to_end_against_fake_server(prom, capsys):
    def relative(start_value, per_day):
        def body(params):
            return matrix([({"instance": "db1"}, daily(start_value, per_day, end=float(params["end"])))])
        return body

    prom.responses["disk_used"] = (200, relative(50, 2))
    prom.responses["disk_size"] = (200, relative(100, 0))
    code = main([
        "--prometheus", prom.url, "--usage-query", "disk_used", "--limit-query", "disk_size",
        "--resource-label", "instance", "--lookback", "10d", "--step", "1d",
        "--horizon", "30d", "--format", "json", "--fail-on-risk",
    ])
    assert code == 2
    (resource,) = json.loads(capsys.readouterr().out)["resources"]
    assert resource["resource"] == "db1"
    assert resource["status"] == "at risk"
    assert resource["days_left"] == pytest.approx(15.0)
    assert {params["step"] for _, params in prom.requests} == {"86400"}


def test_cli_requires_both_queries(capsys):
    assert main(["--prometheus", "http://127.0.0.1:1", "--usage-query", "x"]) == 1
    assert "--limit-query" in capsys.readouterr().err
