from app.services.document_registry import select_document_for_workflow, validate_document_fields


def test_document_selection_tracks_workflow_stage():
    assert select_document_for_workflow("CONSUMER", "PREPARE_NOTICE") == "FORMAL_LEGAL_NOTICE"
    assert select_document_for_workflow("CONSUMER", "EDAAKHIL_COMPLAINT") == "EDAAKHIL_COMPLAINT"
    assert select_document_for_workflow("HOUSING_TENANT") == "TENANT_DEMAND_NOTICE"
    assert select_document_for_workflow("CYBER_FRAUD") == "CYBERCRIME_BANK_FREEZE"


def test_document_validation_rejects_missing_and_placeholder_facts():
    missing = validate_document_fields(
        "TENANT_DEMAND_NOTICE",
        {
            "complainant_name": "Complainant",
            "opposite_party_name": "Respondent",
            "property_address": "",
            "disputed_amount": 0,
            "vacating_date": "2026-02-01",
            "incident_narrative": "Deposit has not been returned.",
        },
    )
    assert set(missing) == {
        "complainant_name",
        "opposite_party_name",
        "property_address",
        "disputed_amount",
    }


def test_document_validation_accepts_confirmed_facts():
    assert validate_document_fields(
        "TENANT_DEMAND_NOTICE",
        {
            "complainant_name": "Saniya Sharma",
            "opposite_party_name": "Raj Verma",
            "property_address": "Sector 62, Noida",
            "disputed_amount": 50000,
            "vacating_date": "2026-02-01",
            "incident_narrative": "The landlord has not returned the security deposit.",
        },
    ) == []
