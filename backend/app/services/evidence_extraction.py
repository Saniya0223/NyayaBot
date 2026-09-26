"""Text extraction for uploaded evidence.

PDF pages are handled one by one: PyMuPDF text first, and only pages without
meaningful text that look scanned are rendered and sent to OCR. Images go to
OCR. DOCX and plain text are read directly. Nothing here invents text; a page
without readable text is stored as empty.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from app.services import ocr_service
from app.services.ocr_service import OCR_MAX_IMAGE_BYTES, OcrError


logger = logging.getLogger("uvicorn.error")

EXTRACTION_VERSION = 1

# "Meaningful text" heuristic: a page must have enough visible characters, be
# mostly letters/digits, and contain a few real words. Tiny artifacts (page
# numbers, stray marks from a scanner's text layer) fail these checks.
MIN_MEANINGFUL_CHARS = 25       # non-whitespace characters
MIN_ALNUM_RATIO = 0.5           # share of non-whitespace characters that are letters/digits
MIN_MEANINGFUL_WORDS = 3        # tokens of two or more letters/digits

# A page without meaningful text is sent to OCR only if it looks image-based.
MIN_VECTOR_DRAWINGS_FOR_OCR = 50
OCR_RENDER_DPI = (200, 150, 110)   # tried in order until the JPEG fits OCR_MAX_IMAGE_BYTES
OCR_JPEG_QUALITY = 80
OCR_MAX_PAGES = 25                 # per document, to bound external calls


class ExtractionError(Exception):
    """A whole-file failure. The original upload is untouched."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class ExtractionResult:
    data: dict[str, Any]
    error_code: Optional[str] = None      # set when the file as a whole could not be processed


def normalize_text(text: str) -> str:
    lines = [" ".join(line.split()) for line in (text or "").replace("\r", "\n").split("\n")]
    return "\n".join(line for line in lines if line).strip()


def is_meaningful_text(text: str) -> bool:
    compact = "".join((text or "").split())
    if len(compact) < MIN_MEANINGFUL_CHARS:
        return False
    alnum = sum(1 for ch in compact if ch.isalnum())
    if alnum / len(compact) < MIN_ALNUM_RATIO:
        return False
    return len(re.findall(r"[^\W_]{2,}", text)) >= MIN_MEANINGFUL_WORDS


def _page(page_number: Optional[int], method: str, text: str, status: str, error_code: str | None = None) -> dict:
    return {
        "page_number": page_number,
        "method": method,
        "text": text,
        "has_readable_text": is_meaningful_text(text),
        "status": status,
        "error_code": error_code,
    }


def _finish(file_type: str, pages: list[dict], page_count: Optional[int], warnings: list[str]) -> dict:
    return {
        "version": EXTRACTION_VERSION,
        "file_type": file_type,
        "page_count": page_count,
        "pages": pages,
        "methods": sorted({page["method"] for page in pages if page["method"] != "none"}),
        "direct_pages": sum(1 for page in pages if page["method"] == "text_extraction"),
        "ocr_pages": sum(1 for page in pages if page["method"] == "ocr" and page["status"] == "ok"),
        "failed_pages": [page["page_number"] for page in pages if page["status"] in {"ocr_failed", "ocr_unavailable", "ocr_skipped"}],
        "has_readable_text": any(page["has_readable_text"] for page in pages),
        "warnings": warnings,
    }


# ── File type detection ──────────────────────────────────────────────────────

def detect_file_type(file_name: str) -> str:
    extension = os.path.splitext(file_name)[1].lower()
    return {
        ".pdf": "pdf", ".png": "image", ".jpg": "image", ".jpeg": "image",
        ".docx": "docx", ".txt": "text", ".eml": "text",
    }.get(extension, "unsupported")


# ── PDF ──────────────────────────────────────────────────────────────────────

def _render_for_ocr(page) -> bytes:
    import pymupdf

    image = b""
    for dpi in OCR_RENDER_DPI:
        pixmap = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB, alpha=False)
        image = pixmap.tobytes("jpeg", jpg_quality=OCR_JPEG_QUALITY)
        if len(image) <= OCR_MAX_IMAGE_BYTES:
            break
    return image


