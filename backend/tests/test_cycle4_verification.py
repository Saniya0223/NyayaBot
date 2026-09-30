"""Cycle 4 / set S4 wiring: D1 (payload redaction), M8 (source provenance), and proof that
the output guard runs on every return path, not only the healthy provider one.

All providers are local fakes. No network, no Groq/Gemini call, no server, no browser.
"""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agents.conversation_agent import ConversationalLegalAgent
from app.domains import domain_registry
from app.llm.contracts import (
    CaseExtraction,
    DocumentAnalysis,
    IssueClassification,
    LLMExtractionContext,
    LLMProvider,
    LLMProviderError,
    LLMRateLimitedError,
    LLMResponseContext,
    ProviderStatus,
    REDACTED_CANDIDATE_KEYS,
)
from app.schemas.chat import ChatTurnRequest
from app.services import llm_conversation as llm_conversation_module
from app.services.case_summary import generate_case_summary
from app.services.llm_conversation import (
    OFFICIAL_SOURCE_LINK,
    VERIFIED_PROVISION,
    GeminiConversationService,
)


FIXTURES = json.loads(
    (Path(__file__).parent / "fixtures" / "wave1_replies.json").read_text(encoding="utf-8")
)
FABRICATED = FIXTURES["W1-03-t3"]["reply"]   # the verbatim C2 reply, invented SCC citation and all


def tenancy_extraction():
    return CaseExtraction.model_validate({
        "user_intent": "recover security deposit",
        "language_style": "hinglish",
        "classification": {
            "category": "HOUSING_TENANT", "issue_type": "SECURITY_DEPOSIT_DISPUTE",
            "confidence": 0.95,
        },
        "facts": {"disputed_amount": 85000, "user_state": "Rajasthan", "user_city": "Jaipur"},
    })


def employment_extraction():
    return CaseExtraction.model_validate({
        "user_intent": "recover unpaid salary",
        "classification": {
            "category": "EMPLOYMENT", "issue_type": "UNPAID_SALARY", "confidence": 0.95,
        },
        "facts": {"monthly_salary": 40000, "user_state": "Karnataka"},
    })


class FakeProvider(LLMProvider):
    """Returns a scripted reply. `fail_with` makes a stage raise without any network call."""

    def __init__(self, extraction, reply="I have noted the facts you reported.",
                 fail_extraction=None, fail_chat=None):
        self.extraction = extraction
        self.reply = reply
        self.fail_extraction = fail_extraction
        self.fail_chat = fail_chat
        self.response_contexts: list[LLMResponseContext] = []
        self.chat_calls = 0

    @property
    def status(self) -> ProviderStatus:
        return ProviderStatus(provider="gemini", model="gemini-test", configured=True,
                              mode="gemini", message="Local fake")

    async def extract_case_updates(self, context: LLMExtractionContext) -> CaseExtraction:
        if self.fail_extraction:
            raise self.fail_extraction("local fake failure")
        return self.extraction

    async def classify_issue(self, context: LLMExtractionContext) -> IssueClassification:
        return self.extraction.classification

    async def chat(self, context: LLMResponseContext) -> str:
        self.chat_calls += 1
        self.response_contexts.append(context)
        if self.fail_chat:
            raise self.fail_chat("local fake failure")
        return self.reply

    async def analyze_document(self, text: str, document_type_hint: str) -> DocumentAnalysis:
        raise NotImplementedError


def build_service(provider):
    return GeminiConversationService(provider=provider, workflow_agent=ConversationalLegalAgent())


def run_turn(service, message, profile=None, history=None):
    return asyncio.run(service.process_turn(
        ChatTurnRequest(message=message, case_id=(profile.case_id if profile else "new")),
        profile, history or [],
    ))


# ------------------------------------------------------------------------ D1

def response_context_with_candidate():
    return LLMResponseContext(
        user_message="Landlord ne deposit wapas nahi kiya.",
        case_summary={"case_id": "c-1", "facts": {}},
        workflow={"current_stage_key": "INFORMAL_REQUEST"},
        domain_context={
            "domain_id": "HOUSING_TENANT",
            "next_fact_candidates": [{
                "key": "opposite_party_name",
                "meaning": "the landlord or property manager involved",
                "purpose": "IDENTITY",
                "priority_reason": "BLOCKING",
                "stage": "UNDERSTANDING_CASE",
            }],
        },
    )


