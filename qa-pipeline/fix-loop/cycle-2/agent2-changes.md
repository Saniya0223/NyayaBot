# Cycle 2 — Implementation report (S1-remainder + S5a)

**Note on authorship.** Agent 2 implemented all source changes but was terminated by a Claude
session limit before writing this report and before converting the flipped `xfail` reproducers.
The orchestrator completed those two mechanical steps and wrote this file from the actual diff
and test results. Every claim below is verified against source or a test run, not inferred from
Agent 2's intent.

## Result

```
1 failed, 424 passed, 3 warnings
FAILED tests/test_doc_generator.py::test_document_generation_notice   <- pre-existing, unrelated
```

Test progression across the loop: **318 -> 349 (cycle 1) -> 409 (Agent 3 probes) -> 424 (cycle 2)**.

All **15** strict-xfail reproducers from cycle 1 now pass and have been converted to plain
regression locks in `tests/test_cycle1_verification.py`. No test was weakened or deleted.

## Files changed

```
backend/app/services/safety_triage.py     +382 -...
backend/app/services/llm_conversation.py  +649 -...
backend/app/services/case_readiness.py     +48 -...
backend/app/agents/conversation_agent.py   +24 -...
                                   1002 insertions, 101 deletions
```

## Per item

### CF-1 — `URGENT_GUIDANCE` unbounded loop (HIGH)
**Problem.** `immediate_danger is True` short-circuited `_safety_route_required` above the release
check; the urgent branch wrote `last_safety_question_group` directly instead of calling
`_record_safety_ask`, so it had no counter and was exempt from both caps added in cycle 1. Agent 4
proved a user could still be trapped at turn 8 while stating they were safe, because `ab` missed
the `SAFE_NOW` regex that only matched `abhi`.
**Fixed.** Release checks reordered ahead of the urgent short-circuit; the urgent branch now records
asks and respects the cap; counters reset on a fresh harm signal after completion (absorbs N7); the
one authorised regex inflection (`ab` alongside `abhi`) added.
**Verified by.** `test_active_danger_branch_must_not_repeat_the_same_question_forever`,
`test_a_new_threat_after_the_cap_still_gets_the_immediate_danger_question`.

### CF-2 — non-personal-threat denylist (HIGH)
**Problem.** `NON_PERSONAL_THREAT_PATTERN` was a literal denylist. Agent 4 generated four fresh
failures in a minute; the list could not be finished. Near-misses of its *own* entries
("cut our water supply" vs "cut water and electricity") reproduced H1 verbatim.
**Fixed — mechanism inverted, not extended.** `_threat_complement()` + `_THREAT_COMPLEMENT_PATTERNS`
+ `_PERSON_HARM_VERB` implement Agent 1's four-way rule: direct person object -> keep; no complement
-> keep; complement containing PERSON + HARM-verb -> keep; otherwise drop. The denylist survives only
as a secondary filter. Agent 1 validated this rule on a 35-string corpus with 0 mismatches before
handing it over.
**Verified by.** `test_near_miss_non_personal_threats_should_not_create_a_safety_case` (4 cases),
plus the four cycle-1 partner+threat regression locks still passing unchanged.

### CF-3 — third party's suicide threat fired the user's crisis script (HIGH)
**Problem.** "My tenant threatened to commit suicide if I evict him" handed the *landlord* a crisis
script and pinned their eviction case.
**Fixed.** `THIRD_PARTY_ATTRIBUTION_PATTERN` + `_third_party_attributed(text, start)` — a positional
test, deliberately not a whole-text scan, because that sentence contains "I" *after* the match and a
global test would have been a no-op.
**Verified by.** `test_a_third_partys_suicide_threat_is_not_the_users_crisis`.

### CF-4 — crisis turn froze the legal case permanently (HIGH)
**Problem.** `safety_triage_resolved` required `immediate_danger is not None`, which a crisis case
never answers, so the case was pinned at `UNDERSTANDING_CASE` on seven unsatisfiable attacker facts.
**Fixed.** `safety_intake_facts_for()` selector makes the intake contract case-scoped; a crisis-only
disclosure no longer inherits attacker-oriented facts. `risk_level` deliberately stays AMBER, per
Agent 4's ruling that RED would pin readiness harder than the bug being fixed.
**Verified by.** `test_a_crisis_disclosure_does_not_permanently_freeze_the_legal_case`.

### CF-5 / S5a — document offered after a suicidal disclosure (HIGH)
**Problem.** A document was still offered two turns after a crisis disclosure. This refuted Agent 2's
own cycle-1 justification for leaving `safety_triage_complete` unset.
**Fixed.** `CRISIS_DOCUMENT_COOLDOWN_TURNS = 3` + `crisis_document_block_active()` in
`case_readiness.py`, consulted at the document affordances (`conversation_agent.py:586` and the
`_document_request_response` call sites). Deliberately a **bounded cooling-off window, not a
permanent ban** — a permanent block would recreate CF-4's trap one layer down.
**Verified by.** `test_no_document_is_offered_soon_after_a_crisis_disclosure`.

### CF-6 — missed ideation inflections (MEDIUM, hard-capped)
**Problem.** Six common wordings missed, two of them bugs in patterns written in cycle 1 — a
trailing `\b` killed `de dunga`, the commonest inflection.
**Fixed.** Exactly the authorised seven changes (two inflection defects, two script-parity fixes,
three literals). **Binding on later cycles: no further wordlist expansion without new evidence.**
**Verified by.** `test_common_ideation_wordings_should_be_detected` (6 cases).

## Scope note carried forward

Agent 1 took only **S5a** (refuse documents on crisis / unresolved safety / RED), not all of C3.
The readiness half — **S5b** — is deferred to pair with S2, because until H7
(`opposite_party_name`) is fixed nearly every case sits at `UNDERSTANDING_CASE`, and a blanket
readiness gate would refuse every document in the product. **C3 is not closed by this cycle.**

## Residual risk

- `xfail` conversion was done by the orchestrator, not Agent 2. The 15 tests pass as plain
  assertions; their assertion bodies were not altered, only the decorators removed.
- N8 (offline `limited_demo` composer returns byte-identical replies to different questions) is
  no longer reproducible through the cycle-1 test, because CF-1's release fix made those turns
  distinct. N8 itself is not fixed — it remains deferred to S3/S8 and has no active reproducer.
- Agent 3 has not yet independently verified cycle 2. These results are Agent 2's changes plus the
  orchestrator's own verification, which is weaker than an adversarial pass.
