"""Read existing PDF text and positions, without performing OCR or networking.

Call under materials.PDF_LOCK: PDFium must not run concurrently with rendering.
Persisted page_index is ONE based. Coordinates use the same PDFium device
transform as the existing renderer, including intrinsic rotation and CropBox.
"""
from __future__ import annotations

import math
import unicodedata
from pathlib import Path

import pypdfium2 as pdfium

TEXT_LAYER_VERSION = "pdfium-existing-text-v1"
MAX_PAGE_CHARACTERS = 50_000


def _empty(reason: str) -> dict:
    return {"usable": False, "text": "", "regions": [], "reason": reason,
            "extraction_version": TEXT_LAYER_VERSION}


def _meaningful(char: str) -> bool:
    return unicodedata.category(char).startswith(("L", "N"))


def _polygon(box):
    left, top, right, bottom = box
    return [{"X": left, "Y": top}, {"X": right, "Y": top},
            {"X": right, "Y": bottom}, {"X": left, "Y": bottom}]


def _large_image(page) -> bool:
    left, bottom, right, top = page.get_bbox()
    area = max(0, right - left) * max(0, top - bottom)
    if area <= 0:
        return False
    for obj in page.get_objects(filter=[pdfium.raw.FPDF_PAGEOBJ_IMAGE]):
        x0, y0, x1, y1 = obj.get_bounds()
        overlap = max(0, min(right, x1) - max(left, x0)) * max(0, min(top, y1) - max(bottom, y0))
        if overlap / area >= .65:
            return True
    return False


def _extract(page, record: dict) -> dict:
    width, height = int(record["width_px"]), int(record["height_px"])
    if width < 1 or height < 1:
        return _empty("INVALID_PAGE_SIZE")
    textpage = page.get_textpage()
    try:
        count = textpage.count_chars()
        if not count:
            return _empty("NO_TEXT_LAYER")
        if count > MAX_PAGE_CHARACTERS:
            return _empty("TEXT_LAYER_TOO_LARGE")
        chars = []
        for index in range(count):
            code = pdfium.raw.FPDFText_GetUnicode(textpage, index)
            chars.append(chr(code) if 0 < code <= 0x10ffff and not 0xd800 <= code <= 0xdfff else "\ufffd")
        text = "".join(chars).replace("\r\n", "\n").replace("\r", "\n")
        meaningful = sum(_meaningful(c) for c in text)
        visible = [c for c in text if not c.isspace()]
        bad = sum(c == "\ufffd" or unicodedata.category(c) in ("Co", "Cs", "Cn") for c in visible)
        if meaningful < 16:
            return _empty("INSUFFICIENT_TEXT")
        if bad / max(1, len(visible)) > .1:
            return _empty("UNREADABLE_TEXT_ENCODING")
        large_image = _large_image(page)
        # A page number or small header over a full-page scan is not a usable
        # representation of the scanned body. Such pages still need OCR/manual.
        if large_image and meaningful < 80:
            return _empty("SPARSE_TEXT_OVER_IMAGE")
        transform = pdfium.PdfPosConv(page, (0, 0, width, height, 0))
        lines, words, line_text, last_box = [], [], [], None
        positioned = 0

        def flush():
            nonlocal words, line_text, last_box
            if words and "".join(line_text).strip():
                points = [point for word in words for point in word["polygon"]]
                box = (min(p["X"] for p in points), min(p["Y"] for p in points),
                       max(p["X"] for p in points), max(p["Y"] for p in points))
                lines.append({"kind": "TEXT", "text": "".join(line_text).strip(),
                              "confidence": None, "polygon_json": _polygon(box),
                              "extra_json": {"words": words, "source": "PDF_TEXT_LAYER"}})
            words, line_text, last_box = [], [], None

        for index, char in enumerate(chars):
            if char in "\r\n":
                flush()
                continue
            if char.isspace():
                if line_text:
                    line_text.append(char)
                continue
            if char == "\ufffd" or not char.isprintable():
                continue
            raw_box = textpage.get_charbox(index)
            if not all(math.isfinite(v) for v in raw_box):
                continue
            x0, y0, x1, y1 = raw_box
            points = [transform.to_bitmap(x, y) for x, y in ((x0,y0),(x0,y1),(x1,y0),(x1,y1))]
            box = (max(0, min(width, min(p[0] for p in points))),
                   max(0, min(height, min(p[1] for p in points))),
                   max(0, min(width, max(p[0] for p in points))),
                   max(0, min(height, max(p[1] for p in points))))
            if box[2] <= box[0] or box[3] <= box[1]:
                continue
            if last_box and (abs((box[1]+box[3])/2-(last_box[1]+last_box[3])/2) > .8*max(box[3]-box[1],last_box[3]-last_box[1]) or len(words) >= 160):
                flush()
            line_text.append(char)
            words.append({"text": char, "polygon": _polygon(box), "confidence": None})
            positioned += int(_meaningful(char))
            last_box = box
        flush()
        if not lines or positioned / max(1, meaningful) < .8:
            return _empty("UNRELIABLE_TEXT_POSITIONS")
        return {"usable": True, "text": text.strip(), "regions": lines,
                "reason": "EXISTING_TEXT_LAYER", "extraction_version": TEXT_LAYER_VERSION,
                "meaningful_characters": meaningful, "positioned_characters": positioned,
                "has_large_image": large_image}
    finally:
        textpage.close()


def extract_pages_text(pdf_path: Path, page_records: list[dict]) -> dict:
    """Read many existing pages using one document handle; no generated assets."""
    results = {}
    try:
        with pdfium.PdfDocument(Path(pdf_path)) as document:
            for record in page_records:
                key = record.get("id", record["page_index"])
                try:
                    index = int(record["page_index"]) - 1
                    if not 0 <= index < len(document):
                        results[key] = _empty("INVALID_PAGE_INDEX")
                        continue
                    page = document[index]
                    try:
                        results[key] = _extract(page, record)
                    finally:
                        page.close()
                except Exception:
                    results[key] = _empty("TEXT_LAYER_UNAVAILABLE")
    except Exception:
        return {record.get("id", record["page_index"]): _empty("INVALID_PDF") for record in page_records}
    return results


def extract_page_text(pdf_path: Path, page_record: dict) -> dict:
    """Return text and per-character pixel positions, or an explicit fallback."""
    key = page_record.get("id", page_record["page_index"])
    return extract_pages_text(pdf_path, [page_record])[key]
