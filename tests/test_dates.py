from dataclasses import FrozenInstanceError
from datetime import date, datetime, timezone

import pytest

from report_utils import DateWindow, month_end, month_start, parse_date


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-11-16", date(2026, 11, 16)),
        ("2026-11-16T18:12:45", date(2026, 11, 16)),
        ("2026-11-16 18:12:45.125", date(2026, 11, 16)),
        ("2026-11-16T23:59:59+11:00", date(2026, 11, 16)),
        ("2026-11-16T23:59:59Z", date(2026, 11, 16)),
        ("16/11/2026", date(2026, 11, 16)),
        ("16-11-2026", date(2026, 11, 16)),
        ("16/11/2026 14:30:05", date(2026, 11, 16)),
        (date(2026, 11, 16), date(2026, 11, 16)),
        (datetime(2026, 11, 16, 11, 15, tzinfo=timezone.utc), date(2026, 11, 16)),
    ],
)
def test_parse_date(raw, expected):
    assert parse_date(raw) == expected


@pytest.mark.parametrize("invalid", ["", "31/02/2026", "11/12/26", "2026-99-99", "November 16, 2026"])
def test_parser_is_strict(invalid):
    with pytest.raises(ValueError):
        parse_date(invalid)


def test_month_boundaries_and_leap_year():
    assert month_start("2024-02-29") == date(2024, 2, 1)
    assert month_end("2024-02-01") == date(2024, 2, 29)
    assert month_end("9999-12-31") == date(9999, 12, 31)


def test_all_reporting_windows():
    ref = "2026-11-16"
    expected = {
        "this_month": (date(2026, 11, 1), date(2026, 11, 30)),
        "last_month": (date(2026, 10, 1), date(2026, 10, 31)),
        "this_calendar_year": (date(2026, 1, 1), date(2026, 12, 31)),
        "last_calendar_year": (date(2025, 1, 1), date(2025, 12, 31)),
        "this_financial_year": (date(2026, 7, 1), date(2027, 6, 30)),
        "last_financial_year": (date(2025, 7, 1), date(2026, 6, 30)),
        "last_12_months_to_end_last_month": (date(2025, 11, 1), date(2026, 10, 31)),
    }
    for method, (start, end) in expected.items():
        window = getattr(DateWindow, method)(ref)
        assert (window.start, window.end) == (start, end), method
        assert window.reference_date == date(2026, 11, 16)


def test_custom_fy_start_and_january_rollover():
    window = DateWindow.this_financial_year("2026-02-12", start_month=4)
    assert (window.start, window.end) == (date(2025, 4, 1), date(2026, 3, 31))
    prev = DateWindow.last_financial_year("2026-02-12", start_month=4)
    assert (prev.start, prev.end) == (date(2024, 4, 1), date(2025, 3, 31))
    assert DateWindow.this_financial_year("2026-02-12", start_month=1).end == date(2026, 12, 31)
    assert DateWindow.last_month("2026-01-01").start == date(2025, 12, 1)


def test_inclusive_sql_bounds_and_immutability():
    window = DateWindow.last_month("2026-11-16")
    assert window.sql_params() == {"start": date(2026, 10, 1), "end": date(2026, 10, 31)}
    assert window.sql_params(prefix="trade_", exclusive_end=True) == {
        "trade_start": date(2026, 10, 1), "trade_end": date(2026, 11, 1)
    }
    with pytest.raises(FrozenInstanceError):
        window.start = date(2026, 9, 1)


def test_invalid_ranges_and_fy_start():
    with pytest.raises(ValueError):
        DateWindow("2026-02-01", "2026-01-01")
    with pytest.raises(ValueError):
        DateWindow.this_financial_year("2026-11-16", 13)
    with pytest.raises(ValueError):
        DateWindow.last_calendar_year("0001-01-01")
