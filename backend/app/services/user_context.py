"""Small, owner-scoped context for chat; case details remain in case storage."""

import re

from sqlalchemy.orm import Session

from app.db.models import CaseModel, ChatCaseSessionModel, UserMemoryModel, UserModel
from app.schemas.user_profile import CaseHistoryItem
from app.schemas.chat import StructuredCaseProfile
from app.services.case_summary import summary_fingerprint


def explicit_memory_text(message: str) -> str | None:
    match = re.fullmatch(r"\s*(?:please\s+)?remember\s+(?:that\s+)?(.{3,500}?)\s*[.!]?\s*", message, re.I | re.S)
    if not match:
        return None
    value = " ".join(match.group(1).split()).rstrip(".! ")
    if re.search(r"\b(?:password|otp|cvv|aadhaar|aadhar|pan number|card number|bank account)\b", value, re.I):
        return None
    return value or None


def case_history(db: Session, user_id: str, limit: int = 20) -> list[CaseHistoryItem]:
    records = (
        db.query(ChatCaseSessionModel)
        .join(CaseModel, CaseModel.id == ChatCaseSessionModel.case_id)
        .filter(ChatCaseSessionModel.user_id == user_id, CaseModel.user_id == user_id,
                ChatCaseSessionModel.is_demo.is_(False))
        .order_by(ChatCaseSessionModel.updated_at.desc())
        .limit(limit)
        .all()
    )
    result = []
    for record in records:
        data = record.profile_data or {}
        cached = data.get("ai_summary_cache") or {}
        summary = None
        if isinstance(cached, dict) and cached.get("text"):
            try:
                if cached.get("fingerprint") == summary_fingerprint(StructuredCaseProfile.model_validate(data)):
                    summary = cached["text"]
            except (ValueError, TypeError):
                pass
        title = str(data.get("title") or record.case.title)[:255]
        category = str(data.get("category") or record.case.category)
        status = str(data.get("status") or record.case.status)
        result.append(CaseHistoryItem(
            case_id=record.case_id,
            title=title, category=category, status=status,
            summary=str(summary)[:400] if summary else f"{title} — {category}; status: {status}.",
        ))
    seen = {item.case_id for item in result}
    legacy = (db.query(CaseModel).filter(CaseModel.user_id == user_id)
              .order_by(CaseModel.updated_at.desc()).limit(limit).all())
    for case in legacy:
        if case.id not in seen and len(result) < limit:
            result.append(CaseHistoryItem(case_id=case.id, title=case.title,
                                          category=case.category, status=case.status or "Open",
                                          summary=f"{case.title} — {case.category}; status: {case.status or 'Open'}."))
    return result


def chat_user_context(db: Session, user: UserModel, message: str, current_case_id: str | None) -> dict:
    """Send only small persistent preferences and explicitly relevant prior cases."""
    context: dict = {}
    lower_message = message.casefold()
    if user.preferred_language:
        context["preferred_language"] = user.preferred_language
    profile_queries = {
        "full_name": ("my name", "profile name", "mera naam", "मेरा नाम"),
        "date_of_birth": ("my dob", "my birth", "date of birth", "जन्मतिथि"),
        "email": ("my email", "my e-mail", "मेरा ईमेल"),
        "phone": ("my phone", "my number", "मेरा फ़ोन", "मेरा नंबर"),
        "state": ("my state", "which state", "मेरा राज्य"),
        "city": ("my city", "where i live", "मेरा शहर"),
        "pin_code": ("my pin", "pin code", "postal code", "पिन कोड"),
        "full_address": ("my address", "मेरा पता"),
    }
    all_profile = any(term in lower_message for term in ("my profile", "what do you know about me", "मेरी प्रोफ़ाइल"))
    selected = {field: getattr(user, field) for field, terms in profile_queries.items()
                if (all_profile or any(term in lower_message for term in terms)) and getattr(user, field)}
    if selected:
        context["profile"] = selected
    memories = (
        db.query(UserMemoryModel)
        .filter(UserMemoryModel.user_id == user.id)
        .order_by(UserMemoryModel.updated_at.desc())
        .limit(30)
        .all()
    )
    preference_terms = ("language", "hindi", "hinglish", "english", "explain", "response",
                        "answer", "brief", "simple", "format", "communicat", "prefer")
    preferences = [item.text for item in memories if item.category in {"preference", "explicit"}
                   and any(term in item.text.casefold() for term in preference_terms)][:5]
    if preferences:
        context["preferences"] = preferences
    # Do not leak unrelated standing facts or previous matters into an ordinary turn.
    wants_history = any(term in lower_message for term in (
        "previous case", "past case", "earlier case", "my cases", "last case", "old case",
        "pichle case", "purane case", "पिछले केस", "पुराने केस",
    ))
    if wants_history:
        context["previous_cases"] = [item.model_dump() for item in case_history(db, user.id, 5)
                                     if item.case_id != current_case_id][:3]
    recall_requested = any(term in lower_message for term in ("remember", "you know about me", "my preference", "याद", "yaad"))
    message_words = set(re.findall(r"\b[a-z]{4,}\b", lower_message)) - {
        "about", "could", "would", "please", "which", "there", "their", "where", "what", "legal", "should",
    }
    relevant = [item.text for item in memories if item.category != "preference" and (
        recall_requested or len(message_words & set(re.findall(r"\b[a-z]{4,}\b", item.text.casefold()))) >= 2
    )][:5]
    if relevant:
        context["saved_information"] = relevant
    return context
