"""Deterministic separation of recommendation and requested generation."""

from app.services.document_generation import assess_document_generation, resolve_requested_document
from app.config import settings
from app.schemas.fact_graph import FactGraphSchema, FinancialBreakdown, PartyInfo
from app.services.doc_generator import doc_generator


def salary_values(**extra):
    return {
        "complainant_name": "Example Employee", "opposite_party_name": "Example Employer",
        "complainant_city": "Jaipur", "disputed_amount": 400000,
        "incident_narrative": "Four months of earned salary remain unpaid.", **extra,
    }


def test_required_fields_alone_block_generation():
    assessment = assess_document_generation("SALARY_DEMAND_NOTICE", "EMPLOYMENT", {"complainant_name": "Example Employee"})
    assert not assessment.ready_to_generate
    assert set(assessment.missing_required_fields) == {"opposite_party_name", "complainant_city", "disputed_amount", "incident_narrative"}
    assert "employee_role" in assessment.missing_optional_fields


def test_optional_fields_can_be_skipped_without_blocking():
    assessment = assess_document_generation("SALARY_DEMAND_NOTICE", "EMPLOYMENT", salary_values())
    assert assessment.ready_to_generate
    assert assessment.status == "OPTIONAL_FIELDS_AVAILABLE"
    assert assessment.missing_required_fields == []
    assert {"employee_role", "unpaid_months", "employer_address"} <= set(assessment.missing_optional_fields)


def test_unsupported_and_cross_domain_documents_do_not_fall_back():
    assert not assess_document_generation("AFFIDAVIT", "EMPLOYMENT", salary_values()).ready_to_generate
    assert not assess_document_generation("SALARY_DEMAND_NOTICE", "CONSUMER", salary_values()).ready_to_generate
    assert resolve_requested_document("prepare the affidavit", "EMPLOYMENT")[0] is None
    assert resolve_requested_document("prepare salary demand letter", "CONSUMER")[0] is None


def test_ambiguous_notice_is_clarified_and_unique_salary_notice_resolves():
    assert resolve_requested_document("create document pdf", "CONSUMER")[0] is None
    assert resolve_requested_document("create notice pdf", "CONSUMER")[0] == "FORMAL_LEGAL_NOTICE"
    assert resolve_requested_document("create notice pdf", "EMPLOYMENT")[0] == "SALARY_DEMAND_NOTICE"
    assert resolve_requested_document("नोटिस बनाओ", "EMPLOYMENT")[0] == "SALARY_DEMAND_NOTICE"


def test_safety_blocks_even_when_required_fields_are_complete():
    assessment = assess_document_generation("SALARY_DEMAND_NOTICE", "EMPLOYMENT", salary_values(), safety_blocked=True)
    assert not assessment.ready_to_generate
    assert assessment.status == "BLOCKED"


def test_optional_details_are_omitted_or_rendered_conditionally(tmp_path, monkeypatch):
    (tmp_path / "documents").mkdir()
    monkeypatch.setattr(settings, "STORAGE_DIR", str(tmp_path))
    facts = FactGraphSchema(
        complainant=PartyInfo(name="Example Employee", city="Jaipur"),
        opposite_party=PartyInfo(name="Example Employer"),
        incident_narrative="Four months of earned salary remain unpaid.",
        category="EMPLOYMENT",
        financials=FinancialBreakdown(amount_paid=400000),
    )
    without = doc_generator.generate_document("case-optional", "SALARY_DEMAND_NOTICE", facts)
    assert "Phone: N/A" not in without.content_html
    assert "Software Engineer" not in without.content_html
    with_details = doc_generator.generate_document(
        "case-optional", "SALARY_DEMAND_NOTICE", facts,
        custom_data={"employee_role": "Software Engineer", "employer_address": "Example address"},
    )
    assert "Software Engineer" in with_details.content_html
    assert "Example address" in with_details.content_html
