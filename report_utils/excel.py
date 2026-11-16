"""Write one or more datasets to Excel with a consolidated audit worksheet."""

from __future__ import annotations

import getpass
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping
from uuid import UUID, uuid4

import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill

from .dataset import Dataset, MetadataSet


@dataclass(frozen=True, slots=True)
class ExportReceipt:
    path: Path
    export_id: UUID
    exported_at_utc: datetime
    row_count: int                 # total rows across all dataset sheets
    sheet_row_counts: tuple[tuple[str, int], ...]
    parent_export_id: UUID | None = None

    def rows_for(self, sheet_name: str) -> int:
        return dict(self.sheet_row_counts)[sheet_name]


def _validate_sheet_name(name: str) -> None:
    if not isinstance(name, str) or not name.strip() or len(name) > 31:
        raise ValueError("Worksheet names must contain 1..31 non-blank characters")
    if any(char in name for char in "[]:*?/\\") or name.startswith("'") or name.endswith("'"):
        raise ValueError(f"Invalid Excel worksheet name {name!r}")


def _dataset_metadata_rows(sheet_name: str, meta: MetadataSet, *, row_count: int, column_count: int) -> list[tuple[str, str, object]]:
    rows: list[tuple[str, str, object]] = [
        (sheet_name, "report_name", meta.report_name),
        (sheet_name, "dataset_id", str(meta.dataset_id)),
        (sheet_name, "created_at_utc", meta.created_at_utc.astimezone(timezone.utc).isoformat()),
        (sheet_name, "prepared_by", meta.prepared_by),
        (sheet_name, "row_count", row_count),
        (sheet_name, "column_count", column_count),
        (sheet_name, "source_system", meta.source_system or ""),
        (sheet_name, "sql_reference", meta.sql_reference or ""),
        (sheet_name, "sql_sha256", meta.sql_sha256 or ""),
    ]
    for named in meta.date_windows:
        prefix = f"date_window.{named.name}"
        rows.extend([
            (sheet_name, f"{prefix}.rule", named.window.rule),
            (sheet_name, f"{prefix}.reference_date", named.window.reference_date.isoformat() if named.window.reference_date else ""),
            (sheet_name, f"{prefix}.start_inclusive", named.window.start.isoformat()),
            (sheet_name, f"{prefix}.end_inclusive", named.window.end.isoformat()),
        ])
    rows.extend((sheet_name, f"sql_parameter.{key}", value) for key, value in meta.sql_parameters)
    rows.append((sheet_name, "notes", meta.notes or ""))
    return rows


def _workbook_metadata_rows(
    receipt: ExportReceipt,
    *,
    report_name: str,
    prepared_by: str,
) -> list[tuple[str, str, object]]:
    # '[workbook]' cannot collide with a legal Excel worksheet name.
    section = "[workbook]"
    return [
        (section, "schema_version", "2"),
        (section, "report_name", report_name),
        (section, "export_id", str(receipt.export_id)),
        (section, "parent_export_id", str(receipt.parent_export_id) if receipt.parent_export_id else ""),
        (section, "exported_at_utc", receipt.exported_at_utc.isoformat()),
        (section, "prepared_by", prepared_by),
        (section, "dataset_count", len(receipt.sheet_row_counts)),
        (section, "row_count", receipt.row_count),
    ]


def _escape_excel_formulas(writer: pd.ExcelWriter, sheet_names: tuple[str, ...]) -> None:
    """Keep formula-like strings from being interpreted as executable formulas."""
    for name in sheet_names:
        worksheet = writer.book[name]
        for row in worksheet.iter_rows(min_row=2):
            for cell in row:
                if isinstance(cell.value, str) and (cell.data_type == "f" or cell.value.startswith(("=", "+", "-", "@"))):
                    cell.value = "'" + cell.value


