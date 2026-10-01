"""Cycle 4 / Agent 3 - adversarial probes against set S4's output guard.

These are deliberately *not* shaped like `test_response_guard.py`. That file was written by the
same agent that wrote the guard, so it shares the guard's assumptions about which shapes a
fabrication takes. Everything here attacks the space immediately around the eight recorded
wave-1 replies: the same harm in a different spelling, and correct content that the rule's
context window happens to sit next to.

Two classes of failure are tested separately and must not be conflated:

* **a fabrication that reaches the user** - the guard did not fire on something invented;
* **correct law or correct advice silently deleted** - the guard fired on something true. The
  user cannot detect this one, which makes it the more dangerous of the two.

Every reproducer for an open gap is `@pytest.mark.xfail(strict=True)`, so closing the gap turns
the scoreboard red and the gap cannot be closed silently. Nothing here is skipped, and no
existing test is touched.

No provider call, no network, no browser: `guard_reply` is a pure function and the payload
checks build `LLMResponseContext` directly.
"""

import inspect
import json
import re
from pathlib import Path

import pytest

from app.agents.conversation_agent import ConversationalLegalAgent
from app.domains import domain_registry
from app.llm.contracts import LLMResponseContext
from app.services.model_law_adoption import MODEL_TENANCY_ADOPTION, UNVERIFIED, adoption_status
from app.services.response_guard import _LOCATOR_RE, _fact_keys, build_allowlist, guard_reply

FIXTURES = json.loads(
    (Path(__file__).parent / "fixtures" / "wave1_replies.json").read_text(encoding="utf-8")
)

NB_HYPHEN = chr(0x2011)
NARROW_NBSP = chr(0x202F)
RUPEE = chr(0x20B9)

agent = ConversationalLegalAgent()

# Model-law corpus metadata exactly as `rag_node.py:121` stamps it.
TENANCY_SOURCES = [
    {"act": "Model Tenancy Act / State Rent Control Acts", "section": "Section 11",
     "title": "Security Deposit and Refund Obligation",
     "document_type": "Model law - State adoption must be verified"},
    {"act": "Model Tenancy Act / State Rent Control Acts", "section": "Section 30",
     "title": "Rent Court Jurisdiction",
     "document_type": "Model law - State adoption must be verified"},
]


def profile(category, **overrides):
    value = agent._init_case_profile("agent3 adversarial case", category_override=category)
    for key, item in overrides.items():
        if hasattr(value, key):
            setattr(value, key, item)
        else:
            value.key_facts[key] = item
    return value


def reply(key: str) -> str:
    return FIXTURES[key]["reply"]


def tenancy_profile():
    return profile("HOUSING_TENANT", user_state="Rajasthan", language_style="hinglish",
                   legal_sources=TENANCY_SOURCES)


# ===================================================================== passing locks
# Behaviour Agent 2 got right. Locked here so a later cycle cannot regress it while
# widening a rule to close one of the gaps below.

@pytest.mark.parametrize("key", sorted(FIXTURES))
def test_every_recorded_reply_is_byte_identical_to_the_transcript_source(key):
    """The fixture must stay verbatim. A retyped ASCII approximation would pass against code
    that fails in production, so this asserts the code points that actually matter."""
    text = reply(key)
    assert text == FIXTURES[key]["reply"]
    assert "\r" not in text
    # every fixture carries at least one non-ASCII code point from the real transcript
    assert any(ord(char) > 0x2000 for char in text)


@pytest.mark.parametrize("key", sorted(FIXTURES))
def test_guard_is_idempotent_on_every_recorded_reply(key):
    """Independent re-derivation of Agent 2's idempotence claim, across all eight replies and
    three scripts, including the ones Agent 2 did not use for a given rule."""
    for category, style in (("HOUSING_TENANT", "hinglish"), ("CYBER_FRAUD", "english"),
                            ("CONSUMER", "english"), ("EMPLOYMENT", "hindi")):
        case = profile(category, user_state="Rajasthan", language_style=style,
                       legal_sources=TENANCY_SOURCES if category == "HOUSING_TENANT" else [])
        once = guard_reply(reply(key), case).text
        twice = guard_reply(once, case).text
        assert twice == once, f"{key}/{category}/{style} is not idempotent"


def test_w1_02_t2_jurisdiction_passage_survives_byte_for_byte():
    """DoD #7's praised passage. Located by slicing the fixture, never retyped."""
    source = reply("W1-02-t2")
    start = source.index("Aapka security deposit")
    passage = source[start:source.index("**Aapko kya karna chahiye:**", start)]
    guarded = guard_reply(source, tenancy_profile()).text
    assert passage in guarded
    # and the postal/online follow-up paragraph, which is the other half of the praise
    online = verbatim_slice(source, "Agar aap Bengaluru se online", "---")
    assert online.strip() in guarded


def verbatim_slice(source: str, start: str, end: str) -> str:
    first = source.index(start)
    return source[first:source.index(end, first)]


def test_adoption_register_contains_no_unsourced_claim():
    """DoD #7's 'Agent 3 reads it and confirms'. Confirmed: the register is empty, so no State
    can be presented as having enacted the MTA 2021 on this repository's word."""
    assert MODEL_TENANCY_ADOPTION == {}
    for state in ("Rajasthan", "Uttar Pradesh", "Assam", "Tamil Nadu", "Andhra Pradesh",
                  "Delhi", "", None, "  Rajasthan  "):
        assert adoption_status(state) == UNVERIFIED


def test_document_citation_map_provisions_survive_in_their_own_domain():
    """The self-contradiction probe: the product prints IT Act s.66D inside a generated notice,
    so the chat message explaining that notice must not lose it."""
    cases = [
        ("CYBER_FRAUD", "Section 66D of the Information Technology Act, 2000 covers "
                        "cheating by personation.", "66D"),
        ("POLICE_COMPLAINT", "Section 173 of the Bharatiya Nagarik Suraksha Sanhita, 2023 "
                             "governs the FIR.", "173"),
        ("HOUSING_TENANT", "Section 73 of the Indian Contract Act, 1872 gives compensation "
                           "for breach.", "73"),
        ("CONSUMER", "File under Section 35 of the Consumer Protection Act, 2019.", "35"),
        ("GENERAL", "Use Section 6(1) of the Right to Information Act, 2005.", "6(1)"),
    ]
    for category, text, token in cases:
        result = guard_reply(text, profile(category))
        assert token in result.text, f"{category}: {token} was deleted"
        assert result.text == text, f"{category}: text was altered"


