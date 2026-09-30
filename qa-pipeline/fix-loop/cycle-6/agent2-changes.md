# Cycle 6 — Agent 2 implementation report (set S5-B)

Items: **C4-1, C4-2, C2-a2, H4-2** — the over-filtering rules that decided from token
co-occurrence rather than from what the matched span asserts.

Mechanism applied at all three sites, per Agent 1's framing and the orchestrator's endorsement:

> (i) Judge the matched span from its own grammar, never from its neighbours.
> (ii) When that reading is inconclusive, **caveat — never delete.**

Part (ii) existed as code only in `_apply_statute` (its `"doubtful"` verdict).
`_apply_outcome_probability` and `_apply_model_law` branch 1b had no inconclusive branch at all,
so every firing was a deletion. That is the shared structural defect this cycle closes.

Baseline re-measured before any edit:
```
1 failed, 770 passed, 16 xfailed, 3 warnings in 33.67s
FAILED tests/test_doc_generator.py::test_document_generation_notice   <- pre-existing, not mine
```

Final:
```
1 failed, 777 passed, 9 xfailed, 3 warnings in 35.53s
FAILED tests/test_doc_generator.py::test_document_generation_notice
```

No browser. No server. No provider call. No commit, no push, no branch.

---

## Suite after each step (no step batched with another)

| after | result |
|---|---|
| baseline | `1 failed, 770 passed, 16 xfailed` |
| step 1 — C4 | `6 failed, 770 passed, 11 xfailed` |
| step 2 — H4-2 locator | `7 failed, 770 passed, 10 xfailed` |
| step 3 — C2-a2 | `8 failed, 770 passed, 9 xfailed` |
| step 4 — H4-2 hoist | `8 failed, 770 passed, 9 xfailed` (unmoved — kept) |
| step 5 — 4 decorators removed | `1 failed, 777 passed, 9 xfailed` |

`770 passed` never moved at any step. The rising "failures" in steps 1-4 are the strict `xfail`
reproducers reporting their own success; their decorators come off in step 5.

---

## C4-1 / C4-2 — correct rates deleted wherever rates naturally live

