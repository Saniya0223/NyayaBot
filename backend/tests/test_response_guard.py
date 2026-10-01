"""Cycle 4 / set S4 - the deterministic output guard, tested against recorded output.

Every reply string here is byte-verbatim from `qa-pipeline/wave-1/transcripts.json`, copied
into `tests/fixtures/wave1_replies.json` so this test does not reach outside `backend/`.

The real code points matter and must never be retyped as ASCII. The recorded replies carry
U+2248 ALMOST EQUAL TO, U+202F NARROW NO-BREAK SPACE (between digits and `%`, and inside
ordinary-looking phrases such as "Section<U+202F>11"), and U+2011 NON-BREAKING HYPHEN inside
"1800-11-001-112". Two conventions keep that honest:

* invisible or confusable code points are written as `chr(0x....)` here, never pasted, so a
  future tidy-up cannot silently swap one for U+0020;
* every byte-exact expectation about a *passage* is sliced out of the fixture by `verbatim()`
  rather than retyped, because a retyped passage would pass against code that fails in
  production.

No provider, no network, no LLM call: `guard_reply` is a pure function.
"""

import json
import re
from pathlib import Path

import pytest

from app.agents.conversation_agent import ConversationalLegalAgent
from app.services.language_style import LanguageScript
from app.services.llm_conversation import gemini_conversation_service as service
from app.services.model_law_adoption import (
    ADOPTED,
    MODEL_TENANCY_ADOPTION,
    UNVERIFIED,
    adoption_status,
)
from app.services.response_guard import guard_reply
from app.services.safety_triage import (
    CRISIS_HELPLINES,
    CRISIS_SUPPORT,
    EMERGENCY_NUMBER,
    _localized_safety_copy as safety_question_and_guidance,
    crisis_support_copy,
)


FIXTURES = json.loads(
    (Path(__file__).parent / "fixtures" / "wave1_replies.json").read_text(encoding="utf-8")
)

ALMOST_EQUAL = chr(0x2248)   # U+2248, in "= 30 %"
NARROW_NBSP = chr(0x202F)    # U+202F, between digits and %, and inside "Section 11"
NBSP = chr(0x00A0)
NB_HYPHEN = chr(0x2011)      # U+2011, in "1800-11-001-112" and "RBI-designated"
RUPEE = chr(0x20B9)

FAKE_RBI_NUMBER = NB_HYPHEN.join(["1800", "11", "001", "112"])
FAKE_SBI_NUMBER = NB_HYPHEN.join(["1800", "11", "222", "222"])

agent = ConversationalLegalAgent()


def reply(key: str) -> str:
    return FIXTURES[key]["reply"]


def verbatim(source: str, start: str, end: str) -> str:
    """A byte-exact slice of the recorded reply, located by ASCII-only anchors."""
    first = source.index(start)
    return source[first:source.index(end, first)]


def loose(text: str) -> str:
    """Normalise only the invisible variants, so a token check is not a spacing check."""
    return (text.replace(NARROW_NBSP, " ").replace(NBSP, " ")
            .replace(NB_HYPHEN, "-").replace(chr(0x2010), "-"))


def profile(category, **overrides):
    value = agent._init_case_profile("guard test case", category_override=category)
    for key, item in overrides.items():
        if hasattr(value, key):
            setattr(value, key, item)
        else:
            value.key_facts[key] = item
    return value


TENANCY_SOURCES = [{
    "act": "Model Tenancy Act / State Rent Control Acts",
    "section": "Section 11",
    "title": "Security Deposit and Refund Obligation",
    "document_type": "Model law - State adoption must be verified",
    "kind": "verified_provision",
}]


def tenancy_profile(state="Rajasthan"):
    return profile(
        "HOUSING_TENANT", user_state=state, user_city="Jaipur", disputed_amount=85000.0,
        language_style="hinglish", script_style="roman", legal_sources=TENANCY_SOURCES,
    )


