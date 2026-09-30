# Cycle 5 — Implementation report (set S5-A)

**Note on authorship.** Cycle 5's Agent 1 was killed by a Claude session limit before writing
anything, and subagents were unavailable for the rest of the window. The orchestrator planned and
implemented S5-A directly, working from `cycle-4/agent4-triage.md` (which the orchestrator wrote)
and its own source verification of each defect. Every claim below is backed by a probe or a test
run, not by intent.

## What was fixed

Four items, each one triaged as **considerable** in cycle 4 and each independent of the others.
None touches the guard's decision architecture except A1, which had to.

### A1 — a fabricated statute was reported as verified in a corpus-less domain (HIGH, worst in set)

**Problem.** `verified_sections` was built **unconditionally** from the curated
`DOCUMENT_CITATIONS` map and the RTI corpus, while `corpus_available` was consulted only inside
`_classify` — which `response_guard.py` short-circuited past on a number match
(`if allow.is_verified_section(nums): continue`). So in EMPLOYMENT / CYBER_FRAUD /
POLICE_COMPLAINT / GENERAL the numbers 73, 173, 66D, 35, 6(1), 2(11), 2(47) were accepted by
**number collision alone, with no Act-name check on the accept path.**

**The design question, answered.** Cycle 4's plan mandated "require an Act-name match only to
*reject*, never to accept" — correct for preventing over-filtering, and precisely what created the
hole. The root cause is that `verified_sections` **flattens `(act, section)` pairs to bare numbers
and throws the Act away**, so every domain's numbers collide with every other's.

So what positively licenses a citation is now one of two things, and never a bare number:

- **The reply names an Act** → the `(Act, section)` **pair** must be one the product vouches for.
  This is what stops "Section 73 of the Payment of Wages Act, 1936" riding on Indian Contract Act
  s.73.
- **The reply names no Act** → the section must be licensed **for this domain**: its own corpus,
  the documents this domain can actually generate, or the domain-agnostic RTI application. A
  corpus-less domain therefore licenses only what its own documents already print, so EMPLOYMENT
  accepts no bare statute number at all.

This only ever *accepts*. Rejection still runs through `_classify`, which prefers a caveat over
deletion, so tightening the accept path cannot delete correct law.

**Fix.** `response_guard.py`: new `verified_pairs` and `domain_sections` on `CitationAllowlist`,
populated in the builder alongside the existing flat set (which is retained — `is_verified_section`
still has callers); new `CitationAllowlist.licensed(cited, named_acts)`; accept path now calls
`licensed()` instead of `is_verified_section()`. Act-less acceptance for document citations is
scoped via `domain.documents`, so the chat message explaining a generated notice keeps its own
provisions without licensing every other domain's numbers.

**Verified.** Stripped in corpus-less domains: Payment of Wages Act s.73, Industrial Disputes Act
s.35, Police Act s.20, Code on Wages s.2(11). Survive byte-identical: IT Act s.66D in CYBER_FRAUD,
BNSS s.173 in POLICE_COMPLAINT, ICA s.73 in HOUSING_TENANT, CPA s.35 in CONSUMER, and bare
"Section 69" / "Section 21" from the corpora. Note the degradation is graceful — the section number
becomes "the applicable provision of", keeping the Act name, rather than a blunt deletion.

**Side effect worth recording:** this also closed **B4** (`test_citing_the_state_act_does_not_trigger_the_model_law_rewrite`)
without touching the model-law rule. That supports the triage's claim that A1 and the B-class share
one root cause: rules keyed on a bare number rather than on what the reply asserts.

#### A regression this fix introduced, and the second fix it needed

The first version of A1 **deleted two correct corpus provisions** and broke
`test_response_guard.py::test_multiple_citations_in_one_line_are_each_judged_separately` — the exact
B-class harm cycle 4 was warned about. Cause: `_named_acts` read every Act name in a ±110/90
character window, so in

> "Sections 34 & 35 and Section 47 and Section 58 all deal with jurisdiction; Section 9 of the
> Wages Code does not."

the *Wages Code* sat inside Section 47's and Section 58's window, and both were judged against it
and stripped. A foreign Act name was poisoning its neighbours.

**Fix.** Act attachment is now **syntactic, not proximity-based**. `_named_acts(block, start, end)`
binds an Act only when it is attached: `of|under|as per|u/s` immediately following the citation
("Section 66D **of the** Information Technology Act, 2000"), or an Act name immediately preceding it
("Consumer Protection Act, 2019, Section 35"). A citation with no attached Act returns an empty list
and takes the domain-scoped act-less route. One reading, shared by the accept path and `_classify`,
so a citation cannot be judged against one set of names and explained against another.

This is the same lesson as cycles 2-3 in a new place: proximity is not a claim.

### A2 — the case-law rule missed the commonest Indian spellings (HIGH, cheapest in set)

**Problem.** `CASE_NAME_RE` was compiled with **no flags** while `REPORTER_RE` four lines below
carried `re.IGNORECASE`, and the separator required a literal period. `Vs.`, `VS.`, `V.`, bare `vs`
and `v/s` all passed with zero redactions and no disclosure.

**Fix, and a correction to the report.** Cycle 4's finding recommended adding `re.IGNORECASE`. That
would have been wrong: `_CASE_PARTY` is `[A-Z][\w...]*`, so a whole-pattern flag makes the leading
`[A-Z]` meaningless and **"apples vs oranges" becomes a case name.** Case-insensitivity is instead
scoped to the separator alone via `(?i:v\.?|vs\.?|v/s|versus)`, leaving party names capital-anchored.
`REPORTER_RE` additionally gained the neutral-citation style ("2021 SCC OnLine SC 456") and cause
titles ("Civil Appeal No. 1234 of 2020", "SLP (C) No. 567/2019", "Writ Petition No. 89 of 2021").

