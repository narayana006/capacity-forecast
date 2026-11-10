import io
from datetime import datetime, timezone

import pytest

from capacity_forecast.loader import UsageFileError, parse_timestamp, read_usage

UTC_NOON = datetime(2025, 5, 1, 12, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "text",
    [
        "2025-05-01T12:00:00Z",
        "2025-05-01T12:00:00+00:00",
        "2025-05-01 12:00:00",
        "2025-05-01T14:00:00+02:00",
        "1746100800",
        "1746100800.0",
    ],
)
def test_timestamp_formats(text):
    assert parse_timestamp(text) == UTC_NOON


def test_date_only_timestamp_is_midnight_utc():
    assert parse_timestamp("2025-05-01") == datetime(2025, 5, 1, tzinfo=timezone.utc)


def test_reads_rows_in_any_column_order():
    text = "Limit,Used,Resource,Timestamp,Team\n500,372.5,db:disk,2025-05-01T12:00:00Z,core\n"
    (sample,) = read_usage(io.StringIO(text))
    assert (sample.resource, sample.time, sample.used, sample.limit) == ("db:disk", UTC_NOON, 372.5, 500.0)


def test_blank_lines_are_skipped():
    text = "timestamp,resource,used,limit\n\n2025-05-01,a,1,2\n,,,\n"
    assert len(read_usage(io.StringIO(text))) == 1


@pytest.mark.parametrize(
    "body, message",
    [
        ("yesterday,a,1,2", "line 2: timestamp"),
        ("2025-05-01,a,lots,2", "line 2: used"),
        ("2025-05-01,a,1,inf", "limit must be a finite"),
        ("2025-05-01,,1,2", "resource is empty"),
    ],
)
def test_bad_rows_name_the_line(body, message):
    with pytest.raises(UsageFileError, match=message):
        read_usage(io.StringIO("timestamp,resource,used,limit\n" + body + "\n"))


def test_missing_columns_and_empty_file():
    with pytest.raises(UsageFileError, match="limit"):
        read_usage(io.StringIO("timestamp,resource,used\n"))
    with pytest.raises(UsageFileError, match="empty"):
        read_usage(io.StringIO(""))