def cyber_profile():
    """The W1-04 turns are Hinglish/roman, so the guard's inserted copy must be too."""
    return profile(
        "CYBER_FRAUD", disputed_amount=47500.0, bank_name="SBI",
        language_style="hinglish", script_style="roman",
    )


def test_fixtures_kept_their_real_code_points():
    """If this fails, someone retyped the fixture and every C4/M7 test below is worthless."""
    assert ALMOST_EQUAL in reply("W1-06-t4")
    assert NARROW_NBSP in reply("W1-06-t4")
    assert FAKE_RBI_NUMBER in reply("W1-04-t2")
    assert FAKE_SBI_NUMBER in reply("W1-04-t3")
    assert "Section" + NARROW_NBSP + "11" in reply("W1-02-t1")


# --------------------------------------------------------------- C2(c): case law

def test_w1_03_t3_fabricated_supreme_court_citation_is_removed():
    """DoD #3. W1-03 t3, verbatim, EMPLOYMENT - a healthy non-degraded turn."""
    source = reply("W1-03-t3")
    source_flat = loose(source)
    removed = ("(2020) 6 SCC 123", "M. S. R. Enterprises", "M.S.R. Enterprises",
               "v. State of Karnataka", "Supreme Court case you can quote", "The Court held")
    for token in removed:
        assert token in source_flat, f"fixture drifted: {token!r}"
    result = guard_reply(source, profile("EMPLOYMENT"))
    flat = loose(result.text)
    for token in removed:
        assert token not in flat, token
    assert "verified case-law database" in result.text
    assert "case_law" in result.rules()


def test_w1_03_t3_unverifiable_statute_and_its_invented_quote_are_removed():
    """DoD #4. The section token and the fabricated verbatim quote go; the prose stays."""
    source = reply("W1-03-t3")
    source_flat = loose(source)
    for token in ("Section 9(1)", "Section 9", "Wages shall be paid in full"):
        assert token in source_flat, f"fixture drifted: {token!r}"
    result = guard_reply(source, profile("EMPLOYMENT"))
    flat = loose(result.text)
    assert "Section 9(1)" not in flat
    assert "Section 9" not in flat
    assert "Wages shall be paid in full" not in flat
    # The genuinely useful wage-period guidance survives.
    assert "before the wage period ends" in flat
    assert "the end of the month" in flat
    # The Act name survives; the disclosure names the authority and pastes no URL.
    assert "Code on Wages, 2019" in flat
    assert "Ministry of Labour & Employment" in result.text
    assert "http" not in result.text
    assert "_" not in result.text
    assert "statute_unverified" in result.rules()


@pytest.mark.parametrize("category", ["CONSUMER", "HOUSING_TENANT", "EMPLOYMENT",
                                      "CYBER_FRAUD", "POLICE_COMPLAINT", "GENERAL"])
def test_case_law_prohibition_is_unconditional_in_every_domain(category):
    """No case-law corpus exists anywhere in the product, so this needs no allowlist."""
    text = (
        "Here is the position.\n\n"
        "- **Ramesh Kumar v. Union of India**, (2019) 4 SCC 501 settled the point.\n\n"
        "Send the notice by registered post."
    )
    result = guard_reply(text, profile(category))
    assert "(2019) 4 SCC 501" not in result.text
    assert "Ramesh Kumar" not in result.text
    assert "Send the notice by registered post." in result.text
    assert "case_law" in result.rules()


def test_reporter_citation_alone_triggers_without_a_party_name():
    result = guard_reply(
        "The forum relied on AIR 2018 SC 1234 when awarding interest.\n\nFile on e-Daakhil.",
        profile("CONSUMER"),
    )
    assert "AIR 2018 SC 1234" not in result.text
    assert "File on e-Daakhil." in result.text


# ------------------------------------------------- C2: the over-filtering locks

