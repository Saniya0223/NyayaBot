import pytest
from pydantic import ValidationError

from app.agents.conversation_agent import ConversationalLegalAgent
from app.agents.rag_node import CATEGORY_TO_CORPUS, RagQueryContext, statutory_rag
from app.domains import DomainRegistry, DomainRegistryError, domain_registry
from app.domains.compatibility import provenance_from_legacy_metadata, read_profile_fact
from app.domains.contracts import (
    DomainDefinition,
    FactDefinition,
    FactStage,
    FactState,
    FactValueType,
    IssueTypeDefinition,
    JurisdictionPolicy,
    JurisdictionRequirement,
    QuestionPriority,
    fact_state,
)
from app.llm.contracts import ExtractedCaseFacts
from app.services.case_readiness import compute_intake_missing_facts
from app.services.language_style import detect_language_script
from app.services.safety_triage import SafetyAssessment, assess_safety


DEEP_DOMAIN_IDS = {"CONSUMER", "HOUSING_TENANT", "EMPLOYMENT", "CYBER_FRAUD"}


def profile_for(category: str):
    return ConversationalLegalAgent()._init_case_profile(
        "domain architecture test",
        f"case-{category.lower()}",
        category_override=category,
    )


def test_registry_loads_four_deep_domains_and_resolves_aliases():
    assert DEEP_DOMAIN_IDS.issubset({domain.id for domain in domain_registry.all()})
    assert domain_registry.require("TENANCY").id == "HOUSING_TENANT"
    assert domain_registry.require("housing").id == "HOUSING_TENANT"
    assert domain_registry.require("CYBER").id == "CYBER_FRAUD"


def test_registered_domain_facts_are_extractable():
    extractable = set(ExtractedCaseFacts.model_fields)
    for domain in domain_registry.all():
        assert {fact.key for fact in domain.facts} <= extractable


def test_registry_rejects_duplicate_ids_and_unknown_alias_targets():
    consumer = domain_registry.require("CONSUMER")
    with pytest.raises(DomainRegistryError, match="duplicate domain IDs"):
        DomainRegistry((consumer, consumer))
    with pytest.raises(DomainRegistryError, match="unknown domain"):
        DomainRegistry((consumer,), aliases={"OLD": "MISSING"})


def test_domain_contract_rejects_duplicate_fact_keys():
    fact = FactDefinition(
        key="event_date",
        value_type=FactValueType.DATE,
        meaning="date of the event",
        priority=QuestionPriority.CORE_EVENT_FACTS,
    )
    with pytest.raises(ValidationError, match="duplicate fact keys"):
        DomainDefinition(
            id="TEST",
            display_name="Test domain",
            case_title="Test case",
            version="1.0",
            description="Definition used only for validation.",
            issue_types=(IssueTypeDefinition(id="TEST_ISSUE", display_name="Test issue"),),
            default_issue_type_id="TEST_ISSUE",
            facts=(fact, fact),
            workflow_binding="TEST",
            jurisdiction=JurisdictionPolicy(
                requirement=JurisdictionRequirement.NOT_CURRENTLY_NEEDED,
                rationale="No jurisdiction is needed for this validation fixture.",
            ),
        )


def test_fact_contract_rejects_invalid_priority():
    with pytest.raises(ValidationError):
        FactDefinition(
            key="bad_priority",
            value_type=FactValueType.TEXT,
            meaning="invalid test priority",
            priority=999,
        )


def test_explicit_false_is_known_and_not_repeated():
    profile = profile_for("CONSUMER")
    profile.opposite_party_name = "Marketplace"
    profile.incident_date = "2026-09-01"
    profile.user_state = "Delhi"
    profile.key_facts.update(
        {
            "product_name": "Laptop",
            "seller_contacted": False,
            "seller_response": "NOT_APPLICABLE",
            "invoice_available": False,
        }
    )
    missing = compute_intake_missing_facts(profile, SafetyAssessment())
    assert "seller_contacted" not in missing
    assert "invoice_available" not in missing
    assert "seller_response" not in missing


def test_consumer_core_facts_precede_and_exclude_optional_order_id():
    profile = profile_for("CONSUMER")
    profile.issue_type = "NON_DELIVERY"
    missing = compute_intake_missing_facts(profile, SafetyAssessment())

    assert missing[:2] == ["opposite_party_name", "product_name"]
    assert "order_reference_id" not in missing


