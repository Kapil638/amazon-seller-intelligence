from __future__ import annotations

from reportlab.lib.colors import Color, HexColor
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import Flowable

from app.reports.fonts import register_report_fonts

PRIMARY_DARK = HexColor("#1B2430")
ACCENT = HexColor("#1F6F7A")
TEXT_PRIMARY = HexColor("#1A1A1A")
TEXT_SECONDARY = HexColor("#5C6370")
BORDER = HexColor("#D5D8DE")
SURFACE = HexColor("#F4F6F8")
SURFACE_DARK = HexColor("#243447")
SUCCESS = HexColor("#2F6B4F")
WARNING = HexColor("#8A6A24")
ATTENTION = HexColor("#8A3B32")
WHITE = HexColor("#FFFFFF")


def status_color(status: str) -> Color:
    key = status.lower()
    if key == "excellent":
        return SUCCESS
    if key == "good":
        return ACCENT
    if key == "fair":
        return WARNING
    if key == "poor":
        return ATTENTION
    return TEXT_SECONDARY


def priority_color(priority: str) -> Color:
    key = priority.lower()
    if key == "high":
        return ATTENTION
    if key == "medium":
        return WARNING
    if key == "low":
        return ACCENT
    return TEXT_SECONDARY


class ScoreBar(Flowable):
    def __init__(self, score: int, max_score: int, width: float, height: float = 5.5 * mm) -> None:
        super().__init__()
        self.score = max(score, 0)
        self.max_score = max(max_score, 1)
        self.bar_width = width
        self.bar_height = height

    def wrap(self, availWidth, availHeight):
        return self.bar_width, self.bar_height

    def draw(self):
        self.canv.setFillColor(SURFACE)
        self.canv.roundRect(0, 0, self.bar_width, self.bar_height, 2, fill=1, stroke=0)
        filled = self.bar_width * min(self.score / self.max_score, 1)
        if filled > 0:
            self.canv.setFillColor(ACCENT)
            self.canv.roundRect(0, 0, filled, self.bar_height, 2, fill=1, stroke=0)


class HeroScore(Flowable):
    def __init__(self, score: int, status: str, width: float, height: float = 46 * mm) -> None:
        super().__init__()
        self.score = score
        self.status = status
        self._width = width
        self._height = height

    def wrap(self, availWidth, availHeight):
        return self._width, self._height

    def draw(self):
        regular, bold, _ = register_report_fonts()
        canv = self.canv
        canv.setFillColor(SURFACE_DARK)
        canv.roundRect(0, 0, self._width, self._height, 6, fill=1, stroke=0)
        canv.setFillColor(WHITE)
        canv.setFont(regular, 8)
        canv.drawCentredString(self._width / 2, self._height - 12 * mm, "LISTING QUALITY")
        canv.setFont(bold, 28)
        canv.drawCentredString(self._width / 2, self._height / 2 - 2 * mm, str(self.score))
        canv.setFont(regular, 9)
        canv.drawCentredString(self._width / 2, self._height / 2 - 9 * mm, "/100")
        canv.setFont(bold, 10)
        canv.drawCentredString(self._width / 2, 8 * mm, self.status.upper())


class NumberedCanvas(canvas.Canvas):
    def __init__(self, *args, asin: str = "", **kwargs):
        canvas.Canvas.__init__(self, *args, **kwargs)
        self._saved_page_states: list[dict] = []
        self._asin = asin

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        register_report_fonts()
        total = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self._chrome(total)
            canvas.Canvas.showPage(self)
        canvas.Canvas.save(self)

    def _chrome(self, total: int) -> None:
        regular, _bold, _uni = register_report_fonts()
        page = self._pageNumber
        width, height = self._pagesize
        self.saveState()
        if page > 1:
            self.setStrokeColor(BORDER)
            self.setLineWidth(0.4)
            self.line(18 * mm, height - 12 * mm, width - 18 * mm, height - 12 * mm)
            self.setFillColor(TEXT_SECONDARY)
            self.setFont(regular, 8)
            self.drawString(18 * mm, height - 10 * mm, "Amazon Seller Intelligence")
            self.drawRightString(width - 18 * mm, height - 10 * mm, "Listing Analysis Report")
        self.setStrokeColor(BORDER)
        self.setLineWidth(0.4)
        self.line(18 * mm, 12 * mm, width - 18 * mm, 12 * mm)
        self.setFillColor(TEXT_SECONDARY)
        self.setFont(regular, 7.5)
        self.drawString(18 * mm, 8 * mm, f"ASIN {self._asin}  ·  Historical Analysis")
        self.drawRightString(width - 18 * mm, 8 * mm, f"Page {page} of {total}")
        self.restoreState()
