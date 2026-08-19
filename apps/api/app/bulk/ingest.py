from __future__ import annotations

import re

from app.core.config import get_settings
from app.core.exceptions import BulkIngestError, BulkLimitExceededError, ReportParseError, ReportUploadError
from app.core.validation import is_valid_asin, normalize_asin
from app.models.bulk import BulkFailure, BulkIngestStats
from app.reports.file_loader import _cell, _extension, _read_csv, _read_xlsx

ASIN_HEADER_ALIASES = frozenset(
    {
        "asin",
        "amazon asin",
        "product asin",
        "amazon_asin",
    }
)
HEADER_SCAN_ROWS = 15


def normalize_header_name(value: str) -> str:
    text = value.strip().casefold()
    text = text.replace("_", " ")
    text = re.sub(r"\s+", " ", text)
    return text


def ingest_asin_file(
    filename: str,
    data: bytes,
    *,
    max_asins: int | None = None,
) -> tuple[BulkIngestStats, list[str], list[BulkFailure]]:
    settings = get_settings()
    limit = settings.max_bulk_asins if max_asins is None else max_asins
    rows = _read_rows(filename, data)
    header_index, column_index, column_name = _find_asin_column(rows)
    stats = BulkIngestStats(filename=filename.rsplit("/", 1)[-1], asin_column=column_name)
    unique: list[str] = []
    seen: set[str] = set()
    failures: list[BulkFailure] = []

    for offset, row in enumerate(rows[header_index + 1 :], start=header_index + 2):
        cells = [_cell(value) for value in row]
        if all(not cell for cell in cells):
            continue
        raw = cells[column_index] if column_index < len(cells) else ""
        stats.input_rows += 1
        if not raw.strip():
            stats.invalid_rows += 1
            failures.append(
                BulkFailure(
                    row=offset,
                    input_asin="",
                    reason="ASIN is blank.",
                    kind="invalid",
                )
            )
            continue
        normalized = normalize_asin(raw)
        if not is_valid_asin(normalized):
            stats.invalid_rows += 1
            failures.append(
                BulkFailure(
                    row=offset,
                    input_asin=raw.strip(),
                    reason="Invalid ASIN format. Expected 10 alphanumeric characters.",
                    kind="invalid",
                )
            )
            continue
        stats.valid_rows += 1
        if normalized in seen:
            stats.duplicate_rows_removed += 1
            continue
        seen.add(normalized)
        unique.append(normalized)

    stats.unique_asins = len(unique)
    if stats.unique_asins > limit:
        raise BulkLimitExceededError(
            f"This file has {stats.unique_asins} unique ASINs. "
            f"The current limit is {limit}. Reduce the file; it was not truncated."
        )
    if stats.unique_asins == 0 and stats.invalid_rows == 0:
        raise BulkIngestError("No ASINs were found in this file.")
    return stats, unique, failures


def _read_rows(filename: str, data: bytes) -> list[list[str]]:
    extension = _extension(filename)
    try:
        if extension == ".csv":
            rows = _read_csv(data)
        elif extension == ".xlsx":
            rows = _read_xlsx(data)
        else:
            raise BulkIngestError("Upload a .csv or .xlsx file.")
    except (ReportUploadError, ReportParseError) as exc:
        raise BulkIngestError(str(exc)) from exc
    if not rows:
        raise BulkIngestError("This file is empty.")
    return rows


def _find_asin_column(rows: list[list[str]]) -> tuple[int, int, str]:
    scan = min(len(rows), HEADER_SCAN_ROWS)
    for index in range(scan):
        for col, cell in enumerate(rows[index]):
            header = _cell(cell)
            key = normalize_header_name(header)
            compact = key.replace(" ", "_")
            if key in ASIN_HEADER_ALIASES or compact in ASIN_HEADER_ALIASES:
                return index, col, header or "ASIN"
    raise BulkIngestError(
        "No recognizable ASIN column was found. Expected one of: "
        "ASIN, asin, Amazon ASIN, Product ASIN, Amazon_ASIN."
    )
