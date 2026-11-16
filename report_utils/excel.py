"""Orchestrate auditable Excel exports from Dataset objects."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill

from .dataset import Dataset, MetadataSet


@dataclass(frozen=True, slots=True)
class ExportReceipt:
    path: Path
    export_id: UUID
    exported_at_utc: datetime
    row_count: int


def _validate_sheet_name(sheet: str) -> None:
    if not isinstance(sheet, str) or not sheet or len(sheet) > 31:
        raise ValueError("Sheet names must contain 1..31 characters")
    if any(ch in sheet for ch in "[]:*?/\\"):
        raise ValueError(f"Invalid character in sheet name {sheet!r}")


def _metadata_rows(meta: MetadataSet, *, receipt: ExportReceipt, data_sheet: str, columns: int):
    rows: list[tuple[str, str | int]] = [
        ("schema_version", "1"),
        ("report_name", meta.report_name),
        ("dataset_id", str(meta.dataset_id)),
        ("export_id", str(receipt.export_id)),
        ("parent_export_id", str(meta.parent_export_id) if meta.parent_export_id else ""),
        ("created_at_utc", meta.created_at_utc.astimezone(timezone.utc).isoformat()),
        ("exported_at_utc", receipt.exported_at_utc.isoformat()),
        ("prepared_by", meta.prepared_by),
        ("data_sheet", data_sheet),
        ("row_count", receipt.row_count),
        ("column_count", columns),
        ("source_system", meta.source_system or ""),
        ("sql_reference", meta.sql_reference or ""),
        ("sql_sha256", meta.sql_sha256 or ""),
    ]
    for named in meta.date_windows:
        prefix = f"date_window.{named.name}"
        rows.extend(
            [
                (f"{prefix}.rule", named.window.rule),
                (f"{prefix}.reference_date", named.window.reference_date.isoformat() if named.window.reference_date else ""),
                (f"{prefix}.start_inclusive", named.window.start.isoformat()),
                (f"{prefix}.end_inclusive", named.window.end.isoformat()),
            ]
        )
    rows.extend((f"sql_parameter.{key}", value) for key, value in meta.sql_parameters)
    rows.append(("notes", meta.notes or ""))
    return rows


def _style_workbook(writer: pd.ExcelWriter, data_sheet: str, metadata_sheet: str) -> None:
    data = writer.book[data_sheet]
    data.freeze_panes = "A2"
    if data.max_row >= 1 and data.max_column >= 1:
        data.auto_filter.ref = data.dimensions
    data.sheet_view.showGridLines = False
    metadata = writer.book[metadata_sheet]
    metadata.freeze_panes = "A2"
    metadata.sheet_view.showGridLines = False
    metadata.column_dimensions["A"].width = 47
    metadata.column_dimensions["B"].width = 76
    metadata.auto_filter.ref = metadata.dimensions

    for ws in (data, metadata):
        for cell in ws[1]:
            cell.fill = PatternFill("solid", fgColor="183153")
            cell.font = Font(color="FFFFFF", bold=True)
            cell.alignment = Alignment(vertical="center")
        ws.row_dimensions[1].height = 23

    # A readable default, with bounded width sampling on larger exports.
    for col_idx, column in enumerate(data.columns, start=1):
        if col_idx > 80:
            break  # Avoid costly presentation work on unusually wide extracts.
        sample = list(data.iter_cols(min_col=col_idx, max_col=col_idx, max_row=min(data.max_row, 101)))[0]
        width = max((len(str(cell.value)) for cell in sample if cell.value is not None), default=10)
        data.column_dimensions[column[0].column_letter].width = max(12, min(width + 3, 38))


def _escape_excel_formula_strings(writer: pd.ExcelWriter, sheet_names: tuple[str, str]) -> None:
    """Prevent text beginning '=' being interpreted as a formula in Excel."""
    for sheet_name in sheet_names:
        sheet = writer.book[sheet_name]
        for row in sheet.iter_rows(min_row=2):
            for cell in row:
                if cell.data_type == "f" and isinstance(cell.value, str):
                    cell.value = "'" + cell.value


def export_dataset(
    dataset: Dataset,
    path: str | Path,
    *,
    data_sheet: str = "Data",
    metadata_sheet: str = "Metadata",
    index: bool = False,
) -> ExportReceipt:
    """Export the dataset with a separate, visible, human-readable audit sheet.

    The exported row count reflects the dataframe AT export time. `export_id`
    changes on every write; `dataset_id` remains fixed for a Dataset instance.

    DataFrame columns containing timezone-aware pandas datetimes must be
    converted to a timezone-naive representation before writing to Excel.
    """
    if not isinstance(dataset, Dataset):
        raise TypeError("dataset must be a Dataset")
    target = Path(path)
    if target.suffix.lower() != ".xlsx":
        raise ValueError("Excel output path must end with .xlsx")
    _validate_sheet_name(data_sheet)
    _validate_sheet_name(metadata_sheet)
    if data_sheet.casefold() == metadata_sheet.casefold():
        raise ValueError("Data and metadata sheets must have different names")

    receipt = ExportReceipt(target, uuid4(), datetime.now(timezone.utc), dataset.row_count)
    rows = _metadata_rows(dataset.metadata, receipt=receipt, data_sheet=data_sheet, columns=len(dataset.dataframe.columns))
    meta_frame = pd.DataFrame(rows, columns=["Field", "Value"])

    # Writer owns presentation and workbook creation; Dataset contains no I/O.
    with pd.ExcelWriter(target, engine="openpyxl", datetime_format="yyyy-mm-dd hh:mm:ss", date_format="yyyy-mm-dd") as writer:
        dataset.dataframe.to_excel(writer, sheet_name=data_sheet, index=index)
        meta_frame.to_excel(writer, sheet_name=metadata_sheet, index=False)
        _escape_excel_formula_strings(writer, (data_sheet, metadata_sheet))
        _style_workbook(writer, data_sheet, metadata_sheet)

    return receipt