@pytest.mark.parametrize("text", [
    "Under Section 35 of the Consumer Protection Act, 2019 you can file the complaint.",
    "Section 2(11) of the Consumer Protection Act, 2019 defines deficiency in service.",
    "The limitation rule is in Section 69, and Section 69(2) allows condonation of delay.",
    "Sections 34 & 35 decide which Commission has pecuniary jurisdiction.",
    "Section 47 covers the State Commission and Section 58 the National Commission.",
    "Section 2(47) covers unfair trade practice and Section 2(34) product liability.",
])
def test_consumer_corpus_citations_pass_through_byte_identical(text):
    """DoD #5. These are the product's main value; deleting one is a silent harm."""
    result = guard_reply(text, profile("CONSUMER"))
    assert result.text == text
    assert result.redactions == ()


@pytest.mark.parametrize("category,text", [
    ("CYBER_FRAUD",
     "Section 66D of the Information Technology Act, 2000 covers cheating by personation."),
    ("POLICE_COMPLAINT",
     "Section 173 of the Bharatiya Nagarik Suraksha Sanhita, 2023 governs the FIR."),
    ("HOUSING_TENANT",
     "Section 73 of the Indian Contract Act, 1872 covers compensation for breach."),
    ("CONSUMER",
     "Section 6(1) of the Right to Information Act, 2005 is how you request information."),
])
def test_curated_document_citations_are_never_deleted(category, text):
    """DoD #5. The product prints these inside documents it generates; deleting one from the
    chat message that explains that same document would be a shipped contradiction."""
    result = guard_reply(text, profile(category))
    assert result.text == text
    assert result.redactions == ()


def test_tenancy_citations_survive_and_only_gain_a_caveat():
    """DoD #5 + H4: the caveat adds a trailing paragraph; it never removes the section."""
    text = "Section 11 covers the deposit and Section 30 the forum."
    result = guard_reply(text, tenancy_profile())
    assert result.text.startswith(text)
    assert result.rules() == ("model_law_adoption_caveat",)


def test_a_bare_section_number_in_the_domain_allowlist_needs_no_act_name():
    text = "Section 35 is the provision that matters here."
    assert guard_reply(text, profile("CONSUMER")).text == text


def test_an_act_that_is_in_no_allowlist_is_rejected_even_where_a_corpus_exists():
    text = "Section 9(1) of the Code on Wages, 2019 fixes the wage period."
    result = guard_reply(text, profile("CONSUMER"))
    assert "Section 9(1)" not in result.text
    assert "statute_unverified" in result.rules()


def test_an_unknown_section_with_no_act_named_is_caveated_not_deleted():
    """Caveat on doubt: a deleted-but-correct citation is a harm the user cannot detect."""
    text = "Section 142 may also be relevant to your complaint."
    result = guard_reply(text, profile("CONSUMER"))
    assert "Section 142" in result.text
    assert result.rules() == ("statute_unconfirmed_caveated",)


# -------------------------------------------------------- C4: win probabilities

def test_w1_06_t4_invented_win_percentages_are_all_removed():
    """DoD #6, against the real U+2248 / U+202F bytes."""
    source = reply("W1-06-t4")
    assert source.count("%") == 4
    assert "Rough chance of a favourable outcome" in source
    result = guard_reply(source, profile("CONSUMER", user_city="Pune",
                                        user_state="Maharashtra", disputed_amount=62000.0))
    assert "%" not in result.text
    assert "Rough chance of a favourable outcome" not in result.text
    assert [item.rule for item in result.redactions] == ["outcome_probability"] * 4
    # The reasoning the report did not fault comes through.
    assert "| Factor | Effect on success chance |" in result.text
    assert "### Which one to chase first?" in result.text
    assert verbatim(source, "### Which one to chase first?", "### Bottom line") in result.text
    assert "District Consumer Disputes Redressal Commission" in result.text
    assert RUPEE + " 62,000" in loose(result.text)
    assert RUPEE + " 90,000" in loose(result.text)
    assert "cannot be reduced to a number" in result.text