def test_consumer_corpus_provisions_survive_every_plausible_phrasing():
    """All seven CONSUMER corpus provisions, in three scripts and both CPA spellings."""
    phrasings = [
        "Section 2(11) of the Consumer Protection Act, 2019 defines deficiency.",
        "Section 2(47) of the Consumer Protection Act 2019 covers unfair trade practice.",
        "Section 2(34) is the definition you need.",
        "Sections 34 & 35 decide the forum.",
        "Section 35 alone is enough to file.",
        "Section 47 covers transfer to the State Commission.",
        "Section 58 is the National Commission's jurisdiction.",
        "Section 69 is the limitation provision, and Section 69(2) allows condonation.",
        "Consumer Protection Act, 2019 ki Section 35 ke tehat complaint file karein.",
        "उपभोक्ता संरक्षण "
        "अधिनियम, 2019 की धारा 35 "
        "के तहत शिकायत दर्ज "
        "करें।",
    ]
    for text in phrasings:
        result = guard_reply(text, profile("CONSUMER"))
        assert result.text == text, f"altered: {text!r} -> {result.text!r}"
        assert result.redactions == ()


def test_tenancy_corpus_provisions_survive_with_a_caveat_never_a_deletion():
    """Lock 2: s.11/15/21/30 are never deleted, in all three scripts."""
    cases = [
        ("hinglish", "Model Tenancy Act ki Section 11 ke mutabik deposit wapas karna hota hai. "
                     "Section 15 aur Section 21 bhi dekhein, aur Section 30 forum decide karta hai."),
        ("english", "Section 11 requires the deposit back. Section 15, Section 21 and Section 30 "
                    "matter too."),
        ("hindi", "धारा 11 के तहत deposit "
                  "लौटाना होता है। "
                  "धारा 30 forum तय करती है।"),
    ]
    for style, text in cases:
        case = profile("HOUSING_TENANT", user_state="Rajasthan", language_style=style,
                       script_style="devanagari" if style == "hindi" else "roman",
                       legal_sources=TENANCY_SOURCES)
        result = guard_reply(text, case)
        for token in re.findall(r"(?:Section|धारा)\s*\d+", text):
            assert token in result.text, f"{style}: {token} deleted"
        assert result.rules() == ("model_law_adoption_caveat",), result.rules()


def test_bare_parent_section_is_not_allowlisted_by_a_corpus_sub_clause():
    """Agent 2's stated invariant, in both directions: `69(2)` is verified against corpus `69`,
    but a bare `Section 2` must not ride on the corpus holding `Section 2(11)`."""
    kept = guard_reply("Section 69(2) allows condonation of delay.", profile("CONSUMER"))
    assert kept.text == "Section 69(2) allows condonation of delay."
    assert kept.redactions == ()

    bare = guard_reply("Section 2 of the Consumer Protection Act, 2019 defines everything.",
                       profile("CONSUMER"))
    assert "statute_unverified" not in bare.rules(), "a bare parent must be caveated, not deleted"
    assert "Section 2" in bare.text
    assert bare.rules() == ("statute_unconfirmed_caveated",), bare.rules()


# ============================================================ D1 - the internal key leak

def test_d1_recorded_vector_is_closed_and_the_backend_copy_survives():
    case = profile("HOUSING_TENANT")
    domain_context = domain_registry.compact_context(case)
    context = LLMResponseContext(user_message="hi", case_summary={}, workflow={},
                                 domain_context=domain_context)
    payload = context.model_payload()
    assert all("key" not in entry for entry in payload["domain_context"]["next_fact_candidates"])
    # llm_conversation.py:517-518 binds the pending interaction from index 0 of this object
    assert domain_context["next_fact_candidates"][0]["key"] == "opposite_party_name"
    assert "opposite_party_name" not in json.dumps(payload["domain_context"]["next_fact_candidates"])


def test_no_raw_profile_field_name_reaches_the_provider_payload():
    """D1 is the *class* 'the model is handed an internal identifier and parrots it', not the
    single field `opposite_party_name`. `compact_context` emits a second one two keys later."""
    for category in ("CONSUMER", "HOUSING_TENANT", "EMPLOYMENT", "CYBER_FRAUD",
                     "POLICE_COMPLAINT"):
        case = profile(category)
        context = LLMResponseContext(
            user_message="hi", case_summary={}, workflow={},
            domain_context=domain_registry.compact_context(case),
        )
        blob = json.dumps(context.model_payload()["domain_context"])
        leaked = sorted(key for key in _fact_keys(category) if f'"{key}"' in blob)
        assert leaked == [], f"{category} leaks {leaked}"


def test_document_field_name_parenthetical_is_scrubbed_from_the_reply():
    """`llm_conversation.py:411-413` hands the model `document:complainant_name`. If the model
    parrots it the way it parroted `opposite_party_name`, nothing removes it: the scrubber's
    closed set is domain facts + profile fields, and document field names are neither."""
    case = profile("HOUSING_TENANT")
    text = "Notice ke liye aapka poora **naam** (complainant_name) chahiye."
    result = guard_reply(text, case)
    assert "complainant_name" not in result.text
    assert "internal_fact_key" in result.rules()


# ============================================================ C2(c) - fabricated case law

def test_c2_recorded_case_law_citation_is_removed():
    source = reply("W1-03-t3")
    tokens = (
        verbatim_slice(source, "(2020)", "123") + "123",
        verbatim_slice(source, "M/s.", "Enterprises") + "Enterprises",
        verbatim_slice(source, "v.", "Karnataka") + "Karnataka",
        verbatim_slice(source, "Supreme Court case you can quote", "\n"),
    )
    for token in tokens:
        assert token in source, f"fixture drifted: {token!r}"
    result = guard_reply(source, profile("EMPLOYMENT"))
    for token in tokens:
        assert token not in result.text, token
    assert "verified case-law database" in result.text


