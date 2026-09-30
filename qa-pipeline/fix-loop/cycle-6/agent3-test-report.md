# Cycle 6 — Agent 3 adversarial test report (set S5-B)

Scope: C4-1, C4-2, C2-a2, H4-2 as implemented by Agent 2.
No browser, no server, no provider call, no commit/push/branch. `safety_triage.py` and
`helplines.py` not modified.

Baseline I measured myself before writing any test:
```
cd backend && .venv/Scripts/python.exe -m pytest -q --ignore=tests/test_browser_autofill.py
1 failed, 777 passed, 9 xfailed, 3 warnings in 34.48s
FAILED tests/test_doc_generator.py::test_document_generation_notice   <- pre-existing, not mine
```

*(final summary line is at the bottom of this file)*

---

## Verdict table

| item | Agent 2 status | my status |
|---|---|---|
| C4-1 (correct rate deleted) | fixed | **partially fixed** — inline path fixed; the whole-line path still deletes correct rates and still appends the causeless note |
| C4-2 (Devanagari rate deleted) | fixed | **partially fixed** — same whole-line path, reproduced in Devanagari and Hinglish |
| C4 under-filter | not reported | **not fixed — defect of this fix.** Fabricated win probabilities in ordinary prose reach the user |
| C2-a2 (naming the real Act) | fixed for the reproducer; residue "narrowed" | **partially fixed** — covers 4 of 25 real State rent Acts, and it **widened `licensed()`**, which is a new A1-class hole |
| H4-2 (procedural advice deleted) | fixed | **partially fixed** — the reproducer is fixed, but `_LOCATOR_RE` reads any 6-digit rupee amount as a PIN code and deletes the same advice again |
| H4-2 hoist (3 new firings) | 3, all desirable | **confirmed 3, all desirable** (one cosmetic language-mismatch nit) |
| A1 not re-opened | verified | **confirmed for corpus-less domains**; re-opened in corpus-ful domains by the `licensed()` widening above |
| locks L1–L17 + idempotency | met | **confirmed** |

---

## C4-1 / C4-2

**Problem.** `_apply_outcome_probability` decided from `body[start-70:end+70]`, so any outcome word
within 70 characters deleted the percentage and appended `_outcome_note`.

**Fix Agent 2 made.** `_WINDOW` and the slice deleted. New `_percent_role` returns
`measurand | probability | unknown` from the span's own grammar; inline removal fires only on
`probability`; `unknown` keeps the text and sets no `hit`. `_OUTCOME_LINE_RE` (the `label: value`
whole-line rule) was **left unchanged in shape** and given a `_percent_role` veto.

### Status: PARTIALLY FIXED — four distinct defects remain

#### (a) NOT FIXED — `_OUTCOME_LINE_RE` is `_WINDOW` reincarnated as a 90-character label window

`_OUTCOME_LINE_RE` (unchanged this cycle — verified with `git show HEAD:backend/app/services/response_guard.py`) is

```
^[\s>*_{DASH}+]*(?:\d+[.)]\s*)?(?P<label>[^:\n]{0,90}):?[\s*_]*(?:APPROX\s*)?\d{1,3}(?:\.\d+)?\s*%[\s*_.)।]*$
```

The colon is **optional** (`:?`) and `label` is `[^:\n]{0,90}`. So this is not "a `label: value` line"
at all — it is **"any line ending in a percentage whose preceding <=90 characters contain an outcome
word"**. `_apply_outcome_probability` then tests the label with
`_OUTCOME_WORD_RE.search(line_match.group("label"))` — token co-occurrence inside a character window,
the exact mechanism this cycle exists to remove. DoD #4 is met textually (`grep _WINDOW` is clean) and
**not met in substance**.

Because the regex anchors the `%` at end of line, **no measurand can ever follow the sign**, so
Agent 2's step-1 veto on this branch can never fire. Agent 2 records this as deviation 2 ("defensive,
not live"). The correct conclusion is the opposite: a veto that can never fire means this branch has
**no assertion test at all**, and the whole line — not merely the number — is deleted.

Reproduced, CONSUMER profile. All 8 deleted whole-line, all 8 with the causeless note appended:

```
REMOVED  If you win, the recoverable interest is 18%.
REMOVED  Interest you can claim on a favourable order: 18%
REMOVED  GST charged on the service if you win: 12%
REMOVED  **Interest on a favourable order:** 18%
REMOVED  Your chances improve if the notice demands a refund of 100%
REMOVED  Likelihood aside, the statutory interest rate is 9%
REMOVED  - Deposit you must pay to win the auction: 50%
REMOVED  Success rate of mediation aside, the agreed penalty is 2%
```