def _looks_scanned(page) -> bool:
    try:
        if page.get_images(full=False):
            return True
        return len(page.get_drawings()) >= MIN_VECTOR_DRAWINGS_FOR_OCR
    except Exception:
        return True


def _plan_pdf(content: bytes, reusable: dict[int, dict]) -> tuple[int, list[dict]]:
    """Synchronous PyMuPDF pass: direct text per page and rendered images for OCR pages."""
    import pymupdf

    try:
        document = pymupdf.open(stream=content, filetype="pdf")
    except Exception as exc:
        raise ExtractionError("CORRUPTED_PDF", "The PDF could not be opened.") from exc
    try:
        if document.needs_pass:
            raise ExtractionError(
                "PASSWORD_PROTECTED_PDF",
                "This PDF is password-protected. Upload an unlocked copy to extract its text.",
            )
        if document.page_count == 0:
            raise ExtractionError("CORRUPTED_PDF", "The PDF has no pages.")
        plans = []
        ocr_budget = OCR_MAX_PAGES
        for index in range(document.page_count):
            number = index + 1
            try:
                page = document.load_page(index)
                direct = normalize_text(page.get_text("text"))
            except Exception:
                plans.append({"page_number": number, "direct": "", "needs_ocr": False, "broken": True})
                continue
            plan = {"page_number": number, "direct": direct, "needs_ocr": False, "image": None}
            if not is_meaningful_text(direct) and _looks_scanned(page):
                plan["needs_ocr"] = True
                if number in reusable:
                    plan["reuse"] = reusable[number]
                elif ocr_budget > 0:
                    ocr_budget -= 1
                    plan["image"] = _render_for_ocr(page)
            plans.append(plan)
        return document.page_count, plans
    finally:
        document.close()


async def _extract_pdf(content: bytes, file_name: str, previous: Optional[dict]) -> ExtractionResult:
    # On retry, successful OCR pages are reused so OCR.space is not called again for them.
    reusable = {
        page["page_number"]: page
        for page in (previous or {}).get("pages", [])
        if page.get("method") == "ocr" and page.get("status") == "ok" and page.get("page_number")
    }
    page_count, plans = await asyncio.to_thread(_plan_pdf, content, reusable)

    pages: list[dict] = []
    warnings: list[str] = []
    ocr_down: Optional[str] = None
    for plan in plans:
        number = plan["page_number"]
        if plan.get("broken"):
            pages.append(_page(number, "none", "", "extraction_failed", "PAGE_UNREADABLE"))
            warnings.append("PAGE_UNREADABLE")
            continue
        if not plan["needs_ocr"]:
            status = "ok" if is_meaningful_text(plan["direct"]) else "no_text"
            pages.append(_page(number, "text_extraction", plan["direct"], status))
            continue
        if plan.get("reuse"):
            pages.append(plan["reuse"])
            continue
        if plan["image"] is None:
            pages.append(_page(number, "ocr", plan["direct"], "ocr_skipped", "OCR_PAGE_LIMIT"))
            warnings.append("OCR_PAGE_LIMIT")
            continue
        if ocr_down:
            pages.append(_page(number, "ocr", plan["direct"], "ocr_unavailable" if ocr_down == "OCR_UNAVAILABLE" else "ocr_failed", ocr_down))
            continue
        try:
            result = await ocr_service.extract_text(plan["image"], f"{os.path.splitext(file_name)[0]}-page-{number}.jpg")
            text = result.text
            pages.append(_page(number, "ocr", text, "ok" if text else "no_text"))
        except OcrError as exc:
            if exc.code == "OCR_UNAVAILABLE":
                ocr_down = exc.code
            pages.append(_page(number, "ocr", plan["direct"], "ocr_unavailable" if exc.code == "OCR_UNAVAILABLE" else "ocr_failed", exc.code))
            warnings.append(exc.code)
    data = _finish("pdf", pages, page_count, sorted(set(warnings)))
    # Whole-file failure only when OCR problems left nothing readable at all.
    if data["failed_pages"] and not data["has_readable_text"]:
        codes = [page["error_code"] for page in pages if page["error_code"]]
        return ExtractionResult(data, error_code=codes[0] if codes else "OCR_FAILED")
    return ExtractionResult(data)


