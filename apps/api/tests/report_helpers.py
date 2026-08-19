from __future__ import annotations

from io import BytesIO
from pathlib import Path

from openpyxl import Workbook

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "reports"


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def make_xlsx(headers: list[str], rows: list[list[object]]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
