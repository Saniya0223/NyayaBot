# Cycle 2 — Agent 3 independent verification

Adversarial pass over Agent 2's six fixes. All probes are deterministic: `assess_safety()` is
called directly, and every replay uses `NoCallProvider`, whose `.calls` is asserted empty. **No
Groq/LLM call was made.** New tests live only in `backend/tests/test_cycle2_verification.py`; no
existing test file was touched, and nothing was fixed, committed or pushed.

Status: **complete** — P1, P2, P3, P4 all probed.

---

## CF-2 — non-personal-threat denylist inverted to a complement rule

**Verdict: PARTIALLY FIXED** (9/10 of the prescribed corpus correct; one false negative)

Probe: `assess_safety(m).is_safety_case` on the 10-string corpus.

| string | expected | actual |
|---|---|---|
| `threatened to kill my brother` | safety | **True** — `['threat']` |
| `dhamki di ki tumhe maar dunga` | safety | **True** — `['dhamki','future_harm']` |
| `threatened to hurt my children` | safety | **True** — `['threat','dependants']` |
| `threatened to come to my house tonight` | safety | **False — `[]`** ❌ |
| `he said he will beat me if I complain` | safety | **True** — `['physical_violence']` |
| `threatened to file a police case` | not safety | **False** ✅ |
| `threatened to report me to the bank` | not safety | **False** ✅ |
| `threatened to blacklist my firm` | not safety | **False** ✅ |
| `dhamki di ki naukri se nikal dunga` | not safety | **False** ✅ |
| `threatened to take me to court` | not safety | **False** ✅ |

The inversion is a real improvement: **all five** non-personal strings are dropped, including
`threatened to report me to the bank` and `threatened to take me to court`, which contain the
person-object `me` and would defeat a naive "is a person mentioned" rule. The old literal denylist
had no entry for any of these five. The complement rule is doing the work it was designed to do.

**The one failure is structural, not a missing list entry.** In
`_drop_non_personal_generic_threats` (safety_triage.py:~330) rule 3 keeps a complement only when it
contains a PERSON **and** a `_PERSON_HARM_VERB`. That verb set is closed and contains only
harm verbs (`kill|murder|beat|hit|attack|assault|harm|hurt|injure|stab|shoot|burn|rape|molest|
abduct|kidnap|acid`, `maar`, `jaan se`, …). **An approach / stalking threat names no harm verb**, so
complement `come to my house tonight` falls to rule 4 and is dropped.

Isolation (proves it is the complement rule, not a missing signal):

```
sc=False  threatened to come to my house tonight
sc=False  he threatened to come to my house tonight
sc=True   my husband threatened to come to my house tonight   <- partner context rescues it
sc=True   he threatened to come to my house tonight and kill me <- harm verb rescues it
sc=False  he said he will come to my house tonight
```

So the gap is exactly: *a threat to approach the user, by an actor who is not an identified
intimate partner.* Coming to someone's house at night is the standard stalking/intimidation
escalation pattern and is a safety case. Logged below as **N2-1 (MEDIUM)**.

---

## CF-3 — positional third-party attribution test

**Verdict: FIXED**

Probe: `assess_safety(m).self_harm` / `.stage` / `.contexts`.

```
self_harm=True   stage=CRISIS_SUPPORT  my landlord is horrible, I want to end my life
self_harm=False  stage=NONE            she said he threatened to kill himself
self_harm=False  stage=NONE            my tenant threatened to commit suicide if I evict him
self_harm=False  stage=NONE            I told my doctor I wanted to die
```

Three of the four are exactly right. The user's own ideation survives an adjacent third-party
clause; both genuinely-third-party sentences are suppressed.

**Judgement on the fourth — `I told my doctor I wanted to die`.** This *should* be the user's
crisis: the speaker is disclosing their own past ideation, and cycle 1 already locked past-tense
disclosure as a crisis (`last week I tried to kill myself` is a live regression test). But the
miss is **not** caused by CF-3. Isolation:

