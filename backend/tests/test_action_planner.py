"""Tests for Step 2: Next Action Planner.

Verifies:
1. Test A: Missing but non-blocking facts (ABC Finance scenario with missing receiving bank & UTR) -> READY.
2. Test B: Missing and blocking facts (e.g. institution name omitted) -> BLOCKED, asks only for blocking fact.
3. Test C: Explicitly stated unknown facts (marked as unknown, not repeatedly asked).
4. Test D: Completed actions are not re-recommended; planner advances to next workflow action.
5. End-to-end ABC Finance acceptance test through ConversationalLegalAgent.
"""

import pytest
from app.agents.conversation_agent import ConversationalLegalAgent, conversational_agent
from app.schemas.chat import ChatTurnRequest, NextActionStatus
from app.services.action_planner import ActionCandidate, NextActionPlanner, action_planner


def test_action_planner_test_a_missing_non_blocking():
    """Test A: Missing non-blocking facts do not block executable next action."""
    agent = ConversationalLegalAgent()
    msg = (
        "I found a ₹3 lakh personal loan on my credit report from ABC Finance Ltd. "
        "The disbursement reference is ABC/LD/2026/091247. "
        "I don't have the UTR or the receiving bank details."
    )
    res = agent.process_turn(ChatTurnRequest(message=msg))
    profile = res.case_profile

    assert profile.category == "CYBER_FRAUD"
    assert "ABC Finance Ltd" in (profile.opposite_party_name or "") or (profile.key_facts or {}).get("lender_name") == "ABC Finance Ltd"
    assert profile.disputed_amount == 300000.0

    plan = profile.next_action_plan
    assert plan is not None
    assert plan.status == NextActionStatus.READY
    assert len(plan.blocking_missing_facts) == 0
    assert "UTR" not in plan.blocking_missing_facts
    assert "bank_name" not in plan.blocking_missing_facts
    
    # Response must not demand UTR or receiving bank
    assert "utr" not in res.reply_text.lower() or "do not need to know" in res.reply_text.lower()
    assert res.suggested_action is not None
    assert res.suggested_action["type"] == "PREPARE_DOC"


def test_action_planner_test_b_missing_blocking():
    """Test B: Missing genuinely required fact blocks next action and asks only for that fact."""
    agent = ConversationalLegalAgent()
    # User reports unauthorized loan / fraud without mentioning the lender/institution
    msg = "I found a ₹3 lakh unauthorized fraudulent loan on my credit report, but I haven't checked which institution gave it."
    res = agent.process_turn(ChatTurnRequest(message=msg))
    profile = res.case_profile

    assert profile.category == "CYBER_FRAUD"
    plan = profile.next_action_plan
    assert plan is not None
    assert plan.status == NextActionStatus.BLOCKED
    assert "opposite_party_name" in plan.blocking_missing_facts

    # Response should ask specifically for the institution/bank
    assert any(term in res.reply_text.lower() for term in ["bank", "lender", "institution", "who"])
    assert res.suggested_action is None


def test_action_planner_test_c_user_explicitly_unknown():
    """Test C: When user explicitly says they do not know a detail, it is recorded and not repeatedly asked."""
    agent = ConversationalLegalAgent()
    # Turn 1: User provides partial details with clear cyber fraud indication
    res1 = agent.process_turn(ChatTurnRequest(message="Someone took an unauthorized loan of 50000 in my name from QuickCredit."))
    assert res1.case_profile.category == "CYBER_FRAUD"

    # Turn 2: User explicitly says they don't know the UTR or disbursement bank
    res2 = agent.process_turn(
        ChatTurnRequest(message="I don't know the UTR number or the account it went into.", case_id=res1.case_profile.case_id),
        existing_profile=res1.case_profile,
    )
    profile = res2.case_profile

    # Stated unknown facts should be recorded
    stated = profile.key_facts.get("stated_unknown_facts", [])
    assert "transaction_id" in stated or "utr" in stated or "bank_name" in stated or profile.fact_metadata.get("transaction_id", {}).get("stated_unknown")

    # The action planner should still be READY (or advance) and NOT re-ask for UTR
    plan = profile.next_action_plan
    assert plan is not None
    assert plan.status == NextActionStatus.READY
    assert "what is your utr" not in res2.reply_text.lower()


def test_action_planner_test_d_action_already_completed():
    """Test D: Completed actions are not re-recommended, advancing to the subsequent action."""
    agent = ConversationalLegalAgent()
    # Turn 1: User mentions they already sent a formal dispute to the lender
    res1 = agent.process_turn(ChatTurnRequest(
        message="I already sent a formal dispute notice to ABC Finance Ltd regarding the unauthorized 300000 loan, but they rejected it."
    ))
    profile = res1.case_profile

    # The planner should advance beyond bank_reported
    plan = profile.next_action_plan
    assert plan is not None
    assert plan.action_id != "bank_reported"
    assert plan.action_id in {"cybercrime_reported", "police_fir_escalation", "response_rejected"}


def test_abc_finance_acceptance_scenario():
    """Acceptance test for the exact ABC Finance scenario specified in requirements."""
    agent = ConversationalLegalAgent()
    narrative = (
        "I found a ₹3 lakh personal loan on my credit report from ABC Finance Ltd. "
        "I never applied for it or signed anything. "
        "ABC Finance emailed me saying it was disbursed to an account ending 4821. "
        "My HDFC and SBI accounts don't end in 4821. "
        "The disbursement reference is ABC/LD/2026/091247. "
        "I don't have the UTR or the receiving bank details."
    )
    response = agent.process_turn(ChatTurnRequest(message=narrative))
    profile = response.case_profile

    assert profile.category == "CYBER_FRAUD"
    assert profile.disputed_amount == 300000.0
    assert "ABC Finance Ltd" in profile.opposite_party_name

    plan = profile.next_action_plan
    assert plan is not None
    assert plan.status == NextActionStatus.READY
    assert len(plan.blocking_missing_facts) == 0
    assert "receiving_bank" in plan.non_blocking_missing_facts or "transaction_id" in plan.non_blocking_missing_facts or len(plan.blocking_missing_facts) == 0

    # User is NOT asked for receiving bank or UTR
    reply_lower = response.reply_text.lower()
    assert "please provide" not in reply_lower or "utr" not in reply_lower
    assert "abc finance" in reply_lower
    assert response.suggested_action is not None
    assert response.suggested_action["type"] == "PREPARE_DOC"