@pytest.mark.parametrize("citation", [
    "Tata Motors Vs. Antonio Paulo Vaz",
    "Tata Motors VS. Antonio Paulo Vaz",
    "Lata Wadhwa vs State of Bihar",
    "Hindustan Unilever v/s Ashok Kumar",
    "Indian Oil Corporation V. Consumer Protection Council",
])
def test_case_law_rule_is_unconditional_across_separator_spellings(citation):
    """DoD #3 says the case-law rule is unconditional. It is conditional on the exact lowercase
    spellings `v.`, `vs.` and `versus`. `Vs.` is the commonest form in Indian consumer-forum
    citation, and nothing else in the pipeline can catch it."""
    text = f"The settled position is in {citation}, which is directly on point.\n\nNext: send a notice."
    result = guard_reply(text, profile("CONSUMER"))
    assert citation not in result.text
    assert "verified case-law database" in result.text


@pytest.mark.parametrize("reporter", [
    "2021 SCC OnLine SC 456",
    "Civil Appeal No. 1234 of 2020",
])
def test_case_law_rule_covers_other_indian_citation_styles(reporter):
    text = f"See {reporter}, decided by the Supreme Court, which is squarely on point.\n\nNext: send a notice."
    result = guard_reply(text, profile("CONSUMER"))
    assert reporter not in result.text


@pytest.mark.parametrize("claim", [
    "A High Court ruling supports exactly this.",
    "Settled case law says the tenant wins here.",
    "The Supreme Court has held that a deposit must be returned within one month.",
    "There is a 2019 judgment directly on this point.",
])
def test_unnamed_authority_forward_reference_at_least_gets_the_disclosure(claim):
    """Agent 2 recorded this as residue only for W1-03 t3, where a *named* case elsewhere in the
    reply happened to trigger the disclosure. Remove the named case and the class is entirely
    unguarded: the user is told a judgment exists and nothing says the bot cannot verify one."""
    text = f"{claim}\n\nNext step: send the formal notice."
    result = guard_reply(text, profile("HOUSING_TENANT", user_state="Rajasthan",
                                       legal_sources=TENANCY_SOURCES))
    assert "verified case-law database" in result.text


def test_w1_03_t3_forward_reference_sentence_does_not_survive():
    source = reply("W1-03-t3")
    promise = verbatim_slice(source, "and a Supreme Court decision", "\n")
    result = guard_reply(source, profile("EMPLOYMENT"))
    assert promise not in result.text


@pytest.mark.parametrize("discussion", [
    "How can I verify a High Court judgment on this issue?",
    "Check whether a Supreme Court decision supports this before citing it.",
])
def test_generic_case_law_verification_discussion_is_preserved(discussion):
    result = guard_reply(discussion, profile("HOUSING_TENANT"))
    assert result.text == discussion
    assert "case_law" not in result.rules()


def test_unnamed_case_law_assertion_preserves_adjacent_advice_and_is_idempotent():
    text = "A High Court ruling supports this. Keep your payment receipts and messages."
    case = profile("HOUSING_TENANT")
    result = guard_reply(text, case)
    assert "A High Court ruling supports this" not in result.text
    assert "Keep your payment receipts and messages." in result.text
    assert result.text.count("verified case-law database") == 1
    assert guard_reply(result.text, case).text == result.text


def test_forward_verification_advice_does_not_license_earlier_court_claim():
    text = "A High Court ruling supports this; please verify it before citing. Keep payment proof."
    result = guard_reply(text, profile("HOUSING_TENANT"))
    assert "A High Court ruling supports this" not in result.text
    assert "Keep payment proof." in result.text
    assert result.text.count("verified case-law database") == 1


def test_unnamed_court_claim_after_unrelated_check_is_removed_without_losing_advice():
    text = ("Check your bank statement, because the Supreme Court has held that deposits "
            "must be returned.")
    result = guard_reply(text, profile("HOUSING_TENANT"))
    assert "the Supreme Court has held" not in result.text
    assert "Check your bank statement" in result.text
    assert "verified case-law database" in result.text


def test_unnamed_supreme_court_has_ruled_claim_is_removed():
    text = "The Supreme Court has ruled that deposits must be refunded within one month."
    result = guard_reply(text, profile("HOUSING_TENANT"))
    assert "The Supreme Court has ruled" not in result.text
    assert "verified case-law database" in result.text


def test_unnamed_court_claim_keeps_same_sentence_evidence_advice():
    text = "A High Court ruling supports your claim, so keep your receipts and messages."
    result = guard_reply(text, profile("HOUSING_TENANT"))
    assert "A High Court ruling supports your claim" not in result.text
    assert "keep your receipts and messages" in result.text
    assert "verified case-law database" in result.text


# ============================================================ C2(a) - fabricated statute

def test_c2_recorded_fabricated_section_is_removed_with_its_quoted_text():
    source = reply("W1-03-t3")
    assert "Section" + NARROW_NBSP + "9(1)" in source
    result = guard_reply(source, profile("EMPLOYMENT"))
    assert "Section 9(1)" not in result.text
    assert "Section" + NARROW_NBSP + "9(1)" not in result.text
    assert "Note on legal sources" in result.text
    assert "Ministry of Labour & Employment" in result.text
    assert "_" not in result.text, "test_api.py:170's lock - no URL may be pasted into prose"
    # the useful prose survives
    assert "before the wage period ends" in result.text


@pytest.mark.parametrize("text,token", [
    ("Under Section 35 of the Industrial Disputes Act, 1947 you can recover the wages.", "35"),
    ("Under Section 73 of the Payment of Wages Act, 1936 you may claim interest.", "73"),
    ("Section 20 of the Police Act, 1861 covers refusal to register an FIR.", "20"),
    ("Section 2(11) of the Code on Wages, 2019 defines your employer.", "2(11)"),
])
def test_no_section_number_is_verified_in_a_corpusless_domain(text, token):
    """Agent 2's own statement of the rule is 'no corpus for the domain -> every section number
    in it is unverifiable'. These four numbers are not. 20, 35, 73 and 173 are among the most
    common section numbers in Indian statute, so this is not a narrow escape."""
    for category in ("EMPLOYMENT", "POLICE_COMPLAINT", "GENERAL"):
        result = guard_reply(text, profile(category))
        assert result.redactions != (), f"{category}: {token} passed unguarded"