```
self_harm=True   I told my doctor I want to die          <- attribution test correctly NOT applied
self_harm=False  I wanted to die                         <- fails with no third party at all
self_harm=True   I wanted to end my life
self_harm=True   yesterday I wanted to kill myself
self_harm=True   I told the police I want to kill myself
```

`I told my doctor I want to die` fires, so `_third_party_attributed` is correctly declining to
suppress a first-person clause after a reporting verb. The bare `I wanted to die` fails on its own.
The cause is the `want_to_die` pattern at safety_triage.py:95:

```python
("want_to_die", r"\b(?:want|wanna|wish) to die\b|\bbetter off (?:dead|without me)\b|\bi should (?:just )?die\b"),
```

`want` has no past-tense alternation, while the sibling `kill_myself` pattern does. This is a
**CF-6 wordlist gap**, charged below as **N6-1 (MEDIUM)**, not a CF-3 defect.

I also ran a CF-3 **over-suppression** sweep, because a positional test that reaches too far would
silently swallow real crises sitting after a quoted clause. It does not:

```
self_harm=True  my wife said the loan is my problem, I want to kill myself
self_harm=True  he told me to get lost, honestly I want to end my life
self_harm=True  she said she will leave me and I want to die
self_harm=True  my boss said I am fired. main khudkushi kar lunga
```

CF-3 is fixed and is not over-reaching.

---

## CF-4 — crisis-scoped intake no longer freezes the legal case

**Verdict: FIXED**

Probe: 5-turn replay — scam intro, crisis disclosure, then three fact-supplying turns.
`provider.calls == []` throughout.

```
T1 doc=False tsc=None missing=['bank_name','bank_reported','cyber_reported','incident_date','transaction_id']
T2 doc=False tsc=0    missing=['bank_name','bank_reported','cyber_reported','incident_date','transaction_id']
T3 doc=False tsc=1    missing=['bank_name','bank_reported','cyber_reported','incident_date','transaction_id']
T4 doc=False tsc=2    missing=['bank_name','bank_reported','cyber_reported']
T5 doc=False tsc=3    missing=['bank_name','bank_reported']
```

Two things are true and both are needed. First, **no attacker-oriented fact ever attaches**:
`threat_details` and `physical_violence_or_weapon` are absent on every turn, so
`safety_intake_facts_for()` is scoping the contract correctly. Second, and this is the part a
freeze-bug would fail, **the missing set actually shrinks as the user answers** (5 -> 3 -> 2). The
case is live, not pinned. Category stays `CYBER_FRAUD`; it is not relabelled by the crisis turn.

`risk_level` stays AMBER, as Agent 2 documented. Readiness stays `UNDERSTANDING_CASE` because the
remaining cyber facts are genuinely unanswered — that is the deferred S2/H7 readiness problem, not
CF-4.

---

## CF-5 — post-crisis document cooldown

**Verdict: FIXED** — and, importantly, it **expires**.

Probe: replay `[scam intro, crisis disclosure, DOC, DOC, DOC, DOC, DOC, DOC]`, asking for the
document on every turn from T3. `provider.calls == []`.

```
T1 doc=False tsc=None crisis=None
T2 doc=False tsc=0    crisis=True   <- crisis disclosed
T3 doc=False tsc=1    crisis=True   <- blocked
T4 doc=False tsc=2    crisis=True   <- blocked
T5 doc=True  tsc=3    crisis=True   <- window LIFTS at exactly CRISIS_DOCUMENT_COOLDOWN_TURNS
T6 doc=True  tsc=4    crisis=True
T7 doc=True  tsc=5    crisis=True
T8 doc=True  tsc=6    crisis=True
```

