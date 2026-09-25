"""Owner-scoped, source-labelled context for memory and case-history questions."""

import re

from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from app.db.models import CaseModel, ChatCaseSessionModel, UserMemoryModel, UserModel
from app.schemas.chat import StructuredCaseProfile
from app.schemas.user_profile import CaseHistoryItem
from app.services.case_summary import recorded_facts, summary_fingerprint


MEMORY_RECALL = "MEMORY_RECALL"
CASE_HISTORY_LOOKUP = "CASE_HISTORY_LOOKUP"
NORMAL = "NORMAL"


def explicit_memory_text(message: str) -> str | None:
    """Chat saves only an explicit instruction, never an ordinary transient fact."""
    match = re.fullmatch(r"\s*(?:please\s+)?remember\s+(?:that\s+)?(.{3,500}?)\s*[.!]?\s*", message, re.I | re.S)
    if not match:
        return None
    value = " ".join(match.group(1).split()).rstrip(".! ")
    if re.search(r"\b(?:password|otp|cvv|aadhaar|aadhar|pan number|card number|bank account)\b", value, re.I):
        return None
    return value or None


def memory_recall_requested(message: str) -> bool:
    text = " ".join(message.casefold().split())
    if any(phrase in text for phrase in ("what do you remember about me", "what do you remember",
                                       "what do you know about me", "what have you saved about me")):
        return True
    about_me = any(phrase in text for phrase in ("mere baare", "mere bare", "mujhse judi",
                                                 "मेरे बारे", "मेरे विषय"))
    remembering = any(word in text for word in ("yaad", "yad", "remember", "याद", "जानते"))
    return about_me and remembering


def history_lookup_requested(message: str) -> bool:
    text = " ".join(message.casefold().split())
    case_word = bool(re.search(r"\b(?:cases?|matters?|mamla|maamla)\b", text)) or any(
        word in text for word in ("केस", "मामला", "मामले")
    )
    previous = bool(re.search(r"\b(?:previous|previously|past|earlier|prior|old|last|pehle|pahle|pichle|purane|purana)\b", text)) or any(
        word in text for word in ("पहले", "पिछले", "पुराने", "पुराना")
    )
    return case_word and (previous or any(phrase in text for phrase in (
        "my cases", "case history", "mere cases", "mere case", "मेरे केस", "मेरे मामले",
    )))


def requested_case_category(message: str) -> str | None:
    text = message.casefold()
    aliases = (
        ("CONSUMER", ("consumer", "उपभोक्ता")),
        ("EMPLOYMENT", ("employment", "salary", "wage", "वेतन", "नौकरी")),
        ("HOUSING_TENANT", ("tenant", "tenancy", "rent", "किरायेदार", "किराया")),
        ("CYBER_FRAUD", ("cyber", "online fraud", "साइबर")),
        ("POLICE_COMPLAINT", ("police", "पुलिस")),
    )
    for category, words in aliases:
        if any(word in text for word in words):
            return category
    return None


def case_history(
    db: Session, user_id: str, limit: int = 20, *,
    category: str | None = None, exclude_case_id: str | None = None,
) -> list[CaseHistoryItem]:
    """The owned Case row is authoritative; a chat session only supplies a cached brief."""
    query = (
        db.query(CaseModel, ChatCaseSessionModel)
        .outerjoin(ChatCaseSessionModel, and_(
            ChatCaseSessionModel.case_id == CaseModel.id,
            ChatCaseSessionModel.user_id == user_id,
            ChatCaseSessionModel.is_demo.is_(False),
        ))
        .filter(CaseModel.user_id == user_id)
    )
    if category:
        query = query.filter(CaseModel.category == category.upper())
    if exclude_case_id:
        query = query.filter(CaseModel.id != exclude_case_id)
    rows = query.order_by(func.coalesce(
        ChatCaseSessionModel.updated_at, CaseModel.updated_at, CaseModel.created_at,
    ).desc()).limit(limit).all()

    result = []
    for case, record in rows:
        data = record.profile_data or {} if record else {}
        cached = data.get("ai_summary_cache") or {}
        summary = None
        profile = None
        if data:
            try:
                profile = StructuredCaseProfile.model_validate(data)
                if isinstance(cached, dict) and cached.get("text") and cached.get("fingerprint") == summary_fingerprint(profile):
                    summary = str(cached["text"])[:400]
            except (ValueError, TypeError):
                pass
        title = case.title
        status = case.status or "Open"
        if not summary:
            details = []
            if profile:
                facts = recorded_facts(profile)
                for key, label in (("opposite_party_name", "other party"),
                                   ("product_name", "item"), ("seller_platform", "platform")):
                    if facts.get(key):
                        details.append(f"{label}: {str(facts[key])[:80]}")
            summary = f"{title} — {case.category}; " + ("; ".join(details) + "; " if details else "") + f"status: {status}."
        result.append(CaseHistoryItem(
            case_id=case.id, title=title, category=case.category, status=status,
            summary=summary[:400],
        ))
    return result


