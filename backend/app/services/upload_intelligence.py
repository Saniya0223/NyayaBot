import io
import os
import re
from typing import Tuple


MAX_UPLOAD_BYTES = 10 * 1024 * 1024
ALLOWED_EXTENSIONS = {".pdf", ".doc", ".docx", ".png", ".jpg", ".jpeg", ".txt", ".eml"}


def sanitize_file_name(file_name: str | None) -> str:
    """Display name only; storage always uses a generated name."""
    name = os.path.basename((file_name or "").replace("\\", "/"))
    name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip().strip(".") or "evidence"
    stem, extension = os.path.splitext(name)
    return f"{stem[:200]}{extension[:10]}"


def _content_matches(extension: str, content: bytes) -> bool:
    head = content[:1024]
    if extension == ".pdf":
        return b"%PDF-" in head
    if extension == ".png":
        return content.startswith(b"\x89PNG\r\n\x1a\n")
    if extension in {".jpg", ".jpeg"}:
        return content.startswith(b"\xff\xd8\xff")
    if extension == ".docx":
        return content.startswith(b"PK\x03\x04")
    if extension == ".doc":
        return content.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")
    return b"\x00" not in content[:8192]  # .txt / .eml must be text


def validate_upload(file_name: str, content: bytes) -> str:
    extension = os.path.splitext(os.path.basename(file_name))[1].lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise ValueError("Unsupported file type. Use PDF, DOCX, TXT, EML, JPG, or PNG.")
    if not content:
        raise ValueError("The selected file is empty.")
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValueError("The selected file is larger than the 10 MB upload limit.")
    if not _content_matches(extension, content):
        raise ValueError(f"The file's content does not look like a {extension.lstrip('.').upper()} file.")
    return extension


def extract_upload_text(file_name: str, content: bytes) -> Tuple[str, str]:
    """Return extracted text and the extraction mode used; empty text is a safe fallback."""
    extension = os.path.splitext(os.path.basename(file_name))[1].lower()
    try:
        if extension in {".txt", ".eml"}:
            return content.decode("utf-8", errors="replace")[:50000], "plain_text"
        if extension == ".docx":
            from docx import Document

            document = Document(io.BytesIO(content))
            paragraphs = [paragraph.text for paragraph in document.paragraphs if paragraph.text.strip()]
            for table in document.tables:
                for row in table.rows:
                    paragraphs.append(" | ".join(cell.text.strip() for cell in row.cells if cell.text.strip()))
            return "\n".join(paragraphs)[:50000], "docx_text"
        if extension == ".pdf":
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(content))
            return "\n".join((page.extract_text() or "") for page in reader.pages)[:50000], "pdf_text"
    except Exception:
        return "", "extraction_unavailable"
    return "", "metadata_only"
