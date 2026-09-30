"""Cycle-3 locks: the readiness ladder (H7), the document gate (S5b/C3) and the
deterministic fact layer (H6, H8, M4).

The two most important things in here are negative:

* a **POLICE_COMPLAINT** case must not be offered a document merely because its
  actions declare no required facts. Once `compute_blocking_missing_facts`
  became action-scoped, an empty `required_fact_keys` scoped the gate to the
  empty set, and a W1-05 replay (death threat in Lucknow) was offered
  `POLICE_COMPLAINT_BNSS` on turn 3 with nothing at all on the record. The RED
  brakes never engaged because the case is AMBER, not RED.
* **W1-03 turn 1** - "just draft me the legal notice right now" - must not
  produce a document affordance. That is finding C3.

Nothing here reaches a provider: `NoCallProvider` raises on any model call and
`provider.calls` is asserted empty wherever it is used.
"""

import asyncio

from app.agents.conversation_agent import ConversationalLegalAgent
from app.schemas.chat import ChatMessage, ChatTurnRequest
from app.services.action_planner import action_planner
from app.services.case_readiness import (
    PRE_INTAKE,
    READY_FOR_ACTION,
    READY_FOR_LEGAL_GUIDANCE,
    UNDERSTANDING_CASE,
    compute_blocking_missing_facts,
    compute_intake_missing_facts,
    compute_readiness,
    document_routing_allowed,
)
from app.services.date_facts import normalize_date_phrase, same_date_ignoring_year
from app.services.fact_extraction import backfill_facts
from app.services.jurisdiction import resolve_jurisdiction
from app.services.llm_conversation import GeminiConversationService
from app.services.safety_triage import SafetyAssessment

from tests.test_safety_language_triage import NoCallProvider


W1_05 = [
    "मेरे पड़ोसी ने कल रात धमकी दी कि जान से मार देगा। मैं लखनऊ में रहता हूँ।",
    "abhi main safe hun, wo chala gaya hai. kal police station gaya tha par SHO ne FIR nahi "
    "likhi, bola aapas me settle kar lo.",
    "Kaunsi section ke under FIR likhna compulsory hai? Aur SP ko complaint kaise karun? "
    "Exact section number aur time limit batao.",
]
W1_03_T1 = (
    "my startup in Bengaluru hasn't paid me for 3 months. just draft me the legal notice right "
    "now, i don't want questions."
)


def replay(messages):
    """Thread a conversation offline, snapshotting state *at each turn*.

    `process_turn` mutates and returns the same profile object every turn, so
    `responses[k].case_profile` reads the conversation's final state. Every
    assertion here is made against a per-turn copy.
    """
    provider = NoCallProvider(configured=False)
    service = GeminiConversationService(
        provider=provider, workflow_agent=ConversationalLegalAgent()
    )
    profile = None
    history: list[ChatMessage] = []
    snapshots = []
    for message in messages:
        response = asyncio.run(service.process_turn(
            ChatTurnRequest(message=message, case_id=profile.case_id if profile else None),
            profile, history,
        ))
        profile = response.case_profile
        snapshots.append({
            "readiness": profile.readiness,
            "category": profile.category,
            "doc_type": profile.recommended_doc_type,
            "next_action": profile.recommended_next_action,
            "document_request": dict(profile.document_request or {}),
            "pending": getattr(profile.pending_interaction, "type", None),
            "suggested": response.suggested_action,
            "user_city": profile.user_city,
            "user_state": profile.user_state,
            "party": profile.opposite_party_name,
            "reply": response.reply_text,
        })
        history += [ChatMessage(sender="user", text=message),
                    ChatMessage(sender="bot", text=response.reply_text)]
    return snapshots, provider


# ================================ H7 / safety — the empty-requirement hole


def test_w1_05_death_threat_is_never_offered_a_document():
    snapshots, provider = replay(W1_05)
    assert provider.calls == []
    for index, snapshot in enumerate(snapshots, start=1):
        assert snapshot["doc_type"] is None, f"turn {index}"
        assert snapshot["next_action"] is None, f"turn {index}"
        assert snapshot["pending"] != "DOCUMENT_CONFIRMATION", f"turn {index}"
        assert not (snapshot["suggested"] or {}).get("open_confirmation_modal"), f"turn {index}"
        assert not snapshot["document_request"], f"turn {index}"
    # The case still climbs: the fix withholds the paperwork, not the help.
    assert snapshots[-1]["readiness"] == READY_FOR_LEGAL_GUIDANCE
    # M4: the city was stated in the same breath as the threat, on a turn that
    # returns before ordinary extraction.
    assert snapshots[0]["user_city"] == "Lucknow"
    assert snapshots[0]["user_state"] == "Uttar Pradesh"
    # H7: "Aur SP ko complaint kaise karun" is a sentence, not a counterparty.
    assert snapshots[-1]["party"] is None


