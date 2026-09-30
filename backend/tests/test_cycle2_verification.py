# -*- coding: utf-8 -*-
"""Agent 3 independent verification of cycle-2 (findings CF-1 .. CF-6).

These probes attack the *new shapes* cycle 2 introduced, not the wordings
cycle 2 was written against:

* CF-2's complement rule is exercised with strings that were never on the old
  denylist, including two ("threatened to report me to the bank", "threatened
  to take me to court") that contain the person-object "me" and would defeat a
  naive "is a person mentioned" discriminator.
* CF-3's positional attribution test is probed for *over*-suppression as well as
  under-suppression: a positional test that reaches too far would silently
  swallow a real crisis stated after a quoted clause.
* CF-5's cooling-off window is probed for **expiry**. A window that never
  reopens is CF-4's freeze in another place, so the value of CF-5 is entirely in
  the turn it lifts.

Nothing here reaches a provider: NoCallProvider raises on any model call and
``provider.calls`` is asserted empty on every replay.

Reproducers that are still broken are strict-xfail, so they XPASS (and fail the
suite) the moment they are fixed.
"""

import asyncio

import pytest

from app.agents.conversation_agent import ConversationalLegalAgent
from app.schemas.chat import ChatMessage, ChatTurnRequest
from app.services.case_readiness import CRISIS_DOCUMENT_COOLDOWN_TURNS
from app.services.llm_conversation import GeminiConversationService
from app.services.safety_triage import CRISIS_SUPPORT, SELF_HARM, assess_safety

from tests.test_safety_language_triage import NoCallProvider


SCAM_T1 = (
    "I am from Indore and I lost Rs 680000 in a trading app scam. They showed fake profits and "
    "then asked for more tax money to let me withdraw."
)
CRISIS_T2 = (
    "Maine apni puri savings aur wife ke gold loan ke paise laga diye. Ab lagta hai jeene ka koi "
    "fayda nahi hai, sab khatam kar dun."
)
DOC_REQUEST = "Mujhe cyber crime complaint draft chahiye. Kya aap bana sakte ho?"
HOUSING_T1 = (
    "My landlord in Pune is not returning my security deposit of Rs 50,000 after I vacated the "
    "flat in March."
)


def replay(messages, configured=False):
    """Thread a conversation, snapshotting state *at each turn*.

    ``process_turn`` mutates and returns the same ``case_profile`` object every
    turn, so ``responses[k].case_profile`` reads the conversation's *final*
    state, not turn k's. Every assertion here is made against a per-turn copy.
    """
    provider = NoCallProvider(configured=configured)
    service = GeminiConversationService(
        provider=provider, workflow_agent=ConversationalLegalAgent()
    )
    profile = None
    history: list[ChatMessage] = []
    snapshots = []
    for message in messages:
        response = asyncio.run(
            service.process_turn(
                ChatTurnRequest(message=message, case_id=profile.case_id if profile else None),
                profile,
                history,
            )
        )
        profile = response.case_profile
        key_facts = dict(profile.key_facts or {})
        snapshots.append(
            {
                "reply": response.reply_text,
                "category": profile.category,
                "risk_level": profile.risk_level,
                "safety_status": profile.safety_status,
                "document_request": response.case_profile.document_request,
                "missing": sorted(profile.intake_missing_facts or []),
                "crisis_offered": key_facts.get("crisis_support_offered"),
                "turns_since_crisis": key_facts.get("turns_since_crisis_support"),
                "triage_complete": key_facts.get("safety_triage_complete"),
                "asks": dict(key_facts.get("safety_question_asks") or {}),
            }
        )
        history += [
            ChatMessage(sender="user", text=message),
            ChatMessage(sender="bot", text=response.reply_text),
        ]
    return snapshots, provider


# ==================================== CF-2 — inverted non-personal-threat rule


@pytest.mark.parametrize(
    "message",
    [
        "threatened to kill my brother",
        "dhamki di ki tumhe maar dunga",
        "threatened to hurt my children",
        "he said he will beat me if I complain",
    ],
)
def test_cf2_personal_threats_survive_the_complement_rule(message):
    assert assess_safety(message).is_safety_case is True, assess_safety(message).matched_signals


@pytest.mark.parametrize(
    "message",
    [
        "threatened to file a police case",
        # Contains the person-object "me": a naive "is a person mentioned" rule fails here.
        "threatened to report me to the bank",
        "threatened to blacklist my firm",
        "dhamki di ki naukri se nikal dunga",
        "threatened to take me to court",
    ],
)
def test_cf2_non_personal_threats_are_dropped_by_the_complement_rule(message):
    assert assess_safety(message).is_safety_case is False, assess_safety(message).matched_signals


# Regression lock: was strict-xfail N2-1; fixed by the complement-rule inversion.
def test_cf2_an_approach_threat_is_still_a_safety_case():
    assert assess_safety("threatened to come to my house tonight").is_safety_case is True


