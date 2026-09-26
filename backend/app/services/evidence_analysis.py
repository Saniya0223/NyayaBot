"""Structured analysis of extracted evidence through the existing LLM provider.

The provider's `analyze_document` receives source-aware text ("[Page 2 | ocr]")
in page-bounded chunks. Every finding it returns is re-checked against the text
that was actually supplied: a value that is not in the text is dropped, and a
page reference is corrected to the page that really contains the value (or
removed). Results are evidence-derived candidates; they never overwrite case
facts, and values that differ from the current case are recorded as conflicts.
"""

from __future__ import annotations

import logging
import unicodedata
import uuid
from typing import Any, Optional

from app.llm.contracts import DocumentAnalysis, LLMProvider, LLMProviderError
from app.schemas.chat import DocumentUploadExtractionRequest, StructuredCaseProfile


logger = logging.getLogger("uvicorn.error")

ANALYSIS_VERSION = 1
ANALYSIS_CHUNK_CHARS = 12000     # per provider call; keeps each request well inside model limits
MAX_ANALYSIS_CHUNKS = 8          # bounds LLM calls per document; later pages are listed as unanalyzed
MIN_FACT_CONFIDENCE = 0.55       # same threshold the existing upload flow used
EVIDENCE_START = "<<<BEGIN UNTRUSTED EVIDENCE TEXT>>>"
EVIDENCE_END = "<<<END UNTRUSTED EVIDENCE TEXT>>>"


def _norm(text: Any) -> str:
    value = unicodedata.normalize("NFKC", str(text)).casefold()
    return "".join(ch for ch in value if not ch.isspace() and ch != ",")


def _label(source: dict) -> str:
    where = f"Page {source['page_number']}" if source["page_number"] is not None else (
        "User-pasted excerpt" if source["method"] == "user_excerpt" else "Document"
    )
    return f"[{where} | {source['method']}]"