@pytest.mark.parametrize("text", [
    "The Commission can award interest at 18% per annum from the date of the complaint.",
    "The invoice shows 12% GST on the service component.",
    "You are asking for a 100% refund of the booking amount.",
    "The agreement provides for interest at 2% per month on delayed rent.",
    "The builder asked for a 50% deposit before handing over possession.",
])
def test_legitimate_percentages_pass_through_byte_identical(text):
    """DoD #6. The closed outcome-word list is what stops this being a percentage ban."""
    result = guard_reply(text, profile("CONSUMER"))
    assert result.text == text
    assert result.redactions == ()


# ------------------------------------------------------------- H4: the model law

def test_w1_02_t1_model_tenancy_act_gains_a_caveat_and_loses_the_cite_instruction():
    """DoD #7, verbatim W1-02 t1, user_state=Rajasthan."""
    source = reply("W1-02-t1")
    assert "hawala de rahe hain" in source
    result = guard_reply(source, tenancy_profile())
    # The provision itself is never deleted - it is the product's only real tenancy content.
    assert "Section 11" in loose(result.text)
    assert verbatim(source, "Model Tenancy Act (ya aapke state", " ke mutabik,") in result.text
    assert "model law hai jise har State ko" in result.text
    assert "Rajasthan" in result.text
    # The "put it in your notice" instruction is rewritten, not merely hedged.
    assert "hawala de rahe hain" not in result.text
    assert "sirf ek starting point" in result.text
    assert "model_law_cite_instruction" in result.rules()
    # Procedural advice and the already-hedged authority guess are untouched.
    assert verbatim(source, "Iske liye aapko local rent authority", "\n") in result.text
    assert "Jaipur Rent Authority" in result.text


def test_w1_02_t2_loses_the_invented_authority_website_and_keeps_the_praised_reasoning():
    """DoD #7. The jurisdiction passage was singled out for praise in report section 5."""
    source = reply("W1-02-t2")
    assert "Rajasthan State Rent Authority ke website" in loose(source)
    assert "Jaipur Municipal Corporation" in source
    result = guard_reply(source, tenancy_profile())
    assert "Section 30" in loose(result.text)
    assert "Rajasthan State Rent Authority ke website" not in loose(result.text)
    assert "Jaipur Municipal Corporation" not in result.text
    # Byte-for-byte: the forum follows the property's location, Jaipur not Bengaluru.
    assert verbatim(source, "Aapka security deposit jo Jaipur", "**Kyun?**") in result.text
    assert verbatim(source, "Agar aap Bengaluru se online", "---") in result.text
    # The institution's name itself is never touched, only the website claim about it.
    assert verbatim(source, "**Jaipur Rent Authority ya District Court**", " (") in result.text
    assert RUPEE + "85,000" in loose(result.text)
    assert "constructed_authority_contact" in result.rules()


def test_w1_02_t2_internal_fact_key_parenthetical_is_scrubbed():
    """D1 belt-and-braces, against the verbatim leak: '... ka **naam** (opposite_party_name)'."""
    assert "(opposite_party_name)" in reply("W1-02-t2")
    result = guard_reply(reply("W1-02-t2"), tenancy_profile())
    assert "opposite_party_name" not in result.text
    assert "ka **naam** chahiye" in loose(result.text)
    assert "internal_fact_key" in result.rules()


def test_a_sourced_adopted_state_gets_no_caveat(monkeypatch):
    """Proves the register is actually consulted rather than the caveat being unconditional."""
    monkeypatch.setitem(MODEL_TENANCY_ADOPTION, "Testland", ADOPTED)
    text = "Section 11 covers the deposit refund obligation."
    result = guard_reply(text, tenancy_profile(state="Testland"))
    assert result.text == text
    assert result.redactions == ()


