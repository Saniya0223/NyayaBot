# Agent 3 — Cycle 4 adversarial test report (set S4)

Set S4 = D1 (prompt key leak) + C2, C4, H4, M7, M8, L1.

```
BEFORE my work:  1 failed, 721 passed, 1 xfailed, 3 warnings
AFTER  my work:  1 failed, 752 passed, 34 xfailed, 3 warnings
FAILED tests/test_doc_generator.py::test_document_generation_notice   <- pre-existing, untouched
```
`+31 passing, 0 lost.` The 34 xfails are N8 plus my 33 `xfail(strict=True)` reproducers. No test
was deleted, skipped, weakened or reordered. No live Groq/Gemini call, no server, no browser,
`tests/test_browser_autofill.py` ignored on every run. `app/services/safety_triage.py` not touched.

New file: **`backend/tests/test_cycle4_adversarial.py`** (31 passing locks + 33 strict-xfail
reproducers). Nothing else in the repo was modified.

---

## Method check first — Agent 2's fixtures are honest

I verified `backend/tests/fixtures/wave1_replies.json` **programmatically byte-for-byte** against
`qa-pipeline/wave-1/transcripts.json` — all 8 replies and their `degraded_quota` flags match
exactly. The real code points are present (U+2011, U+2013, U+2014, U+202F, U+2248, U+20B9, U+2192,
U+FE0E). No retyped ASCII anywhere. Every probe below runs on those bytes, and my own byte-exact
expectations are *sliced out of the fixture*, not typed — the one place I typed an amount by hand
(`₹ 62,000`) failed immediately, because the transcript has U+202F on **both** sides of the rupee
sign. That is exactly the failure mode the method requirement exists to prevent, and it is worth
Agent 4 knowing it is a live hazard, not a theoretical one.

**Everything Agent 2 claimed about the eight recorded replies is true and reproducible.** I
re-derived every one with unified diffs against the verbatim source. The diffs are minimal and
surgical — on W1-02 t2 exactly two lines change, on W1-04 t2 exactly one. The definition of done
is met on the recorded evidence for all seven items.

**The problems are all in the space immediately around that evidence.** Each rule is shaped
tightly enough to pass its DoD criterion on the exact recorded string while leaving neighbouring
shapes of the same harm unhandled — and in three places the rule deletes content that is correct.

---

## Verdict table

| Item | Verdict | Dominant remaining harm |
|---|---|---|
| D1 prompt key leak | **partially fixed** | internal identifier still reaches the model (MED) |
| C2(c) case law | **partially fixed** | fabricated judgment reaches user, no disclosure (**HIGH**) |
| C2(a) statute | **partially fixed** | fabrication passes **and** correct law deleted (**HIGH**) |
| C4 win probability | **partially fixed** | correct rates silently deleted (**HIGH**) |
| H4 model tenancy law | **partially fixed** | correct advice deleted + irrelevant caveat (MED-HIGH) |
| M7 helplines | **partially fixed** | fabricated phone number reaches user (**HIGH**) |
| L1 operator attribution | **fixed** | — |
| M8 no-corpus disclosure | **fixed** (backend) | — |
| Coverage / idempotence | **partially fixed** | one path drops the guard's edit from history (MED) |

---

# 1. D1 — the internal fact key leaked to the user

**Problem.** `domains/registry.py:141-150` put `"key": fact.key` into every
`next_fact_candidates` entry and `llm_conversation.py:388` handed it to the provider, which
parroted it: W1-02 t2 said *"landlord ya property manager ka **naam** (opposite_party_name)
chahiye"*.

**Fix Agent 2 made.** `LLMResponseContext.model_payload()` (`app/llm/contracts.py:205-227`)
rebuilds the candidate list as new dicts with `REDACTED_CANDIDATE_KEYS = ("key",)` removed; both
providers call it instead of `model_dump`; plus a reply-side scrub
(`response_guard._strip_fact_key_parentheticals`) against a closed set read live from the domain
registry and `StructuredCaseProfile.model_fields`.

**Status: partially fixed.**

*What genuinely works* (`test_d1_recorded_vector_is_closed_and_the_backend_copy_survives`): the
payload's candidates carry no `key`, the string `opposite_party_name` is absent from the serialised
`next_fact_candidates`, and `domain_context["next_fact_candidates"][0]["key"]` is still
`"opposite_party_name"` in memory — so `llm_conversation.py:517-518`'s pending-interaction binding
is intact. The separate-payload-object approach is right, and DoD #2 as literally worded is met.

*What is still open.* D1 is the class "the model is handed an internal identifier and parrots it",
not one field name. Two more identifiers reach the provider in the very same payload:

