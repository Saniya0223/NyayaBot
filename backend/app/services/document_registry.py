from typing import Any, Dict, List

from app.schemas.document import DocumentDefinitionSchema


DOCUMENT_DEFINITIONS: Dict[str, DocumentDefinitionSchema] = {
    "GENERAL_COMPLAINT_LETTER": DocumentDefinitionSchema(
        id="GENERAL_COMPLAINT_LETTER",
        name="General Complaint Letter",
        category="complaint",
        applicable_workflows=["GENERAL", "POLICE_COMPLAINT", "CONSUMER"],
        required_fields=["complainant_name", "recipient_name", "complainant_city", "incident_narrative"],
        optional_fields=["recipient_address", "incident_date", "reference_number"],
        evidence_suggestions=["Supporting correspondence", "Receipts or photographs"],
        template_id="general_complaint_v1",
    ),
    "FORMAL_LEGAL_NOTICE": DocumentDefinitionSchema(
        id="FORMAL_LEGAL_NOTICE",
        name="Consumer Grievance Letter",
        category="notice",
        applicable_workflows=["CONSUMER"],
        required_fields=["complainant_name", "opposite_party_name", "complainant_city", "disputed_amount", "incident_narrative"],
        optional_fields=["incident_date", "opposite_party_address", "order_number"],
        evidence_suggestions=["Invoice", "Payment proof", "Support communications"],
        template_id="consumer_notice_v1",
    ),
    "EDAAKHIL_COMPLAINT": DocumentDefinitionSchema(
        id="EDAAKHIL_COMPLAINT",
        name="Consumer Complaint Draft",
        category="complaint",
        applicable_workflows=["CONSUMER"],
        required_fields=["complainant_name", "opposite_party_name", "complainant_city", "disputed_amount", "incident_date", "incident_narrative"],
        optional_fields=["opposite_party_address", "order_number", "previous_requests"],
        evidence_suggestions=["Invoice", "Payment proof", "Formal grievance and response"],
        requires_professional_review=True,
        template_id="consumer_complaint_v1",
    ),
    "SALARY_DEMAND_NOTICE": DocumentDefinitionSchema(
        id="SALARY_DEMAND_NOTICE",
        name="Salary Demand Letter",
        category="notice",
        applicable_workflows=["EMPLOYMENT"],
        required_fields=["complainant_name", "opposite_party_name", "complainant_city", "disputed_amount", "incident_narrative"],
        optional_fields=["employee_role", "unpaid_months", "employer_address"],
        evidence_suggestions=["Appointment letter", "Salary slips", "HR communications"],
        template_id="salary_demand_v1",
    ),
    "TENANT_DEMAND_NOTICE": DocumentDefinitionSchema(
        id="TENANT_DEMAND_NOTICE",
        name="Tenant Security Deposit Demand Letter",
        category="notice",
        applicable_workflows=["HOUSING_TENANT"],
        required_fields=["complainant_name", "opposite_party_name", "property_address", "disputed_amount", "vacating_date", "incident_narrative"],
        optional_fields=["landlord_address", "agreement_date", "previous_requests"],
        evidence_suggestions=["Rental agreement", "Deposit payment proof", "Communications"],
        template_id="tenant_deposit_v1",
    ),
    "POLICE_COMPLAINT_BNSS": DocumentDefinitionSchema(
        id="POLICE_COMPLAINT_BNSS",
        name="Police Complaint Draft",
        category="complaint",
        applicable_workflows=["POLICE_COMPLAINT"],
        required_fields=["complainant_name", "police_station_name", "complainant_city", "incident_narrative"],
        optional_fields=["incident_date", "accused_name", "witnesses"],
        evidence_suggestions=["Original written complaint", "Incident proof", "Witness details"],
        requires_professional_review=True,
        template_id="police_complaint_bnss_v1",
    ),
    "CYBERCRIME_BANK_FREEZE": DocumentDefinitionSchema(
        id="CYBERCRIME_BANK_FREEZE",
        name="Cybercrime Complaint Draft",
        category="complaint",
        applicable_workflows=["CYBER_FRAUD"],
        required_fields=["complainant_name", "bank_name", "complainant_city", "disputed_amount", "incident_date", "transaction_id", "incident_narrative"],
        optional_fields=["fraudster_phone", "fraudster_upi_id", "cybercrime_acknowledgement"],
        evidence_suggestions=["Transaction receipt", "Bank complaint acknowledgement", "Chat screenshots"],
        template_id="cybercrime_complaint_v1",
    ),
    "RTI_SEC6": DocumentDefinitionSchema(
        id="RTI_SEC6",
        name="RTI Application",
        category="application",
        applicable_workflows=["RTI"],
        required_fields=["complainant_name", "recipient_name", "complainant_city", "incident_narrative"],
        optional_fields=["public_authority_address", "period_of_information"],
        evidence_suggestions=["Prior representation", "Relevant reference number"],
        template_id="rti_sec6_v1",
    ),
}


def list_document_definitions() -> List[DocumentDefinitionSchema]:
    return list(DOCUMENT_DEFINITIONS.values())


def validate_document_fields(doc_type: str, data: Dict[str, Any]) -> List[str]:
    definition = DOCUMENT_DEFINITIONS.get(doc_type)
    if not definition:
        return ["document_type"]

    missing: List[str] = []
    placeholders = {
        "complainant", "opposite party", "opposite party / service provider",
        "city", "state", "local district", "respondent",
    }
    for field in definition.required_fields:
        value = data.get(field)
        is_placeholder = isinstance(value, str) and value.strip().lower() in placeholders
        if value is None or value == "" or is_placeholder or (field == "disputed_amount" and float(value or 0) <= 0):
            missing.append(field)
    return missing


def select_document_for_workflow(category: str, stage_key: str = "") -> str:
    if category == "CONSUMER" and "EDAAKHIL" in stage_key:
        return "EDAAKHIL_COMPLAINT"
    defaults = {
        "CONSUMER": "FORMAL_LEGAL_NOTICE",
        "EMPLOYMENT": "SALARY_DEMAND_NOTICE",
        "HOUSING_TENANT": "TENANT_DEMAND_NOTICE",
        "CYBER_FRAUD": "CYBERCRIME_BANK_FREEZE",
        "POLICE_COMPLAINT": "POLICE_COMPLAINT_BNSS",
        "RTI": "RTI_SEC6",
    }
    return defaults.get(category, "GENERAL_COMPLAINT_LETTER")
