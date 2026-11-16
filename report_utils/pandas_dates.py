"""Pandas date helpers that retain SQL high dates like 9999-12-31.

Outputs use object dtype with Python datetime.date / None, rather than pandas
nanosecond timestamps, which cannot represent year 9999. Month rounding is
vectorised with NumPy's day/month-resolution datetime64.
"""

from __future__ import annotations

from calendar import monthrange
from datetime import date

import numpy as np
import pandas as pd

from .dates import DateInput, parse_date


HIGH_DATE = date(9999, 12, 31)


def _optional_date(value: object) -> date | None:
    if value is None or bool(pd.isna(value)):
        return None
    return parse_date(value)  # type: ignore[arg-type]


def _rounded_month(series: pd.Series, *, to_end: bool) -> pd.Series:
    if not isinstance(series, pd.Series):
        raise TypeError("Expected a pandas Series")
    dates = series.map(_optional_date)
    numpy_days = np.array(dates.tolist(), dtype="datetime64[D]")
    months = numpy_days.astype("datetime64[M]")
    if to_end:
        rounded = (months + np.timedelta64(1, "M")).astype("datetime64[D]") - np.timedelta64(1, "D")
    else:
        rounded = months.astype("datetime64[D]")
    result = rounded.astype(object).tolist()
    return pd.Series(result, index=series.index, name=series.name, dtype=object)


def start_of_month(series: pd.Series) -> pd.Series:
    """Start of each month, retaining missing dates and 9999-12-31 safely."""
    return _rounded_month(series, to_end=False)


def end_of_month(series: pd.Series) -> pd.Series:
    """End of each month, retaining missing dates and 9999-12-31 safely."""
    return _rounded_month(series, to_end=True)


def explode_month_ends(
    frame: pd.DataFrame,
    *,
    start_col: str,
    end_col: str,
    output_col: str = "month_end",
    as_of: DateInput | None = None,
    include_partial_final_month: bool = False,
    null_policy: str = "raise",
    max_months_per_row: int = 600,
) -> pd.DataFrame:
    """Repeat each row once for every month-end within [start_col, end_col].

    If an end date is 9999-12-31, `as_of` is REQUIRED to cap an open interval.
    `as_of`, when given, caps every row's end date.

    By default an end date like 2026-03-15 does NOT include 2026-03-31.
    Set include_partial_final_month=True if the final touched month should
    also appear. Null interval boundaries raise unless null_policy='drop'.

    The output index is reset. Input columns are repeated without aggregation.
    """
    if start_col not in frame.columns or end_col not in frame.columns:
        raise KeyError(f"Columns {start_col!r} and {end_col!r} are required")
    if output_col in frame.columns:
        raise ValueError(f"output_col {output_col!r} already exists")
    if null_policy not in {"raise", "drop"}:
        raise ValueError("null_policy must be 'raise' or 'drop'")
    if max_months_per_row < 1:
        raise ValueError("max_months_per_row must be positive")

    cutoff = parse_date(as_of) if as_of is not None else None
    repeat_positions: list[int] = []
    month_ends: list[date] = []

    for position, (raw_start, raw_end) in enumerate(zip(frame[start_col], frame[end_col])):
        start, end = _optional_date(raw_start), _optional_date(raw_end)
        if start is None or end is None:
            if null_policy == "drop":
                continue
            raise ValueError(f"Missing date boundary at row position {position}")
        if start > end:
            raise ValueError(f"start > end at row position {position}: {start} > {end}")
        if end == HIGH_DATE and cutoff is None:
            raise ValueError(
                f"Open-ended 9999-12-31 at row position {position}: pass as_of to cap expansion"
            )
        if cutoff is not None:
            end = min(end, cutoff)
        if start > end:
            continue

        # Iterate over calendar months, then check which month-ends qualify.
        first_month_idx = (start.year - 1) * 12 + start.month - 1
        last_month_idx = (end.year - 1) * 12 + end.month - 1
        total_months = last_month_idx - first_month_idx + 1
        if total_months > max_months_per_row:
            raise ValueError(
                f"Row position {position} spans {total_months} months; "
                f"increase max_months_per_row={max_months_per_row} if intentional"
            )
        for month_idx in range(first_month_idx, last_month_idx + 1):
            year_idx, zero_month = divmod(month_idx, 12)
            year, month = year_idx + 1, zero_month + 1
            month_last = date(year, month, monthrange(year, month)[1])
            if month_last >= start and (month_last <= end or include_partial_final_month):
                repeat_positions.append(position)
                month_ends.append(month_last)

    result = frame.iloc[repeat_positions].copy().reset_index(drop=True)
    result[output_col] = pd.Series(month_ends, dtype=object)
    return result