1. **`domain_context["jurisdiction"]["fact_key"] == "user_state"`**, emitted by
   `registry.compact_context` about 12 lines below `next_fact_candidates`, and **not** stripped by
   `model_payload`. Verified for all five real domains — every single turn ships a raw profile
   field name to both providers. Reproducer:
   `test_no_raw_profile_field_name_reaches_the_provider_payload`.
2. **`missing_information` ships `document:<internal field name>`**
   (`llm_conversation.py:411-413`), e.g. `document:complainant_name`,
   `document:incident_narrative`, `document:landlord_address`.

The reply-side scrub covers (1) — `user_state` is in `StructuredCaseProfile.model_fields` — but
**not** (2): `complainant_name`, `incident_narrative`, `landlord_address`, `employee_role`,
`order_number` and `previous_requests` are document-registry field names and are in neither the
domain fact set nor the profile schema, so they are outside the closed set. Confirmed: guarding
`"Notice ke liye aapka poora **naam** (complainant_name) chahiye."` returns it **unchanged**.
Reproducer: `test_document_field_name_parenthetical_is_scrubbed_from_the_reply`.

So the exact recorded leak is closed and the identical leak one field away is not, on both layers.
Agent 2's structural lock (a test that a future provider cannot call `model_dump`) does not help,
because the leak is inside `domain_context`, which `model_payload` passes through.

**Severity: MEDIUM.** Same harm class as the original finding, lower-value field.

---

# 2. C2(c) — fabricated Supreme Court citation

**Problem.** W1-03 t3 invented *"M/s. M. S. R. Enterprises v. State of Karnataka, (2020) 6 SCC
123"* under a heading *"Supreme Court case you can quote"*, on a healthy turn, in a domain with no
case-law corpus anywhere in the product.

**Fix Agent 2 made.** `response_guard._apply_case_law`: `CASE_NAME_RE` (`X v./vs./versus Y`) and
`REPORTER_RE` (`(YYYY) N SCC M` / `AIR YYYY SC N`), unconditional and allowlist-free; removes the
anchoring markdown block, then later sentences referring to the same party via needles derived from
the reply itself, then an orphaned heading; appends a case-law disclosure.

**Status: partially fixed. This is the most serious item in the set.**

*Verified working* (`test_c2_recorded_case_law_citation_is_removed`): on the verbatim W1-03 t3,
`(2020) 6 SCC 123`, `M. S. R. Enterprises`, `v. State of Karnataka` and the orphaned
`**Supreme Court case you can quote**` heading are all gone, the disclosure lands, and the result
is idempotent. The needle derivation genuinely does catch the differently-spaced
`*M.S.R. Enterprises*` later in the reply. DoD #3 is met **for that string**.

*The rule is not unconditional, which is what DoD #3 actually claims.* `CASE_NAME_RE` is compiled
**without `re.IGNORECASE`** and requires a period on `vs`. Every one of these passes with **zero
redactions and no disclosure**:

| Fabricated citation | Guard result |
|---|---|
| `Tata Motors Vs. Antonio Paulo Vaz` | untouched |
| `Tata Motors VS. Antonio Paulo Vaz` | untouched |
| `Lata Wadhwa vs State of Bihar` | untouched |
| `Hindustan Unilever v/s Ashok Kumar` | untouched |
| `Indian Oil Corporation V. Consumer Protection Council` | untouched |
| `2021 SCC OnLine SC 456` | untouched |
| `Civil Appeal No. 1234 of 2020` | untouched |

`Vs.` is the commonest separator in Indian consumer-forum and High Court citation, and `SCC
OnLine` neutral citations are now the dominant modern form. Reproducers:
`test_case_law_rule_is_unconditional_across_separator_spellings` (5 params),
`test_case_law_rule_covers_other_indian_citation_styles` (2 params).

*The unnamed-authority class is worse than Agent 2 recorded.* Agent 2 logged the surviving
W1-03 t3 sentence *"...and a Supreme Court decision that interprets it."* as residue softened by
"the appended case-law disclosure directly contradicts it". That mitigation is **an accident of
that reply**: the disclosure fired only because a *named* case was also present. Strip the named
case and nothing fires at all. Verified — each of these produces zero redactions and **no
disclosure**:

- "A High Court ruling supports exactly this."
- "Settled case law says the tenant wins here."
- "The Supreme Court has held that a deposit must be returned within one month."
- "There is a 2019 judgment directly on this point."

Reproducer: `test_unnamed_authority_forward_reference_at_least_gets_the_disclosure` (4 params),
plus `test_w1_03_t3_forward_reference_sentence_does_not_survive` so Agent 2's own recorded residue
cannot be closed silently.