**Agent 2's deviation 2 is wrong on its own example.** It claims
`**Interest on a favourable order:** 18% per annum` "never reaches the line rule at all". True — but
only because `per annum` pushes the `%` off end-of-line. Drop those two words and the identical claim
is destroyed:

```
KEPT     '**Interest on a favourable order:** 18% per annum'
REMOVED  '**Interest on a favourable order:** 18%'          rules=('outcome_probability',)
```

Worst realistic form — a markdown rate list, which is how the product formats rates. Input:

```
- **Security deposit refundable:** 100%
- **Statutory interest if you win:** 18%
- **GST on the service you can recover on a favourable order:** 12%
- **Contractual penalty per month:** 2%
```

Output: **two of four bullets are gone**, and the reply gains
`On the chance of winning: the strength of a case cannot be reduced to a number...` although it made
no probability claim. That is C4-1's original **double fault** — a correct-law deletion *plus* a
causeless caveat — reproduced after the fix that reports it closed.
Severity: **correct law silently deleted** (undetectable by the user) **plus a self-contradiction
shipped**.

#### (b) NOT FIXED — C4-2 in the same shape

The Devanagari reproducer passes only because `ब्याज` follows the sign. Move
the measurand into the label — the normal Hindi/Hinglish rate layout — and it is deleted:

```
REMOVED  'Jeetne par milne wala byaj: 18%'
REMOVED  'जीतने पर मिलने वाला ब्याज: 18%'
```

#### (c) NOT FIXED — the under-filter, wider than the orchestrator measured

Orchestrator measured 4 of 8 surviving. I measured **10 of 10** surviving in ordinary English prose
and **4 of 6** in Hinglish/Devanagari prose. Every one is a fabricated win probability reaching the
user — C4's original harm class.

```
KEPT  Rough chance of a favourable outcome: about 30% in your case.
KEPT  Your chance of a favourable order is roughly 30% here.
KEPT  Success rate in such matters is about 55% in my estimate.
KEPT  Probability of recovery: 25% given the evidence you hold.
KEPT  Realistically your chances of winning this are close to 60% today.
KEPT  In my view the likelihood of a favourable order sits near 70% for you.
KEPT  Based on what you have told me, the odds of success come to nearly 65% overall.
KEPT  I estimate the probability that the Commission rules for you at 80% or so.
KEPT  Given the documents, your chance of winning is 30% at best.
KEPT  Win rate for these complaints in Jaipur is about 45% historically.

KEPT  Aapke jeetne ki sambhavna is case mein takriban 30% hai.
KEPT  Is case mein safalta ki sambhavna karib 40% ke aaspaas hai.
KEPT  आपके जीतने की संभावना इस मामले में लगभग 30% है।
KEPT  सफलता की संभावना इस केस में करीब 45% रहती है।
```

The two Hinglish/Devanagari misses are **not** a vocabulary gap: `sambhavna` and
`संभावना` are both in `_PROB_NOUN`. They fail because
`_PROB_BEFORE_RE` admits only copula/approximator tokens in the gap, and `is case mein` /
`इस मामले में` are not in `_PROB_BRIDGE`. The two
strings Agent 2 cites as proof are the *shortest possible* Hindi form; any adverbial between the noun
and the number defeats the test.

Also measured: **only the co-occurrence window catches ranges.** `Your chance of a favourable order
is 30-40%.` and `The likelihood is somewhere between 30% and 40%.` are removed — but by the
`_OUTCOME_LINE_RE` label window of defect (a), not by any assertion test. So the guard's remaining
correct behaviour on ranges rests on the mechanism this cycle set out to delete; fixing (a) without
fixing (c) would make the under-filter strictly worse.

Non-`%` probability forms are not covered at all (pre-existing, not a regression of this fix, but the
same harm and freely emitted by an LLM). All kept:
`one in three`, `50-50 chance`, `even odds`, `Two out of five such complaints succeed`,
`a one-in-four chance`, `Chances are about 3 in 10`, `seventy percent chance of winning`,
`Odds are roughly 2:1 in your favour`.

#### (d) NOT FIXED — the measurand-after veto is directly abusable

The fix's premise is "a measurand after the sign means rate, keep". Put a probability noun *after* a
measurand noun and the fabricated probability survives. This is not contrived: it is **W1-06 t4's own
line with two words swapped.**

