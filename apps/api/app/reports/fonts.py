from __future__ import annotations

from pathlib import Path

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

FONT_REGULAR = "Helvetica"
FONT_BOLD = "Helvetica-Bold"
_UNICODE = False
_RUPEE = False
_REGISTERED = False

_CANDIDATES = [
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
    Path("/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"),
    Path("/System/Library/Fonts/Geneva.ttf"),
    Path("/Library/Fonts/Arial Unicode.ttf"),
    Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
    Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
    Path("/usr/share/fonts/truetype/freefont/FreeSans.ttf"),
]

_BOLD_CANDIDATES = [
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    Path("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
    Path("/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf"),
    Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
    Path("/usr/share/fonts/truetype/freefont/FreeSansBold.ttf"),
]


def _family_hint(path: Path) -> str:
    name = path.name.lower()
    for token in ("dejavu", "liberation", "noto", "freesans", "arial"):
        if token in name:
            return token
    return path.stem.lower()


def register_report_fonts() -> tuple[str, str, bool]:
    """Register an embedded Unicode TTF when available. Safe to call repeatedly."""
    global FONT_REGULAR, FONT_BOLD, _UNICODE, _RUPEE, _REGISTERED
    if _REGISTERED:
        return FONT_REGULAR, FONT_BOLD, _UNICODE
    regular = next((path for path in _CANDIDATES if path.is_file()), None)
    bold = next((path for path in _BOLD_CANDIDATES if path.is_file()), None)
    if regular is not None:
        pdfmetrics.registerFont(TTFont("ASI-Sans", str(regular), asciiReadable=False))
        FONT_REGULAR = "ASI-Sans"
        _UNICODE = True
        if bold is not None and _family_hint(regular) == _family_hint(bold):
            pdfmetrics.registerFont(TTFont("ASI-Sans-Bold", str(bold), asciiReadable=False))
            FONT_BOLD = "ASI-Sans-Bold"
        else:
            FONT_BOLD = "Helvetica-Bold"
        face = pdfmetrics.getFont(FONT_REGULAR).face
        mapping = getattr(face, "charToGlyph", {}) or {}
        _RUPEE = 0x20B9 in mapping
    _REGISTERED = True
    return FONT_REGULAR, FONT_BOLD, _UNICODE


def font_supports_unicode() -> bool:
    register_report_fonts()
    return _UNICODE


def font_supports_rupee() -> bool:
    register_report_fonts()
    return _RUPEE