def test_an_action_requiring_no_facts_does_not_unblock_the_case():
    """POLICE_COMPLAINT's actions declare `required_fact_keys=[]`.

    An empty requirement set means "the planner has nothing to say here", not
    "nothing is needed". Without this, a police case reaches READY_FOR_ACTION
    with an empty record.
    """
    profile = ConversationalLegalAgent()._init_case_profile(
        "Someone threatened me", category_override="POLICE_COMPLAINT",
    )
    safety = SafetyAssessment()
    plan = action_planner.plan_next_action(profile)
    assert plan.required_facts == []
    intake = compute_intake_missing_facts(profile, safety)
    blocking = compute_blocking_missing_facts(profile, safety, intake)
    assert blocking, "an empty requirement set must not read as 'nothing is needed'"
    readiness = compute_readiness(profile, safety, blocking, "Someone threatened me")
    assert readiness != READY_FOR_ACTION
    assert document_routing_allowed(profile, safety, readiness) is False


def test_blocking_facts_agree_with_the_action_planner_where_it_has_an_opinion():
    """H7 definition of done: the two computations agree by construction."""
    agent = ConversationalLegalAgent()
    safety = SafetyAssessment()
    for category, setup, facts in (
        ("CONSUMER", {"opposite_party_name": "Acme Retail"}, {"product_name": "washing machine"}),
        ("EMPLOYMENT", {"opposite_party_name": "Acme Pvt Ltd", "unpaid_months": ["June"]}, {}),
        ("HOUSING_TENANT", {"opposite_party_name": "Landlord", "vacating_date": "1 June 2026"}, {}),
    ):
        profile = agent._init_case_profile("Existing case", category_override=category)
        for key, value in setup.items():
            setattr(profile, key, value)
        profile.key_facts.update(facts)
        intake = compute_intake_missing_facts(profile, safety)
        blocking = compute_blocking_missing_facts(profile, safety, intake)
        plan = action_planner.plan_next_action(profile)
        assert set(blocking) == set(plan.blocking_missing_facts) & set(intake), category


def test_a_case_with_nothing_known_still_stays_at_understanding_case():
    agent = ConversationalLegalAgent()
    safety = SafetyAssessment()
    for category in ("CONSUMER", "EMPLOYMENT", "HOUSING_TENANT"):
        profile = agent._init_case_profile("Something happened", category_override=category)
        intake = compute_intake_missing_facts(profile, safety)
        blocking = compute_blocking_missing_facts(profile, safety, intake)
        readiness = compute_readiness(profile, safety, blocking, "Something happened")
        assert readiness == UNDERSTANDING_CASE, category
        assert document_routing_allowed(profile, safety, readiness) is False


def test_the_middle_rung_is_reachable_and_grants_no_document():
    """A classified case whose issue is understood but whose action is blocked."""
    agent = ConversationalLegalAgent()
    safety = SafetyAssessment()
    profile = agent._init_case_profile("Order never arrived", category_override="CONSUMER")
    profile.key_facts["product_name"] = "washing machine"
    intake = compute_intake_missing_facts(profile, safety)
    blocking = compute_blocking_missing_facts(profile, safety, intake)
    readiness = compute_readiness(profile, safety, blocking, "Order never arrived")
    assert readiness == READY_FOR_LEGAL_GUIDANCE
    assert document_routing_allowed(profile, safety, readiness) is False


# ============================================================ S5b / C3


def test_w1_03_turn_one_earns_no_document_and_leaks_no_fact_key():
    snapshots, provider = replay([W1_03_T1])
    assert provider.calls == []
    first = snapshots[0]
    assert not (first["suggested"] or {}).get("open_confirmation_modal")
    assert first["doc_type"] is None
    assert first["readiness"] in {PRE_INTAKE, UNDERSTANDING_CASE}
    assert "I can prepare this document." not in first["reply"]
    assert first["document_request"].get("status") != "READY_TO_GENERATE"
    for key in ("opposite_party_name", "disputed_amount", "unpaid_months", "monthly_salary"):
        assert key not in first["reply"]


def test_a_parked_document_request_resumes_once_the_case_is_ready():
    agent = ConversationalLegalAgent()
    service = GeminiConversationService(
        provider=NoCallProvider(configured=False), workflow_agent=agent,
    )
    profile = agent._init_case_profile("Salary unpaid", category_override="EMPLOYMENT")
    style_request = ChatTurnRequest(message="please draft the salary notice", case_id=profile.case_id)

    blocked = asyncio.run(service.process_turn(style_request, profile, []))
    assert blocked.suggested_action is None
    assert blocked.case_profile.document_request["status"] == "NEEDS_CASE_FACTS"

    # Supply the one fact the next action needs; the request resumes without the
    # user asking again.
    blocked.case_profile.opposite_party_name = "Example Employer"
    resumed = asyncio.run(service.process_turn(
        ChatTurnRequest(message="My employer is Example Employer.", case_id=profile.case_id),
        blocked.case_profile, [],
    ))
    assert resumed.suggested_action["type"] == "PREPARE_DOC"
    assert resumed.suggested_action["intent"] == "USER_REQUESTED"
    assert resumed.case_profile.document_request["status"] != "NEEDS_CASE_FACTS"


