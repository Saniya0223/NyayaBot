"""OCR.space client: the only module that talks to OCR.space.

It receives image bytes for one image or one rendered PDF page and nothing else
(never case data). It never invents text: an empty OCR result is returned as empty.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from app.config import settings


logger = logging.getLogger("uvicorn.error")

# OCR.space free-tier upload limit; callers downscale images to stay under it.
OCR_MAX_IMAGE_BYTES = 1_000_000
OCR_LANGUAGE = "eng"
OCR_ENGINE = "2"


class OcrError(Exception):
    """A recoverable OCR failure. `code` is safe to store and show."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class OcrResult:
    text: str


def ocr_configured() -> bool:
    return bool(settings.OCR_SPACE_API_KEY.strip())


def _normalize(text: str) -> str:
    lines = [" ".join(line.split()) for line in text.replace("\r", "\n").split("\n")]
    return "\n".join(line for line in lines if line).strip()


async def extract_text(image_bytes: bytes, filename: str, content_type: str = "image/jpeg") -> OcrResult:
    api_key = settings.OCR_SPACE_API_KEY.strip()
    if not api_key:
        raise OcrError("OCR_UNAVAILABLE", "OCR is not configured on this server.")
    data = {
        "language": OCR_LANGUAGE,
        "OCREngine": OCR_ENGINE,
        "scale": "true",
        "detectOrientation": "true",
        "isOverlayRequired": "false",
    }
    try:
        async with httpx.AsyncClient(timeout=settings.OCR_SPACE_TIMEOUT_SECONDS) as client:
            response = await client.post(
                settings.OCR_SPACE_ENDPOINT,
                headers={"apikey": api_key},
                data=data,
                files={"file": (filename, image_bytes, content_type)},
            )
    except httpx.TimeoutException as exc:
        logger.warning("service=ocr_space event=timeout")
        raise OcrError("OCR_TIMEOUT", "The OCR service timed out.") from exc
    except httpx.HTTPError as exc:
        logger.warning("service=ocr_space event=request_failed error_type=%s", type(exc).__name__)
        raise OcrError("OCR_FAILED", "The OCR service could not be reached.") from exc

    if response.status_code != 200:
        logger.warning("service=ocr_space event=http_error status=%s", response.status_code)
        raise OcrError("OCR_FAILED", "The OCR service returned an error.")
    try:
        payload = response.json()
    except ValueError as exc:
        raise OcrError("OCR_FAILED", "The OCR service returned an unreadable response.") from exc
    if not isinstance(payload, dict) or payload.get("IsErroredOnProcessing"):
        logger.warning("service=ocr_space event=processing_error exit_code=%s", (payload or {}).get("OCRExitCode"))
        raise OcrError("OCR_FAILED", "The OCR service could not process this image.")

    parsed = payload.get("ParsedResults") or []
    texts = []
    for result in parsed:
        if not isinstance(result, dict):
            continue
        if str(result.get("FileParseExitCode", 1)) not in {"1"}:
            raise OcrError("OCR_FAILED", "The OCR service could not parse this image.")
        texts.append(result.get("ParsedText") or "")
    return OcrResult(text=_normalize("\n".join(texts)))
