# Cycle 7 Agent 2 — first gate: fabricated Model Tenancy Act citation

## Change

- `backend/app/services/response_guard.py`: positive `(Act, section)` licensing now requires an exact normalized Act name. The only added positive alias maps the canonical `Model Tenancy Act, 2021` to the tenancy corpus's `Model Tenancy Act` label. A prefixed State/City name, a different year, or a fictional suffix cannot inherit that license. The existing alternative/family comparison remains on the caveat decision path.
- The Act-name parser now recognizes 18xx years. This preserves the curated `Indian Contract Act, 1872` citation under exact matching.
- Preceding Act attachment now checks the whole connector and accepts the adjacent Hinglish `ke`, `ki`, or `ka` form. This prevents a partial Act name from an earlier sentence from being treated as attached while preserving `Rajasthan Rent Control Act ke Section 11` as a named, unverified State citation. No model law rewrite is triggered for that State citation.
- `backend/tests/test_cycle4_adversarial.py`: removed the strict xfail decorator from all three fabricated-Act parameters without changing their assertion. Added one direct lock for the positive license boundary and the canonical model-law adoption caveat.

## Deviations from Agent 1 plan

The exact-name change exposed pre-existing parsing assumptions in three passing tests. To preserve those valid citation forms, the narrow patch also extended the Act-year parser to 18xx and corrected the preceding-Act attachment check and Hinglish connectors. No corpus, document citation map, State-law coverage policy, or other finding was changed.

## Verification

- Focused regression/related cases: `11 passed, 335 deselected` after the first parser correction, and `6 passed, 98 deselected` after the Hinglish connector correction.
- Full required suite, run from `backend/` with `./.venv/Scripts/python.exe -m pytest -q --ignore=tests/test_browser_autofill.py`: **1 failed, 782 passed, 44 xfailed**. The sole failure is the known unrelated `tests/test_doc_generator.py::test_document_generation_notice`, which expects `Section 2(11)` in the generated notice. Baseline before this gate was `1 failed, 778 passed, 47 xfailed`; the three fabricated-Act parameters and one new direct lock account for the four added passes.
- `git diff --check`: exit 0.

No browser, server, live LLM call, commit, push, or branch was used. Stop here for Agent 3 review before any other finding.

## Agent 3 follow-up: parser completeness for the same citation gate

Agent 3 found seven additional strict-xfail cases where the parser hid material parts of an Act name from the exact licensing check. `ACT_NAME_RE` now reads attached parenthetical and colon qualifiers and recognizes lowercase names after an explicit citation connector. `_norm_act` normalizes their punctuation while retaining the qualifier words and years. A fabricated `Model Tenancy Act (Rajasthan), 2022`, `(Narnia edition), 2024`, `(2024)`, `Act: Rajasthan 2022`, or lowercase `rajasthan model tenancy act, 2022` cannot take the canonical model-law license. Genuine curated citations with parenthesized years remain intact.

All seven Agent 3 strict-xfail parameters became XPASS under the fix, so their decorators were removed without changing their assertions. Focused suite: `11 passed, 100 deselected`. The immediate full browser-excluding backend suite with the same command above: **1 failed, 789 passed, 44 xfailed**. The sole failure remains the pre-existing `test_document_generation_notice` expectation for `Section 2(11)`. This adds exactly seven passing cases over the first gate and loses no prior pass. No C4, H4-2, corpus, document-citation map, or other finding was changed.

## Ordered step 1: C4 probability versus rate

`response_guard.py::_percent_role` now classifies each percentage using an attached probability noun, an attached rate unit, and the governing noun within that percentage's clause. A subject-matter word such as `refund` cannot shield `65% refund chance`, and a generic phrase such as `on the value at stake` cannot shield an asserted chance. Explicit rates such as `100% refund`, `12% GST`, `18% interest`, `2% per month`, and Hindi/Hinglish `byaj` remain untouched even when a nearby clause says `win` or `favourable`. The former whole-line shortcut now removes a standalone line only when the same per-percentage classifier finds a probability, preserving rate labels and the four-bullet rate list. Ranges such as `30–40%` are matched as one figure, preventing a dangling lower bound when an invented range is removed.