**My judgement on the W1-03 t3 residue specifically:** the appended disclosure does *not*
neutralise it, it converts it into a visible self-contradiction. The guarded reply opens by
promising a Supreme Court decision, delivers none, and closes with "I do not have a verified
case-law database". A user reading that reasonably concludes the bot had a judgment and lost it,
and may go looking for it. Agent 2 is right that they are not misled into citing a fake case; they
are left with an incoherent answer. Worth fixing, but it is second in priority behind the `Vs.`
hole, which ships a usable fake citation with no warning at all.

**Severity: HIGH — a fabrication reaching the user.**

---

# 3. C2(a) — fabricated statute in a corpus-less domain

**Problem.** W1-03 t3 invented `Section 9(1)` of the Code on Wages with fabricated verbatim text,
in EMPLOYMENT, whose `rag.corpus_ids` is `()`.

**Fix Agent 2 made.** `_apply_statute` + `build_allowlist`. Allowlist = this turn's retrieved
provisions ∪ the whole domain corpus ∪ the whole curated `DOCUMENT_CITATIONS` map ∪ the RTI corpus,
plus Act names from `official_sources`. Match on section number alone; a cited sub-clause of an
allowlisted section is verified; Act names used only to reject; no corpus for the domain → every
section number unverifiable; undecidable → caveat and keep.

**Status: partially fixed, and it fails in *both* directions.**

*Verified working* (`test_c2_recorded_fabricated_section_is_removed_with_its_quoted_text`):
`Section 9(1)` gone (including the U+202F-spaced form), the fabricated quotation line gone, the
useful wage-period prose kept, the Ministry of Labour & Employment named without a URL, and
`"_" not in reply_text` still holds. DoD #4 met on that string. The sub-clause invariant also
holds in both directions exactly as Agent 2 claimed — `69(2)` verified against corpus `69`, and a
bare `Section 2` is **not** allowlisted by corpus `2(11)` (it is caveated, not deleted).
Locked by `test_bare_parent_section_is_not_allowlisted_by_a_corpus_sub_clause`.

### 3a. Under-filtering — a fabrication passes a corpus-less domain (HIGH)

`_apply_statute` consults `allow.is_verified_section(nums)` **before** `_classify` ever sees
`corpus_available`, and the curated map plus the RTI corpus are allowlisted **globally by section
number alone**. So in EMPLOYMENT / CYBER_FRAUD / POLICE_COMPLAINT / GENERAL, where the corpus is
empty, this set of section numbers is silently "verified":

```
173  19(1)  2(11)  2(47)  20  35  6(1)  66D  7(1)  73
```

Proven, zero redactions, no caveat, no disclosure:

- `"Under Section 35 of the Industrial Disputes Act, 1947 you can recover the wages."` → untouched
- `"Under Section 73 of the Payment of Wages Act, 1936 you may claim interest."` → untouched
- `"Section 20 of the Police Act, 1861 covers refusal to register an FIR."` → untouched
- `"Section 2(11) of the Code on Wages, 2019 defines your employer."` → untouched

Agent 2's own statement of the rule — *"no corpus for the domain → every section number in it is
unverifiable"* — is therefore false as implemented. 20, 35, 73 and 173 are among the most common
section numbers in Indian statute, so this is not a narrow escape; it is most of the plausible
fabrication space in the three domains the whole finding was about. DoD #4 passes only because
`9(1)` happens not to collide. Reproducer:
`test_no_section_number_is_verified_in_a_corpusless_domain` (4 params × 3 domains).

### 3b. Over-filtering — naming the correct Act makes deletion *more* likely (HIGH)

In a domain **with** a corpus, `_classify` uses a named Act only to *reject*. The consequence is
perverse: the more precisely a reply attributes a real provision, the likelier it is deleted.

| Reply | Result |
|---|---|
| `Section 13 of the applicable rent law` | **kept** + caveat |
| `Section 13 of the Rajasthan Rent Control Act, 1950` | **deleted** → "the applicable provision of the Rajasthan Rent Control Act, 1950" |
| `Section 43 of the Real Estate (Regulation and Development) Act, 2016` | **deleted** |

The Rajasthan case is the sharp one: for a Jaipur tenant that Act *is* the binding law, and the
guard deletes its section number and then appends advice to "check the equivalent provision of the
tenancy or rent-control law that actually applies there" — i.e. it deletes the answer to the
question its own H4 caveat poses. The user cannot detect the loss. Reproducer:
`test_naming_the_real_applicable_act_does_not_cause_deletion` (2 params).

### 3c. The statute note states a falsehood on the caveat-only path

`_statute_note` is shared between the "stripped" and "caveated" branches, so a reply where nothing
was removed still ends with *"I have removed the section numbers I could not confirm."* Verified on
`Section 13 of the applicable rent law`, whose only redaction is
`statute_unconfirmed_caveated`. Reproducer:
`test_statute_note_does_not_claim_a_removal_that_never_happened`.