def test_model_payload_removes_the_internal_fact_key():
    """DoD #2. W1-02 t2 echoed the key verbatim: '... ka naam (opposite_party_name)'."""
    context = response_context_with_candidate()
    payload = context.model_payload()
    candidate = payload["domain_context"]["next_fact_candidates"][0]
    assert "key" not in candidate
    assert "opposite_party_name" not in json.dumps(payload)
    # The product's own human phrasing, which is what the model should ask from, stays.
    assert candidate["meaning"] == "the landlord or property manager involved"
    assert candidate["purpose"] == "IDENTITY"
    assert REDACTED_CANDIDATE_KEYS == ("key",)


def test_model_payload_does_not_mutate_the_in_memory_context():
    """_finish_chat_response reads next_fact_candidates[0]['key'] *after* chat() returns."""
    context = response_context_with_candidate()
    context.model_payload()
    context.model_payload()
    assert context.domain_context["next_fact_candidates"][0]["key"] == "opposite_party_name"
    # A new dict each time: the caller cannot corrupt the source by editing the payload.
    first = context.model_payload()["domain_context"]["next_fact_candidates"][0]
    first["meaning"] = "mutated"
    assert context.domain_context["next_fact_candidates"][0]["meaning"] != "mutated"


def test_every_other_context_field_still_reaches_the_provider():
    context = response_context_with_candidate()
    payload, dumped = context.model_payload(), context.model_dump(mode="json")
    assert set(payload) == set(dumped)
    assert {k: v for k, v in payload.items() if k != "domain_context"} == {
        k: v for k, v in dumped.items() if k != "domain_context"
    }
    assert payload["domain_context"]["domain_id"] == "HOUSING_TENANT"


def test_both_providers_serialise_through_model_payload():
    """A future provider cannot reintroduce the leak by calling model_dump directly."""
    import inspect

    from app.llm import gemini_provider, groq_provider

    for module, name in ((groq_provider, "GroqProvider"), (gemini_provider, "GeminiProvider")):
        source = inspect.getsource(getattr(module, name).chat)
        code = "\n".join(
            line for line in source.splitlines() if not line.lstrip().startswith("#")
        )
        assert "model_payload()" in code, f"{name}.chat must use model_payload()"
        assert "model_dump" not in code, f"{name}.chat must not call model_dump"


def test_pending_interaction_still_binds_to_the_redacted_key_end_to_end():
    """DoD #2: the backend keeps the key even though the model never sees it."""
    provider = FakeProvider(tenancy_extraction(),
                            reply="Landlord ka naam kya hai? Yeh notice ke liye chahiye.")
    service = build_service(provider)
    response = run_turn(service, "Mera landlord deposit wapas nahi de raha, Jaipur ka flat hai.")
    context = provider.response_contexts[0]
    candidates = context.domain_context["next_fact_candidates"]
    assert candidates and "key" in candidates[0]
    assert "key" not in context.model_payload()["domain_context"]["next_fact_candidates"][0]
    pending = response.case_profile.pending_interaction
    assert pending is not None and pending.target_keys == [candidates[0]["key"]]


# ------------------------------------------------------------------------ M8

@pytest.mark.parametrize("category", ["EMPLOYMENT", "CYBER_FRAUD", "POLICE_COMPLAINT", "GENERAL"])
def test_corpusless_domains_report_no_verified_provisions(category):
    """DoD #11. These four declare corpus_ids=(), so no section number in them is verifiable."""
    service = build_service(FakeProvider(employment_extraction()))
    agent = ConversationalLegalAgent()
    profile = agent._init_case_profile("salary issue", category_override=category)
    sources = service._verified_sources(profile, "my salary is unpaid")
    # GENERAL declares no official_sources at all; the other three supply portal links only,
    # never a provision, which is exactly the signal M8 needs to surface.
    assert bool(sources) is (category != "GENERAL")
    assert all(source.get("kind") == OFFICIAL_SOURCE_LINK for source in sources)
    assert not any(source.get("act") and source.get("section") for source in sources)
    status = service._legal_sources_status(profile, sources)
    assert status == {"corpus_available": False, "verified_provisions": 0,
                      "case_law_available": False}


@pytest.mark.parametrize("category,state", [("CONSUMER", "Karnataka"),
                                            ("HOUSING_TENANT", "Rajasthan")])