# ── Images ───────────────────────────────────────────────────────────────────

def _prepare_image(content: bytes, extension: str) -> tuple[bytes, str]:
    """Validate the image decodes and downscale it for OCR if needed."""
    import pymupdf

    try:
        document = pymupdf.open(stream=content, filetype=extension.lstrip(".") or "png")
        page = document.load_page(0)
    except Exception as exc:
        raise ExtractionError("UNREADABLE_IMAGE", "The image could not be read.") from exc
    try:
        mime = "image/png" if extension == ".png" else "image/jpeg"
        if len(content) <= OCR_MAX_IMAGE_BYTES:
            return content, mime
        width = max(page.rect.width, 1)
        image = b""
        for target in (2400, 1800, 1400, 1000):
            scale = min(1.0, target / width)
            pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), colorspace=pymupdf.csRGB, alpha=False)
            image = pixmap.tobytes("jpeg", jpg_quality=OCR_JPEG_QUALITY)
            if len(image) <= OCR_MAX_IMAGE_BYTES:
                break
        return image, "image/jpeg"
    finally:
        document.close()


async def _extract_image(content: bytes, file_name: str) -> ExtractionResult:
    extension = os.path.splitext(file_name)[1].lower()
    image, mime = await asyncio.to_thread(_prepare_image, content, extension)
    try:
        result = await ocr_service.extract_text(image, os.path.basename(file_name), mime)
    except OcrError as exc:
        page = _page(None, "ocr", "", "ocr_unavailable" if exc.code == "OCR_UNAVAILABLE" else "ocr_failed", exc.code)
        return ExtractionResult(_finish("image", [page], None, [exc.code]), error_code=exc.code)
    # No text is an honest result for a photo, not a failure.
    return ExtractionResult(_finish("image", [_page(None, "ocr", result.text, "ok" if result.text else "no_text")], None, []))


# ── DOCX / plain text ────────────────────────────────────────────────────────

def _docx_text(content: bytes) -> str:
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    try:
        document = Document(io.BytesIO(content))
    except Exception as exc:
        raise ExtractionError("UNREADABLE_DOCX", "The DOCX file could not be opened.") from exc
    blocks: list[str] = []
    # Walk the body in order so paragraphs and tables keep their relative position.
    for child in document.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            text = Paragraph(child, document).text.strip()
            if text:
                blocks.append(text)
        elif tag == "tbl":
            for row in Table(child, document).rows:
                cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                if cells:
                    blocks.append(" | ".join(dict.fromkeys(cells)))
    return normalize_text("\n".join(blocks))


async def _extract_docx(content: bytes) -> ExtractionResult:
    text = await asyncio.to_thread(_docx_text, content)
    return ExtractionResult(_finish("docx", [_page(None, "text_extraction", text, "ok" if text else "no_text")], None, []))


def _extract_plain(content: bytes) -> ExtractionResult:
    text = normalize_text(content.decode("utf-8", errors="replace"))
    return ExtractionResult(_finish("text", [_page(None, "text_extraction", text, "ok" if text else "no_text")], None, []))


# ── Entry point ──────────────────────────────────────────────────────────────

async def extract_evidence(file_name: str, content: bytes, previous: Optional[dict] = None) -> ExtractionResult:
    file_type = detect_file_type(file_name)
    try:
        if file_type == "pdf":
            return await _extract_pdf(content, file_name, previous)
        if file_type == "image":
            return await _extract_image(content, file_name)
        if file_type == "docx":
            return await _extract_docx(content)
        if file_type == "text":
            return _extract_plain(content)
        raise ExtractionError("UNSUPPORTED_FILE", "Text extraction is not supported for this file type.")
    except ExtractionError as exc:
        return ExtractionResult(_finish(file_type, [], None, [exc.code]), error_code=exc.code)
    except Exception as exc:  # noqa: BLE001 - never let a parser crash processing
        logger.warning("event=evidence_extraction_error file_type=%s error_type=%s", file_type, type(exc).__name__)
        return ExtractionResult(_finish(file_type, [], None, ["EXTRACTION_FAILED"]), error_code="EXTRACTION_FAILED")