def _style_workbook(writer: pd.ExcelWriter, data_sheets: tuple[str, ...], metadata_sheet: str) -> None:
    for sheet_name in (*data_sheets, metadata_sheet):
        ws = writer.book[sheet_name]
        ws.freeze_panes = "A2"
        ws.sheet_view.showGridLines = False
        ws.auto_filter.ref = ws.dimensions
        for cell in ws[1]:
            cell.fill = PatternFill("solid", fgColor="183153")
            cell.font = Font(color="FFFFFF", bold=True)
            cell.alignment = Alignment(vertical="center")
        ws.row_dimensions[1].height = 23

        if sheet_name == metadata_sheet:
            ws.column_dimensions["A"].width = 24
            ws.column_dimensions["B"].width = 45
            ws.column_dimensions["C"].width = 76
            continue

        # Sample at most 100 rows / 80 columns so formatting remains inexpensive.
        for column_cells in ws.iter_cols(max_col=min(ws.max_column, 80), max_row=min(ws.max_row, 101)):
            width = max((len(str(c.value)) for c in column_cells if c.value is not None), default=10)
            ws.column_dimensions[column_cells[0].column_letter].width = max(12, min(width + 3, 38))


def export_workbook(
    path: str | Path,
    *,
    datasets: Mapping[str, Dataset],
    metadata_sheet: str = "Metadata",
    report_name: str | None = None,
    prepared_by: str | None = None,
    parent_export_id: UUID | None = None,
    index: bool = False,
) -> ExportReceipt:
    """Create a workbook containing one worksheet per Dataset plus one audit sheet.

    Names in `datasets` become Excel worksheet names (in iteration order).
    `Dataset` supplies SQL/date provenance; this function supplies export-level IDs,
    timestamp and row/column counts. It never mutates a Dataset.
    """
    target = Path(path)
    if target.suffix.lower() != ".xlsx":
        raise ValueError("Excel output path must end with .xlsx")
    if not isinstance(datasets, Mapping) or not datasets:
        raise ValueError("datasets must be a non-empty mapping of sheet names to Dataset objects")
    if parent_export_id is not None and not isinstance(parent_export_id, UUID):
        raise TypeError("parent_export_id must be a UUID or None")

    _validate_sheet_name(metadata_sheet)
    sheet_names = tuple(datasets)
    for sheet_name, dataset in datasets.items():
        _validate_sheet_name(sheet_name)
        if not isinstance(dataset, Dataset):
            raise TypeError(f"Worksheet {sheet_name!r} must contain a Dataset")
    names = (*sheet_names, metadata_sheet)
    if len({name.casefold() for name in names}) != len(names):
        raise ValueError("Worksheet names must be unique, ignoring case")

    counts = tuple((sheet_name, dataset.row_count) for sheet_name, dataset in datasets.items())
    receipt = ExportReceipt(
        path=target,
        export_id=uuid4(),
        exported_at_utc=datetime.now(timezone.utc),
        row_count=sum(count for _, count in counts),
        sheet_row_counts=counts,
        parent_export_id=parent_export_id,
    )
    rows = _workbook_metadata_rows(
        receipt,
        report_name=report_name if report_name is not None else target.stem,
        prepared_by=prepared_by if prepared_by is not None else getpass.getuser(),
    )
    for sheet_name, dataset in datasets.items():
        rows.extend(_dataset_metadata_rows(
            sheet_name, dataset.metadata,
            row_count=dataset.row_count,
            column_count=len(dataset.dataframe.columns),
        ))
    metadata_frame = pd.DataFrame(rows, columns=["Sheet", "Field", "Value"])

    with pd.ExcelWriter(target, engine="openpyxl", datetime_format="yyyy-mm-dd hh:mm:ss", date_format="yyyy-mm-dd") as writer:
        for sheet_name, dataset in datasets.items():
            dataset.dataframe.to_excel(writer, sheet_name=sheet_name, index=index)
        metadata_frame.to_excel(writer, sheet_name=metadata_sheet, index=False)
        _escape_excel_formulas(writer, (*sheet_names, metadata_sheet))
        _style_workbook(writer, sheet_names, metadata_sheet)
    return receipt


def export_dataset(
    dataset: Dataset,
    path: str | Path,
    *,
    data_sheet: str = "Data",
    metadata_sheet: str = "Metadata",
    index: bool = False,
    parent_export_id: UUID | None = None,
) -> ExportReceipt:
    """Backwards-compatible single-dataset entry point."""
    return export_workbook(
        path,
        datasets={data_sheet: dataset},
        metadata_sheet=metadata_sheet,
        index=index,
        parent_export_id=parent_export_id,
    )