**A note on the accepted design cost, for Agent 4.** Even working as intended, this rule now
deletes *all* statutory specificity from the three corpus-less domains — a correct `Section 66C of
the IT Act`, `Section 33C of the Industrial Disputes Act` or `BNSS Section 175` is stripped. The
plan authorised that, and the appended disclosure tells the user it happened, so I am not logging
it as a defect. But it is the cycle's largest product-value cost and it is worth stating out loud:
the fix for three domains with no corpus is to stop citing law in them.

---

# 4. C4 — invented numeric win probabilities

**Problem.** W1-06 t4 emitted `**Rough chance of a favourable outcome:** **≈ 30 %**` twice and
`~30 % chance` / `~40 % chance` inline. Nothing in the product computes a success rate.

**Fix Agent 2 made.** `_apply_outcome_probability`: Tier A removes a whole line that is only a
label-plus-percentage where the label carries an outcome word; Tier B removes the quantity inline
when an outcome word is within 70 characters; one script-matched paragraph appended once.

**Status: partially fixed — fixed for the evidence, over-filters badly in ordinary prose.**

*Verified working* (`test_c4_recorded_percentages_are_all_removed`): on the verbatim W1-06 t4 with
the real U+2248/U+202F bytes, all four occurrences are gone, **no `%` remains anywhere**, both
factor tables survive, `Which one to chase first?` survives, `₹⁠62,000` / `₹⁠90,000` / `₹⁠50⁠lakh`
survive with their U+202F spacing, and it is idempotent. DoD #6 met. The unicode handling via
character classes rather than literals is genuinely correct.

*The ±70-character window is far too wide.* DoD #6's five preservation strings pass **only in the
isolated form the DoD states them** (locked by
`test_c4_isolated_legitimate_percentages_pass_byte_identical`, which passes). In realistic legal
prose the outcome word shares the sentence with the percentage, because that is how the sentence is
naturally written — and then the percentage is deleted:

| Reply | Guarded result |
|---|---|
| `A favourable order can include 18% per annum interest on the refund.` | `...can include per annum interest on the refund.` |
| `Your chances improve a lot if you demand a 100% refund in the notice.` | `...if you demand a refund in the notice.` |
| `If you win, the 12% GST charged on the cancelled booking is also refundable.` | `...the GST charged...is also refundable.` |
| `The likelihood of recovery rises when the contract provides 2% per month interest.` | `...provides per month interest.` |
| `संभावना है कि 18% ब्याज मिलेगा।` | `संभावना है कि ब्याज मिलेगा।` |

Three of these are left **grammatically broken** (`include per annum interest`, `provides per month
interest`), all five lose a legally operative quantum, and each then collects a caveat saying the
bot does not calculate a probability — on a reply that never gave one. The Devanagari case is the
worst: संभावना is simply the ordinary Hindi word used before a rate, so in Hindi the rule fires on
normal phrasing.

This is also the one rule that violates the guard module's own stated priority #1, *"Caveat on
doubt, never delete"* — `_apply_outcome_probability` deletes with no doubt check at all.

Reproducers: `test_legitimate_percentage_survives_an_outcome_word_in_the_same_sentence` (4 params),
`test_devanagari_interest_rate_survives_next_to_sambhavna`.

**Severity: HIGH — correct legal content silently deleted.** This is the class the user cannot
detect, and it fires on ordinary sentences, not contrived ones.

---

# 5. H4 — Model Tenancy Act presented as operative Rajasthan law

**Problem.** W1-02 t1 told a Jaipur tenant to cite "Model Tenancy Act ke Section 11" in a notice;
t2 asserted s.30 governs the forum. Both sections are real corpus content; the defect is that
Rajasthan has not enacted the MTA 2021 and the corpus's own
`document_type: "Model law - State adoption must be verified"` never reached the reply.

**Fix Agent 2 made.** `model_law_adoption.py` (empty register) + `_apply_model_law` + a
script-matched adoption caveat, triggered off corpus `document_type`, never by deletion.

**Status: partially fixed.**

*Verified working.* I read `app/services/model_law_adoption.py` in full:
`MODEL_TENANCY_ADOPTION = {}`, and `adoption_status()` returns `UNVERIFIED` for Rajasthan, UP,
Assam, Tamil Nadu, Andhra Pradesh, Delhi, `""`, `None` and a whitespace-padded name. **There is no
unsourced adoption claim in the file** — DoD #7's "Agent 3 reads it and confirms" is satisfied, and
the docstring's curation bar (State Act name, number/year, gazette reference) is the right one.
Locked by `test_adoption_register_contains_no_unsourced_claim`.

