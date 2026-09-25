"""Short-lived, case-scoped referents for replies to structured chat turns."""

from dataclasses import dataclass, field
import re
from typing import Any

from app.domains import domain_registry
from app.domains.compatibility import read_profile_fact
from app.domains.contracts import FactState, FactValueType, fact_state
from app.schemas.chat import PendingInteraction, StructuredCaseProfile
from app.services.document_registry import DOCUMENT_DEFINITIONS


# Intentionally small: the model still interprets normal prose. These forms are
# safe to resolve only because a validated pending target supplies the referent.
YES = frozenset({
    "yes", "yeah", "yep", "yes i have", "i have", "i did", "yes i did",
    "haan", "haan kiya hai", "mere paas hai", "हाँ", "हां", "मेरे पास है", "मैंने किया",
})
NO = frozenset({
    "no", "nope", "not yet", "no not yet", "i haven't", "i have not",
    "i didn't", "i did not", "nahi", "abhi nahi", "नहीं", "अभी नहीं",
})
BOTH = frozenset({"both", "yes both", "i have both", "dono", "haan dono", "दोनों"})
DOCUMENT_YES = YES | frozenset({
    "ok", "okay", "ok make", "okay make", "yes make it", "yes prepare it",
    "make it", "do it", "prepare it", "sure", "please do",
})


def is_bare_confirmation(message: str) -> bool:
    """A referential reply alone cannot establish a new fact or document."""
    return _normalized(message) in DOCUMENT_YES | NO | BOTH


@dataclass(frozen=True)
class PendingResolution:
    status: str = "UNRELATED"  # RESOLVED | AMBIGUOUS | UNRELATED
    values: dict[str, Any] = field(default_factory=dict)
    document_type: str | None = None


def _normalized(message: str) -> str:
    return re.sub(r"[\s.,!?]+", " ", message.casefold()).strip()


def valid_for_case(pending: PendingInteraction | None, profile: StructuredCaseProfile) -> bool:
    if not pending or pending.case_id != profile.case_id:
        return False
    if pending.type == "DOCUMENT_CONFIRMATION":
        if pending.expected_answer_type != "document" or len(pending.target_keys) != 1:
            return False
        definition = DOCUMENT_DEFINITIONS.get(pending.target_keys[0])
        if not definition or profile.category not in definition.applicable_workflows:
            return False
        if pending.source == "recommended_next_action":
            action = profile.recommended_next_action or {}
            return action.get("type") == "PREPARE_DOC" and action.get("doc_type") == pending.target_keys[0]
        return True

    definitions = {fact.key: fact for fact in domain_registry.resolve(profile.category).facts}
    if not all(key in definitions for key in pending.target_keys):
        return False
    for key in pending.target_keys:
        definition = definitions[key]
        present, value = read_profile_fact(profile, key, definition.aliases)
        if fact_state(value, present=present, definition=definition) != FactState.UNKNOWN:
            return False
    if pending.expected_answer_type == "boolean":
        return all(definitions[key].value_type == FactValueType.BOOLEAN for key in pending.target_keys)
    if pending.expected_answer_type == "choice":
        return len(pending.target_keys) == 1 and bool(pending.allowed_choices)
    return pending.expected_answer_type == "text" and len(pending.target_keys) == 1


def resolve_reply(pending: PendingInteraction, message: str) -> PendingResolution:
    value = _normalized(message)
    if pending.type == "DOCUMENT_CONFIRMATION":
        if value in DOCUMENT_YES:
            return PendingResolution("RESOLVED", document_type=pending.target_keys[0])
        if value in NO:
            return PendingResolution("RESOLVED")
        return PendingResolution()

    if pending.expected_answer_type == "choice":
        matches = [choice for choice in pending.allowed_choices if value == _normalized(choice)]
        if len(matches) == 1:
            return PendingResolution("RESOLVED", {pending.target_keys[0]: matches[0]})
        if value in YES | NO | BOTH | frozenset({"ok", "okay"}):
            return PendingResolution("AMBIGUOUS")
        return PendingResolution()

    if pending.expected_answer_type == "boolean":
        if value in BOTH:
            if len(pending.target_keys) == 2:
                return PendingResolution("RESOLVED", dict.fromkeys(pending.target_keys, True))
            return PendingResolution("AMBIGUOUS")
        if value in YES:
            if len(pending.target_keys) == 1:
                return PendingResolution("RESOLVED", {pending.target_keys[0]: True})
            return PendingResolution("AMBIGUOUS")
        if value in NO:
            if len(pending.target_keys) == 1:
                return PendingResolution("RESOLVED", {pending.target_keys[0]: False})
            return PendingResolution("AMBIGUOUS")
    return PendingResolution()


def fact_candidate(profile: StructuredCaseProfile, candidate: dict[str, Any]) -> PendingInteraction | None:
    key = candidate.get("key")
    definition = next((item for item in domain_registry.resolve(profile.category).facts if item.key == key), None)
    if definition is None:
        return None
    present, value = read_profile_fact(profile, key, definition.aliases)
    if fact_state(value, present=present, definition=definition) != FactState.UNKNOWN:
        return None
    answer_type = "boolean" if definition.value_type == FactValueType.BOOLEAN else "text"
    kind = (
        "EVIDENCE_CONFIRMATION" if definition.evidence_related else
        "ACTION_CONFIRMATION" if candidate.get("purpose") == "action_context" else
        "FACT_CONFIRMATION" if answer_type == "boolean" else "CLARIFICATION"
    )
    return PendingInteraction(
        type=kind, target_keys=[key], expected_answer_type=answer_type,
        case_id=profile.case_id, source="next_fact_candidate",
    )


def document_offer(profile: StructuredCaseProfile) -> PendingInteraction | None:
    action = profile.recommended_next_action or {}
    document_type = action.get("doc_type") if action.get("type") == "PREPARE_DOC" else None
    definition = DOCUMENT_DEFINITIONS.get(document_type)
    if not definition or profile.category not in definition.applicable_workflows:
        return None
    return PendingInteraction(
        type="DOCUMENT_CONFIRMATION", target_keys=[document_type],
        expected_answer_type="document", case_id=profile.case_id,
        source="recommended_next_action",
    )