# ============================================================ H8 / H6 / M4


def test_an_unstated_year_is_stripped_and_a_stated_one_is_kept():
    agent = ConversationalLegalAgent()
    profile = agent._init_case_profile("F&F pending", category_override="EMPLOYMENT")
    profile.incident_date = "July 2023"
    profile.fact_metadata["incident_date"] = {
        "value": "July 2023", "source": "groq_chat", "confidence": 0.9, "confirmed": False,
    }
    backfill_facts("after I resigned in July", profile, prior_text="my employer in Pune")
    assert "2023" not in (profile.incident_date or "")
    assert profile.fact_metadata["incident_date"]["year_known"] is False

    other = agent._init_case_profile("Deposit", category_override="HOUSING_TENANT")
    other.incident_date = "March 2023"
    other.fact_metadata["incident_date"] = {
        "value": "March 2023", "source": "groq_chat", "confidence": 0.9, "confirmed": False,
    }
    backfill_facts("and that is the one", other, prior_text="In March 2023 I paid Rs 62000")
    assert other.incident_date == "March 2023"
    assert other.fact_metadata["incident_date"]["year_known"] is True


def test_a_stated_year_survives_falling_out_of_the_history_window():
    """The invariant is a containment check, so the evidence is kept on the case."""
    agent = ConversationalLegalAgent()
    profile = agent._init_case_profile("Deposit", category_override="HOUSING_TENANT")
    backfill_facts("I vacated in March 2023", profile)
    profile.incident_date = "March 2023"
    profile.fact_metadata["incident_date"] = {
        "value": "March 2023", "source": "groq_chat", "confidence": 0.9, "confirmed": False,
    }
    # Later turn, with the original message no longer in `prior_text`.
    backfill_facts("so what do I do now", profile, prior_text="so what do I do now")
    assert profile.incident_date == "March 2023"


def test_a_re_asserted_year_is_not_a_factual_conflict():
    assert same_date_ignoring_year("10 August", "10 August 2026") is True
    assert same_date_ignoring_year("10 August", "11 August 2026") is False


def test_relative_dates_resolve_against_a_fixed_today():
    from datetime import date

    fact = normalize_date_phrase("3 mahine pehle vacate kiya", date(2026, 9, 29))
    assert fact is not None
    assert fact.year_known is True
    assert fact.resolved_date is not None and fact.resolved_date.startswith("2026-06")


def test_the_deterministic_layer_never_overwrites_a_known_amount():
    agent = ConversationalLegalAgent()
    profile = agent._init_case_profile("Deposit", category_override="HOUSING_TENANT")
    profile.disputed_amount = 120000
    backfill_facts("Ek correction - deposit actually Rs 85000 tha", profile)
    assert profile.disputed_amount == 120000


def test_jurisdiction_is_derived_but_never_guessed():
    assert resolve_jurisdiction("मैं लखनऊ में रहता हूँ") == ("Lucknow", "Uttar Pradesh")
    assert resolve_jurisdiction("I am from Indore and I lost Rs 680000") == ("Indore", "Madhya Pradesh")
    assert resolve_jurisdiction("I live in Kotdwar")[1] is None

    agent = ConversationalLegalAgent()
    profile = agent._init_case_profile("Salary unpaid", category_override="EMPLOYMENT")
    profile.user_city = "Bengaluru"
    backfill_facts("what should I do next", profile)
    assert profile.user_state == "Karnataka"


def test_the_first_city_wins_across_a_move():
    agent = ConversationalLegalAgent()
    profile = agent._init_case_profile("Deposit", category_override="HOUSING_TENANT")
    backfill_facts("Flat Jaipur me tha", profile)
    backfill_facts("ab main Bengaluru shift ho gaya hun", profile)
    assert profile.user_city == "Jaipur"
    assert profile.user_state == "Rajasthan"


def test_a_year_unknown_date_counts_as_missing_for_a_document():
    agent = ConversationalLegalAgent()
    service = GeminiConversationService(workflow_agent=agent)
    profile = agent._init_case_profile("Deposit", category_override="HOUSING_TENANT")
    profile.opposite_party_name = "Landlord"
    profile.user_name = "Example Tenant"
    profile.user_city = "Jaipur"
    profile.property_address = "Flat 4, Sector 2"
    profile.vacating_date = "July"
    profile.fact_metadata["vacating_date"] = {"value": "July", "year_known": False}
    assert "vacating_date" in service._missing_document_fields(profile)
    profile.fact_metadata["vacating_date"]["year_known"] = True
    assert "vacating_date" not in service._missing_document_fields(profile)
