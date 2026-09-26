"""Evidence processing pipeline and its read models.

UPLOADED → PROCESSING → COMPLETED / FAILED. Extraction and AI analysis are
separate stages: an analysis failure keeps the extracted text, and a failed
OCR page keeps the other pages. The original file is only ever read.
Processing runs as a FastAPI background task with its own DB session.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Optional

from sqlalchemy.orm import Session

from app.db.models import ChatCaseSessionModel, EvidenceFileModel
from app.db.session import SessionLocal
from app.llm.contracts import LLMProvider
from app.schemas.chat import StructuredCaseProfile
from app.services.evidence_analysis import analyze_evidence
from app.services.evidence_extraction import extract_evidence


logger = logging.getLogger("uvicorn.error")

STALE_AFTER = timedelta(minutes=15)   # a PROCESSING/UPLOADED record older than this was interrupted

ERROR_MESSAGES = {
    "CORRUPTED_PDF": "The PDF appears to be damaged and could not be read. The original file is kept.",
    "PASSWORD_PROTECTED_PDF": "This PDF is password-protected. Upload an unlocked copy to extract its text. The original file is kept.",
    "UNSUPPORTED_FILE": "Text can't be extracted from this file type (for example, old .doc files). The original is kept; upload a PDF or DOCX copy to extract text.",
    "UNREADABLE_IMAGE": "The image could not be read. The original file is kept.",
    "UNREADABLE_DOCX": "The DOCX file could not be opened. The original file is kept.",
    "OCR_UNAVAILABLE": "Text recognition (OCR) isn't configured on this server, so scanned pages and images can't be read yet. The original file is kept.",
    "OCR_TIMEOUT": "Text recognition timed out. You can retry.",
    "OCR_FAILED": "Text recognition failed. You can retry.",
    "EXTRACTION_FAILED": "Text extraction failed. You can retry.",
    "FILE_MISSING": "The stored file could not be found. Please upload it again.",
    "PROCESSING_INTERRUPTED": "Processing was interrupted. You can retry.",
    "PROCESSING_ERROR": "Processing failed unexpectedly. You can retry.",
    "LLM_ANALYSIS_FAILED": "Text was extracted, but AI analysis failed. You can retry the analysis.",
}
NON_RETRYABLE = {"CORRUPTED_PDF", "PASSWORD_PROTECTED_PDF", "UNSUPPORTED_FILE", "UNREADABLE_IMAGE", "UNREADABLE_DOCX", "FILE_MISSING"}


# ── Status read model ────────────────────────────────────────────────────────

def effective_status(record: EvidenceFileModel, now: Optional[datetime] = None) -> tuple[str, Optional[str]]:
    """Status shown to users; an abandoned run is reported as a retryable failure, never an endless spinner."""
    now = now or datetime.utcnow()
    status = record.processing_status
    if status is None:
        return "NOT_PROCESSED", None
    if status in {"UPLOADED", "PROCESSING"}:
        since = record.processing_started_at or record.uploaded_at
        if since is None or now - since > STALE_AFTER:
            return "FAILED", "PROCESSING_INTERRUPTED"
    return status, record.processing_error_code


def retry_stage(record: EvidenceFileModel) -> Optional[str]:
    """"all" re-extracts (reusing finished OCR pages), "analysis" re-runs only AI analysis."""
    status, error = effective_status(record)
    if status in {"UPLOADED", "PROCESSING"}:
        return None
    if status in {"NOT_PROCESSED", "FAILED"}:
        return "all" if error not in NON_RETRYABLE else None
    extraction = record.extraction_data or {}
    if extraction.get("failed_pages"):
        return "all"
    if record.analysis_status in {"FAILED", "LIMITED"}:
        return "analysis"
    return None


def evidence_summary(record: EvidenceFileModel) -> dict[str, Any]:
    status, error = effective_status(record)
    extraction = record.extraction_data or {}
    analysis = record.analysis_data or {}
    analysis_error = analysis.get("error_code")
    shown_error = error or analysis_error
    return {
        "id": record.id,
        "name": record.file_name,
        "file_type": record.file_type,
        "uploaded_at": record.uploaded_at.isoformat() if record.uploaded_at else None,
        "download_url": f"/api/v1/evidence/{record.id}/download",
        "processing_status": status,
        "error_code": shown_error,
        "error_message": ERROR_MESSAGES.get(shown_error) if shown_error else None,
        "retryable": retry_stage(record) is not None,
        "analysis_status": record.analysis_status,
        "analysis_mode": analysis.get("mode"),
        "page_count": record.page_count,
        "has_readable_text": extraction.get("has_readable_text"),
        "methods": extraction.get("methods", []),
        "direct_pages": extraction.get("direct_pages", 0),
        "ocr_pages": extraction.get("ocr_pages", 0),
        "failed_pages": extraction.get("failed_pages", []),
        "findings_count": len(analysis.get("findings", [])),
        "candidate_count": len(analysis.get("candidate_facts", {})),
        "conflict_count": len(analysis.get("conflicts", [])),
        "review_status": (analysis.get("review") or {}).get("status", "NONE"),
    }


def evidence_detail(record: EvidenceFileModel) -> dict[str, Any]:
    analysis = record.analysis_data or {}
    extraction = record.extraction_data or {}
    return {
        **evidence_summary(record),
        "pages": [
            {key: page.get(key) for key in ("page_number", "method", "status", "error_code", "has_readable_text", "text")}
            for page in extraction.get("pages", [])
        ],
        "warnings": extraction.get("warnings", []),
        "analysis": {
            key: analysis.get(key)
            for key in ("mode", "document_type", "summary", "findings", "candidate_facts", "corroborated",
                        "conflicts", "deadlines", "notes", "unanalyzed_pages")
        } if analysis else None,
    }


# ── Pipeline ─────────────────────────────────────────────────────────────────

def _profile_for(db: Session, case_id: str) -> Optional[StructuredCaseProfile]:
    record = db.query(ChatCaseSessionModel).filter(ChatCaseSessionModel.case_id == case_id).first()
    if not record:
        return None
    try:
        return StructuredCaseProfile.model_validate(record.profile_data)
    except Exception:
        return None


async def process_evidence(
    evidence_id: str,
    *,
    provider: LLMProvider,
    workflow_agent: Any,
    stage: str = "all",
    session_factory: Callable[[], Session] = SessionLocal,
) -> None:
    db = session_factory()
    record = None
    try:
        record = db.get(EvidenceFileModel, evidence_id)
        if record is None:
            return
        record.processing_status = "PROCESSING"
        record.processing_error_code = None
        record.processing_started_at = datetime.utcnow()
        record.processing_completed_at = None
        db.commit()

        extraction = record.extraction_data if stage == "analysis" else None
        if not extraction:
            try:
                content = Path(record.file_path).read_bytes()
            except OSError:
                record.processing_status, record.processing_error_code = "FAILED", "FILE_MISSING"
                db.commit()
                return
            result = await extract_evidence(record.file_name, content, previous=record.extraction_data)
            extraction = result.data
            record.extraction_data = extraction
            record.page_count = extraction.get("page_count")
            logger.info(
                "evidence_id=%s case_id=%s file_type=%s pages=%s direct_pages=%s ocr_pages=%s failed_pages=%s event=evidence_extracted",
                record.id, record.case_id, extraction.get("file_type"), extraction.get("page_count"),
                extraction.get("direct_pages"), extraction.get("ocr_pages"), len(extraction.get("failed_pages", [])),
            )
            if result.error_code:
                record.processing_status, record.processing_error_code = "FAILED", result.error_code
                record.analysis_status = "SKIPPED"
                record.processing_completed_at = datetime.utcnow()
                db.commit()
                logger.info("evidence_id=%s status=FAILED error_code=%s event=evidence_processed", record.id, result.error_code)
                return
            db.commit()

        analysis_status, analysis = await analyze_evidence(
            evidence_id=record.id,
            file_name=record.file_name,
            extraction=extraction,
            user_excerpt=record.user_excerpt,
            doc_type_hint=record.doc_type_hint or "",
            profile=_profile_for(db, record.case_id),
            provider=provider,
            workflow_agent=workflow_agent,
        )
        record.analysis_status = analysis_status
        record.analysis_data = analysis
        record.processing_status = "COMPLETED"
        record.processing_completed_at = datetime.utcnow()
        db.commit()
        logger.info(
            "evidence_id=%s case_id=%s status=COMPLETED analysis_status=%s findings=%s conflicts=%s event=evidence_processed",
            record.id, record.case_id, analysis_status, len(analysis.get("findings", [])), len(analysis.get("conflicts", [])),
        )
    except Exception as exc:  # noqa: BLE001 - a background task must always settle its status
        logger.error("evidence_id=%s event=evidence_processing_error error_type=%s", evidence_id, type(exc).__name__)
        db.rollback()
        if record is not None:
            record.processing_status, record.processing_error_code = "FAILED", "PROCESSING_ERROR"
            db.commit()
    finally:
        db.close()


# ── Review hand-off to the existing confirmation flow ────────────────────────

def _where(source: Optional[dict]) -> str:
    if not source:
        return ""
    parts = []
    if source.get("page_number"):
        parts.append(f"page {source['page_number']}")
    if source.get("method") == "ocr":
        parts.append("OCR, may contain recognition errors")
    return f" ({', '.join(parts)})" if parts else ""


def _label(field: str) -> str:
    return field.replace("_", " ").title()


def build_review_turn(record: EvidenceFileModel) -> tuple[str, list[str], Optional[dict]]:
    """Reply text, quick replies, and the `pending_document_extraction` payload (or None)."""
    analysis = record.analysis_data or {}
    candidates: dict = analysis.get("candidate_facts") or {}
    conflicts: list = analysis.get("conflicts") or []
    lines = [f"NyayaBot identified the following in {record.file_name}. It comes from the uploaded document and is not added to your case until you confirm it."]
    if candidates:
        lines.append("\n".join(f"• {_label(field)}: {item['value']}{_where(item.get('source'))}" for field, item in candidates.items()))
    if conflicts:
        lines.append("These differ from your case, so I did not change anything:")
        lines.append("\n".join(
            f"• {_label(item['field'])}: your case has {item['current_value']}, but the document appears to show "
            f"{item['evidence_value']}{_where(item.get('source'))}. Tell me which value is correct."
            for item in conflicts
        ))
    pending = None
    if candidates:
        lines.append("Please confirm these details before I add them. Document extraction can be wrong.")
        pending = {
            "file_name": record.file_name,
            "evidence_id": record.id,
            "facts": {field: item["value"] for field, item in candidates.items()},
            "sources": {field: item.get("source") for field, item in candidates.items()},
            "analysis_summary": analysis.get("summary", ""),
            "source": "evidence_analysis",
        }
        quick_replies = ["Details are correct", "I need to correct them"]
    else:
        quick_replies = ["Continue my case"]
    return "\n\n".join(lines), quick_replies, pending


# ── Chat context (current case only) ─────────────────────────────────────────

PAGE_REQUEST = re.compile(r"(?:\bpage|\bpg\.?|पेज|पृष्ठ)\s*(?:no\.?|number|#)?\s*(\d{1,4})", re.IGNORECASE)
MAX_CONTEXT_FINDINGS = 15
MAX_PAGE_TEXT_CHARS = 4000


def _mentions(message: str, file_name: str) -> bool:
    stem = Path(file_name).stem.casefold()
    tokens = [token for token in re.split(r"[^\w]+", stem) if len(token) >= 4]
    lowered = message.casefold()
    return any(token in lowered for token in tokens)


def evidence_chat_context(db: Session, case_id: str, message: str) -> dict[str, Any]:
    """Compact findings for this case's evidence, plus raw text only for pages the user asked about."""
    records = (
        db.query(EvidenceFileModel)
        .filter(EvidenceFileModel.case_id == case_id)
        .order_by(EvidenceFileModel.uploaded_at)
        .all()
    )
    if not records:
        return {}
    documents = []
    for record in records:
        status, _ = effective_status(record)
        analysis = record.analysis_data or {}
        entry: dict[str, Any] = {"evidence_id": record.id, "file_name": record.file_name, "processing_status": status}
        if status == "COMPLETED":
            entry.update({
                "document_type": analysis.get("document_type"),
                "summary": (analysis.get("summary") or "")[:400],
                "has_readable_text": (record.extraction_data or {}).get("has_readable_text"),
                "findings": [
                    {
                        "type": item["type"], "value": item["value"], "statement": (item.get("statement") or "")[:200],
                        "page": (item.get("source") or {}).get("page_number"),
                        "method": (item.get("source") or {}).get("method"), "clarity": item.get("clarity"),
                    }
                    for item in analysis.get("findings", [])[:MAX_CONTEXT_FINDINGS]
                ],
                "conflicts_with_case": analysis.get("conflicts", []),
                "awaiting_user_review": (analysis.get("review") or {}).get("status") in {"PENDING", "OFFERED"},
            })
        documents.append(entry)

    requested: list[dict] = []
    pages_asked = {int(match) for match in PAGE_REQUEST.findall(message or "")}
    if pages_asked:
        named = [record for record in records if _mentions(message, record.file_name)]
        for record in (named or records):
            for page in (record.extraction_data or {}).get("pages", []):
                if page.get("page_number") in pages_asked and len(requested) < 2:
                    requested.append({
                        "file_name": record.file_name,
                        "page_number": page["page_number"],
                        "method": page.get("method"),
                        "text": (page.get("text") or "")[:MAX_PAGE_TEXT_CHARS],
                        "has_readable_text": page.get("has_readable_text"),
                    })
    return {
        "note": "Evidence-derived and unverified. Not confirmed case facts. OCR text may contain errors.",
        "documents": documents,
        "requested_pages": requested,
    }