**Problem.** `response_guard.py::_apply_outcome_probability`, inner `replace()`, read
`context = body[start - 70 : end + 70]` and deleted the percentage if any outcome word appeared
anywhere in that slice. The percentage's own role was never read. In real legal prose the outcome
word is always nearby, so the rule deleted `18% per annum`, `12% GST`, `100% refund`,
`2% per month` and Devanagari `18% ब्याज` — and then appended `_outcome_note` ("the strength of a
case cannot be reduced to a number") to a reply that had made no probability claim. Double fault:
a deletion *and* a caveat, both wrong.

**Actual fix — `response_guard.py`.**

* `_WINDOW = 70` and the `context` slice are **deleted**.
* New `_percent_role(body, match) -> "measurand" | "probability" | "unknown"` reads the
  percentage's own grammar:
  1. `_MEASURAND_AFTER_RE.match(body, <char after the %>)` → `"measurand"`. A rate states what it
     is a rate *of*, and it states it **after** the sign. Covers rate periods
     (`per annum|per month|prati varsh|…`), measurand nouns
     (`gst|tds|interest|byaj|refund|deposit|penalty|brokerage|…`) and their Devanagari forms, with
     an optional `ka|ki|ke|of|as|the` bridge so `18 % ka byaj` and `30 % of the refund` both read.
  2. else `match.group("prob")` → `"probability"`. `_PERCENT_PHRASE_RE` **already** optionally
     consumed a trailing probability noun and the old code threw that away; the group is now named
     and consulted, widened with `chance of success`, `win rate`, `sambhavna`, `safalta` and the
     Devanagari nouns. No new mechanism.
  3. else a probability noun immediately **before**, across nothing but a copula/approximator/
     punctuation (`_PROB_BEFORE_RE` + `_PROB_BRIDGE`, gap bounded at `_PROB_GAP = 40`) →
     `"probability"`. Hindi word order needs this (`jeetne ki sambhavna 30 % hai`) and so does
     `The probability is 30%.`, which has nothing after the sign.
  4. else `"unknown"`.
* Inline removal now fires only on `"probability"`. `"unknown"` **keeps the text and sets no
  `hit`** — that is the missing rule-1 branch, and it is what stops `_outcome_note` appearing
  without a cause.
* The `label: value` whole-line rule (`_OUTCOME_LINE_RE`) is unchanged in shape — the label *is*
  the predicate, so it is already assertion-shaped — but now takes the step-1 veto through the
  same `_percent_role` call.
* The cheap early-out `_OUTCOME_WORD_RE.search(text)` is kept: its word set is a strict superset
  of `_PROB_NOUN`, so it can only short-circuit text that would be kept anyway.

**The lookahead-only constraint is respected.** The measurand test looks only *after* the `%`.
W1-06 t4's own invented line is `- **Tour‑operator refund:** ~30 % chance,` — `refund` sits
immediately *before* the number, so a look-behind measurand test would have vetoed a real
fabrication and broken the fixture's redaction count. Stated in the code comment so it is not
"simplified" later.

**Status: fixed.** Probe of 21 strings against the real bytes, 21/21 correct
(14 must-keep incl. all 4 C4-1 params, the Devanagari C4-2 case, both orchestrator controls and
all 5 `test_legitimate_percentages...` params; 7 must-drop incl. `Success rate: 65%`,
`जीतने की संभावना 30% है।` and `संभावना लगभग 40 % है।`). W1-06 t4 still yields exactly
`["outcome_probability"] * 4` with zero `%` surviving (DoD #8).

DoD #6 checked literally:
```
guard_reply("A favourable order can include 18% per annum interest on the refund.", CONSUMER)
  redactions == ()            True
  "cannot be reduced to a number" absent  True
  text byte-identical         True
```

---

## C2-a2 — naming the real applicable Act made deletion MORE likely

**Problem.** `_classify`'s last branch returned `"unverified"` (strip) whenever a named Act failed
a string-containment test against `allow.act_names` — reading the failure as *disproof* rather than
as *no information*. The concrete cause: HOUSING_TENANT's corpus label is
`Model Tenancy Act / State Rent Control Acts`, a **disjunction naming a family**, and `_norm_act`
replaces `/` with a space, flattening it to the meaningless `model tenancy act state rent control
acts`. No containment test can see from that string that the Rajasthan Rent Control Act *is* a
State Rent Control Act. So `Section 13 of the Rajasthan Rent Control Act, 1950` was deleted while
`Section 13 of the applicable rent law` was kept with a caveat — the guard then told the user to go
and check the very Act whose section it had just removed.

**Actual fix — `response_guard.py`, three edits.**

1. **`_act_alternatives(value)`** (new, beside `_norm_act`): splits a label on `/` and ` or `
   **before** normalising. One label becomes `('model tenancy act', 'state rent control acts')`.
2. **`build_allowlist`**: `_pair()` registers each alternative, and `act_names` is built from
   `_act_alternatives` instead of a single `_norm_act`. This only *splits* labels the corpus
   already carries; it adds no Act and no `(act, section)` pair whose section was not already in
   the corpus, so `licensed()` is not loosened.
3. **`_act_family(name)`** (new) used in `_classify`'s final branch only: strip a leading
   `model|state|central|union|government` qualifier **or** a State/UT name, strip a trailing year,
   singularise `acts|codes|rules|regulations|ordinances|sanhitas|adhiniyams`, collapse whitespace.
   Compared by **exact key equality**, never containment. Guard rail as Agent 1 required: a key
   shorter than 2 words or 10 characters is refused outright, so a degenerate `act` can never match.

Measured reduction, after the fix:

| named Act | family key | verdict |
|---|---|---|
| `Rajasthan Rent Control Act, 1950` | `rent control act` | recognised → **doubtful** (flips) |
| `Maharashtra Rent Control Act, 1999` | `rent control act` | recognised |
| `Delhi Rent Control Act, 1958` | `rent control act` | recognised |
| `Model Tenancy Act, 2021` | `tenancy act` | recognised |
| `Consumer Protection Act, 2019` | `consumer protection act` | recognised |
| `Code on Wages, 2019` | `code on wages` | **not** recognised → strip (lock holds) |
| `Wages Code` | `wages code` | **not** recognised → strip (lock holds) |
| `Bihar Buildings (Lease, Rent and Eviction) Control Act, 1982` | `buildings (lease rent and eviction) control act` | not recognised → strip (**residue**, see below) |
| `Act` | `""` | refused by the guard rail |

HOUSING_TENANT's family set is now
`{rent control act, tenancy act, consumer protection act, indian contract act,
information technology act, right to information act, bharatiya nagarik suraksha sanhita,
applicable employment and wage law, tenancy act 2021 (state adoption must be checked)}`.

**Status: fixed for the reproducer; the underlying policy asymmetry is narrowed, not eliminated.**
Naming a State variant of a family the corpus itself names is now as safe as naming nothing.
Naming an unrelated Act is still stripped, and a genuinely applicable State tenancy law with a
different family name (the Bihar Act) is still stripped. That residue is the product decision
Agent 1 flagged and the triage handed to the user; I have not papered over it.

### A1 verification — done, as required

Cycle 5's A1 fix depends on `_classify` returning `"unverified"` when `corpus_available` is False,
and that check runs **first**, above the named-Act logic. I did not touch it, and I verified
empirically rather than by argument: **all 4 params × 3 corpus-less domains of
`test_no_section_number_is_verified_in_a_corpusless_domain` still redact** (probe asserts
`redactions != ()` for Industrial Disputes s.35, Payment of Wages s.73, Police Act s.20 and Code on
Wages s.2(11) in EMPLOYMENT / POLICE_COMPLAINT / GENERAL), and the test passes in the suite.
`_act_family` is used on the **reject** side only, where its sole possible effect is
`unverified -> doubtful` — it keeps more and deletes less and can never report a fabrication as
verified. `licensed()` is unchanged except for the label split.

### The `ACT_NAME_RE` parenthesis blind spot

**Not fixed, deliberately**, per Agent 1's and the orchestrator's instruction.
`test_parenthesised_act_name_is_caveated_not_deleted` stays green as written. Fixing the blind spot
would make `real estate regulation and development act` a named-but-unrecognised Act in
HOUSING_TENANT whose family key matches nothing → stripped → that lock turns red and we ship a
brand-new C2-a2 instance on an Act genuinely relevant to housing cases. The blind spot is
load-bearing until the strip-on-unrecognised-Act policy is revisited.

---

## H4-2 — the constructed-authority rule deleted sound procedural advice

**Problem (i).** `_apply_model_law` branch **1b** (the sentence fallback) fired on
`_CONSTRUCTED_AUTHORITY_RE` and `_WEB_CLAIM_RE` merely co-occurring on a line, with no test of
whether the sentence *supplies* a web location or *asks the user to go and find one*. So
`Confirm the exact address of the Jaipur Rent Authority on its official website before filing.` —
which asserts no location and is good practice — was deleted outright.

**Problem (ii).** Branch (1) ran only when `model_law_caveat` was true, computed over the **whole
reply**, so whether the advice survived depended on a `Section 11` in an unrelated sentence.

**Actual fix — `response_guard.py`.**

*Step 1 (the one that flips the reproducer).* New **`_LOCATOR_RE`**, placed beside `_URL_RE` so
C2-c3/c4 and any future fabricated-specificity rule can reuse it (Agent 1's §5 note). It matches a
scheme-ful URL, a bare hostname (`…gov|nic|org|com|net|edu|in|info`), a PIN code, or a
street-address shape. Branch 1b now removes a sentence **only if it supplies a locator**; a
sentence that supplies none is left alone and records nothing — stand down silently, as the plan
preferred, so no new copy can disturb the byte-for-byte locks. Branch **1a** (the parenthetical
swap) is untouched: a parenthetical *is* the locator, which is why W1-02 t2 works.

Measured on the plan's four strings:

| sentence | locator | action |
|---|---|---|
| `Confirm the exact address of the Jaipur Rent Authority on its official website before filing.` | no | **kept** (flips) |
| `File the complaint on the Rajasthan Rent Authority website at rentauthority.rajasthan.gov.in.` | yes | removed |
| `The Jaipur Rent Authority website lists its address as 5 Janpath, Jaipur 302005.` | yes | removed |
| `Download Form II from the Jaipur Rent Authority portal.` | no | kept |

*Step 2 (the hoist), kept.* Branch (1) is extracted into a new
**`_apply_constructed_authority(text, profile, redactions)`** called unconditionally from
`guard_reply` right after `_apply_helplines` (preserving the previous relative order). Branch (2),
the cite-instruction rewrite, remains gated on `model_law_caveat` — it is the only half that is
genuinely about model-law adoption. The suite did not move, so the hoist ships.

**Status: fixed.**

### One correction to Agent 1's measured risk for the hoist

Agent 1 reported the hoist adds "**no new firing anywhere in the recorded evidence**". I
re-measured by diffing `guard_reply` output before vs after the hoist over **all 8 recorded
fixtures × 4 domain profiles (32 combinations)** and found **3 diffs**, all on W1-02 t2 and all the
same line:

```
W1-02-t2 under CONSUMER / CYBER_FRAUD / EMPLOYMENT
  before: ('internal_fact_key', 'statute_unverified')
  after : ('internal_fact_key', 'statute_unverified', 'constructed_authority_contact')
```

Agent 1's sweep was correct for the **tenancy** profile (where the gate was already open); it did
not cover the same reply guarded under a non-tenancy domain. The new firings are branch **1a**
(`website_claim`), they remove the fabricated `Rajasthan State Rent Authority ke website`
parenthetical and **keep** the authority's name — i.e. the hoist closes a hole in which the same
fabrication survived whenever the case's domain had no model-law corpus. This strengthens the case
for Step 2 rather than weakening it, but the plan's "zero new firings" claim as written is wrong
and should not be repeated.

---

## Deviations from the plan, each with its reason

1. **`_LOCATOR_RE` does not include "a quoted or bolded page/form name"**, which the plan's prose
   lists. The plan's own prototype table requires `Download Form II from the Jaipur Rent Authority
   portal.` to be **kept** (no locator), so a form-name signal would contradict the measured spec.
   A bolded span would also risk matching the constructed authority's own bolded name in W1-02 t2.
   Kept to URL / hostname / PIN / street-address, which is what the table actually measures.
2. **The step-1 veto on `_OUTCOME_LINE_RE` is defensive, not live.** That regex anchors the
   percentage at end-of-line, so no measurand can follow the sign today. The plan's example
   `**Interest on a favourable order:** 18% per annum` therefore never reaches the line rule at
   all. I implemented the veto anyway (one `_percent_role` call) and said so in the comment,
   because the alternative — testing the *label* for a measurand — is forbidden by the
   `Tour‑operator refund:` evidence.
3. **`_ACT_YEAR_TAIL_RE` matches `18xx` as well as `19xx|20xx`**, unlike the module's existing
   `ACT_NAME_RE`. The Indian Contract Act is of 1872 and the Police Act of 1861; a family key that
   kept a pre-1900 year would never reduce to its family. Verified no lock depends on the narrower
   form (the only pre-1900 Acts in the guard suite are ICA 1872, which is licensed on the accept
   path, and Police Act 1861, which is in corpus-less domains and never reaches `_act_family`).
4. **`_STATE_PREFIX_RE` is built from `jurisdiction.STATE_VARIANTS`** rather than a new literal
   list. `response_guard.py` gains one import (`app.services.jurisdiction`, which imports only
   `re` and `typing`, so no cycle). A second copy of the State table would drift.
5. **Helper names differ from the plan's** where the shape differs: `_percent_role` (one function
   returning a three-valued verdict) instead of scattered predicates, and
   `_apply_constructed_authority` for the hoisted branch. The plan states its regex and helper
   names are not binding.
6. **Non-ASCII in the patterns I added is written as `\uXXXX` regex escapes, not literal
   characters.** My first write landed literal Devanagari (functionally identical — the probes
   passed either way) and I normalised it to escapes to match the module's stated convention.
   Only three non-ASCII characters remain in the file and all three are pre-existing
   (`§` at `_CITATION_LEAD_RE`, `डर` in `_IDENTIFIER_WORD_RE`, `’` in `_ATTRIB_CONNECTOR`).

---

## Definition of done — against Agent 1's 10 criteria

| # | criterion | result |
|---|---|---|
| 1 | `1 failed, 777 passed, 9 xfailed`, the failure still only `test_document_generation_notice` | **met exactly**, Agent 1's arithmetic was right |
| 2 | 4 flipped reproducers lose their `xfail`, bodies unchanged; 9 remaining xfails all S5-C | **met** — remaining are D1-a, D1-b, C2-c3 ×4, C2-c4, C2-a3, plus N8 in `test_cycle2_verification.py`, all still strict |
| 3 | no live provider call; nothing new matching `groq/gemini/httpx/requests.` in the diff | **met** — grep over the diff is empty; no test added at all |
| 4 | `_WINDOW` gone, no rule decides from a neighbour-window slice | **met** for the rules in scope — see note below |
| 5 | rule 1 is code in `_apply_outcome_probability` and branch 1b | **met** (`"unknown"` keeps; no locator stands down) |
| 6 | no caveat without a cause | **met**, checked literally (output quoted above) |
| 7 | A1 preserved, demonstrated | **met**, 4 params × 3 domains re-probed and test green |
| 8 | W1-06 t4 still exactly `["outcome_probability"] * 4` | **met** |
| 9 | all 17 locks green, L17 by mtime | **met** — 246 lock assertions pass; `safety_triage.py` mtime 2026-09-29T18:13, `helplines.py` 2026-09-30T12:54, both before this session's 18:51+ edits |
| 10 | only `response_guard.py` and `test_cycle4_adversarial.py` touched; no commit/push/branch | **met** (`main.py` was already `M` at session start from cycle 5 and I did not open it) |

**Note on DoD #4.** `grep -n "_WINDOW\|start - \|end + "` is now clean of `_WINDOW`, but three
window slices remain in the module and I left them deliberately:

* `_named_acts`: `block[end:end + 90]` — this is cycle 5's **syntactic attachment** lookahead, the
  bound on how far a `of the <Act>` connector may reach. It is part of reading the span's own
  grammar, not of consulting neighbours.
* `_apply_helplines`: `start - 8 / end + 2` (nearby amount) and `start - 25 / end + 10` (nearby
  identifier word) — these are **exemptions**, i.e. they only ever *prevent* a deletion. A false
  positive there keeps text, which is the direction rule 1 wants. Converting them would be an M7
  change and is out of S5-B's scope.

---

## Files changed

```
backend/app/services/response_guard.py
    - _WINDOW deleted; _apply_outcome_probability rewritten around _percent_role
    - new: _PROB_NOUN, _MEASURAND, _MEASURAND_AFTER_RE, _PROB_BRIDGE, _PROB_BEFORE_RE,
           _PROB_GAP, _percent_role
    - _PERCENT_PHRASE_RE: trailing probability noun promoted to a named group and widened
    - new: _LOCATOR_RE (beside _URL_RE), _apply_constructed_authority (hoisted branch 1)
    - _apply_model_law reduced to the cite-instruction rewrite; guard_reply gains one
      ungated call
    - new: _ACT_ALT_SPLIT_RE, _act_alternatives, _ACT_QUALIFIER_RE, _STATE_PREFIX_RE,
           _ACT_YEAR_TAIL_RE, _ACT_PLURAL_RE, _act_family
    - build_allowlist: _pair() and act_names register each alternative of a label
    - _classify: family-key fallback before returning "unverified"
    - one new import: app.services.jurisdiction.STATE_VARIANTS

backend/tests/test_cycle4_adversarial.py
    - 4 xfail(strict=True) decorators removed. Assertion bodies and docstrings unchanged.
```

`app/services/safety_triage.py` and `app/services/helplines.py` were **not** modified (L17).
`app/main.py` was already modified before this session (cycle 5's uncommitted C3 fix) and I did not
open it. `git diff --stat` on these two files is cumulative with cycle 5's uncommitted work and so
overstates this cycle's delta.

No parametrisation needed splitting this cycle: cycle 5 had already split
`test_naming_the_real_applicable_act_does_not_cause_deletion`, leaving it with exactly the one
Rajasthan param, and that param flipped. All four params of
`test_legitimate_percentage_survives_an_outcome_word_in_the_same_sentence` flipped together.

---

## Left undone, deliberately

* **`ACT_NAME_RE`'s parenthesis blind spot.** Load-bearing; see above.
* **The C2-a2 residual asymmetry.** A named, genuinely applicable State Act whose family name is
  not the corpus's own (`Bihar Buildings (Lease, Rent and Eviction) Control Act, 1982`) is still
  stripped. Closing it requires deciding whether a strip is ever right for a named-but-unvouched-for
  Act, which is the product question the triage handed to the user. Two currently-passing locks
  (`test_an_act_that_is_in_no_allowlist_is_rejected_even_where_a_corpus_exists`,
  `test_multiple_citations_in_one_line_are_each_judged_separately`) require the strip today.
* **The helpline exemption windows** (see DoD #4 note) — out of scope, and they fail in the safe
  direction.
* **Everything in S5-C**: D1-a, D1-b, C2-a3, C2-c3, C2-c4. All 9 remaining xfails are theirs
  (plus N8).

## For S5-C

* **C2-a3 is now more urgent, exactly as Agent 1 predicted.** The Rajasthan Rent Control Act
  sentence now takes the caveat-only path, and the appended `_statute_note` still says "I have
  removed the section numbers I could not confirm" when nothing was removed. My own probe output
  shows it: `rules: ('statute_unconfirmed_caveated',)` with that note attached. `_statute_note`
  needs a removed-vs-caveated-only branch.
* **`_LOCATOR_RE` is placed beside `_URL_RE` and is reusable** by C2-c3/c4 for the "does this
  sentence supply a specific holding" test.
* **`build_allowlist`'s `act_names` construction changed**, so D1-b's implementer should re-read
  that function; the `fact_keys=` line itself is untouched.