def _split_text(text: str) -> list[str]:
    """Split at line boundaries into pieces no larger than one chunk (hard-wrap huge lines)."""
    parts: list[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > ANALYSIS_CHUNK_CHARS:
            if current:
                parts.append(current)
                current = ""
            parts.append(line[:ANALYSIS_CHUNK_CHARS])
            line = line[ANALYSIS_CHUNK_CHARS:]
        if current and len(current) + 1 + len(line) > ANALYSIS_CHUNK_CHARS:
            parts.append(current)
            current = ""
        current = f"{current}\n{line}" if current else line
    if current:
        parts.append(current)
    return parts


def build_sources(extraction: dict, user_excerpt: Optional[str] = None) -> list[dict]:
    """Readable pages as sources; a page larger than a chunk becomes several sources with the same page number."""
    sources: list[dict] = []
    for page in extraction.get("pages", []):
        if not page.get("has_readable_text"):
            continue
        for part in _split_text(page.get("text") or ""):
            sources.append({"page_number": page.get("page_number"), "method": page.get("method"), "text": part})
    excerpt = (user_excerpt or "").strip()
    if excerpt:
        sources.append({"page_number": None, "method": "user_excerpt", "text": excerpt[:ANALYSIS_CHUNK_CHARS]})
    return sources


def chunk_sources(sources: list[dict]) -> list[list[dict]]:
    chunks: list[list[dict]] = []
    current: list[dict] = []
    size = 0
    for source in sources:
        length = len(source["text"]) + 40
        if current and size + length > ANALYSIS_CHUNK_CHARS:
            chunks.append(current)
            current, size = [], 0
        current.append(source)
        size += length
    if current:
        chunks.append(current)
    return chunks


def render_chunk(file_name: str, chunk: list[dict]) -> str:
    body = "\n\n".join(f"{_label(source)}\n{source['text']}" for source in chunk)
    return f"{EVIDENCE_START}\n[EVIDENCE: {file_name}]\n\n{body}\n{EVIDENCE_END}"


def _source_ref(evidence_id: str, file_name: str, source: Optional[dict]) -> Optional[dict]:
    if source is None:
        return None
    return {
        "evidence_id": evidence_id,
        "file_name": file_name,
        "page_number": source["page_number"],
        "method": source["method"],
    }


def _locate(value: Any, chunk: list[dict], claimed_page: Optional[int] = None) -> tuple[Optional[dict], bool]:
    """Find the supplied source containing `value`; prefer the claimed page. Returns (source, corrected)."""
    needle = _norm(value)
    if not needle:
        return None, False
    matches = [source for source in chunk if needle in _norm(source["text"])]
    if not matches:
        return None, False
    for source in matches:
        if claimed_page is not None and source["page_number"] == claimed_page:
            return source, False
    return matches[0], claimed_page is not None


def _fact_needles(value: Any) -> list[str]:
    if isinstance(value, float) or (isinstance(value, int) and not isinstance(value, bool)):
        number = float(value)
        return [str(int(number)) if number.is_integer() else str(number)]
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _is_empty(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    return value in (None, "", [], {}) or (isinstance(value, (int, float)) and value == 0)


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    try:
        return abs(float(a) - float(b)) < 0.01
    except (TypeError, ValueError):
        return _norm(a) == _norm(b)


def _current_value(profile: Optional[StructuredCaseProfile], field: str) -> Any:
    if profile is None:
        return None
    if field in StructuredCaseProfile.model_fields:
        value = getattr(profile, field)
    else:
        value = profile.key_facts.get(field)
    return None if _is_empty(value) else value


def _classify_candidates(
    candidates: dict[str, dict],
    profile: Optional[StructuredCaseProfile],
) -> tuple[dict[str, dict], list[dict], list[dict]]:
    """Split into new candidates, values matching the case, and conflicts with the case."""
    new: dict[str, dict] = {}
    corroborated: list[dict] = []
    conflicts: list[dict] = []
    for field, item in candidates.items():
        current = _current_value(profile, field)
        if current is None:
            new[field] = item
        elif _same(current, item["value"]):
            corroborated.append({"field": field, "value": item["value"], "source": item["source"]})
        else:
            metadata = (profile.fact_metadata.get(field) or {}) if profile else {}
            conflicts.append({
                "field": field,
                "current_value": current,
                "current_confirmed": bool(metadata.get("confirmed")),
                "evidence_value": item["value"],
                "source": item["source"],
            })
    return new, corroborated, conflicts


def _ground_analysis(
    analysis: DocumentAnalysis,
    chunk: list[dict],
    evidence_id: str,
    file_name: str,
    stats: dict[str, int],
) -> tuple[list[dict], dict[str, dict], list[dict]]:
    findings: list[dict] = []
    for finding in analysis.findings:
        source, corrected = _locate(finding.value, chunk, finding.source_page)
        if source is None:
            stats["dropped_ungrounded"] += 1
            continue
        stats["corrected_sources"] += int(corrected)
        findings.append({
            "id": str(uuid.uuid4())[:8],
            "type": finding.type,
            "value": finding.value,
            "statement": finding.statement,
            "clarity": finding.clarity,
            "ocr_derived": source["method"] == "ocr",
            "source": _source_ref(evidence_id, file_name, source),
        })

    confidence_by_field = {item.field: item.confidence for item in analysis.confidence_by_field}
    candidates: dict[str, dict] = {}
    for field, value in analysis.facts.model_dump(exclude_none=True).items():
        if confidence_by_field.get(field, analysis.confidence) < MIN_FACT_CONFIDENCE:
            continue
        if isinstance(value, bool):
            candidates[field] = {"value": value, "source": None}
            continue
        located = [_locate(needle, chunk)[0] for needle in _fact_needles(value)]
        if not located or any(source is None for source in located):
            stats["dropped_ungrounded"] += 1
            continue
        candidates[field] = {"value": value, "source": _source_ref(evidence_id, file_name, located[0])}

    deadlines = []
    for text in analysis.explicit_deadlines:
        source, _ = _locate(text, chunk)
        if source is not None:
            deadlines.append({"text": text, "source": _source_ref(evidence_id, file_name, source)})
        else:
            stats["dropped_ungrounded"] += 1
    return findings, candidates, deadlines


def _rule_based_candidates(
    workflow_agent: Any,
    profile: StructuredCaseProfile,
    file_name: str,
    doc_type_hint: str,
    sources: list[dict],
    evidence_id: str,
) -> dict[str, dict]:
    """Existing deterministic upload extractor, run on a copy of the profile (no state change)."""
    text = "\n".join(source["text"] for source in sources)
    response = workflow_agent.process_document_upload(
        DocumentUploadExtractionRequest(
            case_id=profile.case_id, doc_type=doc_type_hint or "", file_name=file_name, simulated_content=text,
        ),
        profile.model_copy(deep=True),
    )
    facts = (response.case_profile.key_facts.get("pending_document_extraction") or {}).get("facts", {})
    candidates = {}
    for field, value in facts.items():
        located = [_locate(needle, sources)[0] for needle in _fact_needles(value)]
        source = located[0] if located and all(located) else None
        candidates[field] = {"value": value, "source": _source_ref(evidence_id, file_name, source)}
    return candidates


async def analyze_evidence(
    *,
    evidence_id: str,
    file_name: str,
    extraction: dict,
    user_excerpt: Optional[str],
    doc_type_hint: str,
    profile: Optional[StructuredCaseProfile],
    provider: LLMProvider,
    workflow_agent: Any,
) -> tuple[str, dict]:
    """Return (analysis_status, analysis_data). Never raises for provider problems."""
    sources = build_sources(extraction, user_excerpt)
    base: dict[str, Any] = {
        "version": ANALYSIS_VERSION, "mode": None, "document_type": None, "summary": "", "outcome": "UNCLEAR",
        "findings": [], "candidate_facts": {}, "corroborated": [], "conflicts": [], "deadlines": [],
        "notes": [], "unanalyzed_pages": [], "dropped_ungrounded": 0, "corrected_sources": 0,
        "chunks": 0, "error_code": None, "review": {"status": "NONE"},
    }
    if not sources:
        base["notes"].append("No readable text was extracted, so nothing was analyzed.")
        return "SKIPPED", base

    if not provider.status.configured:
        if profile is None:
            return "SKIPPED", base
        candidates = _rule_based_candidates(workflow_agent, profile, file_name, doc_type_hint, sources, evidence_id)
        new, corroborated, conflicts = _classify_candidates(candidates, profile)
        base.update(mode="rule_based", candidate_facts=new, corroborated=corroborated, conflicts=conflicts)
        base["notes"].append("AI analysis is unavailable; only basic rule-based extraction was used.")
        base["review"] = {"status": "PENDING" if new or conflicts else "NONE"}
        return "LIMITED", base

    chunks = chunk_sources(sources)
    analyzed, skipped = chunks[:MAX_ANALYSIS_CHUNKS], chunks[MAX_ANALYSIS_CHUNKS:]
    base["unanalyzed_pages"] = sorted({s["page_number"] for chunk in skipped for s in chunk if s["page_number"]})
    if skipped:
        base["notes"].append(
            f"The document is long; pages {base['unanalyzed_pages'][0]}–{base['unanalyzed_pages'][-1]} were extracted but not analyzed."
            if base["unanalyzed_pages"] else "Part of the document was extracted but not analyzed."
        )
    stats = {"dropped_ungrounded": 0, "corrected_sources": 0}
    findings: list[dict] = []
    candidates: dict[str, dict] = {}
    deadlines: list[dict] = []
    summaries: list[str] = []
    try:
        for chunk in analyzed:
            analysis = await provider.analyze_document(render_chunk(file_name, chunk), doc_type_hint or "")
            chunk_findings, chunk_candidates, chunk_deadlines = _ground_analysis(analysis, chunk, evidence_id, file_name, stats)
            findings.extend(chunk_findings)
            for field, item in chunk_candidates.items():
                if field in candidates and not _same(candidates[field]["value"], item["value"]):
                    base["notes"].append(f"The document shows more than one value for {field.replace('_', ' ')}.")
                    continue
                candidates.setdefault(field, item)
            deadlines.extend(chunk_deadlines)
            base["document_type"] = base["document_type"] or analysis.document_type
            if analysis.summary:
                summaries.append(analysis.summary.strip())
            if base["outcome"] == "UNCLEAR" and analysis.outcome != "UNCLEAR":
                base["outcome"] = analysis.outcome
            base["notes"].extend(note for note in analysis.analysis_notes[:3])
    except LLMProviderError as exc:
        logger.warning("evidence_id=%s event=evidence_analysis_failed error_type=%s", evidence_id, type(exc).__name__)
        failed = {**base, "mode": "llm", "error_code": "LLM_ANALYSIS_FAILED"}
        return "FAILED", failed

    unique: dict[tuple, dict] = {}
    for finding in findings:
        key = (finding["type"], _norm(finding["value"]), (finding["source"] or {}).get("page_number"))
        unique.setdefault(key, finding)
    if base["outcome"] != "UNCLEAR":
        candidates["response_outcome"] = {"value": base["outcome"], "source": None}
    if deadlines:
        candidates["response_deadline_text"] = {"value": deadlines[0]["text"], "source": deadlines[0]["source"]}
    new, corroborated, conflicts = _classify_candidates(candidates, profile)
    base.update(
        mode="llm",
        summary=" ".join(summaries)[:1500],
        findings=list(unique.values()),
        candidate_facts=new,
        corroborated=corroborated,
        conflicts=conflicts,
        deadlines=deadlines,
        chunks=len(analyzed),
        dropped_ungrounded=stats["dropped_ungrounded"],
        corrected_sources=stats["corrected_sources"],
        review={"status": "PENDING" if new or conflicts else "NONE"},
    )
    return "COMPLETED", base