On the verbatim W1-02 t1/t2 with `user_state="Rajasthan"`: `Section 11` and `Section 30` present,
Hinglish adoption caveat present, `"Rajasthan State Rent Authority ke website"` gone,
`(opposite_party_name)` gone, `₹85,000` intact, idempotent. The **Jaipur-vs-Bengaluru jurisdiction
passage passes byte-for-byte** — I confirmed by unified diff that t2 changes exactly two lines (the
website parenthetical and the fact-key parenthetical) and by slicing the passage out of the fixture
and asserting containment, including the postal/online follow-up paragraph. DoD #7 met.
Locked by `test_w1_02_t2_jurisdiction_passage_survives_byte_for_byte` and
`test_h4_recorded_replies_keep_their_sections_and_gain_the_caveat`. All four tenancy corpus
provisions (11/15/21/30) survive in all three scripts with `model_law_adoption_caveat` as the only
rule — locked by `test_tenancy_corpus_provisions_survive_with_a_caveat_never_a_deletion`.

*Open: the model-law trigger is the bare section NUMBER.* `is_model_law_section` tests only the
number, with no check that the reply referred to the model law at all. So:

```
in:  "Aapko notice mein Rajasthan Rent Control Act ke Section 11 ka hawala dena chahiye."
out: "Us provision ko sirf ek starting point maanein, aur notice mein uska hawala dene se
      pehle wahan actually laagu hone wale State law ka equivalent provision confirm kar lein."
     + "Model Tenancy Act, 2021 ke baare mein: ... Mere paas iski koi pushti nahi hai ki yeh
        Rajasthan mein laagu hai ..."
```

Correct advice about the State's own Act is replaced by advice to "confirm the equivalent provision
of the State law that actually applies" — circular, because the reply already named it — and an
MTA-2021 caveat is appended for an Act the reply never mentioned. Reproducer:
`test_citing_the_state_act_does_not_trigger_the_model_law_rewrite`.

*Open: the constructed-authority rule deletes procedural advice.* §3 lock 11 says only a
contact/website claim is touched, *"never the institution's name and never the procedural advice"*.
When no parenthetical is available to swap, `_apply_model_law` falls back to deleting the whole
sentence with no replacement:

```
in:  "Section 11 governs the deposit. Confirm the exact address of the Jaipur Rent Authority
      on its official website before filing. Then attach your rent agreement."
out: "Section 11 governs the deposit. Then attach your rent agreement."
```

The instruction to verify the forum vanishes, silently, and nothing in the appended caveat mentions
it. Worse, whether it vanishes depends on an unrelated section number elsewhere in the reply —
remove `Section 11` from the first sentence and the same advice survives untouched. Reproducer:
`test_procedural_advice_survives_the_constructed_authority_rule`.

Agent 2's narrowing of the rule to *online-location* claims only (dropping `address` /
`contact details`) was the right call and I am not disputing it — it is what keeps W1-02 t1's
`(Rajasthan ke liye "Jaipur Rent Authority" ya similar)` hedge alive. The defect is the
delete-the-sentence fallback, not the trigger.

**Severity: MEDIUM-HIGH — correct advice silently deleted.**

---

# 6. M7 — invented helpline numbers

**Problem.** W1-04 t2 invented `1800‑11‑001‑112` (U+2011) as an RBI helpline and t3 invented
`1800‑11‑222‑222` as SBI's, alongside an off-allowlist `rbi.org.in/Scripts/Complaints.aspx` URL.
There was no registry for any reply to be checked against.

**Fix Agent 2 made.** `app/services/helplines.py` (every entry sourced from a number the repo
already emits, crisis lines *imported* from `safety_triage` so they cannot drift) +
`_apply_helplines`: toll-free shape not in the registry → removed; 3–5 digit short code adjacent to
a helpline-specific word → removed; exclusions for amounts, years, and every digit run belonging to
the user; URL allowlist by exact normalised URL; one script-matched sentence appended; never
injects a number.

**Status: partially fixed.**

*Verified working* (`test_m7_recorded_numbers_are_removed_and_the_real_ones_kept`,
`test_m7_never_touches_the_users_own_digits`): both U+2011 fake numbers gone, `1930` kept,
`24‑hour helpline` kept, `https://www.cybercrime.gov.in/` kept, the rbi.org.in/Scripts URL gone,
`₹47,500` kept, note appended once, idempotent. A reply carrying `9876500011` (= `user_phone`),
`₹85,000`, `Rs 1,45,000` and `order 18499` passes **byte-identical**. The lookarounds on
`_SHORTCODE_RE` do correctly stop `1800-11-001-112` being read as three short codes. DoD #8 and #9
met. The registry's sourcing discipline is genuinely good, and omitting 1915 because it appears
nowhere in the product was the right instinct.

*The filter covers a narrow slice of the shape space.* Agent 2 documented two false negatives (the
`19xx`/`20xx` carve-out and 6+ digit short codes). I found the class is wider — all of these pass
with zero redactions:

