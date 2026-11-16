from dataclasses import FrozenInstanceError
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from openpyxl import load_workbook

from report_utils import Dataset, DateWindow, MetadataSet, NamedWindow


def _metadata_as_dict(path: Path) -> dict:
    book = load_workbook(path)
    assert book.sheetnames == ["Data", "Metadata"]
    sheet = book["Metadata"]
    return {row[0]: row[1] for row in list(sheet.values)[1:]}


def test_export_contains_provenance_and_copies_source(tmp_path: Path):
    input_frame = pd.DataFrame({"id": [1, 2], "amount": [42.0, 99.0]})
    window = DateWindow.last_month("2026-11-16")
    params = window.sql_params(exclusive_end=True)
    meta = MetadataSet.from_sql(
        report_name="Monthly fees",
        sql_text="SELECT * FROM fees WHERE fee_date >= :start AND fee_date < :end",
        sql_reference="queries/fees.sql",
        date_windows=[NamedWindow("fee_date", window)],
        sql_parameters=params,
        prepared_by="test-user",
        source_system="Oracle",
    )
    dataset = Dataset(input_frame, meta)
    input_frame.loc[0, "amount"] = -10
    assert dataset.dataframe.loc[0, "amount"] == 42
    dataset.dataframe.loc[2] = [3, 5.0]  # Row count must be measured at export time.

    first = dataset.to_excel(tmp_path / "first.xlsx")
    second = dataset.to_excel(tmp_path / "second.xlsx")
    assert first.export_id != second.export_id
    assert first.row_count == second.row_count == 3
    d = _metadata_as_dict(first.path)
    assert d["report_name"] == "Monthly fees"
    assert d["dataset_id"] == str(meta.dataset_id)
    assert d["export_id"] == str(first.export_id)
    assert d["row_count"] == 3
    assert d["column_count"] == 2
    assert d["prepared_by"] == "test-user"
    assert d["sql_reference"] == "queries/fees.sql"
    assert len(d["sql_sha256"]) == 64
    assert d["date_window.fee_date.start_inclusive"] == "2026-10-01"
    assert d["date_window.fee_date.end_inclusive"] == "2026-10-31"
    assert d["sql_parameter.end"] == "2026-11-01"
    assert d["source_system"] == "Oracle"

    workbook = load_workbook(first.path)
    assert workbook["Data"].freeze_panes == "A2"
    assert workbook["Data"].max_row == 4
    assert workbook["Metadata"].sheet_state == "visible"


def test_metadata_is_frozen_and_windows_immutable():
    meta = MetadataSet(report_name="Test", date_windows=[NamedWindow("a", DateWindow.this_month("2026-11-16"))])
    assert isinstance(meta.date_windows, tuple)
    with pytest.raises(FrozenInstanceError):
        meta.report_name = "Altered"
    with pytest.raises(AttributeError):
        meta.date_windows.append("a")
    with pytest.raises(ValueError):
        MetadataSet(report_name="Test", date_windows=(NamedWindow("dup", DateWindow.this_month("2026-11-16")), NamedWindow("dup", DateWindow.this_month("2026-11-16"))))


def test_formula_as_text_and_invalid_sheet(tmp_path: Path):
    ds = Dataset(pd.DataFrame({"text": ["=2+2"]}), MetadataSet(report_name="Test", notes="=HYPERLINK(\"fake\")"))
    path = tmp_path / "safe.xlsx"
    ds.to_excel(path)
    book = load_workbook(path)
    assert book["Data"]["A2"].data_type != "f"
    assert book["Metadata"]["B16"].data_type != "f"
    with pytest.raises(ValueError):
        ds.to_excel(tmp_path / "bad.xlsx", data_sheet="Metadata", metadata_sheet="metadata")
