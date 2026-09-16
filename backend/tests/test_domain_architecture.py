import pytest
from pydantic import ValidationError

from app.agents.conversation_agent import ConversationalLegalAgent
from app.agents.rag_node import CATEGORY_TO_CORPUS, RagQueryContext, statutory_rag
from app.domains import DomainRegistry, DomainRegistryError, domain_registry
from app.domains.compatibility import provenance_from_legacy_metadata
from app.domains.contracts import (
    DomainDefinition,
    FactDefinition,
    FactStage,
    FactValueType,
    IssueTypeDefinition,
    JurisdictionPolicy,
    JurisdictionRequirement,
    QuestionPriority,
)
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


def test_legacy_fact_metadata_has_non_verifying_provenance_adapter():
    provenance = provenance_from_legacy_metadata(
        {"source": "groq_chat", "confidence": 0.91, "confirmed": False}
    )
    assert provenance.source.value == "CONVERSATION_EXTRACTION"
    assert provenance.verification.value == "UNVERIFIED"