```
fixture line, removed :  - **Tour‑operator refund:** ~30 % chance,
reshaped,     KEPT    :  - **Tour-operator refund:** 30% refund chance, limited by limitation.
```

More, all KEPT with `rules=()`:
```
I estimate a 65% refund chance in your matter.
There is a 40% refund chance if you file within the limitation period.
Your success rate here is 55% in value terms.
Odds of winning are about 60% on the deposit refund claim.
I would put your chance of winning at 45% on the amount claimed.
Your odds of a favourable order are 70% of the value at stake.
```

`_MEASURAND` contains `value|amount|share|stake|price|cost|margin|fees|charges|rent|refund...` and
`_MEASURAND_AFTER_RE` permits up to three `of|the|on|in|a|an|as|ka|ki|ke` bridge tokens, so a
probability claim need only be followed by a quantity noun within four words to become unkillable.
Severity: **a hallucination reaching the user**, in the guard's flagship harm class.

**The converse direction is safe.** A legitimate rate whose measurand is *not* immediately after the
number takes the `unknown` verdict and is kept — verified:
`The contract sets 18% as the yearly rate if you win the claim.`,
`If you win, 12% is the GST component you can recover.`,
`Interest at 18%, calculated per annum, is recoverable on a favourable order.`
all kept with `rules=()`. That direction only becomes unsafe when the line *also* ends in the
percentage, i.e. defect (a).


---

## C2-a2 — naming the real applicable Act

**Problem.** `_classify`'s last branch read "this Act name is in no allowlist" as *disproof*, so
naming the Act that actually governs the case made deletion more likely than naming nothing.

**Fix Agent 2 made.** (a) `_act_alternatives` splits a corpus label on `/` and ` or ` before
normalising, and `build_allowlist` registers each alternative in `act_names` **and in
`verified_pairs`** via `_pair()`. (b) new `_act_family` reduces both sides to a family key
(strip State/UT or `model|state|central|union` qualifier, strip trailing year, singularise) and
compares by exact key equality, with a >=2-word / >=10-character floor. Used on the reject side
only.

### Status: PARTIALLY FIXED — the reproducer flips, but two real problems

#### (a) NEW A1-CLASS HOLE — edit (a) widened `licensed()`, the accept path

Agent 2's code comment states, and DoD #7 repeats, that `licensed()` "is not loosened". Measured,
it is. Registering the split alternatives in `verified_pairs` puts the **short** key
`model tenancy act` on the accept path, and `licensed()` compares Act names by *substring
containment* (`if named in known or known in named`). So any Act name containing
`model tenancy act` now rides on the corpus's sections 11 / 15 / 21 / 30.

I reconstructed the pre-cycle-6 allowlist (one `_norm_act` per label, no split) with
`dataclasses.replace` and compared the two directly:

```
s.11   rajasthan model tenancy act 2022        OLD licensed=False  NEW licensed=True
s.11   jaipur model tenancy act 2024           OLD licensed=False  NEW licensed=True
s.30   rajasthan model tenancy act 2022        OLD licensed=False  NEW licensed=True
s.11   fictional model tenancy act of narnia   OLD licensed=False  NEW licensed=True
s.11   model tenancy act 2021                  OLD licensed=False  NEW licensed=True
s.11   state rent control acts                 OLD licensed=True   NEW licensed=True   (unchanged)
s.13   rajasthan rent control act 1950         OLD licensed=False  NEW licensed=False  (unchanged)
```

End to end:
```
IN    Section 11 of the Rajasthan Model Tenancy Act, 2022 requires the deposit back in one month.
rules ('model_law_adoption_caveat',)          <- no statute rule at all; the citation is ACCEPTED
```
Rajasthan has not enacted a Model Tenancy Act. Before this cycle that sentence was **stripped**;
now the fabricated Act name is accepted as verified law. `licensed()`'s own docstring gives the
rule being broken: *"the (Act, section) pair must be one the product vouches for. This is what
stops 'Section 73 of the Payment of Wages Act, 1936' riding on Indian Contract Act s.73."*

**Mitigating, honestly:** the `model_law_adoption_caveat` does fire and its copy is good — *"yeh ek
central model law hai jise har State ko khud enact karna padta hai. Mere paas iski koi pushti nahi
hai ki yeh Rajasthan mein laagu hai..."* — so the user is told not to rely on it in Rajasthan. That
drops the severity from "fabrication presented as verified, unwarned" to "fabricated Act name kept
with a caveat that happens to cover it". The Act name itself is never flagged.