| Fabricated number | Why it survives |
|---|---|
| `1860-500-1234` | `_TOLLFREE_RE` is anchored on `1800`; dashes defeat `_SHORTCODE_RE`'s lookarounds |
| `9876543210` ("the SBI fraud helpline") | no rule matches a 10-digit run |
| `022-2260 3000` | no rule matches an STD landline |
| `1966` next to "consumer helpline" | the `19xx` year carve-out |
| `155260` | 6 digits, unmatched |
| `ombudsman.mumbai@rbi.org.in` | the URL rule is http(s)-only; emails are unchecked |

`1860-xxx-xxxx` is the single most common Indian **bank** helpline format — HDFC, ICICI, Axis all
publish one — and M7 was found in exactly that context ("SBI ki 24-hour helpline"). A user who
dials an invented bank fraud helpline may be dialling the fraudster, so I rate this above a
mis-cited section number. Note the registry itself already holds a 10-digit number (AASRA
9820466726), so the shape is expected by the product but unfiltered.

On the `19xx` carve-out specifically, as asked: it is **bounded but wide enough to matter**. It
admits ~200 four-digit numbers, and I confirmed `1966` sits next to the word "helpline"
unchallenged. It is not a gateway to the other shapes (each of those fails for its own independent
reason), and Agent 2's reasoning for it — years are everywhere in legal prose — is sound. It should
be narrowed by requiring a helpline word *and* no nearby Act/year context rather than removed.

Reproducers: `test_fabricated_helpline_in_a_non_1800_shape_is_removed` (3 params),
`test_fabricated_official_email_address_is_removed`.

**Severity: HIGH — a fabrication reaching the user, with phishing-grade consequences.**

---

# 7. L1 — 1930 attributed to RBI

**Problem.** W1-04 t1/t2 and W1-08 t1 all attributed the real `1930` line to RBI. The codebase
never says RBI for any number; a "is this number real?" check alone would have passed L1.

**Fix Agent 2 made.** `_fix_operator_attribution`, riding the registry's `operator` field, run
*before* the number filter; matches only a registry number adjacent to a name in the closed
`ATTRIBUTABLE_AUTHORITIES` set that contradicts that number's operator/aliases, with a helpline
word in the window; both orders handled.

**Status: fixed.** All three recorded forms verified — `1930 helpline (RBI)`, `RBI 1930 helpline`,
`RBI‑designated 1930 helpline` → RBI gone, `1930` retained, rule logged. And W1-08 t1's
*"The Reserve Bank of India's 'Customer protection for unauthorised electronic transactions'
(Notification 2017)"* sentence passes byte-identical, so RBI stays namable as H9 (set S7) requires.
Locked by `test_l1_all_three_recorded_forms_lose_rbi_and_keep_1930`.

*One cosmetic note, not a finding.* On W1-04 t3, `_tidy_edited_line` ate the space before a bold
run on the line where the URL was removed: `RBI ke **"Banking Ombudsman"**` became
`RBI ke**"Banking Ombudsman"**`. Visible but harmless, and confined to lines the guard edited.

---

# 8. M8 — the no-corpus limitation was never disclosed

**Fix Agent 2 made.** `kind` tags on every `legal_sources` entry, `_legal_sources_status` with a
hard `case_law_available: False`, `profile.key_facts["verified_sources_available"]`, and a
**conditional** user-facing disclosure that names the authority and never pastes a URL.

**Status: fixed** (backend). Agent 2's correction to the record is right and important: every turn
in `transcripts.json` records `"sources": null` **and** `"mode": null`, including turns the report
itself calls `mode=limited_demo`, and the API model has neither field. **Wave 2 must read
`llm_mode` and `case_profile.legal_sources`** or it will re-report M8 as unfixed. Naming the
Ministry instead of pasting the URL is the correct resolution of the `test_api.py:170`
`"_" not in reply_text` constraint, and I confirmed that assertion still holds on the guarded
W1-03 t3. Frontend copy (`CaseWorkspacePanel.tsx:124`) not changed, as declared.

---

# 9. Coverage, idempotence, observability

I verified each claim independently rather than re-running Agent 2's tests.

**The funnel is real.** `_tag_response` (`llm_conversation.py:1884-1911`) is reached by every
`ChatTurnResponse` return path — I walked all of them (`:205, 215, 266, 385, 389, 483, 497, 511,
546, 600, 611, 654, 658, 816` plus `main.py:1101, 1203`). `:483` and `:564` return responses that
`_rate_limit_fallback` already tagged at `:600`. No untagged path exists.

