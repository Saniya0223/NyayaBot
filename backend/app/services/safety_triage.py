"""Deterministic safety triage, run before any workflow or document logic.

When someone reports a threat, violence or intimidation, the first useful reply
is about their safety - not their city, their name, or a document template. This
module decides that without calling a model, so the safety path keeps working
when the provider is unavailable, rate limited, or wrong.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional


# IMMEDIATE: danger may be happening now; personal safety outranks case intake.
IMMEDIATE_PATTERNS = [
    r"\bkill (?:me|us|my)\b", r"\bgoing to kill\b", r"\bthreatened to kill\b",
    r"\boutside my (?:house|home|door|flat)\b", r"\bbreaking (?:in|into)\b",
    r"\bright now\b.*\b(?:threat|attack|hurt|beat)\b",
    r"\b(?:has|with) a (?:knife|gun|weapon|rod|acid)\b",
    r"\battack(?:ing|ed) me\b", r"\bbeat(?:ing)? me\b", r"\bhitting me\b",
    r"\bnot safe\b", r"\bin danger\b", r"\bafraid for my life\b",
    r"\bfollowing me\b", r"\bwon'?t let me leave\b",
]

# ELEVATED: a safety-relevant harm is reported, immediacy not yet established.
ELEVATED_TERMS = [
    "threat", "threaten", "threatened", "threatening", "intimidat",
    "harass", "stalk", "abuse", "abusive", "violence", "violent",
    "assault", "molest", "domestic violence", "dowry", "beaten",
    "weapon", "knife", "gun", "acid", "blackmail", "extort",
    "child abuse", "minor", "revenge porn", "obscene",
    "धमकी", "मारपीट", "हिंसा", "पीछा",
]

# Emergency contacts are national and stable; nothing here is case-specific.
EMERGENCY_GUIDANCE = (
    "If you are in immediate danger, call 112 (national emergency) now. "
    "Women's helpline: 181. Child helpline: 1098. "
    "If you can, move to a safe place or to people you trust before anything else."
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


@dataclass(frozen=True)
class SafetyAssessment:
    """Outcome of triage for a single turn."""

    is_safety_case: bool = False
    severity: str = "NONE"  # NONE | ELEVATED | IMMEDIATE
    triage_question: Optional[str] = None
    guidance: Optional[str] = None
    matched_signals: List[str] = field(default_factory=list)

    @property
    def blocks_document_routing(self) -> bool:
        """Safety cases never route to a document before triage is answered."""
        return self.is_safety_case and self.severity in {"IMMEDIATE", "ELEVATED"}

    def to_dict(self) -> dict:
        return {
            "is_safety_case": self.is_safety_case,
            "severity": self.severity,
            "triage_question": self.triage_question,
            "guidance": self.guidance,
            # Signals are matched keywords, not user text, so they are safe to
            # persist and to include in provider context.
            "matched_signals": list(self.matched_signals),
        }


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def assess_safety(message: str, prior_history_text: str = "") -> SafetyAssessment:
    """Classify a turn for safety risk using patterns only - no model call."""
    value = _normalize(message)
    if not value:
        return SafetyAssessment()

    combined = f"{value} {_normalize(prior_history_text)}".strip()

    immediate_hits = [pattern for pattern in IMMEDIATE_PATTERNS if re.search(pattern, combined)]
    if immediate_hits:
        return SafetyAssessment(
            is_safety_case=True,
            severity="IMMEDIATE",
            triage_question=(
                "Are you safe right now? If this person is near you or you feel you are in danger, "
                "please call 112 immediately before we continue."
            ),
            guidance=EMERGENCY_GUIDANCE,
            matched_signals=immediate_hits[:5],
        )

    elevated_hits = [term for term in ELEVATED_TERMS if term in combined]
    if elevated_hits:
        return SafetyAssessment(
            is_safety_case=True,
            severity="ELEVATED",
            triage_question=(
                "Are you in immediate danger right now, or has this person threatened to harm you soon?"
            ),
            guidance=(
                "If the situation becomes urgent at any point, call 112. "
                "Keep any messages, recordings or photographs exactly as they are - do not delete them."
            ),
            matched_signals=elevated_hits[:5],
        )

    return SafetyAssessment()


def safety_triage_resolved(key_facts: dict) -> bool:
    """True once the user has answered the immediate-danger question.

    Only an explicit answer counts. Silence is not treated as safety.
    """
    return key_facts.get("immediate_danger") is not None