**Blast radius is bounded and measurable.** Only **one** of the 3 corpus `act` labels contains a
disjunction, and its alternatives are `model tenancy act` (17 chars) and `state rent control acts`,
so no dangerously short key was created. Sweep of every corpus label for split-created keys under
14 characters: **none**. The hole is exactly the `<anything> Model Tenancy Act <anything>` shape.

Agent 2's A1 verification was sound but incomplete: it probed only `corpus_available == False`
domains, which edit (a) cannot affect. The widening is in **corpus-ful** domains, which it did not
probe.

#### (b) PARTIALLY FIXED — the residue is far wider than Agent 2 states, and one stated example is wrong

`_act_family` recognises only the literal `<State> Rent Control Act` naming style. Measured across
25 real Indian State rent statutes: **4 recognised, 21 stripped.** End-to-end on 14 of them
(`Section 13 of the <Act>` under `tenancy_profile()`), **7 are deleted outright**:

```
caveated  Delhi Rent Control Act, 1958                                   key 'rent control act'
caveated  Maharashtra Rent Control Act, 1999                             key 'rent control act'
caveated  Rajasthan Rent Control Act, 1950 / 2001                        key 'rent control act'
caveated  Chhattisgarh Rent Control Act, 2011                            key 'rent control act'
DELETED   Karnataka Rent Act, 1999                       key ''  (8 chars < the 10-char floor)
DELETED   West Bengal Premises Tenancy Act, 1997         key 'premises tenancy act'
DELETED   Madhya Pradesh Accommodation Control Act, 1961  key 'accommodation control act'
DELETED   Punjab Urban Rent Restriction Act, 1949        key 'urban rent restriction act'
DELETED   Himachal Pradesh Urban Rent Control Act, 1987  key 'urban rent control act'
DELETED   Odisha House Rent Control Act, 1967            key 'house rent control act'
DELETED   Uttar Pradesh Urban Premises Tenancy Act, 2021 key 'urban premises tenancy act'
```

Two things worth Agent 4's attention:

* **`Uttar Pradesh Urban Premises Tenancy Act, 2021` is UP's enactment of the Model Tenancy Act** —
  precisely what HOUSING_TENANT's corpus is about — and it is deleted.
* **`Karnataka Rent Act, 1999` is deleted by the guard rail itself.** Its family key `rent act` is
  8 characters, under the 10-character floor Agent 1 required. The floor is doing real damage to
  short-named Acts, not only to degenerate ones.

**Agent 2's own headline example of the residue is wrong.** It states
`Bihar Buildings (Lease, Rent and Eviction) Control Act, 1982` "still strips". Measured, it is
**caveated, not stripped** — `ACT_NAME_RE` cannot match across `(Lease, ...)`, so no Act is attached
at all and `_classify` returns `"doubtful"` by the parenthesis blind spot. The same accident saves
`Rajasthan Premises (Control of Rent and Eviction) Act, 1950`,
`Kerala/Tamil Nadu/AP/Telangana Buildings (Lease and Rent Control) Act` and
`Tamil Nadu Regulation of Rights and Responsibilities of Landlords and Tenants Act, 2017`. So the
residue lands entirely on **un-parenthesised** State Act names, which is the set above. The
practical shape of the remaining perverse incentive is therefore:

> A parenthesised formal Act name survives by accident. A plain `<State> Rent Control Act` survives
> by design. Every other plain name is deleted. `Rajasthan Premises (Control of Rent and Eviction)
> Act, 1950` (the formal name) is caveated; `Uttar Pradesh Urban Premises Tenancy Act, 2021` (also
> a formal name, no parentheses) is deleted. The decision tracks naming style, not applicability.

#### (c) Family-key collisions — real, but they fail in the safe direction

A fabricated Act that reduces to a corpus family becomes `doubtful` (keep + caveat) instead of
`unverified` (strip):
```
kept+caveat   Section 99 of the Union Consumer Protection Act, 2024
kept+caveat   Section 99 of the Central Consumer Protection Act, 2024
kept+caveat   Section 99 of the Rajasthan Consumer Protection Act, 2024
kept+caveat   Section 99 of the Government Information Technology Act, 2024
kept+caveat   Section 99 of the State Indian Contract Acts, 2024
stripped      Section 99 of the Consumer Protection Rules, 2024     (Rules != Act: no collision)
stripped      Section 99 of the Code on Wages, 2019                 (lock holds)
```
`_ACT_QUALIFIER_RE` strips `model|state|central|union|government|govt` and `_STATE_PREFIX_RE`
strips every `STATE_VARIANTS` surface form, so those five collide by construction. Effect is
`unverified -> doubtful`, i.e. keep + caveat, never accept — the stated trade in the orchestrator's
ground truth §3. **Not a defect.** No path was found where `_act_family` turns a reject into an
accept: it is called only at `response_guard.py:936,938`, inside `_classify`, after the
`corpus_available` early return.

