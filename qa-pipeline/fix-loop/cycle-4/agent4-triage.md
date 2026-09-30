# Cycle 4 — Triage for Agent 1 (set S4)

**Note on authorship.** Agent 4 was killed by the stream watchdog with no progress (it produced only
"I'll start by reading the required documents"). The orchestrator wrote this triage. Every severity
call below is either my own verification against source, or an Agent 3 finding whose reproducer I
read. Where I could not confirm something, it says so.

## Verdict per S4 item

| Item | Verdict |
|---|---|
| **D1** prompt key leak | **partially closed** — payload redaction works, two sibling leaks remain |
| **C2** fabricated citations | **partially closed** — under-filters *and* over-filters, both HIGH |
| **C4** invented percentages | **partially closed** — recorded evidence clean, deletes correct rates in realistic prose |
| **H4** Model Tenancy Act | **partially closed** — no unsourced adoption claim, but deletes sound procedural advice |
| **M7** invented helplines | **partially closed** — recorded evidence clean, common bank-helpline shape passes |
| **L1** 1930 to RBI | **closed** |
| **M8** no-corpus undisclosed | **closed** (backend) |
| Coverage | **partially closed** — one real persistence bug |

Independently measured: `1 failed, 752 passed, 34 xfailed`. Baseline was 458 passed. Zero tests lost
across the cycle. The single failure is the pre-existing `test_document_generation_notice`.

**Everything Agent 2 claimed about the eight recorded wave-1 replies is true.** Agent 3 reproduced
all of it with unified diffs, and verified the fixture is byte-faithful against `transcripts.json`.
Every DoD criterion holds *on the recorded evidence*. All findings below live in the space
immediately around that evidence.

---

## Considerable issues — forwarding 12 of 33

### Class A — a hallucination reaches the user

**A1. No section number is actually verified in a corpus-less domain. (HIGH — worst in the set.)**
*Verified by the orchestrator in source.* `verified_sections` is built **unconditionally** from the
curated `DOCUMENT_CITATIONS` map and the RTI corpus — builder step (d)'s own comment says "no domain
binds it" — while `corpus_available` is consulted only inside `_classify`. But
`response_guard.py:681` (`if allow.is_verified_section(nums): continue`) short-circuits **before**
`_classify` is ever reached. So in EMPLOYMENT / CYBER_FRAUD / POLICE_COMPLAINT / GENERAL,
`corpus_available` is False and yet the numbers 73, 173, 66D, 35, 6(1), 2(11), 2(47) are accepted by
**number collision alone, with no Act-name check on the accept path**.
"Section 73 of the Payment of Wages Act, 1936" passes untouched.
Agent 2's stated rule — "no corpus, so every section number is unverifiable" — is false as built.
DoD #4 passes only because 9(1) happens not to collide with a map number.
Reproducer: `test_no_section_number_is_verified_in_a_corpusless_domain`.

> **Structural tension Agent 1 must resolve, not paper over.** The plan mandated "require an
> Act-name match only to *reject*, never to accept." That rule is correct for preventing
> over-filtering, and it is **precisely** what creates this hole, because the accept path then
> checks no Act name at all. Reordering two lines does not settle it. The real question is what
> *positively* licenses a citation in a domain the corpus does not cover.

**A2. The case-law rule misses the commonest Indian spellings. (HIGH, cheapest fix in the set.)**
*Verified by the orchestrator.* `CASE_NAME_RE` (`response_guard.py:531`) is compiled with **no
flags**, while `REPORTER_RE` four lines below it carries `re.IGNORECASE`; the separator alternation
also requires a literal period. So `Vs.` — the commonest Indian form — plus `VS.`, `V.`, bare `vs`,
`v/s`, "2021 SCC OnLine SC 456" and "Civil Appeal No. 1234 of 2020" all pass with **zero redactions
and no disclosure**. The inconsistency with its immediate neighbour marks this as an oversight
rather than a design choice.
Reproducers: `test_case_law_rule_is_unconditional_across_separator_spellings`,
`test_case_law_rule_covers_other_indian_citation_styles`.

