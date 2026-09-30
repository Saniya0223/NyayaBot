# Cycle 6 — Agent 1 plan (set S5-B)

Read-only diagnosis. No source or test file was modified. Every claim below is a probe I ran
against `guard_reply` with `backend\.venv\Scripts\python.exe`, or a source line I read.
No browser, no server, no provider call.

Baseline confirmed as given: `1 failed, 770 passed, 16 xfailed`; the failure is the pre-existing
`test_doc_generator.py::test_document_generation_notice`.

---

## 1. Verdict on the shared-root-cause generalisation

**It holds, and S5-B is one mechanism — but the mechanism is a *decision discipline*, not a single
shared function.** Two of the three items are literal co-occurrence bugs; the third is a polarity
bug of the same family. All three are repaired by the same two-part rule, applied at three sites:

> **(i) Judge the matched span from its own grammar, never from its neighbours.**
> **(ii) When that reading is inconclusive, caveat — never delete.** (The module's own rule 1.)

Part (ii) is the part that is currently missing *as code*. `_apply_statute` honours rule 1 (it has a
`"doubtful"` verdict that keeps + caveats). `_apply_outcome_probability` and `_apply_model_law`
have **no inconclusive branch at all** — every firing is a deletion. That is the single structural
defect behind C4-1/C4-2 and H4-2, and it is why both "double-fault" (delete *and* mis-caveat).

### Evidence from source

| Item | Site | What it keys on |
|---|---|---|
| C4-1 / C4-2 | `response_guard.py:907-910` | `context = body[start-70 : end+70]` then `_OUTCOME_WORD_RE.search(context)`. A literal character window. The percentage's own role is never read. |
| H4-2 | `response_guard.py:1240-1258` | `_CONSTRUCTED_AUTHORITY_RE.search(line) and _WEB_CLAIM_RE.search(line)` — two tokens co-occurring on a line, with no test of whether the sentence *supplies* a web location or *asks the user to find* one. Gated additionally on `cited_model_law`, computed over the **whole reply** (`guard_reply:1317,1321-1326`), which is why the advice's fate depends on a `Section 11` in another sentence. |
| C2-a2 | `response_guard.py:846-859` (`_classify`) | Not proximity — cycle 5 already fixed that half via `_named_acts` syntactic attachment. The residue is **polarity**: an Act-name token that fails a string-containment test is read as *disproof* rather than as *no information*, so `return "unverified"` (strip) instead of `"doubtful"` (keep + caveat). |

**Refutation of the strong form, stated honestly.** The triage's phrasing — "a number, an Act name,
an outcome word within a character window" — is only accurate for C4 and H4-2. For C2-a2 the token
is now correctly *attached*; what is wrong is what the guard *infers* from failing to recognise it.
So the generalisation survives in its useful form ("key to the claim, not the vocabulary") and
yields one discipline, but it does **not** collapse into one function call shared by three rules,
because the three claims are of three different kinds: a number's unit, a citation's Act, and a
sentence's speech act. Expect three small predicates and one shared invariant, not one predicate.

### A second, unprompted confirmation from the real bytes

The `W1-06-t4` fixture's four invented percentages are:

```
'**Rough chance of a favourable outcome:** **\u2248\u202f30\u202f%**  '
'**Rough chance of a favourable outcome:** **\u2248\u202f40\u202f%**  '
'- **Tour\u2011operator refund:** ~30\u202f% chance, mainly limited by the two\u2011year limitation period.  '
'- **Employer settlement:** ~40\u202f% chance, limited by the three\u2011year limitation period ...'
```

Every one of them is caught by **attachment alone**: two are `label: value` lines whose label is the
predicate, and two are followed *immediately* by the word `chance`. The ±70 window buys the guard
**nothing** on its own evidence — it is pure false-positive surface. `_PERCENT_PHRASE_RE`
(`response_guard.py:870-875`) *already* optionally consumes a trailing probability noun; the code
throws that information away and consults the window instead.

---

## 2. C4-1 / C4-2 — correct rates deleted wherever rates naturally live (HIGH)

### Problem

`response_guard.py:885-923`, `_apply_outcome_probability`, inner `replace()`:

```python
context = body[max(0, start - _WINDOW): min(len(body), end + _WINDOW)]   # :907  _WINDOW = 70
if not _OUTCOME_WORD_RE.search(context):
    return match.group(0)
```

Reproduced (CONSUMER profile, `guard_reply` direct):

| in | out | rules |
|---|---|---|
| `A favourable order can include 18% per annum interest on the refund.` | `...can include per annum interest...` + outcome note | `('outcome_probability',)` |
| `Your chances improve a lot if you demand a 100% refund in the notice.` | `...if you demand a refund...` + outcome note | `('outcome_probability',)` |
| `If you win, the 12% GST charged on the cancelled booking is also refundable.` | `...the GST charged...` + outcome note | `('outcome_probability',)` |
| `The likelihood of recovery rises when the contract provides 2% per month interest.` | `...provides per month interest.` + outcome note | `('outcome_probability',)` |
| `संभावना है कि 18% ब्याज मिलेगा।` | `संभावना है कि ब्याज मिलेगा।` + outcome note | `('outcome_probability',)` |

Control confirmed untouched: `The District Commission awards interest at 9% per annum.` → `()`.

**Note on cycle 4's report:** the orchestrator's correction is right about the *bare* string
`demand a 100% refund` (passes unchanged), but the **reproducer's** param 2 is the full sentence
`Your chances improve a lot if you demand a 100% refund in the notice.`, and that one **does**
reproduce — I measured it above. The fix must handle it, so it is not the example to drop.

Double fault: `_outcome_note` is appended because `hit` is set by a deletion that was never a
probability, so a reply that never mentioned probability is told "the strength of a case cannot be
reduced to a number".

### Proposed fix — `response_guard.py::_apply_outcome_probability`

Delete `_WINDOW` and the `context` slice. Decide from the percentage's **own** attachment, in this
order (first match wins):

1. **Measurand attached after the `%`** → **KEEP**, unconditionally, and set no `hit`.
   New `_MEASURAND_AFTER_RE`, anchored with `.match()` on `body[match.end():]`:
   `per annum|per month|per year|p.a.|annually|monthly|yearly|prati varsh|prati maah|saalana`,
   `gst|tds|vat|cess|tax|interest|byaj|refund|deposit|discount|commission|penalty|advance|
   cashback|brokerage|stamp duty|share|hike|increase|escalation|markup`,
   Devanagari `ब्याज|प्रति वर्ष|वार्षिक|मासिक|जीएसटी|कर|जमा|वापसी|रिफंड`,
   with an optional `ka|ki|ke|of|as` bridge. This is the "a rate states what it is a rate **of**"
   test from the task brief.
2. **Probability noun attached after** → **REMOVE** (inline). `_PERCENT_PHRASE_RE` at
   `response_guard.py:870-875` *already* consumes this optional trailing noun — promote it to a
   named group and add `संभावना|मौका|अवसर|chance of success`. No new mechanism needed.
3. **Probability noun attached immediately before, tight gap only** → **REMOVE**. Hindi word order
   puts the noun first (`जीतने की संभावना 30% है`), and `The probability is 30%.` has nothing after
   the `%`. Bound the gap to ≤ 40 chars of the *same clause* with only a copula/filler between
   (`is|are|at|of|around|about|:|है कि|है`), anchored with `\Z`. Rule 1 in **step 4** below is the
   backstop, so a miss here is a caveat, not a deletion.
4. **`label: value` line whose label carries an outcome word** → **REMOVE the line**. Keep
   `_OUTCOME_LINE_RE` (`:876-880`) exactly as it is — it is already assertion-shaped, because the
   label *is* the predicate. **Add the step-1 veto to it**, so `**Interest on a favourable order:**
   18% per annum` is not deleted by its label.
5. **Otherwise → KEEP**, add no redaction, and do **not** set `hit`, so `_outcome_note` is not
   appended. This is the missing rule-1 branch.

Keep the cheap early return at `:890` (`_OUTCOME_WORD_RE.search(text)`): its word set is a strict
superset of the probability nouns in steps 2-3, so it can only short-circuit cases that would
KEEP anyway.

### Prototype result (read-only, source untouched)

I prototyped exactly this predicate against the real fixture bytes plus every string in the
reproducers, the DoD #6 list and `test_legitimate_percentages_pass_through_byte_identical`:
**19/19 correct, 0 failures.** Decisions:

- KEEP via step 1: all 4 reproducer params, the Devanagari reproducer, the 9% control, the 5-in-one
  DoD string (5 separate percentages, all kept), and all 5 `test_legitimate_percentages...` params.
- DROP: both `**Rough chance of a favourable outcome:** **≈ 30 %**` lines via step 4, both
  `~30 % chance,` / `~40 % chance,` via step 2, plus `30% chance of success`, `The probability is
  30%.`, `जीतने की संभावना 30% है।`, `Your odds are about 40%.`, `Success rate: 65%`.
- The `- **Tour‑operator refund:** ~30 % chance,` line is the trap: `refund` sits immediately
  *before* the number. This is why the measurand test must be **after-only** — a measurand-before
  test would veto a real probability. Verified: that line still drops, via step 2.

**W1-06-t4's redaction count is preserved at exactly 4** (2 whole-line + 2 inline), which
`test_response_guard.py:...==["outcome_probability"]*4` asserts.

### Reproducers this must flip

- `test_legitimate_percentage_survives_an_outcome_word_in_the_same_sentence` (4 params)
- `test_devanagari_interest_rate_survives_next_to_sambhavna`

---

## 3. C2-a2 — naming the real Act makes deletion MORE likely (HIGH, perverse incentive)

### Problem

`response_guard.py:846-859`, `_classify`:

```python
if not allow.corpus_available:
    return "unverified"                    # :849-851  <- cycle 5's A1 fix depends on this
named = list(named) if named is not None else _named_acts(block, position)
if not named:
    return "doubtful"                      # :854-855  no Act named -> keep + caveat
for name in named:
    if any(name in known or known in name for known in allow.act_names):
        return "doubtful"
return "unverified"                        # :859      Act named but unrecognised -> DELETE
```

Reproduced, HOUSING_TENANT / Rajasthan / `legal_sources=TENANCY_SOURCES`:

```
IN : Eviction grounds are in Section 13 of the Rajasthan Rent Control Act, 1950.
OUT: Eviction grounds are in the applicable provision of the Rajasthan Rent Control Act, 1950.
     + "...so I have removed the section numbers I could not confirm."
     rules: ('statute_unverified',)

IN : Eviction grounds are in Section 13 of the applicable rent law.
OUT: unchanged + caveat                    rules: ('statute_unconfirmed_caveated',)
```

`_named_acts` returns `['rajasthan rent control act 1950']` — attachment is working correctly after
cycle 5. The actual defect is in the **matcher**: `allow.act_names` for HOUSING_TENANT contains

```
'model tenancy act state rent control acts'
```

`_norm_act` (`:144-149`) replaces `/` with a space, so the corpus label "Model Tenancy Act / State
Rent Control Acts" — **a disjunction naming a family** — is flattened into one meaningless string,
and a bare `in` test can never see that the Rajasthan Rent Control Act *is* a State Rent Control Act.
The product's own corpus says it covers this Act; the guard cannot read its own label.

### Proposed fix — two edits, `response_guard.py`

**(a) `build_allowlist` (`:263-375`) — preserve the disjunction.** Split each `act` label on `/` and
on ` or ` **before** calling `_norm_act`, and register each alternative as its own entry in
`act_names` (and in `verified_pairs`, so the accept path gains the same reading). One label becomes
`('model tenancy act', 'state rent control acts')`.

**(b) new `_act_family(name)`, used in `_classify`'s final branch.** Reduce both sides to a family
key before comparing: strip a leading State/UT name or a `state|central|union|model` qualifier,
strip a trailing year, singularise `acts|codes|rules|sanhitas|adhiniyams`, collapse whitespace.
Compare by **exact equality of the reduced key** — not substring containment. Unrecognised after
that → `"unverified"`, as today.

### Prototype result (read-only, source untouched)

11/11 correct with exact-key equality (containment also scored 11/11, but equality is tighter, so
take equality):

| named Act | domain | family key | verdict |
|---|---|---|---|
| `rajasthan rent control act 1950` | HOUSING_TENANT | `rent control act` | recognised → **doubtful** (flips the reproducer) |
| `maharashtra rent control act 1999` | HOUSING_TENANT | `rent control act` | recognised |
| `delhi rent control act 1958` | HOUSING_TENANT | `rent control act` | recognised |
| `model tenancy act 2021` | HOUSING_TENANT | `tenancy act` | recognised |
| `consumer protection act 2019` | CONSUMER | `consumer protection act` | recognised |
| `code on wages 2019` | CONSUMER | `code on wages` | **not** recognised → strip (lock holds) |
| `wages code` | CONSUMER | `wages code` | **not** recognised → strip (lock holds) |
| `payment of wages act 1936` | HOUSING_TENANT | `payment of wages act` | not recognised → strip |
| `industrial disputes act 1947` | HOUSING_TENANT | `industrial disputes act` | not recognised → strip |
| `real estate regulation and development act 2016` | HOUSING_TENANT | — | not recognised (see below) |
| `bihar buildings lease rent and eviction control act 1982` | HOUSING_TENANT | `buildings lease rent and eviction control act` | not recognised → strip |

Guard rail Agent 2 must add: require the reduced key to be at least 2 words and 10 characters before
it may match, so a degenerate key such as `act` cannot match everything.

### How this preserves cycle 5's A1 fix — explicitly

1. **The `corpus_available == False` early return at `:849-851` is not touched.** It runs *before*
   the named-Act branch, so EMPLOYMENT / POLICE_COMPLAINT / GENERAL / CYBER_FRAUD keep returning
   `"unverified"` for every citation that reaches `_classify`, named Act or not.
2. **`CitationAllowlist.licensed` (`:184-215`) — the accept path — is not loosened.** Edit (a) only
   *splits* existing labels into alternatives; it introduces no new Act and no new `(act, section)`
   pair whose section was not already in the corpus. `verified_pairs` still requires a pair, never a
   bare number, which is the whole substance of A1.
3. **`_act_family` is used in `_classify` only** — i.e. on the **reject** side, where its only
   possible effect is `unverified → doubtful`, which keeps more law and deletes less. It cannot turn
   a reject into an accept, so it cannot report a fabricated citation as verified.
4. **A1's reproducer:** `test_no_section_number_is_verified_in_a_corpusless_domain` (4 params × 3
   corpus-less domains) asserts `redactions != ()`; unaffected by 1-3. I re-probed all four strings
   while designing this and they all still take the `corpus_available` path.

