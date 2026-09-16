"""Compatibility adapters for persisted profiles and legacy category names."""

from __future__ import annotations

from typing import Any

from app.domains.contracts import FactProvenance, FactSourceKind, VerificationState


CATEGORY_ALIASES: dict[str, str] = {
    "TENANCY": "HOUSING_TENANT",
    "HOUSING": "HOUSING_TENANT",
    "CYBER": "CYBER_FRAUD",
}

# These fields are persisted directly on StructuredCaseProfile. Other domain
# facts remain in key_facts, so Phase 1 needs no database migration.
PROFILE_FACT_FIELDS = frozenset(
    {
        "user_name", "user_city", "user_state", "opposite_party_name",
        "opposite_party_address", "property_address", "disputed_amount",
        "incident_date", "vacating_date", "unpaid_months", "transaction_id",
        "bank_name", "police_station_name",
    }
)


def read_profile_fact(profile: Any, key: str, aliases: tuple[str, ...] = ()) -> tuple[bool, Any]:
    """Read old and new profiles without rewriting their stored category/facts."""
    for candidate in (key, *aliases):
        if candidate in PROFILE_FACT_FIELDS and hasattr(profile, candidate):
            return True, getattr(profile, candidate)
        facts = getattr(profile, "key_facts", {}) or {}
        if candidate in facts:
            return True, facts[candidate]
    return False, None


def provenance_from_legacy_metadata(metadata: dict[str, Any] | None) -> FactProvenance | None:
    if not metadata:
        return None
    source_text = str(metadata.get("source") or "").casefold()
    if source_text.startswith("upload:"):
        source = FactSourceKind.DOCUMENT_EXTRACTION
        verification = VerificationState.DOCUMENT_SUPPORTED if metadata.get("confirmed") else VerificationState.UNVERIFIED
    elif source_text == "user_conflict_confirmation":
        source = FactSourceKind.USER_PROVIDED
        verification = VerificationState.CONFIRMED
    elif source_text:
        source = FactSourceKind.CONVERSATION_EXTRACTION
        verification = VerificationState.CONFIRMED if metadata.get("confirmed") else VerificationState.UNVERIFIED
    else:
        source = FactSourceKind.SYSTEM_DERIVED
        verification = VerificationState.UNVERIFIED
    return FactProvenance(
        source=source,
        verification=verification,
        confidence=metadata.get("confidence"),
        source_reference=metadata.get("source"),
    )