@pytest.mark.parametrize("text,token", [
    ("Eviction grounds are in Section 13 of the Rajasthan Rent Control Act, 1950.", "Section 13"),
])
def test_naming_the_real_applicable_act_does_not_cause_deletion(token, text):
    """The perverse incentive. `Section 13 of the applicable rent law` is kept with a caveat;
    `Section 13 of the Rajasthan Rent Control Act, 1950` - the same section, correctly
    attributed to the law that actually binds a Jaipur tenant - is deleted. The guard then
    advises the user to go and check the very Act whose section it just removed."""
    case = profile("HOUSING_TENANT", user_state="Rajasthan", legal_sources=TENANCY_SOURCES)
    result = guard_reply(text, case)
    assert token in result.text
    assert "statute_unverified" not in result.rules()


def test_statute_note_does_not_claim_a_removal_that_never_happened():
    case = profile("HOUSING_TENANT", user_state="Rajasthan", legal_sources=TENANCY_SOURCES)
    result = guard_reply("Eviction grounds are in Section 13 of the applicable rent law.", case)
    assert result.rules() == ("statute_unconfirmed_caveated",)
    assert "removed the section numbers" not in result.text


@pytest.mark.parametrize("language,script,removed,kept", [
    ("english", "roman", "removed the section numbers", "kept the"),
    ("hinglish", "roman", "hata diya hai", "reply mein rakhe hain"),
    ("hindi", "devanagari", "\u0939\u091f\u093e \u0926\u093f\u092f\u093e \u0939\u0948",
     "\u0909\u0924\u094d\u0924\u0930 \u092e\u0947\u0902 \u0930\u0916\u0947 \u0939\u0948\u0902"),
])
@pytest.mark.parametrize("scenario,source", [
    ("caveated", "Section 142 may be relevant to your complaint."),
    ("stripped", "Section 9(1) of the Code on Wages, 2019 fixes the wage period."),
    ("mixed", "Section 142 may be relevant. Section 9(1) of the Code on Wages, 2019 "
     "fixes the wage period."),
])
def test_c2_a3_statute_note_describes_only_the_actions_taken(
    language, script, removed, kept, scenario, source,
):
    case = profile("CONSUMER", language_style=language, script_style=script)
    result = guard_reply(source, case)
    assert (removed in result.text) == (scenario in {"stripped", "mixed"})
    assert (kept in result.text) == (scenario in {"caveated", "mixed"})
    assert ("statute_unverified" in result.rules()) == (scenario in {"stripped", "mixed"})
    assert ("statute_unconfirmed_caveated" in result.rules()) == (scenario in {"caveated", "mixed"})
    assert ("Section 142" in result.text) == (scenario in {"caveated", "mixed"})
    assert "http" not in result.text
    assert guard_reply(result.text, case).text == result.text


# ============================================================ C4 - win probability

def test_c4_recorded_percentages_are_all_removed():
    source = reply("W1-06-t4")
    result = guard_reply(source, profile("CONSUMER"))
    assert "%" not in result.text
    assert "cannot be reduced to a number" in result.text
    # The recorded amounts carry U+202F on BOTH sides of the rupee sign. Sliced out of the
    # fixture rather than retyped, because an ASCII space here would pass against code that
    # fails on the real bytes.
    for keep in (RUPEE + NARROW_NBSP + "62,000", RUPEE + NARROW_NBSP + "90,000",
                 RUPEE + NARROW_NBSP + "50" + NARROW_NBSP + "lakh",
                 "Which one to chase first?", "Effect on success chance"):
        assert keep in source, f"fixture anchor drifted: {keep!r}"
        assert keep in result.text, keep


def test_c4_isolated_legitimate_percentages_pass_byte_identical():
    """DoD #6's five strings, in the isolated form the definition of done states."""
    text = ("The builder took a 50% deposit and 12% GST and charges 2% per month; "
            "a 100% refund is available at 18% per annum.")
    result = guard_reply(text, profile("CONSUMER"))
    assert result.text == text
    assert result.redactions == ()


@pytest.mark.parametrize("text,token", [
    ("A favourable order can include 18% per annum interest on the refund.", "18%"),
    ("Your chances improve a lot if you demand a 100% refund in the notice.", "100%"),
    ("If you win, the 12% GST charged on the cancelled booking is also refundable.", "12%"),
    ("The likelihood of recovery rises when the contract provides 2% per month interest.", "2%"),
])
def test_legitimate_percentage_survives_an_outcome_word_in_the_same_sentence(text, token):
    """§3 lock 10 reads 'percentages with no outcome word nearby'. In real legal prose the
    outcome word is *always* nearby: 'if you win, you get 12% GST back', 'a favourable order
    can include 18% per annum'. Each of these loses the number, keeps the noun, and ends up
    grammatically broken - and then collects a note saying the bot does not calculate a
    probability, on a reply that never gave one."""
    result = guard_reply(text, profile("CONSUMER"))
    assert result.text == text, f"{token} was deleted: {result.text!r}"
    assert result.redactions == ()


def test_devanagari_interest_rate_survives_next_to_sambhavna():
    text = ("संभावना है कि 18% "
            "ब्याज मिलेगा।")
    case = profile("CONSUMER", language_style="hindi", script_style="devanagari")
    result = guard_reply(text, case)
    assert "18%" in result.text


# ============================================================ H4 - model tenancy law

def test_h4_recorded_replies_keep_their_sections_and_gain_the_caveat():
    case = tenancy_profile()
    t1 = guard_reply(reply("W1-02-t1"), case)
    t2 = guard_reply(reply("W1-02-t2"), case)
    assert "**Section" + NARROW_NBSP + "11**" in t1.text or "Section 11" in t1.text
    assert "Section 30" in t2.text or "Section" + NARROW_NBSP + "30" in t2.text
    assert "model law hai jise har State ko" in t1.text
    assert "model law hai jise har State ko" in t2.text
    assert "Rajasthan State Rent Authority ke website" not in t2.text
    assert "opposite_party_name" not in t2.text
    assert RUPEE + "85,000" in t2.text


def test_citing_the_state_act_does_not_trigger_the_model_law_rewrite():
    """`Aapko notice mein Rajasthan Rent Control Act ke Section 11 ka hawala dena chahiye` is
    correct advice. The guard replaces the whole sentence with 'confirm the equivalent provision
    of the State law that actually applies' - circular, because the reply already named it - and
    appends a Model Tenancy Act caveat that is a non-sequitur here."""
    case = tenancy_profile()
    text = "Aapko notice mein Rajasthan Rent Control Act ke Section 11 ka hawala dena chahiye."
    result = guard_reply(text, case)
    assert "Rajasthan Rent Control Act" in result.text
    assert "model_law_cite_instruction" not in result.rules()