**`limited_demo` / 429 — the wiring is right, but it is not what protects those paths.** I read
both branches: `llm_conversation.py:226-265` (provider unconfigured) and `:567-600` (429 fallback)
compose their reply text **entirely from deterministic composers** — `_limited_demo_prefix`,
`_temporary_failure_prefix`, `_localized_fallback_reply`, `_safe_next_prompt`,
`_fallback_professional_help`, `_emergency_reminder`, the document-gate copy. No model prose ever
reaches the user on either path, so a hallucination is impossible there by construction and the
guard is necessarily a no-op. Agent 2's claim is literally true and the no-op proof is the right
test for it, but Agent 4 should not read "the guard runs on the 429 path" as "the 429 path was
vulnerable and is now defended". The paths that actually carry model text are
`_finish_chat_response` (`:546`, with `user_message`), `process_document_upload` (`:654`) and
`generate_case_summary` — all three guarded.

**`generate_case_summary` caches guarded text — confirmed.** `case_summary.py:118` returns
`guard_summary(brief, profile).text`, and `main.py:981` writes that returned value into
`profile.ai_summary_cache`. There is no path by which the unguarded brief is persisted. DoD #12
met.

**Generated documents need no guard.** I checked: `doc_generator.py` and `document_generation.py`
make **no provider call at all** — document bodies are fully deterministic, so no fabricated
citation can reach a document the user sends to a landlord. The curated-map probe the orchestrator
asked for comes out clean in the other direction too: IT Act s.66D, BNSS s.173, ICA s.73, CPA s.35
and RTI s.6(1) all survive being cited in chat, so the bot does **not** generate a notice citing
s.66D and then strip it from the message explaining that notice. Locked by
`test_document_citation_map_provisions_survive_in_their_own_domain`.

**Idempotence holds.** I re-derived it independently across all 8 fixture replies × 4
profile/script combinations (32 cases) — `guard(guard(x)) == guard(x)` every time, no doubled
caveat. Locked by `test_guard_is_idempotent_on_every_recorded_reply`.

**Observability is PII-safe.** `Redaction` carries only a rule name and a controlled detail
(section token, `toll_free`, `whole_line`, State name); the WARNING log emits case id, category,
mode and rule names only. Nothing I could construct got reply text, a party name or an amount into
a redaction detail.

### Open: one path drops the guard's edit from the stored transcript (MEDIUM)

`main.py:1203-1205`, `review_evidence_findings`:

```python
reply_text, quick_replies, pending = build_review_turn(evidence)
response = ChatTurnResponse(reply_text=reply_text, ...)
response = gemini_conversation_service._tag_response(response, ..._provider_mode)
messages.append(ChatMessage(id=response.message_id, sender="bot", text=reply_text, ...))
                                                          # ^^^^^^^^^^ stale pre-guard local
```

