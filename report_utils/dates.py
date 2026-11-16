"""Strict date parsing and inclusive, immutable reporting date windows."""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime, timedelta


DateInput = str | date | datetime


def parse_date(value: DateInput) -> date:
    """Parse ISO dates/timestamps first, then explicit Australian day-first formats.

    Examples: 2026-11-16, 2026-11-16T09:15:00+11:00,
    2026-11-16 09:15:00, 16/11/2026, 16-11-2026 09:15.

    Timestamp offsets are not converted to another timezone: the calendar date
    in the original timestamp is used. Ambiguous two-digit years are rejected.
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise TypeError(f"Expected str, date or datetime, got {type(value).__name__}")

    raw = value.strip()
    if not raw:
        raise ValueError("Cannot parse an empty date string")

    try:
        return date.fromisoformat(raw)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        pass

    for pattern in (
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%d/%m/%Y %H:%M:%S.%f",
        "%d-%m-%Y %H:%M:%S.%f",
        "%d/%m/%Y %H:%M:%S",
        "%d-%m-%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%d-%m-%Y %H:%M",
    ):
        try:
            return datetime.strptime(raw, pattern).date()
        except ValueError:
            pass

    raise ValueError(
        f"Unrecognised date {value!r}; expected ISO YYYY-MM-DD or Australian DD/MM/YYYY "
        "(optionally with time)"
    )


def month_start(value: DateInput) -> date:
    """First calendar day of the supplied month."""
    d = parse_date(value)
    return date(d.year, d.month, 1)


def month_end(value: DateInput) -> date:
    """Last calendar day of the supplied month, including year 9999."""
    d = parse_date(value)
    return date(d.year, d.month, monthrange(d.year, d.month)[1])


def _shift_month(first_day: date, count: int) -> date:
    """Shift the first of a month by a signed number of months."""
    month_index = (first_day.year - 1) * 12 + first_day.month - 1 + count
    if not 0 <= month_index < 9999 * 12:
        raise ValueError("Date window exceeds Python's supported year range (1..9999)")
    year_index, month_index = divmod(month_index, 12)
    return date(year_index + 1, month_index + 1, 1)


@dataclass(frozen=True, slots=True)
class DateWindow:
    """Inclusive [start, end] date range, optionally derived from a reference date.

    Prefer the named factory classmethods for reporting periods. A custom
    window can be built with DateWindow(start=..., end=...).
    """

    start: date
    end: date
    reference_date: date | None = None
    rule: str = "custom"

    def __post_init__(self) -> None:
        # Allows DateWindow(start="...", end="...") as well as factory methods.
        object.__setattr__(self, "start", parse_date(self.start))
        object.__setattr__(self, "end", parse_date(self.end))
        if self.reference_date is not None:
            object.__setattr__(self, "reference_date", parse_date(self.reference_date))
        if self.start > self.end:
            raise ValueError("DateWindow.start cannot be after DateWindow.end")

    @classmethod
    def this_month(cls, reference_date: DateInput) -> DateWindow:
        ref = parse_date(reference_date)
        return cls(month_start(ref), month_end(ref), ref, "this_month")

    @classmethod
    def last_month(cls, reference_date: DateInput) -> DateWindow:
        ref = parse_date(reference_date)
        first = _shift_month(month_start(ref), -1)
        return cls(first, month_end(first), ref, "last_month")

    @classmethod
    def this_calendar_year(cls, reference_date: DateInput) -> DateWindow:
        ref = parse_date(reference_date)
        return cls(date(ref.year, 1, 1), date(ref.year, 12, 31), ref, "this_calendar_year")

    @classmethod
    def last_calendar_year(cls, reference_date: DateInput) -> DateWindow:
        ref = parse_date(reference_date)
        if ref.year == 1:
            raise ValueError("No previous calendar year exists for year 1")
        year = ref.year - 1
        return cls(date(year, 1, 1), date(year, 12, 31), ref, "last_calendar_year")

    @classmethod
    def this_financial_year(
        cls, reference_date: DateInput, start_month: int = 7
    ) -> DateWindow:
        """Full FY containing reference_date. Australian FY uses start_month=7."""
        ref = parse_date(reference_date)
        _validate_fy_month(start_month)
        start_year = ref.year if ref.month >= start_month else ref.year - 1
        if start_year < 1:
            raise ValueError("Financial year starts before year 1")
        first = date(start_year, start_month, 1)
        last = month_end(_shift_month(first, 11))
        return cls(first, last, ref, f"this_financial_year(start_month={start_month})")

    @classmethod
    def last_financial_year(
        cls, reference_date: DateInput, start_month: int = 7
    ) -> DateWindow:
        ref = parse_date(reference_date)
        current = cls.this_financial_year(ref, start_month)
        first = _shift_month(current.start, -12)
        last = month_end(_shift_month(first, 11))
        return cls(first, last, ref, f"last_financial_year(start_month={start_month})")

    @classmethod
    def last_12_months_to_end_last_month(cls, reference_date: DateInput) -> DateWindow:
        ref = parse_date(reference_date)
        first = _shift_month(month_start(ref), -12)
        last = month_end(_shift_month(month_start(ref), -1))
        return cls(first, last, ref, "last_12_months_to_end_last_month")

    @property
    def end_exclusive(self) -> date:
        """Start of the next day: useful for SQL timestamp filters (< end_exclusive)."""
        if self.end == date.max:
            raise ValueError("Cannot compute exclusive end after 9999-12-31")
        return self.end + timedelta(days=1)

    def sql_params(self, *, prefix: str = "", exclusive_end: bool = False) -> dict[str, date]:
        """Create bound SQL values; use exclusive_end for timestamp columns."""
        return {
            f"{prefix}start": self.start,
            f"{prefix}end": self.end_exclusive if exclusive_end else self.end,
        }


def _validate_fy_month(value: int) -> None:
    if type(value) is not int or not 1 <= value <= 12:
        raise ValueError("start_month must be an integer from 1 to 12")