#### (d) C2-a3 self-contradiction is now on a hot path

Every citation the C2-a2 fix rescues takes the caveat-only branch, and the note there says the
opposite of what happened:

```
IN     Section 13 of the Rajasthan Rent Control Act, 1950 lists the eviction grounds.
rules  ('statute_unconfirmed_caveated',)
note   "...isliye jo section numbers main confirm nahi kar saka unhe hata diya hai."
       ("so I have removed the section numbers I could not confirm")   <- nothing was removed
```

Agent 2 flagged this for S5-C and it is already covered by the strict xfail
`test_statute_note_does_not_claim_a_removal_that_never_happened`, so it cannot close silently. What
is new is the **frequency**: it now fires on all four recognised State rent Act families and on
every family collision in (c). A self-contradiction shipped, on a path cycle 6 deliberately widened.

### A1 — verified independently

`_classify` returns `"unverified"` before the named-Act logic whenever `corpus_available` is False,
and I probed all four corpus-less domains (EMPLOYMENT, CYBER_FRAUD, POLICE_COMPLAINT, GENERAL) with
9 strings each, Act named and unnamed. All four of A1's own strings (Industrial Disputes s.35,
Payment of Wages s.73, Police Act s.20, Code on Wages s.2(11)) redact in **all four** domains.
`corpus_available=False` in all four, and `_act_family` is unreachable there. **A1 holds in
corpus-less domains.**

One unrelated observation, **pre-existing and out of scope**: `Section 35 of the Consumer Protection
Act, 2019` and `Section 66D of the Information Technology Act, 2000` pass unredacted in EMPLOYMENT,
POLICE_COMPLAINT and GENERAL, because the document-citation map registers every domain's
`(act, section)` pairs globally. Those are real provisions and L7 requires them to survive, so this
is arguably intended; it predates cycle 6 (those labels contain no `/`, so the split cannot touch
them) and I am not filing it as an S5-B defect.

---

## H4-2 — the constructed-authority rule

**Problem.** Branch 1b deleted a sentence when `_CONSTRUCTED_AUTHORITY_RE` and `_WEB_CLAIM_RE`
merely co-occurred on a line, with no test of whether the sentence *supplied* a location; and
branch (1) was gated on a `model_law_caveat` computed over the whole reply.

**Fix Agent 2 made.** New `_LOCATOR_RE` (URL, bare hostname, PIN code, street-address shape);
branch 1b removes a sentence only if it supplies a locator, and stands down silently otherwise.
Branch (1) hoisted into `_apply_constructed_authority`, called ungated from `guard_reply`.

### Status: PARTIALLY FIXED

#### (a) The reproducer is genuinely fixed

`Confirm the exact address of the Jaipur Rent Authority on its official website before filing.`
survives, `rules=()`, standalone and inside the reproducer's three-sentence wrapper.

#### (b) NOT FIXED — `_LOCATOR_RE`'s PIN alternative is any 6-digit run

`\b[1-9]\d{5}\b`. Every rupee figure from 1,00,000 to 9,99,999 — the ordinary range of a rent
deposit or a consumer claim — reads as a locator. Branch 1b then deletes the sentence, and the
sentence it deletes is exactly the advice the fix exists to protect:

```
REMOVED  locator='250000'  Confirm on the Jaipur Rent Authority website whether the 250000
                           deposit you paid crosses the pecuniary limit.
REMOVED  locator='120000'  Check the Jaipur Rent Authority website yourself; your claim of
                           120000 rupees may need a different forum.
REMOVED  locator='300000'  You should verify on the Rajasthan Rent Tribunal website whether
                           the 300000 claim is within its limit.
```

