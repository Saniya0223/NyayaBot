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
from app.services.response_guard import _fact_keys, guard_reply

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


@pytest.mark.xfail(strict=True, reason="AGENT3 GAP D1-a: domain_context['jurisdiction']"
                                       "['fact_key'] still ships a raw profile field name to "
                                       "both providers on every single turn")
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


@pytest.mark.xfail(strict=True, reason="AGENT3 GAP D1-b: missing_information ships internal "
                                       "document field names, and the reply-side scrub's "
                                       "closed set does not contain them")
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
    result = guard_reply(source, profile("EMPLOYMENT"))
    for token in ("(2020) 6 SCC 123", "M. S. R. Enterprises", "v. State of Karnataka",
                  "Supreme Court case you can quote"):
        assert token not in result.text, token
    assert "verified case-law database" in result.text


@pytest.mark.parametrize("citation", [
    "Tata Motors Vs. Antonio Paulo Vaz",
    "Tata Motors VS. Antonio Paulo Vaz",
    "Lata Wadhwa vs State of Bihar",
    "Hindustan Unilever v/s Ashok Kumar",
    "Indian Oil Corporation V. Consumer Protection Council",
])
@pytest.mark.xfail(strict=True, reason="AGENT3 GAP C2-c1: CASE_NAME_RE carries no re.IGNORECASE "
                                       "and requires a period on 'vs', so 'Vs.', 'VS.', 'V.', "
                                       "bare 'vs' and 'v/s' all pass unguarded with no "
                                       "disclosure appended")
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
@pytest.mark.xfail(strict=True, reason="AGENT3 GAP C2-c2: REPORTER_RE covers neutral-citation "
                                       "and appeal-number styles for neither shape")
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
@pytest.mark.xfail(strict=True, reason="AGENT3 GAP C2-c3: an unnamed-authority forward reference "
                                       "fires no rule at all, so unlike W1-03 t3 there is not "
                                       "even a contradicting disclosure")
def test_unnamed_authority_forward_reference_at_least_gets_the_disclosure(claim):
    """Agent 2 recorded this as residue only for W1-03 t3, where a *named* case elsewhere in the
    reply happened to trigger the disclosure. Remove the named case and the class is entirely
    unguarded: the user is told a judgment exists and nothing says the bot cannot verify one."""
    text = f"{claim}\n\nNext step: send the formal notice."
    result = guard_reply(text, profile("HOUSING_TENANT", user_state="Rajasthan",
                                       legal_sources=TENANCY_SOURCES))
    assert "verified case-law database" in result.text


@pytest.mark.xfail(strict=True, reason="AGENT3 GAP C2-c4: Agent 2's recorded residue - W1-03 t3's "
                                       "opening promise of a Supreme Court decision survives and "
                                       "is contradicted by the appended disclosure")
def test_w1_03_t3_forward_reference_sentence_does_not_survive():
    source = reply("W1-03-t3")
    promise = verbatim_slice(source, "and a Supreme Court decision", "\n")
    result = guard_reply(source, profile("EMPLOYMENT"))
    assert promise not in result.text


# ============================================================ C2(a) - fabricated statute

def test_c2_recorded_fabricated_section_is_removed_with_its_quoted_text():
    source = reply("W1-03-t3")
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
@pytest.mark.xfail(strict=True, reason="AGENT3 GAP C2-a1: is_verified_section is consulted before "
                                       "corpus_available, and the curated document-citation map "
                                       "plus the RTI corpus are allowlisted globally by section "
                                       "number alone, so a fabricated citation whose number "
                                       "collides with one of them passes a corpus-less domain "
                                       "with zero redactions")
def test_no_section_number_is_verified_in_a_corpusless_domain(text, token):
    """Agent 2's own statement of the rule is 'no corpus for the domain -> every section number
    in it is unverifiable'. These four numbers are not. 20, 35, 73 and 173 are among the most
    common section numbers in Indian statute, so this is not a narrow escape."""
    for category in ("EMPLOYMENT", "POLICE_COMPLAINT", "GENERAL"):
        result = guard_reply(text, profile(category))
        assert result.redactions != (), f"{category}: {token} passed unguarded"


@pytest.mark.parametrize("text,token", [
    ("Eviction grounds are in Section 13 of the Rajasthan Rent Control Act, 1950.", "Section 13"),
    ("Look at Section 43 of the Real Estate (Regulation and Development) Act, 2016.", "Section 43"),
])
@pytest.mark.xfail(strict=True, reason="AGENT3 GAP C2-a2: _classify uses a named Act only to "
                                       "*reject*, so naming the real applicable State Act makes "
                                       "deletion strictly MORE likely than naming no Act at all")