Reproducers I checked this against, all of which must stay green:
`test_no_section_number_is_verified_in_a_corpusless_domain`,
`test_document_citation_map_provisions_survive_in_their_own_domain`,
`test_an_act_that_is_in_no_allowlist_is_rejected_even_where_a_corpus_exists`,
`test_multiple_citations_in_one_line_are_each_judged_separately`,
`test_two_unverifiable_citations_in_one_line_are_both_replaced`,
`test_bare_parent_section_is_not_allowlisted_by_a_corpus_sub_clause`,
`test_a_bare_section_number_in_the_domain_allowlist_needs_no_act_name`,
`test_an_unknown_section_with_no_act_named_is_caveated_not_deleted`.

### Rejected alternative, and the residue I am not closing

The clean generalisation — *an unrecognised Act name is no information, so return `"doubtful"`
always* — **breaks two currently-passing locks**:
`test_an_act_that_is_in_no_allowlist_is_rejected_even_where_a_corpus_exists` and
`test_multiple_citations_in_one_line_are_each_judged_separately` both require
`Section 9 of the Code on Wages` to be **stripped** inside CONSUMER. BRIEF constraint 3 forbids
losing a passing test, so that route is out for this cycle.

So **the perverse incentive is narrowed, not eliminated.** After this fix, naming a State variant of
the Act the corpus itself names is as safe as naming nothing; naming an unrelated Act is still
stripped. The residual asymmetry — e.g. `Bihar Buildings (Lease, Rent and Eviction) Control Act,
1982`, a genuinely applicable State tenancy law with a different family name — remains. That is a
**product decision** (is a strip ever the right answer for a named but unvouched-for Act?) and it is
the same question the triage's cut-list item 3 handed to the user. Flagged, not papered over.