3 of my 6 legitimate-advice probes were deleted solely because they mention the user's own amount.
So H4-2's fix holds only while the reply does not also quote a lakh-range figure — which, in a
tenancy deposit case, it almost always does. This also sits next to L15 ("the user's own digits are
never touched"): the digits are not edited, the whole sentence carrying them is deleted, which is
worse and which L15 does not catch. Severity: **correct advice silently deleted.**

The street-address alternative also fires on a legitimate mixed sentence
(`Look up the Jaipur Rent Authority website for the fee, then visit the office at 5 Janpath,
Jaipur.` → removed). That one is arguably right — the address *is* fabricated — but the fee advice
goes with it, because `_sentences()` is the unit of removal and there is no partial-removal path.

#### (c) The omitted form/page-name signal does let fabrications through — but the plan specs that

7 of 7 fabricated portal specifics that supply no locator survive with `rules=()`, i.e. no deletion,
no caveat, and no trace for anything downstream:

```
KEPT  Download Form II from the Jaipur Rent Authority portal.
KEPT  The Jaipur Rent Authority portal has a dedicated online module for deposit refund complaints.
KEPT  Apply through the 'Tenant Grievance' section of the Jaipur Rent Authority website.
KEPT  The Jaipur Rent Authority website lets you file Form C online and gives you a case number the same day.
KEPT  Upload the rent agreement in the **Deposit Refund** tab on the Jaipur Rent Authority portal.
KEPT  Registration on the Jaipur Rent Authority portal takes two working days and costs five hundred rupees.
KEPT  The Jaipur Rent Court website accepts e-filing between 10 am and 5 pm only.
```

The quoted `'Tenant Grievance'` and bolded `**Deposit Refund**` cases are precisely the signal
Agent 2 dropped. **Agent 2's justification is factually correct**: the plan's own prototype table
requires `Download Form II from the Jaipur Rent Authority portal.` to be KEPT, so a form-name signal
would contradict the measured spec. I have therefore **not** filed a strict-xfail reproducer for
this — doing so would encode a spec change I am not authorised to make. Agent 4 should decide
whether the plan's table was right: "stand down silently" means fabricated form names, tab names,
fees, timings and turnaround claims reach the user with nothing recorded at all.

Related pre-existing narrowness, one line for completeness: `_CONSTRUCTED_AUTHORITY_RE` matches only
`<Place> Rent Authority|Rent Court|Rent Tribunal`, so
`The Jaipur Rent Control Office website lists its address as 5 Janpath, Jaipur 302005.`,
`The Rajasthan Housing Board portal is at housing.<host> ...` and
`File online on the Jaipur District Consumer Commission website at consumer.<host>` all survive
untouched **even though they supply a locator**. Not a cycle-6 regression; relevant because
C2-c3/c4 will inherit `_LOCATOR_RE` and may inherit this blind spot with it.

#### (d) The hoist — Agent 2's count of 3 is correct

I recomputed the pre-hoist gate (`cited_model_law and allow.model_law_sections and not
adoption_confirmed(user_state)`) per fixture x profile and compared it with where
`constructed_authority_contact` fires now, over 8 fixtures x 4 profiles:

```
W1-02-t2  HOUSING_TENANT  gate=True   pre-existing
W1-02-t2  CONSUMER        gate=False  NEW
W1-02-t2  CYBER_FRAUD     gate=False  NEW
W1-02-t2  EMPLOYMENT      gate=False  NEW
=> 3 new firings
```

**Verified: exactly 3, and all three are desirable.** They are branch 1a (`website_claim`): the
fabricated `(Jaipur Municipal Corporation ya Rajasthan State Rent Authority ke website par mil
sakta hai)` parenthetical is replaced and `**Jaipur Rent Authority ya District Court**` is kept. The
hoist closes a hole in which that same fabrication survived whenever the case had no model-law
corpus. Agent 1's "zero new firings" claim is wrong and Agent 2's correction is right.

One cosmetic nit, reported once and not padded: under the non-tenancy profiles the replacement copy
is English (`(you will need to confirm the applicable rent authority or civil court yourself)`)
inside a Hinglish reply, because `_style()` reads `language_style` from the profile and the
non-tenancy probe profiles do not set it. Real production profiles do, so this is unlikely to be
user-visible.

---

## The spurious-note fault

* **`_outcome_note`.** Correctly suppressed on the `unknown` verdict — `guard_reply("A favourable
  order can include 18% per annum interest on the refund.", CONSUMER).redactions == ()` and the note
  is absent. **But it is still appended without a cause via the whole-line rule**: all 8 strings in
  C4 defect (a) collect it, and the markdown rate list collects it after losing two correct bullets.
  So "no caveat without a cause" is met on the path Agent 2 rewrote and **not met** on the path it
  left alone.
* **`_statute_note`.** Still says "I have removed the section numbers I could not confirm" on the
  caveat-only path where nothing was removed — C2-a3, covered by an existing strict xfail, and now
  reached far more often (see C2-a2 (d)).
* **`_adoption_note`.** Appended only under `model_law_caveat`, and the `model_law_adoption_caveat`
  redaction is recorded only if `_append_once` actually changed the text. No causeless firing found.

## Preservation locks L1–L17 and idempotency — all confirmed

Verified independently of the suite:

* **L4** W1-06 t4: exactly **4** `outcome_probability` redactions, `"%" in result.text` is False,
  and `Which one to chase first?`, `Effect on success chance`, `₹\u202f62,000`, `₹\u202f90,000`,
  `₹\u202f50\u202flakh` all kept (the fixture writes the rupee amounts with U+202F; a retyped
  `₹50 lakh` does not match, which is itself a reminder to read fixture bytes).
* **L1 / L2** W1-02 t2: the two `Bengaluru` lines kept byte-for-byte under both HOUSING_TENANT and
  CONSUMER. The `Jaipur` lines change only in the fabricated parenthetical and the
  `(opposite_party_name)` fact-key scrub, as intended.
* **L6 / L7** verified via the suite (`test_consumer_corpus_provisions_survive_every_plausible_phrasing`,
  `test_tenancy_corpus_provisions_survive_with_a_caveat_never_a_deletion`,
  `test_document_citation_map_provisions_survive_in_their_own_domain`) — all green.
* **L8** the ~300 composer x domain no-op combinations — green.
* **Idempotency:** `guard(guard(x)) == guard(x)` on my 9 worst adversarial strings x 4 profiles =
  **36/36**, including the cases that append a note. Nothing double-appends.
* **L16 / byte-faithfulness:** the production label is plain ASCII
  `Model Tenancy Act / State Rent Control Acts` (`app/data/model_tenancy_provisions.json:4`), which
  matches the tests' `TENANCY_SOURCES`, so `_act_alternatives` splitting on `\s*/\s*` is safe. (It
  would also be safe against a U+202F slash, since `\s` matches U+202F.)