def test_adoption_register_carries_no_unsourced_claim():
    """DoD #7. Fabricating adoption status while fixing a fabrication finding would be the
    worst possible outcome of this cycle, so the register ships empty."""
    assert MODEL_TENANCY_ADOPTION == {}
    for state in ("Rajasthan", "Uttar Pradesh", "Maharashtra", "Delhi", "", None):
        assert adoption_status(state) == UNVERIFIED


# ------------------------------------------------------ M7 / L1: helpline numbers

def test_w1_04_t2_invented_toll_free_number_is_removed_and_1930_survives():
    """DoD #8, with the U+2011 form of 1800-11-001-112."""
    source = reply("W1-04-t2")
    assert FAKE_RBI_NUMBER in source
    result = guard_reply(source, cyber_profile())
    assert FAKE_RBI_NUMBER not in result.text
    assert "1800" not in result.text
    assert "1930 helpline" in loose(result.text)
    assert "https://www.cybercrime.gov.in/" in result.text
    assert RUPEE + "47,500" in loose(result.text)
    assert "apne card, passbook" in result.text
    assert "helpline_unverified" in result.rules()


def test_w1_04_t3_invented_bank_helpline_goes_and_the_named_service_stays():
    """DoD #8: the off-allowlist rbi.org.in path goes; the service name is kept."""
    source = reply("W1-04-t3")
    assert FAKE_SBI_NUMBER in source
    assert "https://www.rbi.org.in/Scripts/Complaints.aspx" in source
    result = guard_reply(source, cyber_profile())
    assert FAKE_SBI_NUMBER not in result.text
    assert "1800" not in result.text
    assert "24-hour helpline" in loose(result.text)
    assert "https://www.rbi.org.in/Scripts/Complaints.aspx" not in result.text
    assert "Banking Ombudsman" in result.text
    assert "https://www.cybercrime.gov.in/" in result.text
    assert "url_off_allowlist" in result.rules()


@pytest.mark.parametrize("key,fragment", [
    ("W1-04-t1", "1930 helpline (RBI) call karein"),
    ("W1-04-t2", "RBI 1930 helpline"),
    ("W1-08-t1", "RBI" + NB_HYPHEN + "designated 1930 helpline"),
])
def test_l1_wrong_operator_attribution_is_dropped_and_1930_kept(key, fragment):
    """DoD #10. 1930 is run by I4C / MHA. A 'is the number real?' check alone passes L1."""
    assert fragment in reply(key)
    result = guard_reply(reply(key), profile("CYBER_FRAUD", disputed_amount=680000.0))
    assert "1930" in result.text
    assert fragment not in result.text
    assert not re.search(r"RBI[\s‐-―-]{0,3}(?:designated\s+)?1930", result.text)
    assert "helpline_wrong_operator" in result.rules()


def test_rbi_is_still_namable_for_the_unauthorised_transaction_notification():
    """DoD #10 negative. H9 (set S7) needs RBI namable; only the 1930 operator is corrected."""
    text = (
        "The Reserve Bank of India's " + chr(0x201C) + "Customer protection for unauthorised "
        "electronic transactions" + chr(0x201D) + " notification requires the bank to "
        "investigate the debit."
    )
    result = guard_reply(text, profile("CYBER_FRAUD"))
    assert result.text == text
    assert result.redactions == ()


def test_w1_08_t1_keeps_the_rbi_liability_sentence_byte_identical():
    """The same lock, against the recorded reply that L1 was reported from."""
    source = reply("W1-08-t1")
    result = guard_reply(source, profile("CYBER_FRAUD", disputed_amount=680000.0))
    assert verbatim(source, "* The Reserve Bank of India", "\n") in result.text
    assert RUPEE + "6,80,000" in loose(result.text)