def test_corpus_domains_report_verified_provisions(category, state):
    service = build_service(FakeProvider(employment_extraction()))
    agent = ConversationalLegalAgent()
    profile = agent._init_case_profile("dispute", category_override=category)
    profile.user_state = state
    profile.issue_type = domain_registry.resolve(category).default_issue_type_id
    sources = service._verified_sources(profile, "the refund and deposit were not returned")
    provisions = [s for s in sources if s.get("kind") == VERIFIED_PROVISION]
    # The domain's official_sources URL is the same India Code / MoHUA page the corpus entries
    # already cite, so _verified_sources' pre-existing de-duplication drops the bare link for
    # these two domains. That behaviour is unchanged; what M8 needs is that every entry that
    # does reach the list carries a provenance tag.
    assert provisions
    assert all(s.get("kind") in {VERIFIED_PROVISION, OFFICIAL_SOURCE_LINK} for s in sources)
    assert all(s.get("act") and s.get("section") for s in provisions)
    status = service._legal_sources_status(profile, sources)
    assert status["corpus_available"] is True
    assert status["verified_provisions"] >= 1
    assert status["case_law_available"] is False


def test_provenance_tag_is_on_both_lists_so_membership_still_holds():
    """test_llm_conversation.py:516 asserts identity-of-dicts membership between
    profile.legal_sources and the context list. Tagging one side only would break it."""
    provider = FakeProvider(tenancy_extraction())
    service = build_service(provider)
    response = run_turn(service, "Mera landlord ne Jaipur ke flat ka deposit wapas nahi kiya.")
    context = provider.response_contexts[0]
    assert context.legal_sources
    assert all(source in context.legal_sources for source in response.case_profile.legal_sources)
    assert all("kind" in source for source in context.legal_sources)
    assert context.legal_sources_status["corpus_available"] is True
    assert context.legal_sources_status["case_law_available"] is False
    assert response.case_profile.key_facts["verified_sources_available"] is True


def test_helpline_registry_reaches_the_model_with_its_operator():
    """M7/L1 supplement: the model has real numbers and real operators to reach for."""
    provider = FakeProvider(tenancy_extraction())
    service = build_service(provider)
    run_turn(service, "Mera landlord ne Jaipur ke flat ka deposit wapas nahi kiya.")
    registry = provider.response_contexts[0].helpline_registry
    entry = next(item for item in registry if item["number"] == "1930")
    assert "Indian Cyber Crime Coordination Centre" in entry["operator"]
    assert "RBI" not in json.dumps(registry)
    assert {"number", "service", "operator"} == set(entry)


def test_disclosure_only_appears_when_the_reply_makes_a_provision_claim():
    """DoD #11. Nagging every corpus-less turn is bad product, and test_api.py:170 asserts
    no underscore reaches reply_text on an EMPLOYMENT case."""
    quiet = FakeProvider(employment_extraction(),
                         reply="I have noted the unpaid salary. Which months are outstanding?")
    response = run_turn(build_service(quiet), "My employer has not paid my salary.")
    assert "Note on legal sources" not in response.reply_text
    assert "_" not in response.reply_text

    claiming = FakeProvider(
        employment_extraction(),
        reply="Under Section 9(1) of the Code on Wages, 2019 the wages were due earlier.",
    )
    response = run_turn(build_service(claiming), "My employer has not paid my salary.")
    assert "Note on legal sources" in response.reply_text
    assert "Ministry of Labour & Employment" in response.reply_text
    assert "Section 9(1)" not in response.reply_text
    assert "_" not in response.reply_text
    assert "http" not in response.reply_text


# --------------------------------------------------------- guard path coverage

def spy_on_guard(monkeypatch):
    """Records every guard invocation, so a path's coverage is proved, not assumed."""
    calls: list[str] = []
    real = llm_conversation_module.guard_reply

    def recording(reply, profile, user_message=""):
        calls.append(reply)
        return real(reply, profile, user_message)

    monkeypatch.setattr(llm_conversation_module, "guard_reply", recording)
    return calls