* **L17:** `safety_triage.py` mtime 2026-09-29T18:13, `helplines.py` 2026-09-30T12:54, both before
  `response_guard.py` 2026-09-30T18:51. Neither modified by me either.

### One existing test found to be vacuous (byte-faithfulness finding)

`tests/test_cycle4_adversarial.py::test_c2_recorded_case_law_citation_is_removed` asserts
`"(2020) 6 SCC 123" not in result.text`, `"M. S. R. Enterprises" not in ...`,
`"v. State of Karnataka" not in ...` — but W1-03 t3 writes all three with **U+202F**, so those three
assertions can never fail whatever the guard does. Only the fourth
(`"Supreme Court case you can quote"`) is real. `tests/test_response_guard.py:118` covers the same
behaviour correctly because it folds the code points first with `loose()`. I did **not** touch the
existing test; I added a passing lock
(`test_c2_recorded_case_law_is_removed_in_the_fixtures_own_bytes`) that asserts the same removal in
the fixture's own bytes, so the file no longer depends on a retyped spelling.

---

## New reproducers added (all in `backend/tests/test_cycle4_adversarial.py`, appended only)

| test | params | item | class |
|---|---|---|---|
| `test_c4_a_rate_at_the_end_of_a_line_survives_an_outcome_word_in_its_label` | 6 | C4-1 residue | correct law deleted + causeless note |
| `test_c4_a_devanagari_rate_survives_when_its_measurand_sits_in_the_label` | 2 | C4-2 residue | correct law deleted |
| `test_c4_a_markdown_rate_list_keeps_every_rate_and_earns_no_probability_note` | 1 | C4-1 double fault | correct law deleted + self-contradiction |
| `test_c4_a_win_probability_in_ordinary_prose_is_still_removed` | 9 | C4 under-filter | hallucination reaching the user |
| `test_c4_a_measurand_after_the_number_does_not_rescue_a_win_probability` | 7 | C4 veto abuse | hallucination reaching the user |
| `test_c2_a2_a_real_state_rent_act_outside_the_corpus_family_name_is_not_deleted` | 7 | C2-a2 residue | correct law deleted |
| `test_c2_a2_a_fabricated_state_model_tenancy_act_is_not_accepted_as_verified` | 3 | C2-a2 `licensed()` widening | hallucination reaching the user |
| `test_h4_2_a_six_digit_rupee_amount_is_not_a_locator` | 3 | H4-2 `_LOCATOR_RE` | correct advice deleted |
| `test_c2_recorded_case_law_is_removed_in_the_fixtures_own_bytes` | 1 | byte-faithfulness | **passing lock**, not a gap |