This is the answer to the question the brief asked: the block is a bounded window, not CF-4 in
another place. It engages for exactly three turns and then the legitimate cyber-crime complaint
becomes available again. The boundary is exact — `crisis_document_block_active` returns
`turns < CRISIS_DOCUMENT_COOLDOWN_TURNS`, and the observed flip is at `tsc == 3`.

Locked by `test_cf5_the_document_cooldown_blocks_then_expires`, which asserts **both** halves; the
reopen assertion carries the message "CF-5 is a permanent ban, not a bounded window".

---

## CF-1 — bounded safety loop

**Verdict: FIXED**

Probe: a deliberate 12-turn trapping attempt — threat, then active danger, then "ab main safe hun",
then nine turns of pressing the same unanswered legal question. `provider.calls == []`.

```
T1 stage=CHECK_IMMEDIATE_DANGER  asks={'immediate_danger': 1}
T2 stage=URGENT_GUIDANCE         asks={'immediate_danger': 1, 'urgent_safety': 1}
T3 stage=SAFETY_INTAKE           asks={'immediate_danger': 1, 'urgent_safety': 1, 'incident': 1}
T4 triage_complete=True          asks unchanged from here to T12
...
T12 triage_complete=True  asks={'immediate_danger': 1, 'urgent_safety': 1, 'incident': 1}  triage_question=None
```

The urgent branch fires **once** and releases on `ab main safe hun` — the `ab` inflection CF-1
added — rather than re-asking. No question group reaches 2 asks, let alone 3. `triage_question` is
`None` from T4 to T12, so the user is genuinely out of the loop and not being re-questioned.
I could not trap the user.

**However**, the 12-turn push exposes something the 3-turn cycle-1 lock cannot see: once triage
releases, the replies themselves stop varying. See **N8-R** below.

---

## CF-6 — ideation inflections

**Verdict: PARTIALLY FIXED**

The six authorised wordings all pass (they are live regression locks in
`test_common_ideation_wordings_should_be_detected`, still green in the full run). Past-tense parity
holds for the `kill_myself` family: `I wanted to end my life`, `yesterday I wanted to kill myself`
and `last week I tried to kill myself` all fire.

The one sibling pattern left behind is `want_to_die` (**N6-1**, below): `I want to die` fires,
`I wanted to die` does not. Agent 2's note binds later cycles against wordlist expansion "without
new evidence" — this is new evidence, and it is a *parity* defect within an existing pattern rather
than a new wording.

---

## New issues

### N2-1 — approach/stalking threat dropped by the complement rule - **MEDIUM**

`_PERSON_HARM_VERB` is a closed set of harm verbs. A threat whose complement states *approach*
rather than *harm* names no verb in that set, so rule 3 cannot keep it and it falls to rule 4
(DROP).

```python
assess_safety("threatened to come to my house tonight").is_safety_case     # False, expected True
assess_safety("he threatened to come to my house tonight").is_safety_case  # False
assess_safety("my husband threatened to come to my house tonight").is_safety_case      # True
assess_safety("he threatened to come to my house tonight and kill me").is_safety_case  # True
```

Scope: a threat to approach the user made by an actor who is not an identified intimate partner.
Coming to someone's home at night is the standard stalking-escalation wording and is a safety case.
Likely siblings: `aa jaunga`, `ghar aa raha hun`, "wait outside your office", "follow you home".

Reproducer: `test_cf2_an_approach_threat_is_still_a_safety_case` (strict xfail).
Boundary pinned by `test_cf2_the_approach_threat_gap_is_the_complement_rule_not_a_missing_signal`,
which will start failing if a future fix over-corrects and makes the bare sentence fire for the
wrong reason.

### N6-1 — `want_to_die` has no past tense - **MEDIUM**

safety_triage.py:95 — `r"\b(?:want|wanna|wish) to die\b|..."`. The alternation covers
`want|wanna|wish` but not `wanted|wished`, while the sibling `kill_myself` pattern does handle past
tense. A user disclosing a past ideation episode in these words is missed entirely.