### The `ACT_NAME_RE` parenthesis blind spot — recommendation: do **not** fix it this cycle

`ACT_NAME_RE` (`:153-159`) cannot match across `(Regulation and Development)`, so
`Section 43 of the Real Estate (Regulation and Development) Act, 2016` attaches no Act, takes the
act-less route, and is caveated — the right outcome by accident, locked by
`test_parenthesised_act_name_is_caveated_not_deleted`.

Fixing the blind spot would make `real estate regulation and development act` a *named,
unrecognised* Act in HOUSING_TENANT whose family key matches nothing → **strip** → that lock turns
red and we ship a brand-new instance of exactly the C2-a2 harm, on an Act that is genuinely relevant
to a housing case. **The blind spot is load-bearing until the residual asymmetry above is resolved.**
Keep the lock as it is; its docstring already records the reason honestly. Re-raise the blind spot
only in the cycle that revisits the strip-on-unrecognised-Act policy.

### Reproducer this must flip

- `test_naming_the_real_applicable_act_does_not_cause_deletion` (1 param, Rajasthan Rent Control Act)
---

## 4. H4-2 — the constructed-authority rule deletes sound procedural advice (HIGH)

### Problem

`response_guard.py:1227-1287`, `_apply_model_law`, branch (1). Reproduced with `tenancy_profile()`:

```
IN : Section 11 governs the deposit. Confirm the exact address of the Jaipur Rent Authority
     on its official website before filing. Then attach your rent agreement.
OUT: Section 11 governs the deposit. Then attach your rent agreement.
     rules: ('constructed_authority_contact', 'model_law_adoption_caveat')

IN : <the advice sentence alone>
OUT: unchanged                             rules: ()
```

Two separate defects:

**(i) The sentence fallback has no assertion test at all** (`:1252-1258`). Branch (1) fires on
`_CONSTRUCTED_AUTHORITY_RE and _WEB_CLAIM_RE` co-occurring on a line. Branch **1a** (the
parenthetical swap, `:1236-1250`) is assertion-shaped *by accident and correctly*: a parenthetical
is where the model puts the specific place it invented, so the parenthetical **is** the locator —
which is exactly why W1-02 t2 works and is praised. Branch **1b**, the fallback, has no locator
signal whatsoever, so it deletes the whole sentence — including `confirm ... yourself`, which
asserts nothing and is good practice. This is the missing rule-1 branch again.

**(ii) The firing is not locally predictable.** `_apply_model_law` runs only when
`model_law_caveat` is true (`guard_reply:1317-1326`), and `cited_model_law` is computed over the
**whole reply** by `_apply_statute`. So whether the advice survives depends on a `Section 11` in
another sentence. Branch (1) is about a **fabricated authority contact** and has nothing to do with
model-law adoption; branch (2) (the cite-instruction rewrite) is the one that genuinely does.

