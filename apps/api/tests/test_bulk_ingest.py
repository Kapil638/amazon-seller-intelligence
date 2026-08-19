from pathlib import Path

import pytest

from app.bulk.ingest import ingest_asin_file
from app.core.exceptions import BulkIngestError, BulkLimitExceededError
from tests.bulk_helpers import csv_bytes, xlsx_bytes


def test_valid_csv_extracts_asins() -> None:
    filename, data = csv_bytes(["ASIN"], [["B0BLKSTR01"], ["B0TEST0001"]])
    stats, unique, failures = ingest_asin_file(filename, data)
    assert stats.input_rows == 2
    assert stats.valid_rows == 2
    assert stats.unique_asins == 2
    assert unique == ["B0BLKSTR01", "B0TEST0001"]
    assert failures == []


def test_valid_xlsx_extracts_asins() -> None:
    filename, data = xlsx_bytes(["ASIN", "Title"], [["B0BLKSTR01", "Strong"], ["B0TEST0002", "Pillow"]])
    stats, unique, _failures = ingest_asin_file(filename, data)
    assert unique == ["B0BLKSTR01", "B0TEST0002"]
    assert stats.asin_column == "ASIN"


@pytest.mark.parametrize(
    "header",
    ["ASIN", "asin", "Amazon ASIN", "Product ASIN", "Amazon_ASIN"],
)
def test_asin_column_aliases(header: str) -> None:
    filename, data = csv_bytes([header], [["b0blkstr01"]])
    stats, unique, _failures = ingest_asin_file(filename, data)
    assert unique == ["B0BLKSTR01"]
    assert stats.valid_rows == 1


def test_missing_asin_column() -> None:
    filename, data = csv_bytes(["SKU", "Title"], [["X", "Thing"]])
    with pytest.raises(BulkIngestError, match="No recognizable ASIN column"):
        ingest_asin_file(filename, data)


def test_invalid_asin_does_not_fail_the_file() -> None:
    filename, data = csv_bytes(["ASIN"], [["B0BLKSTR01"], ["NOT-VALID"], ["B0TEST0001"]])
    stats, unique, failures = ingest_asin_file(filename, data)
    assert unique == ["B0BLKSTR01", "B0TEST0001"]
    assert stats.invalid_rows == 1
    assert failures[0].kind == "invalid"
    assert failures[0].input_asin == "NOT-VALID"


def test_blank_rows_are_ignored() -> None:
    filename, data = csv_bytes(["ASIN"], [["B0BLKSTR01"], [""], ["   "], ["B0TEST0001"]])
    stats, unique, _failures = ingest_asin_file(filename, data)
    assert unique == ["B0BLKSTR01", "B0TEST0001"]
    assert stats.input_rows == 2


def test_duplicate_asins_removed_before_unique_list() -> None:
    filename, data = csv_bytes(["ASIN"], [["B0AAAAAAA1"], ["B0AAAAAAA1"], ["B0BBBBBBB2"]])
    stats, unique, _failures = ingest_asin_file(filename, data)
    assert unique == ["B0AAAAAAA1", "B0BBBBBBB2"]
    assert stats.valid_rows == 3
    assert stats.duplicate_rows_removed == 1
    assert stats.unique_asins == 2


def test_sample_fixture_csv() -> None:
    path = Path(__file__).parent / "fixtures" / "bulk" / "mock_asins.csv"
    stats, unique, failures = ingest_asin_file(path.name, path.read_bytes())
    assert stats.duplicate_rows_removed == 1
    assert stats.invalid_rows == 1
    assert "B0UNKNOWN1" in unique
    assert "B0BLKSTR01" in unique
    assert any(item.kind == "invalid" for item in failures)


def test_max_unique_asins_rejected_not_truncated() -> None:
    rows = [[f"B0GEN{index:05d}"] for index in range(101)]
    filename, data = csv_bytes(["ASIN"], rows)
    with pytest.raises(BulkLimitExceededError, match="not truncated"):
        ingest_asin_file(filename, data, max_asins=100)