**Verified.** 8/8 real case forms match; 5/5 prose controls correctly do not ("apples vs oranges",
"cash vs card payment", "online vs offline filing", "compare 7 v 8 sections", "the pros vs the
cons"). Reporter: 5/5 match, 3/3 controls do not.

### A3 — fabricated helplines in non-toll-free shapes survived (HIGH, phishing-grade)

**Problem.** Only the 1800 prefix and bare 3-5 digit short codes were checked. An orchestrator probe
confirmed the hole was **wider than reported** — every other shape passed untouched:
`1860-500-1234` (the commonest Indian *bank* helpline format, and the context M7 was originally
found in), a 10-digit `9876543210`, an STD landline `022-2260 3000`, and
`ombudsman.mumbai@rbi.org.in` (the URL rule is http/https-only).

**Fix.** `_CONTACT_NUMBER_RE` covers the 18xx series (excluding 1800, which `_TOLLFREE_RE` owns and
which runs first — matching twice would re-drop a number that rule deliberately kept), STD landlines,
and 10-digit Indian mobiles. `_EMAIL_RE` covers addresses, on the same argument that makes case law
unconditional: the product publishes no email registry, so every address the model composes is
unverifiable by construction. Both carry the toll-free rule's four exemptions — registry lookup,
`_protected_digits`, a nearby amount, a nearby identifier word — plus `_protected_text` for the
non-numeric case, because an email the user gave us is theirs and echoing it back is not a
fabrication.

**No number was added to the registry.** `helplines.py` is untouched; a wrong entry there would be
the original M7 bug, harder to spot.

**Verified.** All four fabrications dropped. Preserved: 1930 + cybercrime.gov.in, Tele-MANAS 14416,
the user's own phone, an order id, and `Rs 1860500` (an amount that is 1860-shaped).

#### Dangling-connector residue, fixed while here

Removing a contact mid-sentence left the preposition stranded. This was **pre-existing** — cycle 4's
toll-free rule already produced "Call the toll-free helpline **at for** help." — but widening the
rule makes it fire more often, and shipping broken grammar is the same defect class the triage
criticised in C4. Removal callbacks now emit a private `_GAP` sentinel that `_resolve_gaps` consumes
along with any stranded `at|on|to|via|through|par`, then closes the seam. Applied to toll-free too,
so behaviour is consistent.

### C3 — the evidence-review endpoint persisted the unguarded reply (MED-HIGH, two-word fix)

**Problem.** `main.py` reassigned `response` from `_tag_response(...)`, then built the stored
`ChatMessage` from the **stale `reply_text` local**. The user saw guarded text once and the
unguarded text on reload, so the unguarded version was what persisted in the database.

**Fix.** The appended `ChatMessage` now takes `response.reply_text` and `response.quick_replies`,
matching the sibling path that was already correct. Checked the rest of `main.py`: every other
persistence site (5 of them) already used `response.*`; this was the only one.

## Reproducers flipped

Cycle 4 left 33 `xfail(strict=True)` reproducers. S5-A flipped **17** of them:

| Reproducer | Item |
|---|---|
| `test_case_law_rule_is_unconditional_across_separator_spellings` (5 params) | A2 |
| `test_case_law_rule_covers_other_indian_citation_styles` (2 params) | A2 |
| `test_fabricated_helpline_in_a_non_1800_shape_is_removed` (3 params) | A3 |
| `test_fabricated_official_email_address_is_removed` | A3 |
| `test_evidence_review_endpoint_persists_the_guarded_reply` | C3 |
| `test_no_section_number_is_verified_in_a_corpusless_domain` (4 params) | A1 |
| `test_citing_the_state_act_does_not_trigger_the_model_law_rewrite` | B4, closed by A1 |

All 17 have had their `xfail` decorators removed and now stand as plain regression locks, per the
cycle-1/2 convention. Assertion bodies are unchanged.

**One partial flip, handled honestly.** `test_naming_the_real_applicable_act_does_not_cause_deletion`
(B2) had two params and only one flipped — the RERA one — and **not because B2 is fixed.**
`ACT_NAME_RE` cannot match across the parenthetical in "Real Estate (Regulation and Development)
Act", so no Act attaches, the citation takes the act-less route, and `_classify` returns "doubtful",
which keeps it with a caveat. That is the module's own rule 1 reached by accident. Promoting the
whole test would have misreported B2 as half-fixed, so the parametrisation was **split**: the
Rajasthan Rent Control case stays a strict xfail under B2 for S5-B, and the RERA case became
`test_parenthesised_act_name_is_caveated_not_deleted`, locked with its reason stated in the
docstring. The `ACT_NAME_RE` parenthesis blind spot is recorded for S5-B.

## Files changed

```
backend/app/services/response_guard.py   — A1 licensing, A2 patterns, A3 shapes, gap resolution
backend/app/main.py                      — C3, one persistence site
backend/tests/test_cycle4_adversarial.py — 5 xfail decorators removed (locks), header note
```

`app/services/safety_triage.py` and `app/services/helplines.py` were **not** modified.

## Still open, deliberately

**S5-B** — the rest of the B-class: B1 (correct rates deleted when an outcome word shares the
sentence, plus an irrelevant probability disclaimer), B2 (naming the real Act makes deletion *more*
likely), B3 (procedural advice deleted by the constructed-authority rule). B4 fell out of A1; the
other three did not, and they remain keyed on token proximity rather than on what the sentence
asserts. The `_named_acts` attachment fix in this cycle is the template.

**S5-C** — D1-a/D1-b (the `jurisdiction.fact_key` leak and the document-field-name scrub), C2 (the
statute note claiming a removal that never happened), C1 (the unnamed-authority class firing
nothing).