**A3. Fabricated helplines in non-toll-free shapes survive. (HIGH.)**
`1860-500-1234` — the commonest Indian **bank** helpline format, and M7 was originally found in
exactly that context — plus a 10-digit `9876543210` labelled "SBI fraud helpline", `022-2260 3000`,
and a fabricated `ombudsman.mumbai@rbi.org.in` (the URL rule is http/https-only).
**I agree with Agent 3 that this outranks a mis-cited section.** An invented bank fraud number is
phishing-grade: a user who calls it can lose money directly, and the product's authority is what
persuaded them to call.
Reproducers: `test_fabricated_helpline_in_a_non_1800_shape_is_removed`,
`test_fabricated_official_email_address_is_removed`.

### Class B — correct law is silently deleted

This class is worse than it looks, because the user cannot detect the loss.

**B1. Correct rates are deleted wherever rates naturally live. (HIGH.)**
*Reproduced by the orchestrator directly against the guard:*

```
IN : A favourable order can include 18% per annum interest on the refund.
OUT: A favourable order can include per annum interest on the refund.
     + "...the strength of a case cannot be reduced to a number, and I do not calculate one."
```

Double fault: the statutory rate is deleted, **and** a probability disclaimer is appended to a reply
that never mentioned probability. Same for "If you win, the 12% GST charged" becoming "the GST
charged". Breaks the guard module's own rule 1 ("caveat on doubt, never delete") and preservation
lock 10. My controls passed correctly: "The District Commission awards interest at 9% per annum"
(no outcome word in the window) is untouched.
**Correction to Agent 3:** its "demand a 100% refund" to "demand a refund" example does **not**
reproduce in isolation — that string passes through unchanged. The mechanism is confirmed by the
other two; do not chase that example.
Reproducers: `test_legitimate_percentage_survives_an_outcome_word_in_the_same_sentence`,
`test_devanagari_interest_rate_survives_next_to_sambhavna`.

**B2. Naming the real Act makes deletion *more* likely. (HIGH — perverse incentive.)**
"Section 13 of the applicable rent law" is kept and caveated; "Section 13 of the Rajasthan Rent
Control Act, 1950" is **deleted** — and the guard then advises the user to check the very Act whose
section it just removed. The rule punishes precision and rewards vagueness, which is backwards for a
legal product.
Reproducer: `test_naming_the_real_applicable_act_does_not_cause_deletion`.

**B3. The constructed-authority rule deletes sound procedural advice. (HIGH.)**
"Confirm the exact address of the Jaipur Rent Authority on its official website before filing."
vanishes outright — correct, useful advice. Worse, *whether* it vanishes depends on an unrelated
section number elsewhere in the same reply, so the behaviour is not locally predictable. Breaks
preservation lock 11.
Reproducer: `test_procedural_advice_survives_the_constructed_authority_rule`.

**B4. Citing the State Act triggers the model-law rewrite for an Act never mentioned. (MED-HIGH.)**
The model-law trigger is the bare section *number*, so a reply correctly citing "Rajasthan Rent
Control Act ke Section 11" has its advice replaced by circular "confirm the equivalent State
provision" text plus an MTA-2021 caveat for a law the reply never named.
Reproducer: `test_citing_the_state_act_does_not_trigger_the_model_law_rewrite`.

> **B2, B3, B4 and A1 share one root cause, and so does B1.** Every one of these rules keys on
> **the presence of a token** — a number, an Act name, an outcome word within a character window —
> rather than on **what the sentence actually asserts**. This is the cycles 2-3 lesson recurring a
> third time. Cycle 2 learned not to enumerate an open class; cycle 3 learned to read the right
> computation; cycle 5's lesson is that a guard must key to the *claim*, not to the *vocabulary*.
> If that generalisation holds, S5's middle block is **one conceptual fix, not four patches.**

### Class C — self-contradiction shipped to the user

**C1. The unnamed-authority class fires nothing at all. (MED.)**
"A High Court ruling", "settled case law", "The Supreme Court has held" trigger no rule. Agent 2
understated its own residue: W1-03 t3's disclosure appeared **only** because a *named* case was also
present. Agent 3's judgement, which I endorse: the disclosure does not neutralise the forward
reference, it converts it into a **visible self-contradiction** — promise a judgment, deliver none,
then state there is no case-law database.
Reproducers: `test_unnamed_authority_forward_reference_at_least_gets_the_disclosure`,
`test_w1_03_t3_forward_reference_sentence_does_not_survive`.

