"""On-demand case briefs built only from already recorded case state."""

import hashlib
import json

from app.llm.contracts import LLMProvider, LLMResponseContext
from app.schemas.chat import StructuredCaseProfile


SUMMARY_FACT_KEYS = frozenset({
    "disputed_amount", "incident_date", "vacating_date", "unpaid_months",
    "opposite_party_name", "property_address", "user_city", "user_state",
    "product_name", "seller_platform", "purchase_timing", "seller_contacted",
    "seller_response_received", "seller_response", "landlord_contacted",
    "landlord_reason", "hr_contacted", "bank_reported", "cyber_reported",
    "termination_occurred", "eviction_proceeding_started",
    "formal_proceeding_started", "court_notice_received", "issue_description",
})


def recorded_facts(profile: StructuredCaseProfile) -> dict[str, object]:
    """Exclude inferred/internal fields, stale conflicts and unconfirmed uploads."""
    result = {}
    for key in SUMMARY_FACT_KEYS:
        metadata = profile.fact_metadata.get(key) or {}
        source = str(metadata.get("source") or "")
        if not (source == "chat" or source.endswith("_chat") or source == "document_confirmation"
                or source == "user_conflict_confirmation" or source.startswith("upload:")):
            continue
        if (metadata.get("confidence") or 0) < 0.75 and not metadata.get("confirmed"):
            continue
        value = getattr(profile, key) if hasattr(profile, key) else profile.key_facts.get(key)
        if value is None or value == "" or value == [] or value != metadata.get("value"):
            continue
        result[key] = value
    return dict(sorted(result.items()))


def summary_ready(profile: StructuredCaseProfile) -> bool:
    safety = profile.safety_status or {}
    if safety.get("is_safety_case") and (safety.get("immediate_danger") is not False or not profile.key_facts.get("safety_triage_complete")):
        return False
    visible_facts = set(recorded_facts(profile)) - {"issue_description"}
    return profile.category != "GENERAL" and bool(visible_facts or profile.documents or profile.actions_completed)


def summary_input(profile: StructuredCaseProfile) -> dict:
    return {
        "language_style": profile.language_style,
        "script_style": profile.script_style,
        "category": profile.category if profile.category != "GENERAL" else None,
        "issue_type": profile.issue_type,
        "facts": recorded_facts(profile),
        "current_stage": profile.current_stage_label,
        "reported_actions": [
            {"type": item.get("type"), "label": item.get("label"), "recorded_at": item.get("date")}
            for item in profile.actions_completed if item.get("type") != "document_prepared"
        ],
        "identified_laws": profile.legal_sources,
        "provided_documents": [
            {"name": item.get("name"), "type": item.get("file_type")}
            for item in profile.provided_documents
        ],
        "generated_documents": [
            {"title": item.get("title"), "type": item.get("type"), "created_at": item.get("created_at")}
            for item in profile.documents
        ],
        "suggested_action": (
            profile.next_action_plan.model_dump(mode="json")
            if profile.next_action_plan and profile.next_action_plan.status == "READY"
            else profile.recommended_next_action
        ),
    }


def summary_fingerprint(profile: StructuredCaseProfile) -> str:
    payload = json.dumps(summary_input(profile), ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def generate_case_summary(profile: StructuredCaseProfile, provider: LLMProvider) -> str:
    data = summary_input(profile)
    instruction = (
        "Generate an on-demand concise case brief with these headings: Situation, Legal Issue, "
        "Current Status, Relevant Laws, Documents, Potential Next Steps. Use only the supplied "
        "recorded case data. Write a short narrative, not a repetition of fact bullets. Distinguish "
        "user-reported actions from system-generated documents. An action's recorded_at is when it "
        "was recorded, not necessarily when it happened. Do not invent an event date, fact, law, "
        "section, deadline, outcome, or document. If a section lacks data, say so briefly. "
        "Present next steps only as possibilities, not completed actions. Keep the user's supplied "
        "language and script style. Do not output a full legal document."
    )
    return (await provider.chat(LLMResponseContext(
        user_message=instruction,
        response_mode="CASE_SUMMARY",
        case_summary=data,
        workflow={"current_stage_label": profile.current_stage_label, "suggested_action": data["suggested_action"]},
        legal_sources=profile.legal_sources,
        language_style=profile.language_style,
        script_style=profile.script_style,
        readiness=profile.readiness,
        professional_help=profile.professional_help,
        professional_help_should_surface=False,
    ))).strip()