def _profile_context(user: UserModel, message: str, recall: bool) -> dict[str, str]:
    text = message.casefold()
    fields = {
        "full_name": ("my name", "profile name", "mera naam", "मेरा नाम"),
        "date_of_birth": ("my dob", "my birth", "date of birth", "जन्मतिथि"),
        "email": ("my email", "my e-mail", "मेरा ईमेल"),
        "phone": ("my phone", "my number", "मेरा फ़ोन", "मेरा नंबर"),
        "state": ("my state", "which state", "मेरा राज्य"),
        "city": ("my city", "where i live", "मेरा शहर"),
        "pin_code": ("my pin", "pin code", "postal code", "पिन कोड"),
        "full_address": ("my address", "मेरा पता"),
        "preferred_language": ("my language", "preferred language", "मेरी भाषा"),
    }
    summary_fields = {"full_name", "city", "state", "preferred_language"}
    all_profile = recall or any(phrase in text for phrase in ("my profile", "what do you know about me", "मेरी प्रोफ़ाइल"))
    return {
        field: value for field, terms in fields.items()
        if (value := getattr(user, field)) and (
            (all_profile and field in summary_fields) or any(term in text for term in terms)
        )
    }


def _relevant_memories(memories: list[UserMemoryModel], message: str, recall: bool) -> list[dict[str, str]]:
    if recall:
        chosen = memories[:10]
    else:
        style_terms = ("language", "hindi", "hinglish", "english", "explain", "response",
                       "answer", "brief", "simple", "format", "communicat", "prefer")
        message_words = set(re.findall(r"\b[a-z]{4,}\b", message.casefold())) - {
            "about", "could", "would", "please", "which", "there", "their", "where", "what", "legal", "should",
        }
        chosen = [item for item in memories if (
            item.category in {"preference", "explicit"} and any(term in item.text.casefold() for term in style_terms)
        ) or len(message_words & set(re.findall(r"\b[a-z]{4,}\b", item.text.casefold()))) >= 2][:5]
    return [{"category": item.category, "text": item.text} for item in chosen]


def chat_user_context(db: Session, user: UserModel, message: str, current_case_id: str | None) -> dict:
    """Select distinct durable sources; never copy prior-case facts into the current case."""
    recall = memory_recall_requested(message)
    history = not recall and history_lookup_requested(message)
    intent = MEMORY_RECALL if recall else CASE_HISTORY_LOOKUP if history else NORMAL
    context: dict = {"intent": intent}

    profile = _profile_context(user, message, recall)
    if profile:
        context["profile_context"] = profile

    if not history:
        memories = (db.query(UserMemoryModel).filter(UserMemoryModel.user_id == user.id)
                    .order_by(UserMemoryModel.updated_at.desc()).limit(50).all())
        selected = _relevant_memories(memories, message, recall)
        if selected or recall:
            context["saved_memory_context"] = selected

    if recall or history:
        category = requested_case_category(message) if history else None
        owned = case_history(db, user.id, 6, category=category, exclude_case_id=current_case_id)
        context["previous_case_context"] = {
            "source": "owned_cases",
            "category_filter": category,
            "cases": [item.model_dump(exclude={"case_id"}) for item in owned[:5]],
            "has_more": len(owned) > 5,
        }
    return context


def case_history_lookup_reply(context: dict, language_style: str) -> str:
    """Answer case-existence questions from owned records, never model inference."""
    history = context["previous_case_context"]
    cases = history["cases"]
    category = history.get("category_filter")
    category_name = {
        "CONSUMER": "consumer", "EMPLOYMENT": "employment",
        "HOUSING_TENANT": "tenancy", "CYBER_FRAUD": "cyber-fraud",
        "POLICE_COMPLAINT": "police-complaint",
    }.get(category, "")
    if language_style == "hindi":
        hindi_category = {
            "CONSUMER": "उपभोक्ता", "EMPLOYMENT": "रोज़गार",
            "HOUSING_TENANT": "किरायेदारी", "CYBER_FRAUD": "साइबर धोखाधड़ी",
            "POLICE_COMPLAINT": "पुलिस शिकायत",
        }.get(category, "")
        subject = f"{hindi_category} श्रेणी का " if hindi_category else ""
        if not cases:
            return f"मुझे आपके खाते में {subject}कोई पिछला मामला दर्ज नहीं मिला। इसका मतलब यह नहीं कि ऐसा मामला कभी हुआ ही नहीं।"
        details = "; ".join(f"{item['title']} (स्थिति: {item['status']})" for item in cases)
        reply = f"हाँ, आपके खाते में {subject}पिछला मामला दर्ज है: {details}." if len(cases) == 1 else f"आपके खाते में ये पिछले मामले दर्ज हैं: {details}."
        return reply + (" अन्य मामले भी हैं; पूरी सूची प्रोफ़ाइल में देखें।" if history["has_more"] else "")
    if language_style == "hinglish":
        subject = f"{category_name} " if category_name else ""
        if not cases:
            return f"Mujhe aapke account mein pehle ka {subject}case record nahi mila. Isse yeh prove nahi hota ki aisa matter kabhi hua hi nahi."
        details = "; ".join(f"{item['title']} (status: {item['status']})" for item in cases)
        reply = f"Haan, aapke account mein pehle ka {subject}case record hai: {details}." if len(cases) == 1 else f"Aapke account mein yeh purane cases recorded hain: {details}."
        return reply + (" Aur cases Profile mein dekhein." if history["has_more"] else "")
    subject = f"{category_name} " if category_name else ""
    if not cases:
        return f"I couldn't find a previous {subject}case in your account. That doesn't rule out a matter that was never saved here."
    details = "; ".join(f"{item['title']} (status: {item['status']})" for item in cases)
    reply = f"Yes. Your account has a previous {subject}case: {details}." if len(cases) == 1 else f"Your account has these previous cases: {details}."
    return reply + (" See Profile for more cases." if history["has_more"] else "")