@pytest.mark.parametrize("text", [
    "Your order 18499 has still not been delivered.",
    "The deposit of " + RUPEE + "85,000 is still with the landlord.",
    "You reported a loss of Rs 1,45,000 from the account.",
    "The booking amount was " + RUPEE + " 62,000 and the salary dues " + RUPEE + " 90,000.",
])
def test_numbers_that_belong_to_the_case_pass_through_byte_identical(text):
    """DoD #9. Indian-grouped amounts must never be read as phone numbers."""
    result = guard_reply(text, profile("CONSUMER", disputed_amount=85000.0))
    assert result.text == text
    assert result.redactions == ()


def test_a_number_matching_the_users_own_phone_is_never_touched():
    text = "I will note the callback number 9876543210 you gave for the helpline follow-up."
    value = profile("CYBER_FRAUD", user_phone="9876543210")
    assert guard_reply(text, value).text == text


def test_a_number_quoted_from_the_users_own_turn_is_never_touched():
    text = "You said the landlord's number is 9123456780 - call him once more before the notice."
    guarded = guard_reply(text, tenancy_profile(),
                          user_message="landlord ka number 9123456780 hai")
    assert guarded.text == text


def test_the_guard_never_injects_a_helpline_number():
    """DoD #9. test_cycle1_verification.py:216 and test_safety_crisis_and_scope.py:197,207
    assert crisis numbers are absent from ordinary turns. The registry is a filter, not a
    footer."""
    text = "I have noted the facts. Please send the landlord a formal written notice."
    result = guard_reply(text, tenancy_profile())
    for _, number in CRISIS_HELPLINES:
        assert number not in result.text
    assert "1930" not in result.text
    assert "15100" not in result.text


# ---------------------------------------------------- the deterministic composers

def deterministic_texts():
    """DoD #13. Every deterministic reply the service composes. The guard is wired at the
    funnel, so it runs on all of these and must be a provable no-op on each."""
    styles = [
        LanguageScript("english", "roman"),
        LanguageScript("hinglish", "roman"),
        LanguageScript("hindi", "devanagari"),
    ]
    out: list[tuple[str, str]] = []
    for style in styles:
        tag = f"{style.language}-{style.script}"
        out.append((f"document_deferral[{tag}]", service._document_deferral_reply(style)))
        out.append((f"limited_demo_prefix[{tag}]", service._limited_demo_prefix(style)))
        question, guidance = crisis_support_copy(style)
        out.append((f"crisis_question[{tag}]", question))
        out.append((f"crisis_support[{tag}]", guidance))
        for danger in (True, False, None):
            for dependants in (True, False):
                for stage in (CRISIS_SUPPORT, "NONE"):
                    q, g = safety_question_and_guidance(style, danger, dependants, stage)
                    out.append((f"safety_q[{tag}-{danger}-{dependants}-{stage}]", q or ""))
                    out.append((f"safety_g[{tag}-{danger}-{dependants}-{stage}]", g or ""))
        for category in ("CONSUMER", "HOUSING_TENANT", "EMPLOYMENT", "CYBER_FRAUD",
                         "POLICE_COMPLAINT", "GENERAL"):
            value = profile(category, user_state="Rajasthan", user_city="Jaipur",
                            disputed_amount=85000.0)
            out.append((f"not_ready[{category}-{tag}]",
                        service._document_not_ready_reply(value, style)))
            out.append((f"localized_fallback[{category}-{tag}]",
                        service._localized_fallback_reply(value, "Tell me more.", style)))
            out.append((f"safe_next_prompt[{category}]", service._safe_next_prompt(value)))
            out.append((f"professional_help[{category}-{tag}]",
                        service._fallback_professional_help(value, style)))
            urgent = profile(category, urgent_guidance_given=True)
            out.append((f"emergency_reminder[{category}-{tag}]",
                        service._emergency_reminder(urgent, style, "RED", "")))
    out.append(("temporary_failure_prefix", service._temporary_failure_prefix()))
    # main.py:1096-1099 upload acknowledgement, verbatim.
    out.append(("upload_ack",
                "I've attached rent-agreement.pdf to your case and I'm reading it now. When "
                "it's processed you'll see what I found under Provided by you. Nothing from "
                "the document is added to your case until you confirm it."))
    return [(name, text) for name, text in out if text]


