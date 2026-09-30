# Cycle 6 — Orchestrator ground truth (verified against evidence and source)

Written while Agent 1 planned. Agent 2: where this contradicts `agent1-plan.md` **on a fact**, this
file wins; on **what to do** about a fact, the plan leads.

## 1. C4's assertion test is settled by the recorded evidence

Extracted verbatim from `tests/fixtures/wave1_replies.json["W1-06-t4"]`, real bytes escaped:

```
**Rough chance of a favourable outcome:** **≈ 30 %**
**Rough chance of a favourable outcome:** **≈ 40 %**
- **Tour‑operator refund:** ~30 % chance, mainly limited by the two‑year limitation period.
- **Employer settlement:** ~40 % chance, limited by the three‑year limitation period ...
```

All four invented percentages share one property: each is **attached to a probability noun** —
either the line's own label is "Rough chance of a favourable outcome", or the word `chance`
immediately follows the number. **None of them states a unit.**

Every legitimate percentage does the exact opposite — it names what it is a percentage *of*, and the
unit is adjacent: `18% per annum`, `12% GST`, `100% refund`, `2% per month`, `50% deposit`.

So the test is a property of the percentage **itself**, not of its neighbours:

> A percentage is a probability claim when a probability noun is **attached** to it (as a following
> word, or as its line's label). An outcome word merely appearing within 70 characters is not
> evidence of anything — that is the `_WINDOW = 70` bug.

This is cycle 5's `_named_acts` fix reapplied: **attachment, not proximity.** The generalisation in
`cycle-4/agent4-triage.md` holds for C4 on the evidence.

Consequence for the rule's shape: the outcome-word list stops being the load-bearing part. It may
survive as a weak secondary signal, but it must not be sufficient on its own to delete a number, and
it must not be sufficient to **append the probability note** either — the double fault I reproduced
was a deleted rate *plus* a disclaimer on a reply that made no probability claim.

## 2. Controls that must keep passing (orchestrator-reproduced)

Deleted today, must survive after the fix:
```
A favourable order can include 18% per annum interest on the refund.
If you win, the 12% GST charged on the service is also recoverable.
```
Untouched today, must stay untouched:
```
The District Commission awards interest at 9% per annum in such matters.
You can demand a 100% refund of the amount you paid.
```

**Correction to an earlier orchestrator claim — Agent 1 was right and I was wrong.** I previously
recorded that cycle 4's `"demand a 100% refund"` example did not reproduce. That was true only of the
bare sentence above, which contains no probability noun and so passes for an unrelated reason. The
reproducer's actual param (`test_cycle4_adversarial.py:403`) is the full sentence:
```
Your chances improve a lot if you demand a 100% refund in the notice.   ->   demand a refund
```
which **does** reproduce, verified. So cycle 4's report was correct and the fix must handle it. It
does, via the `refund` measurand. Both strings above belong in the suite: one as a must-not-fire
control, one as a must-survive case inside a genuine probability sentence.

## 3. C2-a2 must not re-open A1

Cycle 5's A1 fix depends on `_classify` returning `"unverified"` when `corpus_available` is False.
That check sits **above** the named-Act logic, so changing the named-Act branch from `"unverified"`
to `"doubtful"` does not touch it — EMPLOYMENT / CYBER_FRAUD / POLICE_COMPLAINT / GENERAL keep
refusing bare statute numbers. Verify against
`test_no_section_number_is_verified_in_a_corpusless_domain` (4 params, now a plain lock).

What the change *does* mean: in a domain **with** a corpus, a citation naming an unrecognised Act
becomes keep-plus-caveat instead of delete. That is the module's own rule 1 and it is the right
default — but it does let a fabricated "Section 99 of the Fictional Act, 1999" survive in CONSUMER
with a caveat attached. That is a deliberate, stated trade: an unverifiable citation the user is
warned about beats silently deleting correct law they can never detect the loss of.

## 4. `ACT_NAME_RE` parenthesis blind spot

`ACT_NAME_RE` cannot match across a parenthetical, so "Real Estate (Regulation and Development) Act"
is not recognised as an Act name at all. The citation therefore takes the act-less route and is
caveated — correct behaviour reached by accident. `test_parenthesised_act_name_is_caveated_not_deleted`
locks it. If C2-a2 makes the named-Act path caveat anyway, the blind spot stops mattering for this
case and the lock stays valid either way.

## 5. Verified baseline for this cycle

Measured by the orchestrator, not copied:
```
1 failed, 770 passed, 16 xfailed, 3 warnings
FAILED tests/test_doc_generator.py::test_document_generation_notice   <- pre-existing, unrelated
```
The 16 xfails map to 9 open gap IDs: C2-a2, C2-a3, C2-c3, C2-c4, C4-1, C4-2, D1-a, D1-b, H4-2,
plus N8 outside this file. S5-B owns C4-1, C4-2, C2-a2, H4-2.

---

# Post-implementation finding (orchestrator, after Agent 2)

## C4 now UNDER-filters: a real win probability reaches the user

Agent 2 removed the ±70 window, which fixed the over-filtering. But removing it opened the
opposite fault, and Agent 2's own suite does not catch it because every recorded W1-06 t4 line is a
`label: value` shape with the value at end-of-line. In ordinary prose the claim survives.

Measured against the guard, 4 of 8 probability claims **survive**:

```
SURVIVES  Rough chance of a favourable outcome: about 30% in your case.
SURVIVES  Your chance of a favourable order is roughly 30% here.
SURVIVES  Success rate in such matters is about 55% in my estimate.
SURVIVES  Probability of recovery: 25% given the evidence you hold.
removed   There is a 40% chance of winning.              (noun immediately AFTER)
removed   The chance of success is 35%.                  (short copula gap)
removed   I would put your odds at around 45% overall.    (short copula gap)
removed   Rough chance of a favourable outcome: about 30%  (label: value at EOL)
```

**Root cause.** Three predicates each cover a narrow shape and nothing covers prose:
`_MEASURAND_AFTER_RE` is lookahead-only (correct, keep it); the `prob` group needs the probability
noun *immediately after* the `%`; `_PROB_BEFORE_RE` needs the noun before but only across a copula
with a gap of 40 or less; `_OUTCOME_LINE_RE` needs the `%` at end of line. A probability noun that
sits earlier in the sentence with ordinary words between it and the number falls through all four.

This is C4's original harm class — a fabricated win probability presented to a user — so it is not a
cosmetic regression of the fix.

**Suggested shape, for whoever takes it.** Widen the before-test from "only a copula, gap <= 40" to
**a probability noun anywhere earlier in the same clause**, and let `_MEASURAND_AFTER_RE` be the
**veto**. That ordering preserves every over-filter fix, because each surviving rate has its
measurand immediately after the number:

| string | measurand after `%` | verdict wanted |
|---|---|---|
| `18% per annum interest` | `per annum` | keep (rate) |
| `12% GST charged` | `GST` | keep (rate) |
| `demand a 100% refund` | `refund` | keep (rate) |
| `9% per annum` | `per annum` | keep (rate) |
| `roughly 30% here` | none | remove (probability) |
| `about 55% in my estimate` | none | remove (probability) |

So: measurand-after wins outright; failing that, a probability noun anywhere earlier in the clause is
sufficient. The lookahead-only constraint on the measurand test still holds and must not be relaxed —
W1-06 t4's `- **Tour-operator refund:** ~30 % chance,` puts `refund` *before* the number, and a
measurand-before test would veto a genuine probability.

**Verified baseline at this point:** Agent 2 reports `1 failed, 777 passed, 9 xfailed`; orchestrator
re-run in progress. `770 passed` never moved during Agent 2's five steps, so nothing was lost.