def test_cf2_the_approach_threat_gap_is_the_complement_rule_not_a_missing_signal():
    """Pins the boundary of the N2-1 fix so it cannot over-correct.

    Originally this recorded the gap: the bare approach threat was dropped while
    the same complement was rescued by a partner context or a harm verb, proving
    the defect lived in the complement rule rather than in a missing signal. The
    rule now treats a threat to turn up where someone lives as a threat to them,
    so all three are safety cases - and the boundary that still matters is that
    a *lawful* act against the same dwelling is not.
    """
    assert assess_safety("he threatened to come to my house tonight").is_safety_case is True
    assert assess_safety(
        "my husband threatened to come to my house tonight"
    ).is_safety_case is True
    assert assess_safety(
        "he threatened to come to my house tonight and kill me"
    ).is_safety_case is True
    # The dwelling token must not turn an ordinary tenancy threat into a safety
    # case: the lawful-process test still has to drop these.
    assert assess_safety("threatened to evict me from my house").is_safety_case is False
    assert assess_safety("threatened to cancel my house allotment").is_safety_case is False
    assert assess_safety("landlord threatened to lock my room").is_safety_case is False


# ============================== CF-3 — positional third-party attribution test


@pytest.mark.parametrize(
    "message",
    [
        "she said he threatened to kill himself",
        "my tenant threatened to commit suicide if I evict him",
    ],
)
def test_cf3_a_third_partys_threat_is_not_the_users_crisis(message):
    assessment = assess_safety(message)
    assert SELF_HARM not in assessment.contexts, assessment.matched_signals
    assert assessment.self_harm is False
    assert assessment.stage != CRISIS_SUPPORT


@pytest.mark.parametrize(
    "message",
    [
        "my landlord is horrible, I want to end my life",
        # Over-suppression guard: the positional test must not reach across a
        # quoted third-party clause and swallow the user's own disclosure.
        "my wife said the loan is my problem, I want to kill myself",
        "he told me to get lost, honestly I want to end my life",
        "she said she will leave me and I want to die",
        "my boss said I am fired. main khudkushi kar lunga",
        # A reporting verb whose complement is the user's own first-person clause.
        "I told my doctor I want to die",
        "I told the police I want to kill myself",
    ],
)
def test_cf3_does_not_over_suppress_the_users_own_disclosure(message):
    assessment = assess_safety(message)
    assert assessment.self_harm is True, assessment.matched_signals
    assert assessment.stage == CRISIS_SUPPORT


# ========================================================= CF-6 — ideation gaps


# Regression lock: was strict-xfail N6-1; fixed by adding past-tense inflections.
@pytest.mark.parametrize("message", ["I wanted to die", "I told my doctor I wanted to die"])
def test_cf6_past_tense_want_to_die_is_detected(message):
    assert assess_safety(message).self_harm is True


def test_cf6_past_tense_parity_holds_for_the_sibling_patterns():
    """Shows N6-1 is a one-pattern gap, not a general past-tense failure."""
    assert assess_safety("I wanted to end my life").self_harm is True
    assert assess_safety("yesterday I wanted to kill myself").self_harm is True


# ========================= CF-4 + CF-5 — the case reopens and the window lifts


def test_cf4_the_legal_case_keeps_progressing_after_a_crisis_disclosure():
    snapshots, provider = replay(
        [
            SCAM_T1,
            CRISIS_T2,
            "sorry. ab kya karun, paisa wapas milega kya?",
            "App ka naam QuickTrade tha, paise 12 March ko bheje the, transaction id 5512309981.",
            "Haan maine bank ko bata diya hai aur cybercrime.gov.in par complaint bhi kar di hai.",
        ]
    )
    assert provider.calls == []
    # Attacker-oriented intake never attaches to a crisis-only disclosure.
    for snapshot in snapshots:
        assert "threat_details" not in snapshot["missing"]
        assert "physical_violence_or_weapon" not in snapshot["missing"]
    # The case is not frozen: supplying facts actually removes them.
    assert len(snapshots[4]["missing"]) < len(snapshots[2]["missing"]) < 6
    assert snapshots[4]["category"] == "CYBER_FRAUD"


def test_cf5_the_document_cooldown_blocks_then_expires():
    """The whole value of CF-5 is the turn it lifts, so assert both halves."""
    snapshots, provider = replay(
        [SCAM_T1, CRISIS_T2, DOC_REQUEST, DOC_REQUEST, DOC_REQUEST, DOC_REQUEST]
    )
    assert provider.calls == []
    assert snapshots[1]["crisis_offered"] is True
    assert snapshots[1]["turns_since_crisis"] == 0

    blocked = [s for s in snapshots if s["turns_since_crisis"] in (0, 1, 2)]
    assert blocked, "no turn observed inside the cooling-off window"
    for snapshot in blocked:
        assert snapshot["document_request"] is None, snapshot["turns_since_crisis"]

    reopened = [
        s
        for s in snapshots
        if (s["turns_since_crisis"] or 0) >= CRISIS_DOCUMENT_COOLDOWN_TURNS
        and s["crisis_offered"]
    ]
    assert reopened, "the cooling-off window never reopened"
    assert reopened[0]["document_request"] is not None, (
        "CF-5 is a permanent ban, not a bounded window: the document never came back"
    )


