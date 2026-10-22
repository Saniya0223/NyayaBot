"""Draft document facts from the current case, never from the chat transcript.

This is deliberately factual composition, not legal analysis. The user reviews
the resulting statement in the document confirmation form before generation.
"""

from app.schemas.chat import StructuredCaseProfile


def _text(profile: StructuredCaseProfile, key: str) -> str:
    value = getattr(profile, key, None)
    if value is None:
        value = profile.key_facts.get(key)
    if not isinstance(value, str):
        return ""
    metadata = profile.fact_metadata.get(key)
    if metadata and metadata.get("value") != value:
        return ""
    value = " ".join(value.split()).strip(" .")
    # A long free-text answer may itself contain whole chat turns. Do not turn
    # it into a supposedly verified statement in a legal document.
    return value if len(value) <= 180 and "?" not in value else ""


def _flag(profile: StructuredCaseProfile, key: str) -> bool | None:
    value = profile.key_facts.get(key)
    return value if isinstance(value, bool) else None


def _sentence(text: str) -> str:
    return text.rstrip(" .") + "."


def draft_document_narrative(profile: StructuredCaseProfile) -> str:
    """Create a short, case-specific statement without inventing missing facts.

    Free-form issue descriptions and prior messages are intentionally excluded:
    they can contain questions, new cases, and unrelated personal details.
    """
    category = profile.category
    party = _text(profile, "opposite_party_name")
    amount = profile.disputed_amount if profile.disputed_amount > 0 else None
    sentences: list[str] = []

    if category == "CONSUMER":
        product = _text(profile, "product_name")
        platform = _text(profile, "seller_platform")
        purchase_timing = _text(profile, "purchase_timing")
        incident_date = _text(profile, "incident_date")
        with_party = f" from {party}" if party else ""
        via = f" through {platform}" if platform else ""
        if profile.issue_type == "NON_DELIVERY":
            subject = f"the {product}" if product else "the ordered item"
            sentences.append(_sentence(f"The complainant reports that {subject} ordered{with_party}{via} has not been delivered"))
        elif profile.issue_type == "REFUND_NOT_RECEIVED":
            subject = f"the {product}" if product else "the purchase"
            sentences.append(_sentence(f"The complainant reports that a refund relating to {subject}{with_party} has not been received"))
        elif profile.issue_type == "DEFECTIVE_PRODUCT":
            subject = f"the {product}" if product else "the product"
            sentences.append(_sentence(f"The complainant reports that {subject} supplied{with_party} was defective"))
        elif profile.issue_type == "SERVICE_DEFICIENCY":
            sentences.append(_sentence(f"The complainant reports a problem with the service provided{with_party}"))
        elif product or party or platform:
            subject = f"the {product}" if product else "a purchase or service"
            sentences.append(_sentence(f"The complainant reports a consumer dispute concerning {subject}{with_party}{via}"))
        if sentences and purchase_timing:
            sentences.append(_sentence(f"The purchase was made around {purchase_timing}"))
        elif sentences and incident_date:
            sentences.append(_sentence(f"The relevant date recorded for this dispute is {incident_date}"))
        if sentences and _flag(profile, "advance_payment_made") is True:
            sentences.append("The complainant reports making an advance payment.")
        if sentences and amount is not None:
            sentences.append(_sentence(f"The amount in dispute is Rs. {amount:,.2f}"))
        if sentences and _flag(profile, "seller_contacted") is True:
            if _flag(profile, "seller_response_received") is False:
                sentences.append("The complainant contacted the seller or platform but reports receiving no response.")
            else:
                sentences.append("The complainant contacted the seller or platform about this matter.")

    elif category == "HOUSING_TENANT":
        address = _text(profile, "property_address")
        vacating = _text(profile, "vacating_date")
        if profile.issue_type == "SECURITY_DEPOSIT_DISPUTE":
            sentences.append("The complainant reports that the security deposit for the rented property has not been returned.")
        elif profile.issue_type == "RENT_PAYMENT_DISPUTE":
            sentences.append("The complainant reports a dispute about rent or payment for the rented property.")
        elif profile.issue_type == "EVICTION_NOTICE":
            sentences.append("The complainant reports a dispute concerning an eviction notice for the rented property.")
        elif profile.issue_type == "PROPERTY_CONDITION":
            sentences.append("The complainant reports a dispute concerning the condition of the rented property.")
        if sentences and address:
            sentences.append(_sentence(f"The property is located at {address}"))
        if sentences and amount is not None:
            sentences.append(_sentence(f"The amount in dispute is Rs. {amount:,.2f}"))
        if sentences and vacating:
            sentences.append(_sentence(f"The recorded vacating or handover date is {vacating}"))
        if sentences and _flag(profile, "landlord_contacted") is True:
            sentences.append("The complainant has contacted the landlord about this matter.")

    elif category == "EMPLOYMENT":
        periods = profile.unpaid_months
        if profile.issue_type == "UNPAID_DELAYED_SALARY":
            sentences.append(_sentence(f"The complainant reports that salary owed by {party} remains unpaid" if party else "The complainant reports that salary remains unpaid"))
        elif profile.issue_type == "TERMINATION_ISSUE":
            sentences.append("The complainant reports a dispute concerning termination of employment.")
        elif profile.issue_type == "EMPLOYMENT_DUES":
            sentences.append("The complainant reports that employment dues remain outstanding.")
        elif profile.issue_type == "WORKPLACE_GRIEVANCE":
            sentences.append("The complainant reports an unresolved workplace grievance.")
        if sentences and periods:
            valid_periods = [
                " ".join(item.split()).strip(" .") for item in periods
                if isinstance(item, str) and 0 < len(item.strip()) <= 60 and "?" not in item
            ][:4]
            if valid_periods:
                sentences.append(_sentence(f"The period identified by the complainant is {', '.join(valid_periods)}"))
        if sentences and amount is not None:
            sentences.append(_sentence(f"The amount claimed is Rs. {amount:,.2f}"))
        if sentences and _flag(profile, "hr_contacted") is True:
            sentences.append("The complainant has raised the matter with HR or management.")

    elif category == "CYBER_FRAUD":
        date = _text(profile, "incident_date")
        bank = _text(profile, "bank_name")
        if profile.issue_type == "UPI_BANK_TRANSFER_FRAUD":
            sentences.append("The complainant reports an unauthorized or fraudulent UPI or bank transfer.")
        elif profile.issue_type == "CARD_PAYMENT_FRAUD":
            sentences.append("The complainant reports an unauthorized or fraudulent card payment.")
        elif profile.issue_type == "ACCOUNT_TAKEOVER":
            sentences.append("The complainant reports unauthorized access to an account.")
        elif profile.issue_type == "ONLINE_SCAM":
            sentences.append("The complainant reports an online financial scam.")
        if sentences and date:
            sentences.append(_sentence(f"The incident was reported as occurring on {date}"))
        if sentences and bank:
            sentences.append(_sentence(f"The bank or payment provider involved is {bank}"))
        if sentences and amount is not None:
            sentences.append(_sentence(f"The amount in dispute is Rs. {amount:,.2f}"))
        if sentences and _flag(profile, "bank_reported") is True:
            sentences.append("The complainant has notified the bank or payment provider.")
        if sentences and _flag(profile, "cyber_reported") is True:
            sentences.append("The complainant has reported the matter through a cybercrime reporting channel.")

    # General, police and RTI cases often have no structured description of the
    # actual event/request. Leave the required field blank for the user's own
    # reviewed statement instead of presenting chat text as a finished draft.
    return " ".join(sentences)