def test_known_fact_is_not_returned_again():
    profile = profile_for("EMPLOYMENT")
    profile.opposite_party_name = "Example Employer"
    missing = compute_intake_missing_facts(profile, SafetyAssessment())
    assert "opposite_party_name" not in missing


def test_administrative_identifiers_are_distinct_concepts():
    consumer = domain_registry.require("CONSUMER")
    facts = {fact.key: fact for fact in consumer.facts}
    assert {
        "order_reference_id",
        "payment_transaction_id",
        "shipment_tracking_id",
    }.issubset(facts)
    assert len({facts[key].meaning for key in (
        "order_reference_id", "payment_transaction_id", "shipment_tracking_id"
    )}) == 3


def test_document_only_fields_are_not_intake_candidates():
    profile = profile_for("HOUSING_TENANT")
    missing = compute_intake_missing_facts(profile, SafetyAssessment())
    assert "user_name" not in missing
    assert "property_address" not in missing
    assert all(fact.stage != FactStage.DOCUMENT for fact in domain_registry.unresolved_facts(profile))


@pytest.mark.parametrize("domain_id", sorted(DEEP_DOMAIN_IDS))
def test_existing_workflow_binding_and_state_are_preserved(domain_id):
    profile = profile_for(domain_id)
    domain = domain_registry.require(domain_id)
    assert domain.workflow_binding == domain_id
    assert profile.category == domain_id
    assert profile.legal_journey
    assert profile.current_stage_key == profile.legal_journey[0].id


def test_rag_mapping_and_existing_corpus_retrieval_remain_compatible():
    assert CATEGORY_TO_CORPUS["HOUSING_TENANT"] == "TENANCY"
    assert CATEGORY_TO_CORPUS["TENANCY"] == "TENANCY"
    assert CATEGORY_TO_CORPUS["CONSUMER"] == "CONSUMER"
    citations = statutory_rag.retrieve_for_context(
        RagQueryContext(category="CONSUMER", issue_type="DEFECTIVE_PRODUCT"),
        limit=2,
    )
    assert citations


def test_legacy_tenancy_case_resolves_without_mutating_category():
    profile = profile_for("HOUSING_TENANT")
    profile.category = "TENANCY"
    assert domain_registry.resolve(profile.category).id == "HOUSING_TENANT"
    assert profile.category == "TENANCY"
    assert compute_intake_missing_facts(profile, SafetyAssessment())


def test_safety_and_language_remain_global_and_domain_independent():
    safety = assess_safety("mere pati ne mujhe jaan se marne ki dhamki di")
    assert safety.is_safety_case is True
    assert safety.safety_context == "DOMESTIC_OR_PARTNER"
    assert domain_registry.resolve("NOT_A_DOMAIN").id == "GENERAL"

    assert detect_language_script("my landlord kept my deposit").language == "english"
    assert detect_language_script("मेरे मकान मालिक ने जमा राशि रख ली").script == "devanagari"
    roman = detect_language_script("mere landlord ne deposit wapas nahi diya")
    assert (roman.language, roman.script) == ("hinglish", "roman")


def test_compact_domain_context_exposes_priority_reason_not_full_configuration():
    profile = profile_for("CONSUMER")
    context = domain_registry.compact_context(profile)
    assert context["domain_id"] == "CONSUMER"
    assert len(context["next_fact_candidates"]) <= 4
    assert context["next_fact_candidates"][0]["priority_reason"] == "ISSUE_IDENTIFICATION"
    assert "facts" not in context


def test_consumer_conversation_remembers_negative_answers_and_skips_form_fields():
    profile = profile_for("CONSUMER")
    profile.issue_type = "NON_DELIVERY"
    profile.opposite_party_name = "Mahima Gift Gallery"
    profile.key_facts.update({
        "seller_platform": "Instagram",
        "product_name": "resin items",
        "purchase_timing": "about a month ago",
        "advance_payment_made": True,
        "seller_contacted": True,
        "seller_response_received": False,
        "seller_response": "No replies; calls unanswered",
        "desired_outcome": "delivery",
    })
    domain = domain_registry.require("CONSUMER")
    definitions = {fact.key: fact for fact in domain.facts}
    assert fact_state(False, present=True, definition=definitions["seller_response_received"]) == FactState.FALSE
    assert fact_state(profile.key_facts["seller_response"], present=True, definition=definitions["seller_response"]) == FactState.KNOWN

    context = domain_registry.compact_context(profile)
    candidate_keys = {item["key"] for item in context["next_fact_candidates"]}
    assert context["issue_understood"] is True
    assert context["guidance_possible"] is True
    assert not candidate_keys & {
        "seller_contacted", "seller_response_received", "seller_response",
        "desired_outcome", "incident_date", "order_reference_id",
        "payment_transaction_id", "shipment_tracking_id", "user_name",
    }
    assert all(item["purpose"] != "document_only" for item in context["next_fact_candidates"])


