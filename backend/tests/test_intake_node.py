import pytest
from app.agents.intake_node import IntakeFactExtractor
from app.agents.classifier_node import IssueClassifier

def test_fact_extraction_amounts_and_dates():
    narrative = "I ordered a laptop from Amazon on 15-01-2026 for Rs. 65,000. It broke in 2 days and return was rejected."
    facts = IntakeFactExtractor.extract_facts(narrative)
    
    assert facts.financials.amount_paid == 65000.0
    assert "Amazon" in facts.opposite_party.name
    assert facts.incident_date == "15-01-2026"
    assert facts.completion_score >= 0.5
    assert len(facts.evidence_inventory) > 0

def test_missing_facts_and_clarification_generation():
    sparse_narrative = "The company didn't give me my money back."
    facts = IntakeFactExtractor.extract_facts(sparse_narrative)
    
    assert facts.is_complete is False
    assert len(facts.missing_facts) > 0
    assert len(facts.clarification_questions) > 0

def test_classification_and_escalation_guardrail():
    serious_narrative = "My husband assaulted me and the police refused to file an FIR."
    res = IssueClassifier.classify_and_evaluate(serious_narrative)
    
    assert res["severity_level"] == "ESCALATED_LAWYER"
    assert res["can_self_serve"] is False
    assert "Advocates Act" in res["escalation_reason"]