### Proposed fix — `response_guard.py::_apply_model_law`, plus one line in `guard_reply`

**Step 1 (required — this is what flips the reproducer). Give branch 1b a locator test.** Add
`_LOCATOR_RE`: a bare hostname (`\b[\w-]+(\.[\w-]+)+\.(gov|nic|org|com|net|in)\b`), a `http(s)://`
URL, a 6-digit PIN code, a quoted or bolded page/portal/form name, a street-address shape. Then:

- sentence **supplies a locator** → remove it as today, `Redaction("constructed_authority_contact",
  "sentence")`. This is the fabrication: a specific place the product cannot verify.
- sentence **supplies no locator** → **remove nothing.** It asserts no location; it asks the user to
  go and find one. Either stand down silently, or (optional, if Agent 2 wants a visible trace)
  append the existing `_authority_contact_rewrite(profile)` parenthetical under a **new** rule name
  such as `constructed_authority_caveated`. The reproducer asserts
  `"constructed_authority_contact" not in result.rules()`, so a distinct name is required if a rule
  is recorded at all. **Prefer standing down**: the sentence contains no claim, so there is nothing
  to caveat, and adding copy risks the byte-for-byte locks in §5.

Prototype check on the real strings:

| sentence | locator | action |
|---|---|---|
| `Confirm the exact address of the Jaipur Rent Authority on its official website before filing.` | no | **keep** (flips the reproducer) |
| `File the complaint on the Rajasthan Rent Authority website at rentauthority.rajasthan.gov.in.` | yes | remove |
| `The Jaipur Rent Authority website lists its address as 5 Janpath, Jaipur 302005.` | yes | remove |
| `Download Form II from the Jaipur Rent Authority portal.` | no | keep (soft claim, rule 1) |

Do **not** touch branch 1a. W1-02 t2 goes down 1a (`replaced = True`), so the fallback never runs
there and the byte-for-byte lock is structurally unaffected.

**Step 2 (recommended, separable). Hoist branch (1) out of the model-law gate** so it runs on every
reply, and leave branch (2) gated on `model_law_caveat`. That is what makes the behaviour locally
predictable.

**Measured risk: zero on the recorded evidence.** I evaluated branch (1)'s preconditions *ungated*
over all eight wave-1 fixtures. Exactly **one** line in **one** fixture matches:

```
W1-02-t2: url=False paren_swap=True
  '1. **Jaipur Rent Authority ya District Court** ka exact address aur contact details confirm
   karein (Jaipur Municipal Corporation ya Rajasthan State Rent Authority ke website par ...'
```

and that line already fires today (t2 cites Section 30, so the gate is open). So hoisting adds
**no new firing anywhere in the recorded evidence**. Still: do Step 1, run the suite, then do
Step 2 and run again. If Step 2 destabilises anything, ship Step 1 alone — Step 1 is what the
reproducer needs; Step 2 only removes the non-local coupling.

