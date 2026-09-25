"""Backend authority for supported document selection and generation readiness."""

from typing import Any

from app.schemas.document import DocumentAssessment, DocumentField
from app.services.document_registry import DOCUMENT_DEFINITIONS, validate_document_fields


FIELD_LABELS = {
    "complainant_name": "Your full legal name", "complainant_city": "City / jurisdiction",
    "complainant_address": "Your address", "complainant_state": "Your state",
    "complainant_pin_code": "Your PIN code", "complainant_phone": "Your phone number",
    "recipient_name": "Recipient name", "opposite_party_name": "Other party name",
    "police_station_name": "Police station", "bank_name": "Bank / payment app",
    "disputed_amount": "Amount claimed", "incident_narrative": "What happened / purpose of the document",
    "property_address": "Rented property address", "vacating_date": "Vacating / handover date",
    "incident_date": "Incident date", "transaction_id": "Transaction ID",
}


def resolve_requested_document(message: str, category: str, hint: str | None = None) -> tuple[str | None, str | None]:
    """Resolve only a supported, domain-compatible document; never use a generic fallback."""
    allowed = [item for item, definition in DOCUMENT_DEFINITIONS.items()
               if category in definition.applicable_workflows]
    if hint:
        if hint not in DOCUMENT_DEFINITIONS or hint not in allowed:
            return None, "That document is not supported for this case."
        return hint, None
    text = message.casefold()
    if any(item.casefold().replace("_", " ") in text or definition.name.casefold() in text
           for item, definition in DOCUMENT_DEFINITIONS.items() if item not in allowed):
        return None, "That document is not supported for this case."
    named = [item for item in allowed if item.casefold().replace("_", " ") in text or DOCUMENT_DEFINITIONS[item].name.casefold() in text]
    if len(named) == 1:
        return named[0], None
    if len(named) > 1:
        return None, "Which document would you like to prepare?"
    if any(word in text for word in ("salary", "wage", "pay")) and "SALARY_DEMAND_NOTICE" in allowed:
        return "SALARY_DEMAND_NOTICE", None
    if "deposit" in text and "TENANT_DEMAND_NOTICE" in allowed:
        return "TENANT_DEMAND_NOTICE", None
    if "rti" in text and "RTI_SEC6" in allowed:
        return "RTI_SEC6", None
    if "cyber" in text and "CYBERCRIME_BANK_FREEZE" in allowed:
        return "CYBERCRIME_BANK_FREEZE", None
    if "police" in text and "POLICE_COMPLAINT_BNSS" in allowed:
        return "POLICE_COMPLAINT_BNSS", None
    if "consumer" in text and "FORMAL_LEGAL_NOTICE" in allowed:
        return "FORMAL_LEGAL_NOTICE", None
    matches = [item for item in allowed if (
        (any(word in text for word in ("notice", "नोटिस")) and DOCUMENT_DEFINITIONS[item].category == "notice")
        or (any(word in text for word in ("complaint", "शिकायत")) and DOCUMENT_DEFINITIONS[item].category == "complaint")
        or (any(word in text for word in ("letter", "पत्र")) and DOCUMENT_DEFINITIONS[item].category in {"complaint", "notice"})
    )]
    if len(matches) == 1:
        return matches[0], None
    if len(matches) > 1:
        return None, "Which document would you like to prepare?"
    if len(allowed) == 1 and not any(word in text for word in ("unsupported", "affidavit", "contract", "agreement", "notice", "complaint", "letter", "application", "नोटिस", "शिकायत", "पत्र", "आवेदन")):
        return allowed[0], None
    return None, "Please name the supported document you want to prepare."


def assess_document_generation(
    doc_type: str, category: str, values: dict[str, Any], *,
    intent: str = "USER_REQUESTED", safety_blocked: bool = False,
) -> DocumentAssessment:
    definition = DOCUMENT_DEFINITIONS.get(doc_type)
    if not definition or category not in definition.applicable_workflows:
        return DocumentAssessment(
            document_type=doc_type, intent=intent, status="BLOCKED", ready_to_generate=False,
            blockers=["Unsupported document for this case."],
        )
    fields = [
        DocumentField(
            key=key, label=FIELD_LABELS.get(key, key.replace("_", " ").capitalize()),
            required=key in definition.required_fields,
            data_type="number" if key == "disputed_amount" else "multiline" if key == "incident_narrative" else "text",
            value=values.get(key),
        )
        for key in (*definition.required_fields, *definition.optional_fields)
    ]
    missing_required = validate_document_fields(doc_type, values)
    missing_optional = [field.key for field in fields if not field.required and not values.get(field.key)]
    blockers = (["Immediate safety needs attention first."] if safety_blocked else [])
    if missing_required:
        status = "NEEDS_REQUIRED_FIELDS"
    elif missing_optional:
        status = "OPTIONAL_FIELDS_AVAILABLE"
    else:
        status = "READY_TO_GENERATE"
    return DocumentAssessment(
        document_type=doc_type, intent=intent, status="BLOCKED" if blockers else status,
        ready_to_generate=not (missing_required or blockers),
        fields=fields, missing_required_fields=missing_required,
        missing_optional_fields=missing_optional, blockers=blockers,
    )