def test_no_seller_contact_is_known_and_does_not_trigger_response_questions():
    profile = profile_for("CONSUMER")
    profile.issue_type = "NON_DELIVERY"
    profile.key_facts["seller_contacted"] = False
    missing = compute_intake_missing_facts(profile, SafetyAssessment())
    candidates = {item["key"] for item in domain_registry.compact_context(profile)["next_fact_candidates"]}
    assert "seller_contacted" not in missing
    assert "seller_response_received" not in missing
    assert "seller_contacted" not in candidates
    assert "seller_response_received" not in candidates
    assert "seller_response" not in candidates


def test_legacy_consumer_response_and_outcome_are_not_asked_again():
    profile = profile_for("CONSUMER")
    profile.issue_type = "NON_DELIVERY"
    profile.key_facts.update({
        "seller_name": "Mahima Gift Gallery",
        "seller_contacted": True,
        "seller_response_summary": "No reply to messages or calls",
        "desired_resolution": "delivery",
    })
    context = domain_registry.compact_context(profile)
    candidate_keys = {item["key"] for item in context["next_fact_candidates"]}
    assert context["issue_understood"] is True
    assert not candidate_keys & {
        "opposite_party_name", "seller_response_received", "seller_response", "desired_outcome",
    }
    assert "seller_name" in profile.key_facts
    assert "opposite_party_name" not in profile.key_facts


def test_false_zero_empty_and_not_applicable_remain_distinct():
    definitions = {fact.key: fact for fact in domain_registry.require("CONSUMER").facts}
    assert fact_state(False, present=True, definition=definitions["seller_contacted"]) == FactState.FALSE
    assert fact_state(None, present=False, definition=definitions["seller_contacted"]) == FactState.UNKNOWN
    assert fact_state("", present=True, definition=definitions["seller_response"]) == FactState.UNKNOWN
    assert fact_state("NOT_APPLICABLE", present=True, definition=definitions["seller_response"]) == FactState.NOT_APPLICABLE
    cyber_amount = next(fact for fact in domain_registry.require("CYBER_FRAUD").facts if fact.key == "disputed_amount")
    assert fact_state(0, present=True, definition=cyber_amount) == FactState.UNKNOWN


def test_legacy_alias_is_read_when_canonical_direct_field_is_empty():
    profile = profile_for("CYBER_FRAUD")
    profile.key_facts["transaction_ref"] = "legacy-reference"
    present, value = read_profile_fact(profile, "transaction_id", ("transaction_ref",))
    assert present is True
    assert value == "legacy-reference"
    assert "transaction_id" not in {fact.key for fact in domain_registry.unresolved_facts(profile)}


@pytest.mark.parametrize("domain_id,field,value", [
    ("CONSUMER", "product_name", "resin items"),
    ("HOUSING_TENANT", "vacating_date", "2026-08-10"),
    ("EMPLOYMENT", "opposite_party_name", "Example Employer"),
    ("CYBER_FRAUD", "scam_method", "account takeover"),
])
def test_minimum_context_supports_guidance_across_domains(domain_id, field, value):
    profile = profile_for(domain_id)
    if hasattr(profile, field):
        setattr(profile, field, value)
    else:
        profile.key_facts[field] = value
    assert domain_registry.issue_understood(profile)
    assert domain_registry.guidance_possible(profile)
    assert compute_intake_missing_facts(profile, SafetyAssessment())
    profile.safety_status = {"is_safety_case": True, "triage_complete": False}
    assert not domain_registry.guidance_possible(profile)


def test_legacy_fact_metadata_has_non_verifying_provenance_adapter():
    provenance = provenance_from_legacy_metadata(
        {"source": "groq_chat", "confidence": 0.91, "confirmed": False}
    )
    assert provenance.source.value == "CONVERSATION_EXTRACTION"
    assert provenance.verification.value == "UNVERIFIED"
