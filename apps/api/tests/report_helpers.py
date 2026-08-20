from __future__ import annotations

import re
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

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


def make_xlsx_with_stale_dimension(headers: list[str], rows: list[list[object]]) -> bytes:
    """Amazon Ads XLSX exports often declare dimension A1 while containing a full table."""
    payload = make_xlsx(headers, rows)
    out = BytesIO()
    with ZipFile(BytesIO(payload), "r") as source, ZipFile(out, "w") as dest:
        for info in source.infolist():
            data = source.read(info.filename)
            if info.filename.startswith("xl/worksheets/") and info.filename.endswith(".xml"):
                text = data.decode("utf-8")
                data = re.sub(r'dimension ref="[^"]+"', 'dimension ref="A1"', text).encode("utf-8")
            dest.writestr(info, data)
    return out.getvalue()