All eight gap reproducers are `@pytest.mark.xfail(strict=True)`, so a later fix reports a FAILURE
and cannot close silently. No existing test was weakened, skipped, renamed or deleted; the only edit
to an existing file is an append.

---

## Ranked for Agent 4

1. **C4 under-filter + measurand-veto abuse** — a fabricated win probability reaching the user, in
   the guard's own flagship harm class, in the most natural prose forms. 16 of 16 probes survive.
   The orchestrator's suggested shape (probability noun anywhere earlier in the clause, with
   measurand-after as the veto) fixes the under-filter but **not** the abuse in (d), because there
   the measurand veto still wins. The veto needs to lose to an attached probability noun, not beat
   it.
2. **C4-1 `_OUTCOME_LINE_RE` whole-line rule** — correct law silently deleted plus a causeless
   caveat, i.e. the exact double fault this cycle reported closed. It is also the last surviving
   character-window decision in the module, so DoD #4 is not met in substance. Fixing it requires
   fixing (1) first, or the range forms stop being caught at all.
3. **C2-a2 `licensed()` widening** — `<State> Model Tenancy Act <year>` accepted as verified where
   it was previously stripped. Bounded to one label and mitigated by the adoption caveat, but it is
   a regression on the accept path and Agent 2's claim that `licensed()` is unchanged is wrong.
4. **H4-2 PIN over-match** — correct advice deleted whenever the reply quotes a lakh-range amount.
   One-character-class fix; high frequency in tenancy cases.
5. **C2-a2 residue** — 21 of 25 real State rent Acts still stripped, including UP's Model Tenancy
   Act enactment, and `Karnataka Rent Act, 1999` stripped by the 10-character floor itself. This is
   the product decision already handed to the user; the new information is its width (4 of 25, not
   "one Bihar Act") and the fact that Agent 2's stated example is caveated rather than stripped.

---

## Final test run

```
cd backend && .venv/Scripts/python.exe -m pytest -q --ignore=tests/test_browser_autofill.py
1 failed, 778 passed, 47 xfailed, 3 warnings in 36.38s
FAILED tests/test_doc_generator.py::test_document_generation_notice
```

Arithmetic against the baseline `1 failed, 777 passed, 9 xfailed`:

* **passed 777 -> 778.** +1 is the new passing byte-faithfulness lock. **Nothing was lost.**
* **xfailed 9 -> 47.** +38 is the 8 new reproducers (6 + 2 + 1 + 9 + 7 + 7 + 3 + 3 params), all
  `strict=True`, so each reports a FAILURE the moment it is fixed.
* the one failure is still only `test_doc_generator.py::test_document_generation_notice`,
  pre-existing and unrelated.
* no browser, no server, no provider call, no network. No commit, no push, no branch.
* files touched: `backend/tests/test_cycle4_adversarial.py` (**append only** — plus one docstring
  made raw to clear a `DeprecationWarning` I introduced) and this report.
  `app/services/response_guard.py`, `safety_triage.py` and `helplines.py` untouched by me.

## Verdict per S5-B item

| item | verdict |
|---|---|
| **C4-1** | **partially fixed** — inline path genuinely fixed; `_OUTCOME_LINE_RE`'s 90-char label window still deletes correct rates whole-line and still appends the causeless note. The plan's own example `**Interest on a favourable order:** 18%` is deleted. |
| **C4-2** | **partially fixed** — same whole-line path, reproduced in Hinglish and Devanagari. |
| **C4 (new fault)** | **not fixed** — the fix under-filters. 16 of 16 fabricated win probabilities reach the user across English prose, Hinglish, Devanagari, and the measurand-veto abuse, which is W1-06 t4's own line with two words swapped. |
| **C2-a2** | **partially fixed** — the reproducer flips, but `licensed()` was widened (a new A1-class hole on the accept path) and the residue is 21 of 25 real State rent Acts, not one Bihar Act. |
| **H4-2** | **partially fixed** — the reproducer flips and the hoist is right (3 new firings, all desirable, count verified independently). But `_LOCATOR_RE`'s PIN alternative is any 6-digit run, so the same advice is deleted again whenever the reply quotes a lakh-range amount. |