### Reproducer this must flip

- `test_procedural_advice_survives_the_constructed_authority_rule`

---

## 5. Preservation locks — carry all of these forward

Everything in `tests/test_response_guard.py` and `tests/test_cycle4_adversarial.py` must stay green.
These are the ones the three fixes above could plausibly break, with the test that proves each.

| # | Lock | Test |
|---|---|---|
| L1 | **W1-02 t2's Jaipur-vs-Bengaluru passage, byte for byte** (both halves: the jurisdiction paragraph and the `Agar aap Bengaluru se online` follow-up) | `test_w1_02_t2_jurisdiction_passage_survives_byte_for_byte`, `test_w1_02_t2_loses_the_invented_authority_website_and_keeps_the_praised_reasoning` |
| L2 | **W1-02 t2 still loses `Rajasthan State Rent Authority ke website` and `Jaipur Municipal Corporation`**, and still records `constructed_authority_contact` | same two tests, `test_h4_recorded_replies_keep_their_sections_and_gain_the_caveat` |
| L3 | **W1-04's 1930 / cybercrime.gov.in sequencing**: `1930` kept, `https://www.cybercrime.gov.in/` kept, the fabricated `1800‑11‑001‑112` / `1800‑11‑222‑222` and the RBI Complaints URL gone, `₹47,500` and `24‑hour helpline` kept | `test_m7_recorded_numbers_are_removed_and_the_real_ones_kept` |
| L4 | **W1-06 t4 clean of all four invented percentages**, `%` absent, **exactly four** `outcome_probability` redactions, `Which one to chase first?` / `Effect on success chance` / `₹ 62,000` / `₹ 90,000` / `₹ 50 lakh` kept | `test_c4_recorded_percentages_are_all_removed`, `test_w1_06_t4_invented_win_percentages_are_all_removed` |
| L5 | **Legitimate percentages survive byte-identical**: 50% deposit, 12% GST, 2% per month, 100% refund, 18% per annum — isolated and combined — plus the 9% control | `test_c4_isolated_legitimate_percentages_pass_byte_identical`, `test_legitimate_percentages_pass_through_byte_identical` (5 params) |
| L6 | **All 11 corpus provisions survive**: CONSUMER 2(11), 2(34), 2(47), 34&35, 35, 47, 58, 69, 69(2) in three scripts and both CPA spellings; HOUSING_TENANT 11, 15, 21, 30 in three scripts, caveated and never deleted | `test_consumer_corpus_provisions_survive_every_plausible_phrasing`, `test_tenancy_corpus_provisions_survive_with_a_caveat_never_a_deletion`, `test_multiple_citations_in_one_line_are_each_judged_separately` |
| L7 | **Document-generation citations survive in chat**: IT Act 66D in CYBER_FRAUD, BNSS 173 in POLICE_COMPLAINT, ICA 73 in HOUSING_TENANT, CPA 35 in CONSUMER, RTI 6(1) in GENERAL — text unaltered | `test_document_citation_map_provisions_survive_in_their_own_domain` |
| L8 | **The guard is a provable no-op on every deterministic composer** (~300 parametrised composer × domain combinations, all three scripts) | `test_guard_is_a_provable_no_op_on_every_deterministic_composer` |
| L9 | **Idempotence** on all eight recorded replies × 4 domain/style combinations, and on the retry/resume path | `test_guard_is_idempotent_on_every_recorded_reply`, the DoD #14 test in `test_response_guard.py:526` |
| L10 | **A1 stays closed**: no bare section number is verified in a corpus-less domain (4 strings × 3 domains) | `test_no_section_number_is_verified_in_a_corpusless_domain` |
| L11 | **B4 stays closed**: citing the State Act does not trigger the model-law rewrite | `test_citing_the_state_act_does_not_trigger_the_model_law_rewrite` |
| L12 | **The parenthesised-Act accident stays a caveat, not a deletion** | `test_parenthesised_act_name_is_caveated_not_deleted` |
| L13 | **W1-03 t3 still loses `Section 9(1)` and the invented verbatim quote**, keeps `Code on Wages, 2019` and `before the wage period ends`, and pastes no URL or underscore | `test_c2_recorded_fabricated_section_is_removed_with_its_quoted_text`, `test_w1_03_t3_unverifiable_statute_and_its_invented_quote_are_removed` |
| L14 | **W1-02 t1**: `Section 11` kept, cite-instruction rewritten, `Jaipur Rent Authority` and the `local rent authority` procedural line untouched | `test_w1_02_t1_model_tenancy_act_gains_a_caveat_and_loses_the_cite_instruction` |
| L15 | **The user's own digits are never touched**, and `Rs 1860500` (an amount of 1860 shape) survives | `test_m7_never_touches_the_users_own_digits`, `test_fabricated_helpline_in_a_non_1800_shape_is_removed` |
| L16 | **Fixtures stay byte-faithful**; no retyped ASCII approximation | `test_every_recorded_reply_is_byte_identical_to_the_transcript_source` |
| L17 | **`safety_triage.py` is not modified.** Set S1 is closed and cycle 2 introduced a real safety regression there. Also do not modify `helplines.py` — a wrong registry entry is the original M7 bug. | mtime check |