def test_procedural_advice_survives_the_constructed_authority_rule():
    """Lock 11: 'Only a contact/website/address claim about a constructed named authority is
    touched - never the institution's name and never the procedural advice.' With no
    parenthetical to swap, the whole instruction to verify the forum disappears, silently, and
    whether it disappears depends on an unrelated section number elsewhere in the reply."""
    advice = "Confirm the exact address of the Jaipur Rent Authority on its official website before filing."
    text = f"Section 11 governs the deposit. {advice} Then attach your rent agreement."
    result = guard_reply(text, tenancy_profile())
    assert "Confirm the exact address" in result.text
    assert "constructed_authority_contact" not in result.rules()


# ============================================================ M7 - invented helplines

def test_m7_recorded_numbers_are_removed_and_the_real_ones_kept():
    fake_rbi = NB_HYPHEN.join(["1800", "11", "001", "112"])
    fake_sbi = NB_HYPHEN.join(["1800", "11", "222", "222"])
    source_t2 = reply("W1-04-t2")
    source_t3 = reply("W1-04-t3")
    assert fake_rbi in source_t2
    assert fake_sbi in source_t3
    assert "https://www.rbi.org.in/Scripts/Complaints.aspx" in source_t3
    case = profile("CYBER_FRAUD", language_style="hinglish")
    t2 = guard_reply(source_t2, case)
    t3 = guard_reply(source_t3, case)
    assert fake_rbi not in t2.text
    assert fake_sbi not in t3.text
    assert "1930" in t2.text
    assert "https://www.cybercrime.gov.in/" in t2.text
    assert "https://www.rbi.org.in/Scripts/Complaints.aspx" not in t3.text
    assert RUPEE + "47,500" in t2.text
    assert "24" + NB_HYPHEN + "hour helpline" in t3.text


def test_m7_never_touches_the_users_own_digits():
    case = profile("CYBER_FRAUD", user_phone="9876500011", disputed_amount=47500)
    text = ("Aapka registered number 9876500011 bank record mein hai. Claim " + RUPEE +
            "85,000 hai, Rs 1,45,000 ka invoice aur order 18499. Helpline se verify karein.")
    result = guard_reply(text, case)
    assert result.text == text
    assert result.redactions == ()


@pytest.mark.parametrize("text,number", [
    ("Call your bank's fraud helpline at 1860-500-1234 immediately.", "1860-500-1234"),
    ("The SBI fraud helpline is 9876543210 - call it now.", "9876543210"),
    ("Call the RBI Ombudsman helpline on 022-2260 3000.", "022-2260 3000"),
])
def test_fabricated_helpline_in_a_non_1800_shape_is_removed(text, number):
    """Agent 2 documented two false negatives (the 19xx/20xx carve-out and 6+ digit codes).
    These three are a wider class it did not record. `1860-xxx-xxxx` is the single most common
    Indian *bank* helpline format - which is the exact context M7 was found in - and a user who
    dials an invented fraud helpline may be dialling the fraudster."""
    result = guard_reply(text, profile("CYBER_FRAUD"))
    assert number not in result.text
    assert "helpline_unverified" in result.rules()


def test_fabricated_official_email_address_is_removed():
    text = "Email the Banking Ombudsman at ombudsman.mumbai@rbi.org.in for escalation."
    result = guard_reply(text, profile("CYBER_FRAUD"))
    assert "ombudsman.mumbai@rbi.org.in" not in result.text


# ============================================================ L1 - operator attribution

def test_l1_all_three_recorded_forms_lose_rbi_and_keep_1930():
    case = profile("CYBER_FRAUD", language_style="hinglish")
    for source, key in (("1930 helpline (RBI) call karein", None),
                        ("RBI 1930 helpline ko call karein", None),
                        ("RBI" + NB_HYPHEN + "designated 1930 helpline", None)):
        result = guard_reply(source, case)
        assert "1930" in result.text
        assert "RBI" not in result.text, source
        assert "helpline_wrong_operator" in result.rules()
    # H9's lock: RBI stays namable for the unauthorised-transaction notification
    rbi_ok = ("RBI ki “Customer protection for unauthorised electronic transactions” "
              "notification ke mutabik bank ko turant inform karein.")
    assert guard_reply(rbi_ok, case).text == rbi_ok


# ============================================================ coverage / persistence

def test_evidence_review_endpoint_persists_the_guarded_reply():
    """`review_evidence_findings` calls `_tag_response`, which rewrites `response.reply_text`,
    and then builds the stored `ChatMessage` from the *original* `reply_text` local. The user
    sees the guarded reply in the API response and the unguarded one when the case is reloaded,
    which defeats the guard on the one path that carries evidence-extraction model output."""
    from app import main as main_module

    source = inspect.getsource(main_module.review_evidence_findings)
    tag_at = source.index("_tag_response")
    persisted = source[tag_at:]
    assert "text=response.reply_text" in persisted, (
        "the persisted ChatMessage must be built from the guarded response, not the "
        "pre-guard local variable"
    )


@pytest.mark.parametrize("text,token", [
    ("Look at Section 43 of the Real Estate (Regulation and Development) Act, 2016.", "Section 43"),
])
def test_parenthesised_act_name_is_caveated_not_deleted(token, text):
    """Split out of the C2-a2 reproducer in cycle 5 because it passes, but **not** because C2-a2
    is fixed - the mechanism is unchanged and the Rajasthan case above still fails.

    `ACT_NAME_RE` cannot match across the parenthetical in "Real Estate (Regulation and
    Development) Act", so no Act attaches to the citation, it takes the act-less route, and
    `_classify` returns "doubtful" - keep and caveat. That is the module's own rule 1 ("caveat on
    doubt, never delete") reached by accident rather than by design. Locked so the accident cannot
    silently become a deletion later; the blind spot itself is noted for S5-B.
    """
    result = guard_reply(text, profile("HOUSING_TENANT"))
    assert token in result.text


