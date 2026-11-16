from datetime import date

import pandas as pd
import pytest

from report_utils import end_of_month, explode_month_ends, start_of_month


def test_month_rounding_preserves_null_and_high_date():
    source = pd.Series(["2024-02-14", "9999-12-31", None, "31/03/2026", pd.NaT], index=[5, 6, 7, 8, 9])
    starts = start_of_month(source)
    ends = end_of_month(source)
    assert starts.index.tolist() == [5, 6, 7, 8, 9]
    assert starts.tolist() == [date(2024, 2, 1), date(9999, 12, 1), None, date(2026, 3, 1), None]
    assert ends.tolist() == [date(2024, 2, 29), date(9999, 12, 31), None, date(2026, 3, 31), None]


def test_explode_contains_only_month_ends_within_window():
    frame = pd.DataFrame({"id": ["A", "B"], "start": ["2026-01-15", "2026-02-01"], "end": ["2026-03-15", "2026-02-28"]})
    actual = explode_month_ends(frame, start_col="start", end_col="end")
    assert list(zip(actual["id"], actual["month_end"])) == [
        ("A", date(2026, 1, 31)),
        ("A", date(2026, 2, 28)),
        ("B", date(2026, 2, 28)),
    ]
    inclusive_final = explode_month_ends(frame, start_col="start", end_col="end", include_partial_final_month=True)
    assert ("A", date(2026, 3, 31)) in list(zip(inclusive_final["id"], inclusive_final["month_end"]))


def test_sentinel_requires_cap_and_as_of_cap_is_exact():
    frame = pd.DataFrame({"start": ["2026-08-01"], "end": ["9999-12-31"]})
    with pytest.raises(ValueError, match="as_of"):
        explode_month_ends(frame, start_col="start", end_col="end")
    out = explode_month_ends(frame, start_col="start", end_col="end", as_of="2026-10-15")
    assert out["month_end"].tolist() == [date(2026, 8, 31), date(2026, 9, 30)]


def test_missing_date_validation_or_drop():
    frame = pd.DataFrame({"start": [None], "end": ["2026-03-31"]})
    with pytest.raises(ValueError, match="Missing"):
        explode_month_ends(frame, start_col="start", end_col="end")
    out = explode_month_ends(frame, start_col="start", end_col="end", null_policy="drop")
    assert out.empty
    assert "month_end" in out.columns


def test_invalid_dates_raise_not_silently_coerce():
    with pytest.raises(ValueError):
        end_of_month(pd.Series(["nonsense"]))
