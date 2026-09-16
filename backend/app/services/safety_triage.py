"""Deterministic, multilingual safety triage for NyayaBot.

This module runs before model extraction, legal classification, retrieval, or
document routing. It separates a serious threat from evidence that danger is
happening *now*, records the relationship/context, and produces language- and
script-matched safety wording without relying on an LLM.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from app.services.language_style import LanguageScript, detect_language_script


GREEN = "GREEN"
AMBER = "AMBER"
RED = "RED"

NONE = "NONE"
GENERIC_THREAT = "GENERIC_THREAT"
DOMESTIC_OR_PARTNER = "DOMESTIC_OR_PARTNER"
PHYSICAL_VIOLENCE = "PHYSICAL_VIOLENCE"
STALKING = "STALKING"
WEAPON = "WEAPON"
CHILD_SAFETY = "CHILD_SAFETY"

CHECK_IMMEDIATE_DANGER = "CHECK_IMMEDIATE_DANGER"
URGENT_GUIDANCE = "URGENT_GUIDANCE"
SAFETY_INTAKE = "SAFETY_INTAKE"
NO_SAFETY_TRIAGE = "NONE"


SignalPattern = tuple[str, str]

THREAT_PATTERNS: Sequence[SignalPattern] = (
    ("threat", r"\bthreat(?:s|ened|ening|en)?\b"),
    ("dhamki", r"\bdhamk[iy]\b|धमकी"),
    ("death_threat", r"\b(?:jaan|jan) se ma+r(?:ne)?\b|जान से मार"),
    ("future_harm", r"\b(?:maar|mar) (?:dunga|dungi|dega|degi|denge|dalega)\b|मार (?:दूंगा|दूँगा|देगा|देगी)"),
    ("kill_threat", r"\b(?:kill|murder) (?:me|us|him|her)\b|\bgoing to kill\b"),
    ("hurt_threat", r"\bhurt (?:me|us|karega|karegi)\b"),
    ("intimidation", r"\bintimidat(?:e|ed|es|ing|ion)\b|डरा(?:ना|ता|ती)|डराता"),
)

VIOLENCE_PATTERNS: Sequence[SignalPattern] = (
    ("physical_violence", r"\b(?:beat(?:ing|en)?|hit(?:ting)?|attack(?:ing|ed)?|assault(?:ed|ing)?) (?:me|us|him|her)\b"),
    ("roman_hindi_violence", r"\b(?:maar|mar|peet) (?:raha|rahi|rahe) (?:hai|hain)\b"),
    ("hindi_violence", r"(?:मार|पीट) (?:रहा|रही|रहे) (?:है|हैं)|मारपीट|हिंसा"),
    ("abuse", r"\b(?:domestic violence|physical abuse|sexual assault|rape|molest(?:ed|ing)?)\b|घरेलू हिंसा|यौन हमला"),
)

STALKING_PATTERNS: Sequence[SignalPattern] = (
    ("stalking", r"\bstalk(?:s|ed|ing|er)?\b|\bfollows? me\b"),
    ("peecha", r"\bpeecha kar (?:raha|rahi|rahe) (?:hai|hain)\b|पीछा कर (?:रहा|रही|रहे)"),
    ("repeated_harassment", r"\b(?:harass(?:ed|es|ing|ment)?|repeatedly threatens?)\b|बार[- ]?बार धमकी"),
)

WEAPON_PATTERNS: Sequence[SignalPattern] = (
    ("weapon", r"\b(?:weapon|knife|gun|pistol|rifle|acid|chaku|chaaku|bandook)\b|हथियार|चाकू|बंदूक|तेज़ाब"),
)

PARTNER_PATTERNS: Sequence[SignalPattern] = (
    ("intimate_partner", r"\b(?:husband|wife|spouse|partner|boyfriend|girlfriend|pati|patni)\b|पति|पत्नी|जीवनसाथी|प्रेमी|प्रेमिका"),
)

CHILD_PATTERNS: Sequence[SignalPattern] = (
    ("dependants", r"\b(?:child|children|kid|kids|minor|bachcha|bachche|bachchon|dependant|dependent)s?\b|बच्चा|बच्चे|बच्चों|नाबालिग"),
)

CURRENT_DANGER_PATTERNS: Sequence[SignalPattern] = (
    ("right_now", r"\b(?:right now|currently|at this moment)\b|\babhi\b|अभी"),
    ("outside_home", r"\boutside (?:my|our|the) (?:house|home|door|flat)\b|\b(?:ghar ke )?bahar khad[aei]? (?:hai|hain)\b|घर के बाहर खड़ा"),
    ("person_nearby", r"\b(?:is|standing|waiting) (?:near|beside) me\b|\b(?:mere|meri|hamare) paas (?:hai|hain)\b|मेरे पास (?:है|हैं)"),
    ("cannot_leave", r"\b(?:won'?t|will not|cannot|can'?t) let me leave\b|जाने नहीं दे"),
    ("breaking_in", r"\bbreaking (?:in|into)\b|दरवाज़ा तोड़"),
    ("not_safe", r"\b(?:i am|i'm|we are|we're) not safe\b|\b(?:main|hum) safe nahi (?:hun|hain)\b|मैं सुरक्षित नहीं"),
    ("in_danger", r"\b(?:i am|i'm|we are|we're) in (?:immediate )?danger\b|\b(?:main|hum) khatre mein (?:hun|hain)\b|खतरे में"),
)

SAFE_NOW_PATTERNS: Sequence[SignalPattern] = (
    ("safe_now", r"\b(?:i am|i'm|we are|we're) safe(?: now| right now)?\b|\b(?:main|hum) (?:abhi |filhal )?safe (?:hun|hain)\b|मैं (?:अभी )?सुरक्षित (?:हूँ|हूं)|हम (?:अभी )?सुरक्षित हैं"),
    ("no_immediate_danger", r"\b(?:no|not in) immediate danger\b|\babhi (?:koi )?khatra nahi\b|अभी कोई खतरा नहीं"),
    ("person_left", r"\b(?:he|she|they|the person) (?:has |have )?left\b|\bwoh chala gaya\b|वह चला गया"),
)

EXPLICIT_DANGER_PATTERNS: Sequence[SignalPattern] = (
    ("explicit_danger", r"\b(?:i am|i'm|we are|we're) in danger\b|\b(?:main|hum) khatre mein (?:hun|hain)\b|मैं खतरे में"),
    ("explicit_not_safe", r"\bnot safe\b|\bsafe nahi\b|सुरक्षित नहीं"),
)

EVIDENCE_PATTERN = re.compile(
    r"\b(?:whatsapp|message|messages|chat|recording|audio|video|cctv|photo|photos|screenshot|screenshots|witness|witnesses|saboot)\b|सबूत|रिकॉर्डिंग|गवाह",
    re.IGNORECASE,
)
POLICE_PATTERN = re.compile(r"\b(?:police|fir|thana|sho|112|181)\b|पुलिस|थाना", re.IGNORECASE)
REPEATED_PATTERN = re.compile(r"\b(?:again|before|earlier|repeated|repeatedly|many times|often|pehle bhi|baar baar|har baar)\b|पहले भी|बार बार", re.IGNORECASE)
ONE_TIME_PATTERN = re.compile(r"\b(?:first time|never before|pehli baar)\b|पहली बार", re.IGNORECASE)
NO_EVIDENCE_PATTERN = re.compile(r"\b(?:no|don'?t have|do not have) (?:any )?(?:evidence|proof|messages|recording)\b|\b(?:koi )?saboot nahi\b|कोई सबूत नहीं", re.IGNORECASE)
NO_POLICE_PATTERN = re.compile(r"\b(?:not|haven'?t|have not|didn'?t|did not) (?:called|contacted|told|reported to) (?:the )?police\b|\bpolice (?:ko )?(?:nahi|nahin)\b|पुलिस को नहीं", re.IGNORECASE)
NO_VIOLENCE_PATTERN = re.compile(r"\b(?:no|not any|never) (?:physical violence|weapon|knife|gun)\b|\b(?:maarpeet|hathiyar|chaku|bandook) nahi\b|कोई मारपीट नहीं|कोई हथियार नहीं", re.IGNORECASE)
DATE_PATTERN = re.compile(
    r"\b(?:today|yesterday|tonight|last night|aaj|kal|raat|\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4})\b|आज|कल|आज रात",
    re.IGNORECASE,
)

HARM_SIGNAL_LABELS = frozenset(
    {
        "threat", "dhamki", "death_threat", "future_harm", "kill_threat",
        "hurt_threat", "intimidation", "physical_violence",
        "roman_hindi_violence", "hindi_violence", "abuse", "stalking",
        "peecha", "repeated_harassment", "weapon",
    }
)

SIMPLE_YES_ANSWERS = frozenset(
    {"yes", "yes i am", "yeah", "yep", "haan", "han", "ha", "ji", "हाँ", "हां", "जी"}
)
SIMPLE_NO_ANSWERS = frozenset(
    {"no", "no i am not", "nope", "nahi", "nahin", "nhi", "नहीं", "नही"}
)


SAFETY_INTAKE_FACTS = [
    "immediate_danger",
    "threat_details",
    "incident_date",
    "repeated_incidents",
    "physical_violence_or_weapon",
    "evidence_available",
    "police_contacted",
]


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").casefold()).strip()


def simple_yes_no(text: str) -> Optional[bool]:
    """Interpret an unambiguous short answer in supported scripts."""
    value = re.sub(r"[^a-z\u0900-\u097f ]", "", _normalize(text))
    if value in SIMPLE_YES_ANSWERS:
        return True
    if value in SIMPLE_NO_ANSWERS:
        return False
    return None


def _hits(patterns: Sequence[SignalPattern], text: str) -> list[str]:
    return [label for label, pattern in patterns if re.search(pattern, text, re.IGNORECASE)]


def _unique(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _localized_safety_copy(
    style: LanguageScript,
    immediate_danger: Optional[bool],
    dependants_present: bool,
) -> tuple[Optional[str], Optional[str]]:
    if style.language == "hindi" and style.script == "devanagari":
        if immediate_danger is True:
            question = "क्या आप बच्चों सहित किसी सुरक्षित जगह या भरोसेमंद व्यक्ति के पास जा सकते हैं?" if dependants_present else "क्या आप अभी किसी सुरक्षित जगह या भरोसेमंद व्यक्ति के पास जा सकते हैं?"
            guidance = "पहले सुरक्षित जगह पर जाएँ और 112 पर कॉल करें। यदि सुरक्षित हो तो 181 महिला हेल्पलाइन या बच्चों के लिए 1098 पर भी कॉल कर सकते हैं।"
        elif immediate_danger is False:
            question = None
            guidance = "ठीक है—यह जानना महत्वपूर्ण है कि आप अभी सुरक्षित हैं।"
        else:
            question = "क्या आप अभी तुरंत खतरे में हैं, या वह व्यक्ति अभी आपके पास है?"
            guidance = "अगर आपको अभी तुरंत खतरा है, तो पहले किसी सुरक्षित जगह पर जाएँ और 112 पर कॉल करें।"
        return question, guidance

    if style.language == "hinglish" and style.script == "roman":
        if immediate_danger is True:
            question = "Kya aap bachchon ke saath kisi safe jagah ya bharosemand vyakti ke paas ja sakte hain?" if dependants_present else "Kya aap abhi kisi safe jagah ya bharosemand vyakti ke paas ja sakte hain?"
            guidance = "Pehle kisi safe jagah par jaiye aur 112 par call kijiye. Agar safe ho, to 181 women's helpline ya bachchon ke liye 1098 par bhi call kar sakte hain."
        elif immediate_danger is False:
            question = None
            guidance = "Theek hai—yeh jaanna zaroori hai ki aap abhi safe hain."
        else:
            question = "Kya aap abhi turant khatre mein hain, ya woh vyakti abhi aapke paas hai?"
            guidance = "Agar aapko abhi turant khatra hai, to pehle kisi safe jagah par jaiye aur 112 par call kijiye."
        return question, guidance

    if immediate_danger is True:
        question = "Can you move to a safe place or reach a trusted person right now, with the children if they are with you?" if dependants_present else "Can you move to a safe place or reach a trusted person right now?"
        guidance = "Move to safety first and call 112. If safe to do so, you can also call the women's helpline at 181 or Childline at 1098."
    elif immediate_danger is False:
        question = None
        guidance = "Thank you for confirming that you are safe right now."
    else:
        question = "Are you in immediate danger right now, or is that person currently near you?"
        guidance = "If the danger is immediate, move to a safe place and call 112 before we continue."
    return question, guidance


@dataclass(frozen=True)
class SafetyAssessment:
    """Structured outcome of deterministic safety triage for one turn."""

    is_safety_case: bool = False
    safety_level: str = GREEN
    safety_context: str = NONE
    contexts: List[str] = field(default_factory=list)
    immediate_danger: Optional[bool] = None
    dependants_present: bool = False
    language_style: str = "english"
    script_style: str = "roman"
    stage: str = NO_SAFETY_TRIAGE
    triage_question: Optional[str] = None
    guidance: Optional[str] = None
    matched_signals: List[str] = field(default_factory=list)

    @property
    def severity(self) -> str:
        """Compatibility view for callers using the earlier severity names.

        The legacy value described how serious the wording was, not whether the
        danger was occurring now. New routing must use safety_level together
        with immediate_danger.
        """
        if self.safety_level == RED or any(
            signal in {"death_threat", "kill_threat", "future_harm"}
            for signal in self.matched_signals
        ):
            return "IMMEDIATE"
        return "ELEVATED" if self.safety_level == AMBER else "NONE"

    @property
    def has_safety_relevance(self) -> bool:
        return self.is_safety_case or self.dependants_present

    @property
    def blocks_document_routing(self) -> bool:
        return self.is_safety_case and self.safety_level in {AMBER, RED}

    def to_dict(self) -> dict:
        return {
            "is_safety_case": self.is_safety_case,
            "safety_level": self.safety_level,
            "severity": self.severity,
            "safety_context": self.safety_context,
            "contexts": list(self.contexts),
            "immediate_danger": self.immediate_danger,
            "dependants_present": self.dependants_present,
            "language": self.language_style,
            "script": self.script_style,
            "stage": self.stage,
            "triage_question": self.triage_question,
            "guidance": self.guidance,
            "matched_signals": list(self.matched_signals),
        }


def assess_safety(
    message: str,
    prior_history_text: str = "",
    prior_safety: Optional[dict] = None,
    prior_language: Optional[str] = None,
    prior_script: Optional[str] = None,
    prior_question_group: Optional[str] = None,
) -> SafetyAssessment:
    """Classify safety before any model, workflow, retrieval, or document work."""
    value = _normalize(message)
    prior_value = _normalize(prior_history_text)
    prior_safety = prior_safety or {}
    style = detect_language_script(value, prior_language, prior_script)

    current_threats = _hits(THREAT_PATTERNS, value)
    current_violence = _hits(VIOLENCE_PATTERNS, value)
    current_stalking = _hits(STALKING_PATTERNS, value)
    current_weapons = _hits(WEAPON_PATTERNS, value)
    current_partner = _hits(PARTNER_PATTERNS, value)
    current_children = _hits(CHILD_PATTERNS, value)

    prior_contexts = list(prior_safety.get("contexts") or [])
    if not prior_contexts and prior_value:
        if _hits(THREAT_PATTERNS, prior_value):
            prior_contexts.append(GENERIC_THREAT)
        if _hits(VIOLENCE_PATTERNS, prior_value):
            prior_contexts.append(PHYSICAL_VIOLENCE)
        if _hits(STALKING_PATTERNS, prior_value):
            prior_contexts.append(STALKING)
        if _hits(WEAPON_PATTERNS, prior_value):
            prior_contexts.append(WEAPON)
        if _hits(PARTNER_PATTERNS, prior_value):
            prior_contexts.append(DOMESTIC_OR_PARTNER)
        if _hits(CHILD_PATTERNS, prior_value):
            prior_contexts.append(CHILD_SAFETY)

    contexts: list[str] = []
    if current_partner:
        contexts.append(DOMESTIC_OR_PARTNER)
    if current_weapons:
        contexts.append(WEAPON)
    if current_violence:
        contexts.append(PHYSICAL_VIOLENCE)
    if current_stalking:
        contexts.append(STALKING)
    if current_threats:
        contexts.append(GENERIC_THREAT)
    if current_children:
        contexts.append(CHILD_SAFETY)
    contexts = _unique([*prior_contexts, *contexts])

    current_harm = bool(current_threats or current_violence or current_stalking or current_weapons)
    is_safety_case = bool(prior_safety.get("is_safety_case")) or current_harm or any(
        context in contexts
        for context in (GENERIC_THREAT, DOMESTIC_OR_PARTNER, PHYSICAL_VIOLENCE, STALKING, WEAPON)
    )
    dependants_present = bool(current_children) or bool(prior_safety.get("dependants_present")) or CHILD_SAFETY in contexts

    explicit_danger = _hits(EXPLICIT_DANGER_PATTERNS, value)
    explicit_safe = _hits(SAFE_NOW_PATTERNS, value)
    current_danger = _hits(CURRENT_DANGER_PATTERNS, value)
    ongoing_violence = bool(current_violence and re.search(r"\b(?:raha|rahi|rahe|beating|hitting|attacking)\b|(?:रहा|रही|रहे)", value))
    contextual_answer = simple_yes_no(value) if prior_question_group == "immediate_danger" else None

    if contextual_answer is True:
        explicit_danger.append("contextual_yes_to_immediate_danger")
        immediate_danger: Optional[bool] = True
    elif contextual_answer is False:
        explicit_safe.append("contextual_no_to_immediate_danger")
        immediate_danger = False
    elif explicit_danger:
        immediate_danger: Optional[bool] = True
    elif explicit_safe:
        immediate_danger = False
    elif is_safety_case and (ongoing_violence or current_danger or (current_weapons and current_harm)):
        immediate_danger = True
    elif current_harm and prior_safety.get("triage_complete"):
        # A newly disclosed threat after a completed check starts a fresh
        # immediate-danger check unless the current turn itself resolves it.
        immediate_danger = None
    elif "immediate_danger" in prior_safety:
        immediate_danger = prior_safety.get("immediate_danger")
    else:
        immediate_danger = None

    safety_level = RED if immediate_danger is True else AMBER if is_safety_case else GREEN
    if not is_safety_case:
        stage = NO_SAFETY_TRIAGE
    elif immediate_danger is True:
        stage = URGENT_GUIDANCE
    elif immediate_danger is None:
        stage = CHECK_IMMEDIATE_DANGER
    else:
        stage = SAFETY_INTAKE

    priority = (DOMESTIC_OR_PARTNER, WEAPON, PHYSICAL_VIOLENCE, STALKING, GENERIC_THREAT, CHILD_SAFETY)
    safety_context = next((context for context in priority if context in contexts), NONE)
    question, guidance = (
        _localized_safety_copy(style, immediate_danger, dependants_present)
        if is_safety_case
        else (None, None)
    )
    signals = _unique(
        [
            *current_threats,
            *current_violence,
            *current_stalking,
            *current_weapons,
            *current_partner,
            *current_children,
            *explicit_danger,
            *explicit_safe,
            *current_danger,
        ]
    )
    return SafetyAssessment(
        is_safety_case=is_safety_case,
        safety_level=safety_level,
        safety_context=safety_context,
        contexts=contexts,
        immediate_danger=immediate_danger,
        dependants_present=dependants_present,
        language_style=style.language,
        script_style=style.script,
        stage=stage,
        triage_question=question,
        guidance=guidance,
        matched_signals=signals[:12],
    )


def extract_safety_facts(message: str, assessment: SafetyAssessment) -> dict:
    """Extract only high-confidence safety facts from the current turn."""
    value = _normalize(message)
    facts: dict = {}
    if assessment.immediate_danger is not None:
        facts["immediate_danger"] = assessment.immediate_danger
    if any(signal in HARM_SIGNAL_LABELS for signal in assessment.matched_signals):
        facts["threat_details"] = message.strip()
    date = DATE_PATTERN.search(value)
    if date:
        facts["incident_date"] = date.group(0)
    if REPEATED_PATTERN.search(value):
        facts["repeated_incidents"] = True
    elif ONE_TIME_PATTERN.search(value):
        facts["repeated_incidents"] = False
    if PHYSICAL_VIOLENCE in assessment.contexts or WEAPON in assessment.contexts:
        facts["physical_violence_or_weapon"] = True
    elif NO_VIOLENCE_PATTERN.search(value):
        facts["physical_violence_or_weapon"] = False
    if EVIDENCE_PATTERN.search(value):
        facts["evidence_available"] = True
    elif NO_EVIDENCE_PATTERN.search(value):
        facts["evidence_available"] = False
    if POLICE_PATTERN.search(value):
        facts["police_contacted"] = not bool(NO_POLICE_PATTERN.search(value))
    elif NO_POLICE_PATTERN.search(value):
        facts["police_contacted"] = False
    if assessment.dependants_present:
        facts["dependants_present"] = True
        if assessment.immediate_danger is True:
            facts["dependants_at_risk"] = True
    return facts


def safety_triage_resolved(key_facts: dict) -> bool:
    """True only after the user explicitly answers the immediate-danger check."""
    return key_facts.get("immediate_danger") is not None