All 25 C4 strict-xfail parameters became XPASS and were promoted to plain tests with their assertions unchanged. Four new contrasts cover `30% refund chance`, `100% refund`, an invented probability range, and a legitimate interest-rate range. Focused C4/recorded-reply suite: **42 passed**. Immediate full browser-excluding backend suite, same command as above: **1 failed, 818 passed, 19 xfailed**. The only failure is still the pre-existing `test_document_generation_notice` expectation for `Section 2(11)`. Passing count rose by 29, exactly the 25 promoted parameters plus four new contrasts; no earlier passing test was lost. H4-2 and all later ordered findings remain untouched.

## Agent 3 C4 follow-up: two figures and explanatory commas

Agent 3 found that an earlier `30%` chance could make a later `100%` refund in the same line look like another probability, while paired explanatory commas could hide the chance noun from a single `30%`. `_percent_role` now starts each later figure's context after the previous percent sign. If the most recent two clause breaks are commas enclosing an aside, it retains the governing probability noun before that aside, without crossing a hard sentence break. This applies to English, Roman Hinglish, Devanagari, and a mixed markdown line.

All seven new strict-xfail parameters became XPASS and were promoted with their assertions unchanged. Focused cases and controls: **12 passed**. Immediate full browser-excluding backend suite, same command: **1 failed, 825 passed, 19 xfailed**. The only failure remains `test_document_generation_notice` expecting `Section 2(11)`; the seven promotions account for all seven added passes, with no lost pass. H4-2 and later findings remain untouched.

## Ordered step 2: H4-2 six-digit amounts versus postal locators

`response_guard.py::_LOCATOR_RE` now treats a six-digit number as a postal locator only when it is explicitly labelled as a PIN, postal code, post code, or ZIP code. A bare `250000 deposit`, `120000 rupees`, or `300000 claim` no longer causes a sentence that advises checking the real rent authority website to be removed. Existing scheme URLs, hostnames, and street-address shapes remain locator signals; the authority-name and website-claim policy was not changed.

The three H4-2 strict-xfail parameters were promoted to plain tests with their assertions unchanged. Six new passing locks cover a labelled `PIN 302005`, a concrete street address, URL/hostname matching, and a trailing `302005 PIN`. Focused H4-2/authority tests: **13 passed**. Immediate full browser-excluding backend suite, same command: **1 failed, 834 passed, 16 xfailed**. The sole failure remains the pre-existing `test_document_generation_notice` expectation for `Section 2(11)`. Passing count rose by nine (three promotions plus six new locks); no earlier pass was lost. C2-a3 and later ordered findings remain untouched.

## Ordered step 3: C2-a3 truthful statute note

`response_guard.py::_statute_note` now receives the actual `statute_unverified` and `statute_unconfirmed_caveated` outcomes already recorded by the guard. It says section numbers were removed only when one was stripped; for a caveat-only reply it says the cited number was retained and needs verification. A mixed reply accurately mentions both actions. The note retains its existing official-source guidance without pasting a URL. English, Roman Hinglish, and Devanagari copy use the same outcome split, and the existing marker keeps a second guard pass idempotent.

The one C2-a3 strict-xfail test became XPASS and was promoted with its assertion unchanged. Nine added deterministic locks cover caveat-only, stripped-only, and mixed replies across all three scripts, including no-URL and repeat-guard checks. Focused statute cases: **13 passed**. Immediate full browser-excluding backend suite, same command: **1 failed, 844 passed, 15 xfailed**. The sole failure remains the pre-existing `test_document_generation_notice` expectation for `Section 2(11)`. Passing count rose by ten, exactly the promotion plus nine new locks; no earlier passing test was lost. D1 and later steps remain untouched.

## Ordered step 4: D1 internal identifier leak

`LLMResponseContext.model_payload()` now removes `domain_context.jurisdiction.fact_key` from its JSON-mode copy, as it already did for `next_fact_candidates[*].key`. The source `LLMResponseContext.domain_context` is not changed, so deterministic pending-interaction binding keeps its original key. Both Groq and Gemini still use this shared serialization method; their caching and retry code was not touched.

During active document intake, `llm_conversation.py` now maps the persisted missing fact keys to the existing user-facing `FIELD_LABELS` from `document_generation.py` before constructing `missing_information`. It does not change `profile.missing_document_fields`, document routing, readiness, or pending state. `response_guard.py::_fact_keys` also reads required document fields from `DOCUMENT_DEFINITIONS`, allowing the existing parenthetical scrub to remove an echoed `(complainant_name)`.

