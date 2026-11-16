# Report Audit Utils

A deliberately small Python package for report-date windows, pandas date operations, provenance metadata, and Excel exports with a **visible Metadata worksheet**.

## Installation

Requires Python 3.11+.

```bash
python -m pip install -e '.[dev]'
pytest -q
```

## Date windows

```python
from report_utils import DateWindow, parse_date

raw = '16/11/2026 14:35:00'
reference_date = parse_date(raw)
window = DateWindow.last_12_months_to_end_last_month(reference_date)
assert window.start.isoformat() == '2025-11-01'
assert window.end.isoformat() == '2026-10-31'

fy = DateWindow.this_financial_year(reference_date, start_month=7)
assert (fy.start.isoformat(), fy.end.isoformat()) == ('2026-07-01', '2027-06-30')

# Date columns: inclusive start/end; timestamps: end EXCLUSIVE.
sql_bindings = window.sql_params(exclusive_end=True)
# {'start': datetime.date(2025, 11, 1), 'end': datetime.date(2026, 11, 1)}
```

Named DateWindow factories:

- `this_month(ref)`, `last_month(ref)`
- `this_calendar_year(ref)`, `last_calendar_year(ref)`
- `this_financial_year(ref, start_month=7)`, `last_financial_year(ref, start_month=7)`
- `last_12_months_to_end_last_month(ref)`

All windows **include** both `start` and `end`; a financial year denotes the *full financial year*, not year-to-date.

Parsing accepts ISO date, ISO timestamps (including offsets), Australian `DD/MM/YYYY` or `DD-MM-YYYY` with optional time. Other formats and two-digit years are rejected. A timestamp's calendar date is retained without timezone conversion.

## Dataset to Excel, with metadata

```python
from pathlib import Path
import pandas as pd
from report_utils import Dataset, DateWindow, MetadataSet, NamedWindow

window = DateWindow.last_month('2026-11-16')
sql = Path('queries/fees.sql').read_text(encoding='utf-8')
params = window.sql_params(exclusive_end=True)
# df = pd.read_sql_query(sql, engine, params=params)  # connection handling is YOUR choice

df = pd.DataFrame({'account': ['A', 'B'], 'fee': [15.5, 20.0]})
metadata = MetadataSet.from_sql(
    report_name='Monthly fee report',
    sql_text=sql,
    sql_reference='queries/fees.sql',
    source_system='Oracle',
    date_windows=(NamedWindow('fee_date', window),),
    sql_parameters=params,
)
report = Dataset(df, metadata)
receipt = report.to_excel('fees.xlsx')
print(receipt.export_id, receipt.row_count)
```

A real example that does not need a SQL connection: `python examples/example_report.py`.

The Metadata worksheet records report and dataset IDs, export ID, optional parent export ID, UTC creation/export times, preparer, row/column count, source system, SQL file reference and SHA-256 of the supplied SQL text, named date-window definitions and the supplied bound SQL parameters. `MetadataSet` is frozen, and its window/parameter collections are converted to tuples. `Dataset` copies the DataFrame upon construction, while the DataFrame inside a Dataset remains mutable.

Each export gets a **new export_id**; the same Dataset keeps the same **dataset_id**. To indicate a manually curated successor report in a new Dataset, set `parent_export_id=previous_receipt.export_id`. Changing a workbook later in Excel does **not** update metadata, detect edits, or create an edit history. Use SharePoint/OneDrive version history or a controlled import/diff/checksum workflow if that level of traceability is required. SQL hash confirms exactly the supplied SQL text, not necessarily the query ultimately executed by a database; keep query execution and metadata generation on the same source string.

Only the output module handles workbook writing. The default Excel sheet names are `Data` and `Metadata`; the Metadata sheet is deliberately visible, with filters and frozen headers. Strings imported from a data system that begin with `=` are escaped so they cannot execute as formulas; this adds a leading apostrophe in the stored cell value.

## Pandas operations, including SQL high dates

```python
import pandas as pd
from report_utils import start_of_month, end_of_month, explode_month_ends

s = pd.Series(['2026-01-12', '9999-12-31', None])
print(start_of_month(s).tolist())  # [2026-01-01, 9999-12-01, None]
print(end_of_month(s).tolist())    # [2026-01-31, 9999-12-31, None]

intervals = pd.DataFrame({
    'id': [1, 2],
    'start_date': ['2026-01-15', '2026-08-01'],
    'end_date': ['2026-03-15', '9999-12-31'],
})
expanded = explode_month_ends(
    intervals,
    start_col='start_date',
    end_col='end_date',
    as_of='2026-10-31',
)
```

These operations return **Python `date` objects** (object-dtype Series) so that the year 9999 is representable. A null remains `None`. The month-end explosion has precise semantics: generate each EOM date `start_date <= month_end <= end_date`. An unfinished final month is excluded unless `include_partial_final_month=True`. The open-ended `9999-12-31` sentinel *requires* `as_of` to avoid expanding thousands of months; `as_of` also caps other intervals. Missing start/end boundaries raise by default (`null_policy='drop'` opts out). An optional month-count guard prevents accidental giant expansions.

## Package layout

```text
report_utils/
  dates.py           # pure stdlib scalar parsing and DateWindow
  pandas_dates.py    # series month transformations and interval explosion
  dataset.py         # Dataset, NamedWindow, frozen MetadataSet
  excel.py           # Excel export orchestration and ExportReceipt
  __init__.py        # public API
examples/example_report.py
tests/
pyproject.toml
```

### Intentional boundaries

- **Not an extraction framework.** Keep connections, secrets, SQL execution and error handling in your reporting scripts. Build this in once a repeatable extraction pattern is clear.
- **Not an Excel revision tracker.** Provenance captures a snapshot at export; subsequent changes aren't detected.
- **Excel limitations apply.** Convert timezone-aware pandas datetime columns to naive datetimes or strings before export. Excel also has inherent type/precision limits.
- **SQL predicate distinction matters.** Use `>= :start AND < :end` for timestamps with `exclusive_end=True`; use `BETWEEN :start AND :end` for pure SQL dates only if both endpoints should be included.
