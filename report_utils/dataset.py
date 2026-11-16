"""DataFrame ownership and deeply immutable report provenance metadata."""

from __future__ import annotations

import getpass
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping
from uuid import UUID, uuid4

import pandas as pd

from .dates import DateWindow


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class NamedWindow:
    """How a DateWindow was used, e.g. 'transaction_date' or 'snapshot_date'."""

    name: str
    window: DateWindow

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("NamedWindow.name cannot be empty")


@dataclass(frozen=True, slots=True)
class MetadataSet:
    """Immutable provenance associated with a dataset.

    Dynamic export metadata (export timestamp, export ID and live row count)
    belongs to the Excel writer, not to this immutable object.
    """

    report_name: str
    dataset_id: UUID = field(default_factory=uuid4)
    created_at_utc: datetime = field(default_factory=_utc_now)
    prepared_by: str = field(default_factory=getpass.getuser)
    source_system: str | None = None
    sql_reference: str | None = None
    sql_sha256: str | None = None
    date_windows: tuple[NamedWindow, ...] = ()
    sql_parameters: tuple[tuple[str, str], ...] = ()
    parent_export_id: UUID | None = None
    notes: str | None = None

    def __post_init__(self) -> None:
        if not self.report_name.strip():
            raise ValueError("MetadataSet.report_name cannot be empty")
        if self.created_at_utc.tzinfo is None or self.created_at_utc.utcoffset() is None:
            raise ValueError("created_at_utc must be timezone-aware")
        if not isinstance(self.dataset_id, UUID):
            raise TypeError("dataset_id must be a UUID")
        if self.parent_export_id is not None and not isinstance(self.parent_export_id, UUID):
            raise TypeError("parent_export_id must be a UUID or None")

        # Convert any passed lists/mappings to immutable tuples as well.
        windows = tuple(self.date_windows)
        if any(not isinstance(window, NamedWindow) for window in windows):
            raise TypeError("date_windows must contain NamedWindow values")
        names = [item.name for item in windows]
        if len(names) != len(set(names)):
            raise ValueError("date_windows names must be unique")
        object.__setattr__(self, "date_windows", windows)

        params = self.sql_parameters
        if isinstance(params, Mapping):
            params = tuple(params.items())
        params = tuple((str(k), str(v)) for k, v in params)
        if len(params) != len({key for key, _ in params}):
            raise ValueError("sql_parameters names must be unique")
        object.__setattr__(self, "sql_parameters", params)

    @classmethod
    def from_sql(
        cls,
        *,
        report_name: str,
        sql_text: str,
        sql_reference: str | None = None,
        **kwargs: object,
    ) -> MetadataSet:
        """Hash exactly the SQL text passed in (UTF-8, no normalisation)."""
        if not isinstance(sql_text, str):
            raise TypeError("sql_text must be a string")
        return cls(
            report_name=report_name,
            sql_reference=sql_reference,
            sql_sha256=hashlib.sha256(sql_text.encode("utf-8")).hexdigest(),
            **kwargs,  # type: ignore[arg-type]
        )


@dataclass(slots=True)
class Dataset:
    """DataFrame and immutable provenance; takes a defensive copy on creation."""

    dataframe: pd.DataFrame
    metadata: MetadataSet

    def __post_init__(self) -> None:
        if not isinstance(self.dataframe, pd.DataFrame):
            raise TypeError("Dataset.dataframe must be a pandas DataFrame")
        if not isinstance(self.metadata, MetadataSet):
            raise TypeError("Dataset.metadata must be a MetadataSet")
        self.dataframe = self.dataframe.copy(deep=True)

    @property
    def row_count(self) -> int:
        return len(self.dataframe)

    def to_excel(
        self,
        path: str | Path,
        *,
        data_sheet: str = "Data",
        metadata_sheet: str = "Metadata",
        index: bool = False,
    ) -> "ExportReceipt":
        """Thin convenience wrapper; all export logic lives in excel.py."""
        from .excel import ExportReceipt, export_dataset

        return export_dataset(
            self,
            path,
            data_sheet=data_sheet,
            metadata_sheet=metadata_sheet,
            index=index,
        )