# ================================================ CF-1 — bounded safety triage


def test_cf1_a_user_cannot_be_trapped_in_safety_triage():
    snapshots, provider = replay(
        [
            "mere pati ne mujhe dhamki di",
            "haan, wo abhi mere ghar ke bahar khada hai",
            "ab main safe hun",
            "Kaunsi section ke under FIR likhna compulsory hai?",
            "Exact section number batao please",
            "Please koi to jawab do",
            "Section kya hai? Bataiye na",
            "Main puch rahi hun section number",
            "Aur time limit kya hai?",
            "Reply kijiye please",
            "Kya karun main?",
            "Batao na",
        ]
    )
    assert provider.calls == []
    # The urgent branch fires once and releases on "ab main safe hun" (the `ab`
    # inflection CF-1 added), rather than re-asking on every later turn.
    assert snapshots[1]["safety_status"]["stage"] == "URGENT_GUIDANCE"
    assert snapshots[2]["safety_status"]["stage"] != "URGENT_GUIDANCE"
    assert snapshots[3]["triage_complete"] is True
    # No question group is asked a third time, and the urgent group is capped too.
    final_asks = snapshots[-1]["asks"]
    assert final_asks, "no ask counter recorded"
    assert all(count <= 2 for count in final_asks.values()), final_asks
    assert final_asks.get("urgent_safety", 0) <= 2, final_asks
    # No triage question is still being asked at the end of a 12-turn push.
    assert snapshots[-1]["safety_status"]["triage_question"] is None


@pytest.mark.xfail(
    strict=True,
    reason=(
        "N8 (deferred to S3/S8, reproducer restored): once triage releases, the offline "
        "limited_demo composer returns a byte-identical reply to seven different questions. "
        "The cycle-1 lock only inspects a 3-turn tail, so it does not see this."
    ),
)
def test_cf1_released_turns_are_not_byte_identical_replies():
    snapshots, _ = replay(
        [
            "mere pati ne mujhe dhamki di",
            "haan, wo abhi mere ghar ke bahar khada hai",
            "ab main safe hun",
            "Kaunsi section ke under FIR likhna compulsory hai?",
            "Exact section number batao please",
            "Please koi to jawab do",
            "Section kya hai? Bataiye na",
            "Main puch rahi hun section number",
            "Aur time limit kya hai?",
        ]
    )
    tail = [s["reply"] for s in snapshots[3:]]
    assert len(set(tail)) == len(tail), f"{len(tail) - len(set(tail))} duplicate replies"


# ================================================= Regression: an ordinary case


def test_an_ordinary_housing_case_is_untouched_by_cycle_two():
    snapshots, provider = replay(
        [
            HOUSING_T1,
            "Deposit Rs 50,000 tha, maine 15 March ko flat khali kiya.",
            "Landlord ka naam Suresh Patil hai, rent agreement bhi hai mere paas.",
        ]
    )
    assert provider.calls == []
    for snapshot in snapshots:
        assert snapshot["category"] == "HOUSING_TENANT"
        assert snapshot["risk_level"] == "GREEN"
        assert snapshot["safety_status"] is None
        # No crisis machinery attaches to a case that never had one.
        assert snapshot["crisis_offered"] in (None, False)
        assert snapshot["turns_since_crisis"] in (None, 0)


def test_process_turn_returns_one_mutated_profile_object_for_the_whole_thread():
    """Documents a trap in the replay pattern the cycle-1 tests use.

    ``responses[k].case_profile`` is the *same object* for every k, so any
    assertion indexed at a mid-conversation turn actually reads final state.
    """
    provider = NoCallProvider(configured=False)
    service = GeminiConversationService(
        provider=provider, workflow_agent=ConversationalLegalAgent()
    )
    profile = None
    history: list[ChatMessage] = []
    profiles = []
    for message in (SCAM_T1, CRISIS_T2, "sorry, ab kya karun?"):
        response = asyncio.run(
            service.process_turn(
                ChatTurnRequest(message=message, case_id=profile.case_id if profile else None),
                profile,
                history,
            )
        )
        profile = response.case_profile
        profiles.append(response.case_profile)
        history += [
            ChatMessage(sender="user", text=message),
            ChatMessage(sender="bot", text=response.reply_text),
        ]
    assert provider.calls == []
    assert profiles[0] is profiles[1] is profiles[2]