# ==================================================================================
# Cycle 6 / Agent 3 - adversarial probes against set S5-B's output
#
# Everything below attacks Agent 2's cycle-6 fix, not cycle 4's guard. The three harm
# classes stay separate, because they cost different things:
#   * a fabrication reaching the user;
#   * correct law or correct advice silently deleted (the user cannot detect this one);
#   * a self-contradiction shipped (a caveat with no cause).
# Byte-faithful throughout: the real U+2011 / U+202F / U+2248 code points, never a
# retyped ASCII approximation, so a probe cannot pass here and fail in production.
# ==================================================================================

# ------------------------------------------------- C4-1 residue: the whole-line rule

@pytest.mark.parametrize("text", [
    "**Interest on a favourable order:** 18%",
    "Interest you can claim on a favourable order: 18%",
    "If you win, the recoverable interest is 18%.",
    "GST charged on the service if you win: 12%",
    "- Deposit you must pay to win the auction: 50%",
    "Likelihood aside, the statutory interest rate is 9%",
])
def test_c4_a_rate_at_the_end_of_a_line_survives_an_outcome_word_in_its_label(text):
    """Cycle 6 removed `_WINDOW` from the inline path but left `_OUTCOME_LINE_RE` untouched.

    Its colon is optional (`:?`) and its label group is `[^:\n]{0,90}`, so the rule is not
    "a `label: value` line" - it is "any line ending in a percentage whose preceding 90
    characters contain an outcome word", and `_apply_outcome_probability` then tests that
    label with `_OUTCOME_WORD_RE`. That is token co-occurrence inside a character window,
    the exact mechanism this cycle exists to remove. Because the regex anchors the `%` at
    end of line no measurand can ever follow the sign, so the step-1 veto cycle 6 added to
    this branch can never fire and the branch has no assertion test at all. The whole line
    goes, not just the number. Correct law, silently deleted.
    """
    result = guard_reply(text, profile("CONSUMER"))
    assert result.text == text, f"correct rate deleted: {result.text!r}"
    assert result.redactions == ()


@pytest.mark.parametrize("text", [
    "Jeetne par milne wala byaj: 18%",
    "\u091c\u0940\u0924\u0928\u0947 \u092a\u0930 \u092e\u093f\u0932\u0928\u0947 "
    "\u0935\u093e\u0932\u093e \u092c\u094d\u092f\u093e\u091c: 18%",
])
def test_c4_a_devanagari_rate_survives_when_its_measurand_sits_in_the_label(text):
    """C4-2's own reproducer passes only because `byaj` happens to follow the sign. Hindi
    and Hinglish normally put the measurand in the label ("jeetne par milne wala byaj:
    18%"), which puts it *before* the number - where cycle 6's lookahead-only measurand
    test, correctly, refuses to look. The whole-line rule then deletes the line.
    """
    case = profile("CONSUMER", language_style="hindi", script_style="devanagari")
    result = guard_reply(text, case)
    assert "18%" in result.text, f"correct rate deleted: {result.text!r}"


def test_c4_a_markdown_rate_list_keeps_every_rate_and_earns_no_probability_note():
    """The double fault cycle 6 set out to close, reproduced on the shape the product
    actually emits rates in. Two of four bullets are deleted and the reply is then told
    "the strength of a case cannot be reduced to a number" although it made no probability
    claim: correct law deleted *and* a self-contradiction shipped, in one reply.
    """
    text = (
        "Here is what the law allows you to claim.\n"
        "\n"
        "- **Security deposit refundable:** 100%\n"
        "- **Statutory interest if you win:** 18%\n"
        "- **GST on the service you can recover on a favourable order:** 12%\n"
        "- **Contractual penalty per month:** 2%\n"
    )
    result = guard_reply(text, profile("CONSUMER"))
    assert "**Statutory interest if you win:** 18%" in result.text
    assert "**GST on the service you can recover on a favourable order:** 12%" in result.text
    assert "cannot be reduced to a number" not in result.text
    assert result.redactions == ()


# ------------------------------------------- C4 under-filter opened by removing _WINDOW

@pytest.mark.parametrize("text", [
    "Rough chance of a favourable outcome: about 30% in your case.",
    "Your chance of a favourable order is roughly 30% here.",
    "Success rate in such matters is about 55% in my estimate.",
    "Probability of recovery: 25% given the evidence you hold.",
    "Realistically your chances of winning this are close to 60% today.",
    "Based on what you have told me, the odds of success come to nearly 65% overall.",
    "Win rate for these complaints in Jaipur is about 45% historically.",
    "Aapke jeetne ki sambhavna is case mein takriban 30% hai.",
    "\u0906\u092a\u0915\u0947 \u091c\u0940\u0924\u0928\u0947 \u0915\u0940 "
    "\u0938\u0902\u092d\u093e\u0935\u0928\u093e \u0907\u0938 \u092e\u093e\u092e\u0932\u0947 "
    "\u092e\u0947\u0902 \u0932\u0917\u092d\u0917 30% \u0939\u0948\u0964",
])
def test_c4_a_win_probability_in_ordinary_prose_is_still_removed(text):
    """Removing the +/-70 window fixed the over-filter and opened the opposite fault. Three
    predicates each cover one narrow shape: the `prob` group needs the noun immediately
    after the sign, `_PROB_BEFORE_RE` needs it immediately before across nothing but a
    copula within 40 characters, and `_OUTCOME_LINE_RE` needs the sign at end of line. A
    probability noun sitting earlier in the same clause with ordinary words in between falls
    through all three. This is C4's original harm class: a number the product does not
    compute, presented to a user as their chance of winning. The Hinglish and Devanagari
    cases are not a vocabulary gap - `sambhavna` is already in `_PROB_NOUN`; they fail
    because "is case mein" is not in `_PROB_BRIDGE`.
    """
    result = guard_reply(text, profile("CONSUMER"))
    assert "%" not in result.text, f"invented win probability survived: {result.text!r}"
    assert "outcome_probability" in result.rules()