The two D1 strict-xfail tests were promoted unchanged. Two new locks verify the final JSON-serialized provider payload omits `fact_key` while the source retains it, and that user-facing document labels leave persisted missing fields unchanged. Focused `test_cycle4_adversarial.py` + `test_cycle4_verification.py`: **148 passed, 12 xfailed**. Immediate full browser-excluding backend suite with `./.venv/Scripts/python.exe -m pytest -q --ignore=tests/test_browser_autofill.py`: **1 failed, 861 passed, 13 xfailed**. The only failure remains the known `test_document_generation_notice` expectation for `Section 2(11)`. Versus the prior 844/15 gate, D1 accounts for two promotions and two added locks; the remaining 13 additional passes came from independently added browser-agent tests present in the shared worktree. I did not edit or run browser automation. C2-c3/c4 and test-integrity work remain untouched.

## Ordered step 5: C2-c3/c4 unsupported court-judgment claims

`response_guard.py::_apply_case_law` now also detects affirmative claims about unnamed court holdings, rulings, judgments, and decisions. It removes only the assertion sentence and adds the existing case-law disclosure once. The W1-03 t3 opening promise of a Supreme Court decision no longer survives the removal of its fabricated named citation. Adjacent practical advice remains, and generic questions or instructions about where/how to verify a decision are left unchanged. The verification exception applies only before the claimed assertion, so adding “please verify” afterward cannot shield an affirmative unsupported claim.

All five C2-c3/c4 strict-xfail parameters were promoted unchanged. Four new passing locks cover two generic verification discussions, sentence-level advice preservation with guard idempotency, and a later-verification masking contrast. Focused `test_cycle4_adversarial.py` + `test_response_guard.py`: **376 passed, 7 xfailed**. Immediate full browser-excluding backend suite with the same command: **1 failed, 870 passed, 8 xfailed**. The only failure remains `test_document_generation_notice` expecting `Section 2(11)`; compared with 861/13, the five promotions and four new locks account for all nine added passes. No case-law corpus, reporter citation, provider, document, or browser code was changed in this step. The ordered test-integrity edit has not started.

## Final planned step: fixture-byte test integrity

Only `backend/tests/test_cycle4_adversarial.py` and `backend/tests/test_response_guard.py` changed in this step. The old W1-03 t3 case-law test no longer checks retyped ASCII-space tokens against a U+202F fixture: it slices the reporter, party, `v.` clause, and heading from the original fixture bytes, asserts each span exists in that source, then asserts each is absent from the guarded result. The separate byte-faithful lock remains and its outdated description was corrected. The related statute negative now confirms the fixture's `Section<U+202F>9(1)` is present before checking removal.

The audit also added source-presence checks to fixture-derived W1-03 normalized case-law/statute assertions, W1-06 probability text, W1-02 model-law citation instruction and website claims, and W1-04 U+2011 helpline numbers plus the off-allowlist URL. Existing assertions were retained and no application behavior changed. Focused `test_cycle4_adversarial.py` + `test_response_guard.py`: **376 passed, 7 xfailed**. Full browser-excluding backend suite, same command: **1 failed, 870 passed, 8 xfailed**—identical to the prior gate. The only failure is the known `test_document_generation_notice` / `Section 2(11)` mismatch. Independent browser-agent edits in the shared worktree were left untouched.

## Agent 3 final review: seven bounded residual regressions

`response_guard.py` now includes bracketed and dash-separated Act qualifiers in `ACT_NAME_RE` before exact positive citation licensing. `Model Tenancy Act [Rajasthan], 2022` and `Model Tenancy Act - Rajasthan, 2022` can no longer inherit the canonical Model Tenancy Act license. Its percentage classifier now reads a one-comma short bridge such as `chance of success, roughly 30%`, while a later explicit rate label after paired commas (for example `interest, if awarded, is 18%`) outranks an earlier chance noun. The case-law detector now recognizes `has ruled`; it does not treat an unrelated earlier `Check your bank statement` as a case-verification request, and the scrub preserves clearly separate practical evidence advice in the same sentence as an unsupported holding.

All seven newly added strict-xfail parameters became XPASS and were promoted unchanged. Focused `test_cycle4_adversarial.py` + `test_response_guard.py` + `test_cycle4_verification.py`: **406 passed, 7 xfailed**. Immediate full browser-excluding backend suite with `./.venv/Scripts/python.exe -m pytest -q --ignore=tests/test_browser_autofill.py`: **1 failed, 877 passed, 8 xfailed**. Compared with 870/8, the seven promoted parameters account for every added pass; no prior pass was lost. The only failure remains the pre-existing `test_document_generation_notice` expectation for `Section 2(11)`. Unrelated browser-agent files remain untouched.