---

## 6. Definition of done — every criterion checkable without a live LLM call

Run exactly, and only:

```
cd backend && .venv/Scripts/python.exe -m pytest -q --ignore=tests/test_browser_autofill.py
```

1. **Count.** `1 failed, 777 passed, 9 xfailed`. That is the baseline `770 passed` plus the 7
   reproducer cases S5-B flips (4 C4-1 params + 1 C4-2 + 1 C2-a2 param + 1 H4-2), and `16 xfailed`
   minus those 7. The one failure is still `test_doc_generator.py::test_document_generation_notice`
   and nothing else. **No test may be lost or skipped.**
2. **`xfail(strict=True)` decorators removed** from the 4 flipped reproducers, per the cycle-1/2
   convention, so they stand as plain regression locks. **Assertion bodies unchanged.** The 9
   remaining xfails are all S5-C (D1-a, D1-b, C2-c3 ×4 params, C2-c4, C2-a3) and must stay strict.
3. **No live provider call.** `grep -rn "groq\|gemini\|httpx\|requests\." ` over the diff returns
   nothing new; no test added that is not a pure `guard_reply` / `AsyncMock` call.
4. **`_WINDOW` is gone** from `response_guard.py`, and no rule in the module decides from a
   character-window slice of its neighbours. `grep -n "_WINDOW\|start - \|end + " app/services/response_guard.py`
   returns nothing in a decision path.
5. **Rule 1 is code, not a comment.** `_apply_outcome_probability` and `_apply_model_law` branch 1b
   each have an explicit inconclusive branch that keeps the text. Demonstrated by the reproducers in
   §2 and §4.
6. **No caveat without a cause.** `_outcome_note` is appended only when a percentage was actually
   judged a probability: `guard_reply("A favourable order can include 18% per annum interest on the
   refund.", CONSUMER).redactions == ()` and the note is absent.
7. **A1 preserved, demonstrated.** All 4 params × 3 corpus-less domains of
   `test_no_section_number_is_verified_in_a_corpusless_domain` pass, and `licensed()` is unchanged
   except for the label split described in §3(a).
8. **W1-06 t4 still yields exactly `["outcome_probability"] * 4`.** Not three, not five.
9. **All 17 locks L1-L17 in §5 green**, with L17 verified by file mtime rather than by assertion.
10. **Files touched:** `backend/app/services/response_guard.py` and
    `backend/tests/test_cycle4_adversarial.py` (decorator removals only). Nothing else.
    **No commit, no push, no branch.**

---

## 7. Scope

**In scope (S5-B):** C4-1, C4-2, C2-a2, H4-2.

**Out of scope (S5-C), untouched by this plan:** D1-a (`domain_context['jurisdiction']['fact_key']`
leak), D1-b (document-field-name scrub), C2-a3 (the statute note claiming a removal that never
happened), C2-c3 and C2-c4 (the unnamed-authority class firing nothing).

### What this plan constrains in S5-C

- **C2-a3 gets easier and more urgent.** §3 converts a class of citations from `statute_unverified`
  to `statute_unconfirmed_caveated`, so the caveat-only path — the one whose note falsely says
  "I have removed the section numbers I could not confirm" — will now be reached *more often*,
  including on the Rajasthan Rent Control Act sentence. `_statute_note` (`:430-461`) needs a
  removed-vs-caveated-only branch. S5-C should do C2-a3 **immediately after** this cycle; the
  wrong-note harm is amplified by §3.