```python
assess_safety("I wanted to die").self_harm                   # False, expected True
assess_safety("I told my doctor I wanted to die").self_harm  # False, expected True
assess_safety("I wanted to end my life").self_harm           # True   <- parity broken
```

Reproducer: `test_cf6_past_tense_want_to_die_is_detected` (strict xfail, 2 params).
**This is not a CF-3 defect** — `I told my doctor I want to die` fires correctly, so the positional
attribution test is behaving; the pattern is what fails.

### N8-R — offline composer repetition, reproducer restored - **MEDIUM** (offline path only)

Agent 2 recorded N8 as "no longer reproducible". It is reproducible again with a longer tail. After
triage releases, seven consecutive different questions get a **byte-identical** reply:

```
distinct replies across T4..T12: 2 of 9
  x7  'Limited demo mode — Groq is not configured, so this reply uses local workflow rules only.\n\nI have en...'
  x2  'Limited demo mode — Groq configured nahi hai, isliye yeh reply local rules use karta hai.\n\nJo hua us...'
```

This is the general `limited_demo` composer, not the safety branch (`triage_question` is `None`
throughout), so it does **not** reopen CF-1 — but a distressed user who has just left safety triage
now receives the same paragraph seven times. The cycle-1 lock
`test_active_danger_branch_must_not_repeat_the_same_question_forever` passes only because it
inspects a 3-turn tail; extend the tail and it breaks.

Reproducer: `test_cf1_released_turns_are_not_byte_identical_replies` (strict xfail). Still correctly
deferred to S3/S8, but it now has an active reproducer again and the deferral note should be updated.

### N3-1 — `process_turn` returns one mutated profile object for the whole thread - **LOW** (test-integrity)

`responses[k].case_profile` is the *same object* for every k; it is mutated in place each turn. My
first pass at the CF-4/CF-5 probes reported `tsc=4` on all six turns and `tsc=6` on all eight before
I noticed. Any assertion indexed at a mid-conversation turn silently reads **final** state.

This does not invalidate the cycle-1 conclusions — those tests happen to assert on the last turn —
but `test_w1_08_turn_three_is_answered_not_repeated`'s `responses[1].case_profile.category` check is
vacuous, and the pattern is a live trap for cycle 3. Every assertion in
`test_cycle2_verification.py` is made against a per-turn copy instead.

Reproducer (documented, passing): `test_process_turn_returns_one_mutated_profile_object_for_the_whole_thread`.

### Not a defect, noted

An ordinary `HOUSING_TENANT` case is untouched: `GREEN` / `safety_status=None` /
`crisis_support_offered` never set, across three turns. It also never reaches a document offer, but
that is the deferred S5b/H7 readiness gate, not a cycle-2 regression.

---

## Verdict summary

| item | verdict |
|---|---|
| CF-1 urgent-branch loop bounded | **FIXED** |
| CF-2 denylist inverted to complement rule | **PARTIALLY FIXED** (N2-1) |
| CF-3 positional third-party test | **FIXED** (and not over-suppressing) |
| CF-4 crisis-scoped intake | **FIXED** |
| CF-5 bounded document cooldown | **FIXED** (verified to expire at tsc=3) |
| CF-6 ideation inflections | **PARTIALLY FIXED** (N6-1) |

## Test run

New file `backend/tests/test_cycle2_verification.py` in isolation:

```
25 passed, 4 xfailed in 0.87s
```

Full suite — `cd backend && .venv/Scripts/python.exe -m pytest -q`:

```
1 failed, 449 passed, 4 xfailed, 3 warnings in 55.26s
FAILED tests/test_doc_generator.py::test_document_generation_notice   <- pre-existing, unrelated
```

424 -> 449 passed (+25). The single failure is the pre-existing `test_doc_generator` one, unchanged.
No strict xfail XPASSed, so all four new reproducers are genuinely still broken. No existing test
was modified, nothing was fixed, and nothing was committed or pushed.