def test_naming_the_real_applicable_act_does_not_cause_deletion(token, text):
    """The perverse incentive. `Section 13 of the applicable rent law` is kept with a caveat;
    `Section 13 of the Rajasthan Rent Control Act, 1950` - the same section, correctly
    attributed to the law that actually binds a Jaipur tenant - is deleted. The guard then
    advises the user to go and check the very Act whose section it just removed."""
    case = profile("HOUSING_TENANT", user_state="Rajasthan", legal_sources=TENANCY_SOURCES)
    result = guard_reply(text, case)
    assert token in result.text
    assert "statute_unverified" not in result.rules()


@pytest.mark.xfail(strict=True, reason="AGENT3 GAP C2-a3: on the caveat-only path the appended "
                                       "note tells the user sections were removed when none were")
def test_statute_note_does_not_claim_a_removal_that_never_happened():
    case = profile("HOUSING_TENANT", user_state="Rajasthan", legal_sources=TENANCY_SOURCES)
    result = guard_reply("Eviction grounds are in Section 13 of the applicable rent law.", case)
    assert result.rules() == ("statute_unconfirmed_caveated",)
    assert "removed the section numbers" not in result.text


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
@pytest.mark.xfail(strict=True, reason="AGENT3 GAP C4-1: the +/-70 character outcome-word window "
                                       "deletes a legitimate rate or quantum whenever an outcome "
                                       "word shares the sentence, which is exactly where such "
                                       "percentages naturally appear")
def test_legitimate_percentage_survives_an_outcome_word_in_the_same_sentence(text, token):
    """§3 lock 10 reads 'percentages with no outcome word nearby'. In real legal prose the
    outcome word is *always* nearby: 'if you win, you get 12% GST back', 'a favourable order
    can include 18% per annum'. Each of these loses the number, keeps the noun, and ends up
    grammatically broken - and then collects a note saying the bot does not calculate a
    probability, on a reply that never gave one."""
    result = guard_reply(text, profile("CONSUMER"))
    assert result.text == text, f"{token} was deleted: {result.text!r}"
    assert result.redactions == ()


@pytest.mark.xfail(strict=True, reason="AGENT3 GAP C4-2: the same false positive in Devanagari, "
                                       "where the Hindi word for 'probability' is also the "
                                       "ordinary word used before a legitimate rate")
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


@pytest.mark.xfail(strict=True, reason="AGENT3 GAP H4-1: the model-law rules trigger on the bare "
                                       "section NUMBER, so a reply that correctly cites the "
                                       "State's own Rent Control Act is rewritten and given an "
                                       "MTA-2021 caveat about an Act it never mentioned")
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


@pytest.mark.xfail(strict=True, reason="AGENT3 GAP H4-2: the constructed-authority rule's "
                                       "sentence-level fallback deletes the procedural advice "
                                       "outright, which §3 lock 11 forbids")
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
    case = profile("CYBER_FRAUD", language_style="hinglish")
    t2 = guard_reply(reply("W1-04-t2"), case)
    t3 = guard_reply(reply("W1-04-t3"), case)
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
@pytest.mark.xfail(strict=True, reason="AGENT3 GAP M7-1: the filter only knows the 1800 prefix "
                                       "and bare 3-5 digit short codes, so a fabricated number "
                                       "in the 1860 bank format, a fabricated 10-digit mobile, "
                                       "or a fabricated STD landline all reach the user")
def test_fabricated_helpline_in_a_non_1800_shape_is_removed(text, number):
    """Agent 2 documented two false negatives (the 19xx/20xx carve-out and 6+ digit codes).
    These three are a wider class it did not record. `1860-xxx-xxxx` is the single most common
    Indian *bank* helpline format - which is the exact context M7 was found in - and a user who
    dials an invented fraud helpline may be dialling the fraudster."""
    result = guard_reply(text, profile("CYBER_FRAUD"))
    assert number not in result.text
    assert "helpline_unverified" in result.rules()


@pytest.mark.xfail(strict=True, reason="AGENT3 GAP M7-2: the URL allowlist is http(s)-only, so a "
                                       "fabricated official email address is not checked at all")
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

@pytest.mark.xfail(strict=True, reason="AGENT3 GAP COV-1: main.py's evidence-review endpoint "
                                       "guards response.reply_text but persists the stale "
                                       "unguarded local, so the redaction is lost from history")
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