_COMPOSERS = deterministic_texts()


@pytest.mark.parametrize("name,text", _COMPOSERS, ids=[name for name, _ in _COMPOSERS])
def test_guard_is_a_provable_no_op_on_every_deterministic_composer(name, text):
    for category in ("CONSUMER", "HOUSING_TENANT", "EMPLOYMENT", "CYBER_FRAUD",
                     "POLICE_COMPLAINT", "GENERAL"):
        value = profile(category, user_state="Rajasthan", disputed_amount=85000.0)
        result = guard_reply(text, value)
        assert result.text == text, f"{name} changed under {category}"
        assert result.redactions == (), f"{name} produced redactions under {category}"


def test_crisis_copy_carries_its_numbers_through_all_three_scripts():
    """DoD #9. safety_triage.py:530-559 copy: 112, 181, 1098, 14416, 1800-599-0019, 9820466726."""
    for style in (LanguageScript("english", "roman"), LanguageScript("hinglish", "roman"),
                  LanguageScript("hindi", "devanagari")):
        _, support = crisis_support_copy(style)
        result = guard_reply(support, profile("CYBER_FRAUD"))
        assert result.text == support
        for _, number in CRISIS_HELPLINES:
            assert number in result.text
        assert EMERGENCY_NUMBER in result.text
        _, guidance = safety_question_and_guidance(style, True, True, "NONE")
        assert guard_reply(guidance, profile("HOUSING_TENANT")).text == guidance
        assert "112" in guidance and "181" in guidance and "1098" in guidance


# ---------------------------------------------------------- idempotence + shape

@pytest.mark.parametrize("key,category,state", [
    ("W1-02-t1", "HOUSING_TENANT", "Rajasthan"),
    ("W1-02-t2", "HOUSING_TENANT", "Rajasthan"),
    ("W1-03-t3", "EMPLOYMENT", None),
    ("W1-04-t1", "CYBER_FRAUD", None),
    ("W1-04-t2", "CYBER_FRAUD", None),
    ("W1-04-t3", "CYBER_FRAUD", None),
    ("W1-06-t4", "CONSUMER", "Maharashtra"),
    ("W1-08-t1", "CYBER_FRAUD", None),
])
def test_guard_is_idempotent(key, category, state):
    """DoD #14. The retry/resume path re-guards a reply, so a second pass must be a no-op."""
    value = (tenancy_profile(state) if category == "HOUSING_TENANT"
             else profile(category, user_state=state, disputed_amount=47500.0,
                          language_style="hinglish", script_style="roman"))
    once = guard_reply(reply(key), value)
    twice = guard_reply(once.text, value)
    assert twice.text == once.text
    assert twice.redactions == ()


def test_every_edit_is_machine_readable():
    """DoD #15. A wrong removal must show up in a test, not vanish silently."""
    result = guard_reply(reply("W1-03-t3"), profile("EMPLOYMENT"))
    assert result.redactions
    for item in result.redactions:
        assert item.rule and re.fullmatch(r"[a-z_]+", item.rule)
        assert item.as_log().startswith(item.rule)
    assert result.changed


def test_empty_and_missing_inputs_are_safe():
    assert guard_reply("", profile("CONSUMER")).text == ""
    assert guard_reply("   ", profile("CONSUMER")).text == "   "
    assert guard_reply("Some reply.", None).text == "Some reply."


def test_legal_sources_entries_without_act_or_section_do_not_raise():
    """test_groq_prompt_caching.py:182 passes legal_sources with no 'act' key at all."""
    value = profile("CONSUMER", legal_sources=[{"text": "x", "section": "Section 35"}])
    assert guard_reply("Section 35 applies here.", value).text == "Section 35 applies here."


# ------------------------------------------- identifiers that look like short codes