`_tag_response` rewrites `response.reply_text`, but the persisted `ChatMessage` is built from the
original `reply_text` local. The user sees the guarded reply in the API response and the
**unguarded** one when the case is reloaded — on the one path that carries evidence-extraction
model output. The sibling path at `main.py:1096-1101` does this correctly (`text=response.reply_text`),
which is what makes this a slip rather than a design choice. Reproducer:
`test_evidence_review_endpoint_persists_the_guarded_reply` (a structural lock on the source, in the
same spirit as Agent 2's `model_dump` lock).

---

# 10. Preservation locks (§3) — pass/fail

| Lock | Result |
|---|---|
| 1. CONSUMER 2(11), 2(47), 2(34), 34 & 35, 35, 47, 58, 69, 69(2) | **pass**, all phrasings, 3 scripts, both CPA spellings |
| 2. TENANCY 11, 15, 21, 30 survive with a caveat | **pass**, 3 scripts |
| 3. Document-map citations (ICA 73, BNSS 173, IT 66D, RTI 6(1), CPA 35) | **pass** |
| 4. Section with no Act named, number in allowlist | **pass** |
| 5. W1-02 t2 jurisdiction reasoning verbatim | **pass**, byte-for-byte by diff |
| 6. W1-04 cyber sequencing (1930, cybercrime.gov.in, one question) | **pass** |
| 7. Devanagari crisis path + all `safety_triage` copy | **pass** (Agent 2's suite; re-confirmed no crisis number is ever injected) |
| 8. `₹85,000`, `Rs 1,45,000`, `₹62,000` | **pass** |
| 9. Hinglish/Devanagari register on inserted text | **pass** |
| 10. `18%`, `12% GST`, `100% refund`, `2% per month`, `50% deposit` | **pass isolated, FAIL in prose** — see C4 |
| 11. Real institutions; only contact/website claims touched, never procedural advice | **FAIL** — see H4-2 |
| 12. User's own digits, order numbers, `user_phone` | **pass** |
| 13. Digits from the user's own uploaded evidence | **pass** (`_protected_digits` reads `provided_documents`) |
| 14. No-op over every deterministic composer | **pass** |
| 15. `"_" not in reply_text` on EMPLOYMENT | **pass** |

Two locks broken: 10 (in realistic prose) and 11.

---

# 11. New reproducers, by name

All in `backend/tests/test_cycle4_adversarial.py`. 33 are `xfail(strict=True)`, so each turns the
scoreboard **red** when fixed and cannot be closed silently.

**Strict-xfail gap reproducers**
- `test_no_raw_profile_field_name_reaches_the_provider_payload` — D1-a
- `test_document_field_name_parenthetical_is_scrubbed_from_the_reply` — D1-b
- `test_case_law_rule_is_unconditional_across_separator_spellings` (5 params) — C2-c1
- `test_case_law_rule_covers_other_indian_citation_styles` (2 params) — C2-c2
- `test_unnamed_authority_forward_reference_at_least_gets_the_disclosure` (4 params) — C2-c3
- `test_w1_03_t3_forward_reference_sentence_does_not_survive` — C2-c4 (Agent 2's own residue)
- `test_no_section_number_is_verified_in_a_corpusless_domain` (4 params) — C2-a1
- `test_naming_the_real_applicable_act_does_not_cause_deletion` (2 params) — C2-a2
- `test_statute_note_does_not_claim_a_removal_that_never_happened` — C2-a3
- `test_legitimate_percentage_survives_an_outcome_word_in_the_same_sentence` (4 params) — C4-1
- `test_devanagari_interest_rate_survives_next_to_sambhavna` — C4-2
- `test_citing_the_state_act_does_not_trigger_the_model_law_rewrite` — H4-1
- `test_procedural_advice_survives_the_constructed_authority_rule` — H4-2
- `test_fabricated_helpline_in_a_non_1800_shape_is_removed` (3 params) — M7-1
- `test_fabricated_official_email_address_is_removed` — M7-2
- `test_evidence_review_endpoint_persists_the_guarded_reply` — COV-1

**Passing locks added (31)**
- `test_every_recorded_reply_is_byte_identical_to_the_transcript_source` (8 params)
- `test_guard_is_idempotent_on_every_recorded_reply` (8 params × 4 profiles)
- `test_w1_02_t2_jurisdiction_passage_survives_byte_for_byte`
- `test_adoption_register_contains_no_unsourced_claim`
- `test_document_citation_map_provisions_survive_in_their_own_domain`
- `test_consumer_corpus_provisions_survive_every_plausible_phrasing`
- `test_tenancy_corpus_provisions_survive_with_a_caveat_never_a_deletion`
- `test_bare_parent_section_is_not_allowlisted_by_a_corpus_sub_clause`
- `test_d1_recorded_vector_is_closed_and_the_backend_copy_survives`
- `test_c2_recorded_case_law_citation_is_removed`
- `test_c2_recorded_fabricated_section_is_removed_with_its_quoted_text`
- `test_c4_recorded_percentages_are_all_removed`
- `test_c4_isolated_legitimate_percentages_pass_byte_identical`
- `test_h4_recorded_replies_keep_their_sections_and_gain_the_caveat`
- `test_m7_recorded_numbers_are_removed_and_the_real_ones_kept`
- `test_m7_never_touches_the_users_own_digits`
- `test_l1_all_three_recorded_forms_lose_rbi_and_keep_1930`

---

# 12. Recommendation to Agent 4 — priority order

1. **C2-a1** (fabricated section passes a corpus-less domain via a map-number collision). One-line
   ordering change: test `corpus_available` before `is_verified_section`, or scope the
   `DOCUMENT_CITATIONS`/RTI tier by Act name as well as number. Highest ratio of harm removed to
   risk taken.
2. **C4-1/C4-2** (legitimate rates deleted). Require the outcome word and the percentage in the
   same *clause*, or require the percentage to be the sentence's predicate — and on doubt caveat
   rather than delete, per the module's own rule 1. Currently breaks grammar in English and fires
   on normal Hindi.
3. **C2-c1** (`Vs.`, `vs`, `v/s`, `V.`). Add `re.IGNORECASE` and make the period optional. Near-zero
   risk; it is the single cheapest fix in the set.
4. **M7-1** (1860 / 10-digit / STD shapes). Extend the shape set, keeping `_protected_digits` as the
   guard against deleting the user's or their evidence's numbers.
5. **C2-a2 / H4-1 / H4-2** (over-filtering on correctly-attributed State law, and the
   delete-the-sentence fallback). These three share a root cause: a rule keyed on a number or a
   name instead of on what the reply actually asserts.
6. **COV-1** (`main.py:1205` persists the stale local). Two-word fix.
7. **D1-a/D1-b**, **C2-a3**, **C2-c3/c4**, **M7-2** — real, lower value.

Not worth a cycle on their own: the `_tidy_edited_line` space-eating cosmetic, and the accepted
design cost that corpus-less domains now cite no statute at all (worth a product decision, not a
bug fix).