@pytest.mark.parametrize("text", [
    "I estimate a 65% refund chance in your matter.",
    "There is a 40% refund chance if you file within the limitation period.",
    "Your success rate here is 55% in value terms.",
    "Odds of winning are about 60% on the deposit refund claim.",
    "I would put your chance of winning at 45% on the amount claimed.",
    "Your odds of a favourable order are 70% of the value at stake.",
    "- **Tour" + NB_HYPHEN + "operator refund:** ~30" + NARROW_NBSP + "% refund chance, "
    "mainly limited by the two" + NB_HYPHEN + "year limitation period.",
])
def test_c4_a_measurand_after_the_number_does_not_rescue_a_win_probability(text):
    """The fix's premise is "a measurand after the sign means rate, keep". `_MEASURAND`
    includes `value|amount|share|stake|price|cost|refund|fees|charges`, and
    `_MEASURAND_AFTER_RE` allows three `of|the|on|in|a|an|as|ka|ki|ke` bridge tokens, so a
    probability claim only has to be followed by a quantity noun within four words to
    become unkillable. The last parameter is W1-06 t4's own invented line - real bytes -
    with two words swapped: the fixture's spelling is removed, this one is not.
    """
    result = guard_reply(text, profile("CONSUMER"))
    assert "%" not in result.text, f"invented win probability survived: {result.text!r}"


@pytest.mark.parametrize("text,is_probability", [
    ("I estimate a 30% refund chance for this claim.", True),
    ("The requested remedy is a 100% refund.", False),
    ("Your odds of success are 30\u201340% on these facts.", True),
    ("If you win, statutory interest ranges from 9\u201312% per annum.", False),
])
def test_c4_a_probability_and_rate_contrasts_include_ranges(text, is_probability):
    result = guard_reply(text, profile("CONSUMER"))
    if is_probability:
        assert "%" not in result.text
        assert "outcome_probability" in result.rules()
    else:
        assert result.text == text
        assert result.redactions == ()


@pytest.mark.parametrize("text", [
    "Your chance of winning is 30% and the refund is 100% of the amount paid.",
    "Jeetne ke chances 30% hain aur refund 100% hai.",
    "\u091c\u0940\u0924\u0928\u0947 \u0915\u0940 \u0938\u0902\u092d\u093e\u0935\u0928\u093e "
    "30% \u0939\u0948 \u0914\u0930 \u0930\u093f\u092b\u0902\u0921 100% \u0939\u094b\u0917\u093e.",
    "- **Win chance:** 30% and **Refund:** 100%.",
])
def test_c4_a_conjoined_refund_percentage_survives_probability_removal(text):
    result = guard_reply(text, profile("CONSUMER"))
    assert "30%" not in result.text, f"invented chance survived: {result.text!r}"
    assert "100%" in result.text, f"legitimate refund deleted: {result.text!r}"
    assert "outcome_probability" in result.rules()


@pytest.mark.parametrize("text", [
    "Your chance, based on these facts, is 30%.",
    "Jeetne ki sambhavna, aapke facts ke hisab se, 30% hai.",
    "\u091c\u0940\u0924\u0928\u0947 \u0915\u0940 \u0938\u0902\u092d\u093e\u0935\u0928\u093e, "
    "\u0907\u0938 \u092e\u093e\u092e\u0932\u0947 \u092e\u0947\u0902, 30% \u0939\u0948.",
])
def test_c4_a_probability_with_explanatory_commas_is_removed(text):
    result = guard_reply(text, profile("CONSUMER"))
    assert "30%" not in result.text, f"invented chance survived: {result.text!r}"
    assert "outcome_probability" in result.rules()


def test_c4_a_interest_rate_after_uncertain_chance_and_aside_is_unchanged():
    text = "Your chance is uncertain, but interest, if awarded, is 18%."
    result = guard_reply(text, profile("CONSUMER"))
    assert result.text == text
    assert result.redactions == ()


def test_c4_a_chance_with_one_explanatory_comma_is_removed():
    text = "Your chance of success, roughly 30%."
    result = guard_reply(text, profile("CONSUMER"))
    assert "30%" not in result.text
    assert "outcome_probability" in result.rules()


# ------------------------------------------------------------- C2-a2 residue and widening

@pytest.mark.xfail(strict=True, reason="C2-a2 residue: a real State rent Act whose name is "
                                      "not '<State> Rent Control Act' is still deleted")
@pytest.mark.parametrize("act", [
    "Karnataka Rent Act, 1999",
    "West Bengal Premises Tenancy Act, 1997",
    "Madhya Pradesh Accommodation Control Act, 1961",
    "Punjab Urban Rent Restriction Act, 1949",
    "Himachal Pradesh Urban Rent Control Act, 1987",
    "Odisha House Rent Control Act, 1967",
    "Uttar Pradesh Urban Premises Tenancy Act, 2021",
])
def test_c2_a2_a_real_state_rent_act_outside_the_corpus_family_name_is_not_deleted(act):
    """`_act_family` recognises only the literal "<State> Rent Control Act" naming style, so
    it covers Delhi, Maharashtra, Rajasthan and Chhattisgarh and nothing else. Every Act
    here is the operative rent statute of its State; the last one is Uttar Pradesh's
    enactment of the Model Tenancy Act, i.e. exactly what HOUSING_TENANT's corpus is about.
    `Karnataka Rent Act, 1999` is deleted by the guard rail itself - its family key `rent
    act` is 8 characters, under the 10-character floor. The perverse incentive C2-a2 named
    is narrowed to four States, not removed: naming the applicable Act is still more
    dangerous than naming none, for most of India.
    """
    text = f"Eviction grounds are in Section 13 of the {act}."
    result = guard_reply(text, tenancy_profile())
    assert "Section 13" in result.text, f"correct State law deleted: {result.text!r}"


@pytest.mark.parametrize("act", [
    "Rajasthan Model Tenancy Act, 2022",
    "Jaipur Model Tenancy Act, 2024",
    "Fictional Model Tenancy Act of Narnia",
])
def test_c2_a2_a_fabricated_state_model_tenancy_act_is_not_accepted_as_verified(act):
    """Cycle 6's edit (a) registers each alternative of "Model Tenancy Act / State Rent
    Control Acts" in `verified_pairs`, which puts the short key `model tenancy act` on the
    **accept** path. `licensed()` compares Act names by substring containment, so any name
    containing `model tenancy act` now rides on the corpus's sections 11/15/21/30 and the
    citation is reported as verified with no statute rule at all. Before this cycle the
    same sentence was stripped. Agent 2's code comment states "`licensed()` is not
    loosened"; measured, it is - `licensed("11", ["rajasthan model tenancy act 2022"])`
    goes False -> True. A1's own docstring rationale ("this is what stops Section 73 of the
    Payment of Wages Act riding on Indian Contract Act s.73") is the rule being broken.
    """
    text = f"Section 11 of the {act} requires the deposit back within one month."
    result = guard_reply(text, tenancy_profile())
    assert any(rule.startswith("statute") for rule in result.rules()), (
        f"fabricated Act accepted as verified: {result.rules()!r}")


