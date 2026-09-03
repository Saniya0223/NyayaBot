from app.agents.conversation_agent import conversational_agent
from app.schemas.chat import ChatTurnRequest, DocumentUploadExtractionRequest

def test_conversational_tenant_deposit_flow():
    # Turn 1: User introduces problem in natural English/Hinglish
    req1 = ChatTurnRequest(
        message="My landlord is not returning my 50000 deposit."
    )
    res1 = conversational_agent.process_turn(req1)
    
    assert res1.case_profile.category == "HOUSING_TENANT"
    assert res1.case_profile.disputed_amount == 50000.0
    assert "city and state" in res1.reply_text.lower() or "which city" in res1.reply_text.lower()

    # Turn 2: User provides city and agreement status
    req2 = ChatTurnRequest(
        message="I was living in Noida. I have the rental agreement and landlord name is Raj Verma.",
        case_id=res1.case_profile.case_id
    )
    res2 = conversational_agent.process_turn(req2, res1.case_profile)
    
    assert res2.case_profile.user_city == "Noida"
    assert res2.case_profile.opposite_party_name == "Raj Verma"
    assert any(e.id == "rental_agreement" and e.is_available for e in res2.case_profile.evidence_checklist)

    # Turn 3: User provides vacating date. A date must never be mistaken for a new amount.
    req3 = ChatTurnRequest(
        message="I vacated on 1st Feb 2026.",
        case_id=res2.case_profile.case_id
    )
    res3 = conversational_agent.process_turn(req3, res2.case_profile)
    
    assert res3.case_profile.disputed_amount == 50000.0
    assert res3.case_profile.is_ready_for_document is False

    req4 = ChatTurnRequest(
        message="I have both the rental agreement and bank transfer proof for the deposit.",
        case_id=res3.case_profile.case_id,
    )
    res4 = conversational_agent.process_turn(req4, res3.case_profile)
    req5 = ChatTurnRequest(
        message="The landlord says he is keeping it for repairs but gave no bills.",
        case_id=res4.case_profile.case_id,
    )
    res5 = conversational_agent.process_turn(req5, res4.case_profile)

    assert res5.case_profile.is_ready_for_document is True
    assert res5.suggested_action is not None
    assert res5.suggested_action["doc_type"] == "TENANT_DEMAND_NOTICE"
    assert "enough information" in res5.reply_text.lower()

def test_conversational_salary_flow():
    req = ChatTurnRequest(
        message="My employer ABC Pvt Ltd in Delhi has not paid my salary for two months amounting to Rs. 90,000."
    )
    res = conversational_agent.process_turn(req)
    
    assert res.case_profile.category == "EMPLOYMENT"
    assert res.case_profile.disputed_amount == 90000.0
    assert res.case_profile.user_city == "Delhi"
    assert "ABC Pvt Ltd" in res.case_profile.opposite_party_name
    assert "unpaid_months" in res.case_profile.missing_required_fields

def test_stage_transition_when_user_says_sent_today():
    req_init = ChatTurnRequest(message="My landlord isn't returning ₹50,000 deposit in Noida.")
    res_init = conversational_agent.process_turn(req_init)

    # User says "I sent the demand notice today"
    req_sent = ChatTurnRequest(
        message="I sent the demand letter today via Speed Post.",
        case_id=res_init.case_profile.case_id
    )
    res_sent = conversational_agent.process_turn(req_sent, res_init.case_profile)
    
    assert res_sent.case_profile.current_stage_key in ["AWAITING_RESPONSE", "AWAITING_LANDLORD_RESPONSE"]
    assert "awaiting response" in res_sent.reply_text.lower() or "recorded that your formal demand was sent" in res_sent.reply_text.lower()

def test_document_upload_intelligence():
    req = DocumentUploadExtractionRequest(
        doc_type="RENTAL_AGREEMENT",
        file_name="lease_deed_2026.pdf"
    )
    profile = conversational_agent._init_case_profile("tenant deposit", "test-123")
    res = conversational_agent.process_document_upload(req, profile)
    
    assert any(e.id == "rental_agreement" and e.is_available for e in res.case_profile.evidence_checklist)
    assert "attached lease_deed_2026.pdf" in res.reply_text
    assert "did not add any invented details" in res.reply_text
    assert res.case_profile.opposite_party_name is None
    assert res.case_profile.disputed_amount == 0


def test_amount_conflict_requires_confirmation():
    first = conversational_agent.process_turn(ChatTurnRequest(message="My landlord kept my Rs 50,000 deposit in Pune."))
    conflict = conversational_agent.process_turn(
        ChatTurnRequest(message="Actually the deposit amount is Rs 45,000.", case_id=first.case_profile.case_id),
        first.case_profile,
    )

    assert conflict.case_profile.disputed_amount == 50000.0
    assert conflict.case_profile.key_facts["pending_conflict"]["candidate"] == 45000.0

    confirmed = conversational_agent.process_turn(
        ChatTurnRequest(message="Use Rs 45,000", case_id=first.case_profile.case_id),
        conflict.case_profile,
    )
    assert confirmed.case_profile.disputed_amount == 45000.0
    assert "pending_conflict" not in confirmed.case_profile.key_facts


def test_timeline_actions_are_idempotent():
    initial = conversational_agent.process_turn(ChatTurnRequest(message="My employer ABC Ltd in Delhi owes Rs 30,000 salary."))
    sent = conversational_agent.process_turn(
        ChatTurnRequest(message="I sent the demand letter today.", case_id=initial.case_profile.case_id),
        initial.case_profile,
    )
    sent_again = conversational_agent.process_turn(
        ChatTurnRequest(message="I sent it today by Speed Post.", case_id=initial.case_profile.case_id),
        sent.case_profile,
    )

    actions = [item for item in sent_again.case_profile.actions_completed if item["type"] == "formal_demand_sent"]
    events = [item for item in sent_again.case_profile.timeline if item["type"] == "formal_demand_sent"]
    assert len(actions) == 1
    assert len(events) == 1


def test_confirmed_rejection_upload_advances_the_workflow():
    profile = conversational_agent._init_case_profile("A seller refuses my refund", "upload-reply-case")
    analyzed = conversational_agent.process_document_upload(
        DocumentUploadExtractionRequest(
            case_id=profile.case_id,
            doc_type="REJECTION_REPLY",
            file_name="seller-response.txt",
            simulated_content="Your refund request is rejected because it is outside our return window.",
        ),
        profile,
    )
    assert analyzed.case_profile.key_facts["pending_document_extraction"]["facts"]["response_outcome"] == "REJECTED"

    confirmed = conversational_agent.process_turn(
        ChatTurnRequest(message="Details are correct", case_id=profile.case_id),
        analyzed.case_profile,
    )
    assert confirmed.case_profile.current_stage_key == "EDAAKHIL_COMPLAINT"
    assert len([action for action in confirmed.case_profile.actions_completed if action["type"] == "response_rejected"]) == 1