@pytest.mark.parametrize("category,text", [
    ("CONSUMER", "Quote your order number 18499 when you call the seller's helpline."),
    ("CONSUMER", "Your complaint number 4821 was acknowledged; call the seller to follow up."),
    ("CYBER_FRAUD",
     "Note the FIR number 0345 and the acknowledgement number 99213 from the portal."),
    ("CONSUMER", "The pin code 302001 applies and the invoice number 7781 is on the bill."),
    ("CONSUMER", "The Consumer Protection Act, 2019 replaced the 1986 Act."),
])
def test_case_identifiers_are_never_read_as_helpline_numbers(category, text):
    """Section 3 item 12: a 3-5 digit run is the same shape as an order reference. The
    short-code rule needs a word meaning helpline specifically, and stands down near an
    identifier word - which is also why a bare year survives."""
    result = guard_reply(text, profile(category))
    assert result.text == text
    assert result.redactions == ()


# ----------------------------------------------- several citations in one sentence

def test_multiple_citations_in_one_line_are_each_judged_separately():
    """The verified ones stay byte-identical in place; only the unverifiable one is replaced."""
    text = ("Sections 34 & 35 and Section 47 and Section 58 all deal with jurisdiction; "
            "Section 9 of the Wages Code does not.")
    result = guard_reply(text, profile("CONSUMER"))
    assert "Sections 34 & 35 and Section 47 and Section 58 all deal with jurisdiction" in result.text
    assert "Section 9 of the Wages Code" not in result.text
    assert [item.detail for item in result.redactions if item.rule == "statute_unverified"] == ["9"]


def test_two_unverifiable_citations_in_one_line_are_both_replaced():
    text = ("Under Section 5 of the Payment of Wages Act, 1936 and Section 9(1) of the "
            "Code on Wages, 2019, wages were due.")
    result = guard_reply(text, profile("EMPLOYMENT"))
    assert "Section 5" not in result.text
    assert "Section 9(1)" not in result.text
    assert "Payment of Wages Act, 1936" in result.text
    assert "Code on Wages, 2019" in result.text
    assert [item.detail for item in result.redactions
            if item.rule == "statute_unverified"] == ["5", "9(1)"]


# ------------------------------------------------------------- script matching

DEVANAGARI_RANGE = re.compile("[" + chr(0x900) + "-" + chr(0x97F) + "]")


@pytest.mark.parametrize("language,script,marker", [
    ("english", "roman", "On the chance of winning"),
    ("hinglish", "roman", "Jeetne ke chance ke baare mein"),
    ("hindi", "devanagari", chr(0x91C) + chr(0x940) + chr(0x924) + chr(0x928) + chr(0x947)),
])
def test_inserted_copy_is_script_matched_not_english_only(language, script, marker):
    """Section 3 item 9: the guard's own text follows the user's language and script."""
    text = "There is roughly a 40% chance of a favourable outcome on these facts."
    value = profile("CONSUMER", language_style=language, script_style=script)
    result = guard_reply(text, value)
    assert "%" not in result.text
    assert marker in result.text
    if script == "devanagari":
        assert DEVANAGARI_RANGE.search(result.text)


def test_hindi_approximator_is_consumed_with_the_quantity():
    """Otherwise an inline removal leaves a dangling 'lagbhag' / (Devanagari) behind."""
    lagbhag = chr(0x932) + chr(0x917) + chr(0x92D) + chr(0x917)
    sambhavna = chr(0x938) + chr(0x902) + chr(0x92D) + chr(0x93E) + chr(0x935) + chr(0x928) + chr(0x93E)
    text = sambhavna + " " + lagbhag + " 40 % " + chr(0x939) + chr(0x948) + chr(0x964)
    value = profile("CONSUMER", language_style="hindi", script_style="devanagari")
    result = guard_reply(text, value)
    assert "%" not in result.text
    assert lagbhag not in result.text.split("\n\n")[0]
    assert sambhavna in result.text