def test_c2_a2_a_only_the_canonical_model_tenancy_name_licenses_section_11():
    allow = build_allowlist(tenancy_profile())
    assert allow.licensed("11", ["model tenancy act"])
    assert allow.licensed("11", ["model tenancy act 2021"])
    for name in (
        "rajasthan model tenancy act 2022",
        "jaipur model tenancy act 2024",
        "fictional model tenancy act of narnia",
        "model tenancy act 2022",
    ):
        assert not allow.licensed("11", [name]), name

    result = guard_reply(
        "Section 11 of the Model Tenancy Act, 2021 concerns the security deposit.",
        tenancy_profile(),
    )
    assert "Section 11" in result.text
    assert "statute_unverified" not in result.rules()
    assert "model_law_adoption_caveat" in result.rules()


@pytest.mark.parametrize("act", [
    "Model Tenancy Act (Rajasthan), 2022",
    "Model Tenancy Act (Narnia edition), 2024",
    "Model Tenancy Act (2024)",
    "Model Tenancy Act: Rajasthan 2022",
])
def test_c2_a2_a_fabricated_model_act_suffix_must_not_be_positively_licensed(act):
    """The Act after `of the` includes the qualifier; matching only through `Act` is
    insufficient evidence that the exact canonical model law was named."""
    text = f"Section 11 of the {act} requires the deposit back."
    result = guard_reply(text, tenancy_profile())
    assert any(rule.startswith("statute") for rule in result.rules()), (
        f"fabricated Act accepted as verified: {result.rules()!r}")


def test_c2_a2_a_lowercase_fabricated_model_act_must_not_be_bare_section():
    text = "Section 11 of the rajasthan model tenancy act, 2022 requires the deposit back."
    result = guard_reply(text, tenancy_profile())
    assert any(rule.startswith("statute") for rule in result.rules()), (
        f"fabricated Act accepted as verified: {result.rules()!r}")


@pytest.mark.parametrize("category, text, section", [
    ("CYBER_FRAUD", "Section 66D of the Information Technology Act (2000) covers "
     "cheating by personation.", "Section 66D"),
    ("POLICE_COMPLAINT", "Section 173 of the Bharatiya Nagarik Suraksha Sanhita (2023) "
     "governs the FIR.", "Section 173"),
])
def test_c2_a2_a_curated_citation_with_parenthesized_year_is_not_deleted(
    category, text, section,
):
    result = guard_reply(text, profile(category))
    assert section in result.text, f"curated citation deleted: {result.text!r}"


@pytest.mark.parametrize("act", [
    "Model Tenancy Act [Rajasthan], 2022",
    "Model Tenancy Act - Rajasthan, 2022",
])
def test_c2_a2_a_other_qualifiers_cannot_inherit_model_law_license(act):
    text = f"Section 11 of the {act} governs deposits."
    result = guard_reply(text, tenancy_profile())
    assert any(rule.startswith("statute") for rule in result.rules()), (
        f"qualified Act positively licensed: {result.rules()!r}")


# ------------------------------------------------------- H4-2: _LOCATOR_RE over-matches

@pytest.mark.parametrize("text", [
    "Confirm on the Jaipur Rent Authority website whether the 250000 deposit you paid "
    "crosses the pecuniary limit.",
    "Check the Jaipur Rent Authority website yourself; your claim of 120000 rupees may "
    "need a different forum.",
    "You should verify on the Rajasthan Rent Tribunal website whether the 300000 claim is "
    "within its limit.",
])
def test_h4_2_a_six_digit_rupee_amount_is_not_a_locator(text):
    r"""`_LOCATOR_RE`'s PIN-code alternative is `\b[1-9]\d{5}\b` - any six-digit run. Every
    rupee figure from 1 lakh to 9.99 lakh reads as a locator, which is the ordinary range of
    a rent deposit or a consumer claim. Branch 1b then deletes the sentence, and the
    sentence it deletes is precisely the "go and confirm it yourself" advice that H4-2's fix
    exists to protect. So the fix protects that advice only while the reply does not also
    mention the user's own amount. Correct advice, silently deleted.
    """
    result = guard_reply(text, tenancy_profile())
    assert result.text == text, f"correct advice deleted: {result.text!r}"
    assert "constructed_authority_contact" not in result.rules()


@pytest.mark.parametrize("text", [
    "The Jaipur Rent Authority website lists office PIN 302005 for filing.",
    "The Jaipur Rent Authority website lists its office at 12 Ashok Nagar Road.",
])
def test_h4_2_a_explicit_postal_or_street_locator_remains_guarded(text):
    assert _LOCATOR_RE.search(text)
    result = guard_reply(text, tenancy_profile())
    assert text not in result.text
    assert "constructed_authority_contact" in result.rules()


@pytest.mark.parametrize("locator", [
    "https://rent-authority.example.gov.in/office",
    "rent-authority.example.gov.in",
    "PIN code: 302005",
    "302005 PIN",
])
def test_h4_2_a_url_hostname_and_labelled_pin_are_locators(locator):
    assert _LOCATOR_RE.search(locator)


# ------------------------------------------- a passing lock, not a gap: byte-faithfulness

def test_c2_recorded_case_law_is_removed_in_the_fixtures_own_bytes():
    """A second byte-faithful lock for the W1-03 citation and later short-form reference."""
    source = reply("W1-03-t3")
    tokens = (f"(2020){NARROW_NBSP}6{NARROW_NBSP}SCC{NARROW_NBSP}123",
              f"M.{NARROW_NBSP}S.{NARROW_NBSP}R.{NARROW_NBSP}Enterprises",
              f"v.{NARROW_NBSP}State of Karnataka",
              "M.S.R. Enterprises")
    for token in tokens:
        assert token in source, f"fixture drifted: {token!r}"
    result = guard_reply(source, profile("EMPLOYMENT"))
    for token in tokens:
        assert token not in result.text, token