- **C2-c3 / C2-c4 must not reintroduce a co-occurrence rule.** The obvious fix — fire the case-law
  disclosure when `High Court|Supreme Court|settled case law|judgment` appears — is exactly the
  token-presence design this cycle is removing. If a disclosure is appended on an unnamed authority,
  append only; do **not** delete the sentence unless it supplies a specific holding or citation.
  C2-c4 does ask for a deletion (`promise not in result.text`), so S5-C's Agent 1 must define the
  assertion test for "promises a judgment I will now quote" versus "states the law generally", and
  should reuse the locator/attachment shape from §2 and §4 rather than inventing a third pattern.
- **`_LOCATOR_RE` from §4 is reusable** by C2-c3/c4 and by any future fabricated-specificity rule.
  Put it near `_URL_RE`, not inside `_apply_model_law`.
- **D1-a / D1-b are orthogonal.** They live in `llm_conversation.py` /
  `domain_registry.compact_context` and in `_strip_fact_key_parentheticals` /
  `CitationAllowlist.fact_keys`. Nothing in this plan touches either. `fact_keys` is built in
  `build_allowlist` (`:374`), which §3(a) edits — so D1-b's implementer should re-read that function
  after this cycle lands, but the `fact_keys` line itself is untouched.

---

## 8. Implementation order for Agent 2

1. **C4** (§2). Self-contained, prototype-validated 19/19, and it removes `_WINDOW` — the clearest
   instance of the shared root cause. Run the suite.
2. **H4-2 Step 1** (§4). Self-contained. Run the suite.
3. **C2-a2** (§3). Touches `build_allowlist`, so it carries the most regression surface — do it with
   the other two already green so any breakage is unambiguously attributable. Run the suite.
4. **H4-2 Step 2** (the hoist). Optional; revert if anything moves.
5. Remove the 4 `xfail` decorators. Run the suite. Report.

Do not batch steps 1-3 into one edit-then-test. Cycle 5's A1 regression (two correct corpus
provisions deleted) was found only because the suite was run between changes.

---

## Appendix — baseline I re-measured, and the xfail ledger

```
cd backend && .venv/Scripts/python.exe -m pytest -q --ignore=tests/test_browser_autofill.py
1 failed, 770 passed, 16 xfailed, 3 warnings in 35.60s
FAILED tests/test_doc_generator.py::test_document_generation_notice
```

No browser, no server, no provider call. `tests/test_cycle4_adversarial.py` collects 64 tests.

The 16 strict xfails, by set — Agent 3 should grade against this ledger:

| Reproducer | Params | Item | Set |
|---|---|---|---|
| `test_legitimate_percentage_survives_an_outcome_word_in_the_same_sentence` | 4 | C4-1 | **S5-B — flip** |
| `test_devanagari_interest_rate_survives_next_to_sambhavna` | 1 | C4-2 | **S5-B — flip** |
| `test_naming_the_real_applicable_act_does_not_cause_deletion` | 1 | C2-a2 | **S5-B — flip** |
| `test_procedural_advice_survives_the_constructed_authority_rule` | 1 | H4-2 | **S5-B — flip** |
| `test_no_raw_profile_field_name_reaches_the_provider_payload` | 1 | D1-a | S5-C |
| `test_document_field_name_parenthetical_is_scrubbed_from_the_reply` | 1 | D1-b | S5-C |
| `test_unnamed_authority_forward_reference_at_least_gets_the_disclosure` | 4 | C2-c3 | S5-C |
| `test_w1_03_t3_forward_reference_sentence_does_not_survive` | 1 | C2-c4 | S5-C |
| `test_statute_note_does_not_claim_a_removal_that_never_happened` | 1 | C2-a3 | S5-C |
| `test_cycle2_verification.py:295` | 1 | N8 offline fallback composer | S3/S8 |

7 of 16 belong to S5-B. Target after this cycle: **1 failed, 777 passed, 9 xfailed.**

### Scratch prototypes

Both prototypes live in the session scratchpad and are read-only re-implementations of the proposed
predicates — they import from `response_guard` but modify nothing:

```
<scratchpad>/proto_c4.py     19/19 correct  (C4 measurand/probability attachment test)
<scratchpad>/proto_c2a2.py   11/11 correct  (C2-a2 act-family reduction, exact-key equality)
<scratchpad>/proto_h4.py     branch-(1) precondition sweep over all 8 fixtures + locator test
```

Agent 2 should re-derive them in `response_guard.py` rather than copy them verbatim: the prototypes
use standalone regexes, whereas the real fix must reuse `APPROX`, `DASH`, `_PERCENT_PHRASE_RE`,
`_OUTCOME_LINE_RE`, `_norm_act` and `_URL_RE` so the U+2011 / U+202F / U+2248 code points in the
fixtures keep matching.