**C2. The statute note claims a removal that never happened. (MED, cheap.)**
On the caveat-only path the note still says "I have removed the section numbers I could not confirm"
when nothing was removed. The bot misreporting its own action is a credibility harm out of all
proportion to the fix cost.
Reproducer: `test_statute_note_does_not_claim_a_removal_that_never_happened`.

**C3. The evidence-review endpoint persists the *unguarded* reply. (MED-HIGH, two-word fix.)**
*Verified by the orchestrator.* `main.py:1204` reassigns `response` from `_tag_response(...)`, then
`main.py:1205` appends `ChatMessage(..., text=reply_text, ...)` — the **stale local**. The sibling
path at `main.py:1107` correctly uses `response.reply_text`. The user sees guarded text once and
unguarded text on reload, so **the unguarded version is what persists in the database**, which is
the durable artifact.
Reproducer: `test_evidence_review_endpoint_persists_the_guarded_reply`.

### Class D — D1 is not closed, on its third attempt

**D1-a.** `domain_context["jurisdiction"]["fact_key"]` is `"user_state"`, still shipping a raw
profile field name to **both** providers every single turn; `model_payload()` only sanitised
`next_fact_candidates`. `missing_information` likewise ships `document:complainant_name`.
**D1-b.** The reply-side scrub's closed set omits document field names, so `(complainant_name)`
passes unredacted — the identical harm to the original finding, one field over.
Reproducers: `test_no_raw_profile_field_name_reaches_the_provider_payload`,
`test_document_field_name_parenthetical_is_scrubbed_from_the_reply`.

Forwarded as considerable **because of its history, not its size**: D1 was deferred out of cycle 3
(DoD #7 unmet), taken first in cycle 4 precisely so it could not be crowded out again, and is
*still* open. It is cheap. It should not survive a third cycle.

---

## Cut list — dropped deliberately, do not re-raise

1. **`_tidy_edited_line` eats a space before a bold run** (`RBI ke**"Banking Ombudsman"**`).
   Cosmetic. Agent 3 nominated it for the cut; agreed.
2. **M7's remaining documented false negatives** — a fabricated 4-digit number in the 19xx/20xx
   range, and 6+-digit short codes. Agent 2 disclosed both rather than hiding them, and Agent 3
   confirmed the carve-out is bounded (~200 numbers) and **not** a gateway: every other shape fails
   independently. Record the bound; do not spend a cycle on it. A3 covers the shapes that matter.
3. **Corpus-less domains now cite no statute at all.** This is a **product decision, not a bug** —
   and it is the direct, intended consequence of fixing C2. It belongs to the user, not to the loop.
   Being surfaced to them separately.
4. Everything else in Agent 3's 33. The remainder are accuracy nits that change no user outcome.

**Process item, already done — do not re-litigate.** The QA harness was grading M8 against API
fields that never existed (`sources`, `mode`; the API exposes `llm_mode` and
`case_profile.legal_sources`). The orchestrator has patched `qa-pipeline/run_wave.py` to read the
real fields and to split out `verified_provisions`. Wave 2 will grade M8 correctly.

---

## Recommended S5 for Agent 1

**S5-A — independent, cheap, provably safe. Do this block first.**
A1 (gate the allowlist per domain), A2 (`re.IGNORECASE` plus optional period), A3 (extend the
helpline shape set, keeping `_protected_digits` as the net), C3 (`main.py:1205`).
Four unrelated one-to-few-line fixes, each closing a live leak, none touching the guard's decision
architecture. Highest harm removed per unit of risk.

**Start with A1.** It is the only finding where the product currently tells a user that a fabricated
statute is verified, and fixing it also forces the design question in S5-B into the open.

**S5-B — the conceptual fix. One mechanism, not four patches.**
B1, B2, B3, B4. Re-key these rules to what the sentence **asserts** rather than to which tokens
appear near each other. Treat the shared root cause as the deliverable; if Agent 1 arrives at four
separate patches, that is a signal the generalisation was not actually applied.
Binding constraint carried forward: **caveat on doubt, never delete** — the guard's own rule 1,
which B1 and B3 currently violate.

**S5-C — finish what is owed.**
D1-a and D1-b (third attempt; do not defer again), C2, C1.

**Do not touch `app/services/safety_triage.py`.** Set S1 is closed; I verified cycle 4 did not write
to it (mtime 09-29 18:13 against cycle-4 files at 09-30 12:53 onward).
