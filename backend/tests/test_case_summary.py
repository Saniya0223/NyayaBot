import asyncio
from types import SimpleNamespace

from app.agents.conversation_agent import ConversationalLegalAgent
from app.services.case_summary import (
    generate_case_summary, recorded_facts, summary_fingerprint, summary_ready,
)


def profile():
    return ConversationalLegalAgent()._init_case_profile("A defective phone", category_override="CONSUMER")


def test_summary_uses_only_recorded_facts_and_cache_changes_with_case_state():
    case = profile()
    case.disputed_amount = 25000
    case.key_facts["product_name"] = "Phone"
    case.fact_metadata["disputed_amount"] = {"value": 25000, "source": "groq_chat", "confidence": 0.95}
    case.fact_metadata["product_name"] = {"value": "Tablet", "source": "groq_chat", "confidence": 0.95}
    assert recorded_facts(case) == {"disputed_amount": 25000}
    assert summary_ready(case)
    first = summary_fingerprint(case)
    case.updated_at = "tomorrow"
    case.ai_summary_cache = {"fingerprint": first, "text": "Old summary"}
    assert summary_fingerprint(case) == first
    case.actions_completed.append({"type": "formal_demand_sent", "label": "Sent demand", "date": "2026-09-25"})
    assert summary_fingerprint(case) != first


def test_summary_is_unavailable_for_unidentified_case():
    case = profile()
    case.category = "GENERAL"
    assert not summary_ready(case)


def test_provider_is_called_only_by_explicit_summary_generation():
    case = profile()
    case.key_facts["product_name"] = "Phone"
    case.fact_metadata["product_name"] = {"value": "Phone", "source": "groq_chat", "confidence": 0.95}

    class FakeProvider:
        def __init__(self):
            self.calls = []
            self.status = SimpleNamespace(configured=True)

        async def chat(self, context):
            self.calls.append(context)
            return "Situation\nA phone dispute."

    provider = FakeProvider()
    assert summary_ready(case)
    assert provider.calls == []
    assert asyncio.run(generate_case_summary(case, provider)) == "Situation\nA phone dispute."
    assert len(provider.calls) == 1
    assert provider.calls[0].case_summary["facts"] == {"product_name": "Phone"}
    assert provider.calls[0].legal_sources == []