def test_guard_runs_on_the_live_provider_path_and_removes_the_fabrication():
    """DoD #12 path 1, with the verbatim W1-03 t3 reply coming back from a fake provider."""
    provider = FakeProvider(employment_extraction(), reply=FABRICATED)
    response = run_turn(build_service(provider), "My employer has not paid my salary for 4 months.")
    assert provider.chat_calls == 1
    assert "(2020) 6 SCC 123" not in response.reply_text
    assert "M. S. R. Enterprises" not in response.reply_text
    assert "Supreme Court case you can quote" not in response.reply_text
    assert "Section 9(1)" not in response.reply_text
    assert "verified case-law database" in response.reply_text


def test_guard_runs_on_the_offline_limited_demo_path(monkeypatch):
    """DoD #12 path 2. The reply here is deterministic, so the guard must be a no-op - but it
    must still have run, because this is the path a prompt cannot reach."""
    calls = spy_on_guard(monkeypatch)
    provider = FakeProvider(tenancy_extraction(), fail_extraction=LLMProviderError)
    response = run_turn(build_service(provider), "My landlord kept my deposit")
    assert response.llm_mode == "limited_demo"
    assert calls and calls[-1] == response.reply_text


def test_guard_runs_on_the_429_rate_limit_fallback(monkeypatch):
    """DoD #12 path 3."""
    calls = spy_on_guard(monkeypatch)
    provider = FakeProvider(tenancy_extraction(), fail_chat=LLMRateLimitedError)
    response = run_turn(build_service(provider), "Mera landlord deposit wapas nahi de raha.")
    assert response.llm_mode == "limited_demo"
    assert response.retry_action is not None
    assert calls and calls[-1] == response.reply_text


def test_guard_runs_on_the_resume_retry_path():
    """DoD #12 path 4: the retried reply is model text, so a real fabrication must be removed."""
    provider = FakeProvider(employment_extraction(), fail_chat=LLMRateLimitedError)
    service = build_service(provider)
    first = run_turn(service, "My employer has not paid my salary for four months.")
    checkpoint = first._retry_state
    assert checkpoint and checkpoint["stage"] == "FINAL_CHAT"

    provider.fail_chat = None
    provider.reply = FABRICATED
    resumed = asyncio.run(service.resume_response(first.case_profile, checkpoint))
    assert "(2020) 6 SCC 123" not in resumed.reply_text
    assert "Section 9(1)" not in resumed.reply_text
    assert "verified case-law database" in resumed.reply_text


def test_guard_runs_inside_generate_case_summary_before_it_is_cached():
    """DoD #12 path 5. main.py:981 writes this string into profile.ai_summary_cache and serves
    it from there forever, so the guard has to run inside the function, not after it."""
    agent = ConversationalLegalAgent()
    profile = agent._init_case_profile("unpaid salary", category_override="EMPLOYMENT")
    profile.user_state = "Karnataka"

    class SummaryProvider:
        status = SimpleNamespace(configured=True)

        def __init__(self):
            self.calls = []

        async def chat(self, context):
            self.calls.append(context)
            return FABRICATED

    provider = SummaryProvider()
    brief = asyncio.run(generate_case_summary(profile, provider))
    assert len(provider.calls) == 1
    assert provider.calls[0].legal_sources_status["case_law_available"] is False
    assert "(2020) 6 SCC 123" not in brief
    assert "M. S. R. Enterprises" not in brief
    assert "Section 9(1)" not in brief
    assert "verified case-law database" in brief


def test_guard_is_the_single_funnel_inside_tag_response():
    """Structural lock: the guard lives in _tag_response, which every return path uses."""
    import inspect

    source = inspect.getsource(GeminiConversationService._tag_response)
    assert "guard_reply(" in source
    whole = inspect.getsource(GeminiConversationService)
    assert whole.count("self._tag_response(") >= 10


def test_guard_logging_never_carries_the_reply_text(caplog):
    """DoD #15: controlled vocabulary only - rule names, case id, category, mode."""
    provider = FakeProvider(employment_extraction(), reply=FABRICATED)
    with caplog.at_level("WARNING", logger="uvicorn.error"):
        response = run_turn(build_service(provider), "My employer has not paid my salary.")
    guard_records = [r for r in caplog.records if "response_guard_redacted" in r.getMessage()]
    assert guard_records
    message = guard_records[0].getMessage()
    assert "case_law" in message and "EMPLOYMENT" in message
    assert "M. S. R. Enterprises" not in message
    assert "SCC" not in message
    assert response.case_profile.case_id in message
