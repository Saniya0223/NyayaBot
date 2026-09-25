import asyncio
import uuid

from fastapi.testclient import TestClient

from app.agents.conversation_agent import ConversationalLegalAgent
from app.db.models import CaseModel, ChatCaseSessionModel, UserMemoryModel, UserModel
from app.db.session import SessionLocal
from app.llm.contracts import (
    CaseExtraction, DocumentAnalysis, IssueClassification, LLMExtractionContext,
    LLMProvider, LLMResponseContext, ProviderStatus,
)
from app.main import app
from app.schemas.chat import ChatMessage, ChatTurnRequest
from app.services.llm_conversation import GeminiConversationService
from app.services.user_context import (
    CASE_HISTORY_LOOKUP, MEMORY_RECALL, NORMAL, case_history, chat_user_context,
    case_history_lookup_reply, history_lookup_requested, memory_recall_requested,
)


def _client(label: str) -> tuple[TestClient, str]:
    unique = uuid.uuid4().hex[:8]
    email = f"{label}-{unique}@example.com"
    client = TestClient(app)
    response = client.post('/api/v1/auth/signup', json={
        'full_name': f'{label} User', 'email': email, 'password': 'StrongPass123!',
    })
    assert response.status_code == 201
    return client, response.json()['id']


def _case(db, user_id: str, category: str, title: str, status: str = 'Open') -> CaseModel:
    case = CaseModel(id=str(uuid.uuid4()), user_id=user_id, case_number=uuid.uuid4().hex[:12],
                     title=title, category=category, status=status)
    db.add(case)
    db.flush()
    return case


class CaptureProvider(LLMProvider):
    def __init__(self):
        self.response_context: LLMResponseContext | None = None

    @property
    def status(self) -> ProviderStatus:
        return ProviderStatus(provider='groq', model='fake-local', configured=True,
                              mode='groq', message='Local deterministic fake')

    async def extract_case_updates(self, context: LLMExtractionContext) -> CaseExtraction:
        return CaseExtraction(user_intent='Asks about prior information', classification=IssueClassification(
            category='EMPLOYMENT', issue_type='UNPAID_SALARY', confidence=0.99,
        ))

    async def classify_issue(self, context: LLMExtractionContext) -> IssueClassification:
        return IssueClassification(category='EMPLOYMENT', issue_type='UNPAID_SALARY', confidence=0.99)

    async def chat(self, context: LLMResponseContext) -> str:
        self.response_context = context
        return 'Local fake response'

    async def analyze_document(self, text: str, document_type_hint: str) -> DocumentAnalysis:
        raise NotImplementedError


def test_roman_hinglish_memory_and_history_intents():
    assert memory_recall_requested('tumhe mere baare mein kya yaad hai?')
    assert memory_recall_requested('What do you remember about me?')
    assert memory_recall_requested('तुम्हें मेरे बारे में क्या याद है?')
    assert history_lookup_requested('kya mera pehle koi consumer case tha?')
    assert history_lookup_requested('Did I previously have a Consumer case?')
    assert history_lookup_requested('क्या मेरा पहले कोई उपभोक्ता केस था?')
    assert not history_lookup_requested('meri unpaid salary kitni hai?')


def test_case_history_category_query_is_owned_and_not_limited_to_closed_cases():
    alice, alice_id = _client('history-category-a')
    bob, bob_id = _client('history-category-b')
    with SessionLocal() as db:
        old_consumer = _case(db, alice_id, 'CONSUMER', 'Demo Gift Gallery delivery', 'Understanding case')
        _case(db, bob_id, 'CONSUMER', 'Private consumer matter')
        for index in range(7):
            _case(db, alice_id, 'EMPLOYMENT', f'Employment matter {index}')
        db.commit()
        matches = case_history(db, alice_id, limit=5, category='CONSUMER')
        assert [item.case_id for item in matches] == [old_consumer.id]
        assert matches[0].status == 'Understanding case'
        assert '2500' not in str(matches)
        assert case_history(db, bob_id, category='CONSUMER')[0].title == 'Private consumer matter'
    response = alice.get('/api/v1/profile/case-history?category=CONSUMER')
    assert response.status_code == 200
    assert [item['title'] for item in response.json()] == ['Demo Gift Gallery delivery']
    assert 'Private consumer matter' not in str(response.json())
    assert 'Demo Gift Gallery' not in str(bob.get('/api/v1/profile/case-history').json())


def test_history_brief_uses_recorded_case_facts_not_chat_transients():
    _, user_id = _client('brief-source')
    with SessionLocal() as db:
        case = _case(db, user_id, 'CONSUMER', 'Consumer Dispute')
        profile = ConversationalLegalAgent()._init_case_profile(
            'A resin item was not delivered', case_id=case.id, category_override='CONSUMER',
        )
        profile.opposite_party_name = 'Demo Gift Gallery'
        profile.key_facts['product_name'] = 'resin item'
        for key, value in (('opposite_party_name', 'Demo Gift Gallery'), ('product_name', 'resin item')):
            profile.fact_metadata[key] = {'value': value, 'source': 'chat', 'confidence': 1.0, 'confirmed': True}
        db.add(ChatCaseSessionModel(case_id=case.id, user_id=user_id, is_demo=False,
                                    profile_data=profile.model_dump(mode='json'),
                                    messages_data=[{'sender': 'user', 'text': "I am at my cousin's house today"}]))
        db.commit()
        brief = case_history(db, user_id, category='CONSUMER')[0].summary
        assert 'Demo Gift Gallery' in brief
        assert 'resin item' in brief
        assert 'cousin' not in brief


def test_chat_history_answer_is_grounded_in_owned_consumer_case():
    client, user_id = _client('history-answer')
    with SessionLocal() as db:
        _case(db, user_id, 'CONSUMER', 'Earlier resin delivery matter')
        current = _case(db, user_id, 'EMPLOYMENT', 'Current unpaid salary matter')
        profile = ConversationalLegalAgent()._init_case_profile(
            'My employer has not paid salary', case_id=current.id, category_override='EMPLOYMENT',
        )
        db.add(ChatCaseSessionModel(case_id=current.id, user_id=user_id, is_demo=False,
                                    profile_data=profile.model_dump(mode='json'), messages_data=[]))
        current_case_id = current.id
        db.commit()
    response = client.post('/api/v1/chat/message', json={
        'case_id': current_case_id, 'message': 'kya mera pehle koi consumer case tha?',
    })
    assert response.status_code == 200
    assert response.json()['case_profile']['language_style'] == 'hinglish'
    assert response.json()['reply_text'].startswith('Haan')
    assert 'Earlier resin delivery matter' in response.json()['reply_text']
    assert response.json()['case_profile']['category'] == 'EMPLOYMENT'
    assert response.json()['case_profile']['disputed_amount'] == 0


def test_memory_recall_separates_sources_and_excludes_transient_recent_text():
    client, user_id = _client('memory-recall')
    with SessionLocal() as db:
        user = db.get(UserModel, user_id)
        user.city = 'Jaipur'
        db.add(UserMemoryModel(user_id=user_id, category='preference', text='Explain legal steps simply'))
        consumer = _case(db, user_id, 'CONSUMER', 'Resin item non-delivery')
        current = _case(db, user_id, 'EMPLOYMENT', 'Unpaid salary matter')
        db.commit()

        ordinary = chat_user_context(db, user, 'meri unpaid salary kitni hai?', current.id)
        assert ordinary['intent'] == NORMAL
        assert 'previous_case_context' not in ordinary
        assert 'Resin item' not in str(ordinary)
        assert '2500' not in str(ordinary)

        recall = chat_user_context(db, user, 'tumhe mere baare mein kya yaad hai?', current.id)
        assert recall['intent'] == MEMORY_RECALL
        assert recall['profile_context']['full_name'] == user.full_name
        assert recall['profile_context']['city'] == 'Jaipur'
        assert recall['saved_memory_context'] == [{'category': 'preference', 'text': 'Explain legal steps simply'}]
        assert [item['title'] for item in recall['previous_case_context']['cases']] == [consumer.title]
        assert current.title not in str(recall['previous_case_context'])
        assert 'cousin' not in str(recall)

        lookup = chat_user_context(db, user, 'kya mera pehle koi consumer case tha?', current.id)
        assert lookup['intent'] == CASE_HISTORY_LOOKUP
        assert lookup['previous_case_context']['category_filter'] == 'CONSUMER'
        assert [item['title'] for item in lookup['previous_case_context']['cases']] == [consumer.title]
        assert 'saved_memory_context' not in lookup

    # The ordinary chat statement is retained only in the current case chat,
    # never automatically promoted to durable memory.
    temporary = client.post('/api/v1/chat/message', json={'message': "I am at my cousin's house today"})
    assert temporary.status_code == 200
    assert client.get('/api/v1/profile/memories').json()[0]['text'] == 'Explain legal steps simply'


def test_final_context_does_not_relabel_recent_chat_or_import_old_amount():
    _, user_id = _client('context-isolation')
    provider = CaptureProvider()
    service = GeminiConversationService(provider=provider, workflow_agent=ConversationalLegalAgent())
    current = service.workflow_agent._init_case_profile('My salary was not paid', category_override='EMPLOYMENT')
    old_chat = [ChatMessage(sender='user', text="I am at my cousin's house today")]
    with SessionLocal() as db:
        user = db.get(UserModel, user_id)
        _case(db, user_id, 'CONSUMER', 'Resin purchase worth ₹2,500')
        db.commit()
        recall = chat_user_context(db, user, 'tumhe mere baare mein kya yaad hai?', current.case_id)
        result = asyncio.run(service.process_turn(ChatTurnRequest(message='tumhe mere baare mein kya yaad hai?'),
                                                  current, old_chat, user_context=recall))
        assert provider.response_context is not None
        assert provider.response_context.recent_messages == []
        assert provider.response_context.user_context['intent'] == MEMORY_RECALL
        assert provider.response_context.case_summary['category'] == 'EMPLOYMENT'
        assert 'facts' not in provider.response_context.case_summary
        assert result.case_profile.disputed_amount == 0

        ordinary = chat_user_context(db, user, 'meri unpaid salary kitni hai?', current.case_id)
        asyncio.run(service.process_turn(ChatTurnRequest(message='meri unpaid salary kitni hai?'),
                                         current, [], user_context=ordinary))
        assert provider.response_context.user_context['intent'] == NORMAL
        assert 'previous_case_context' not in provider.response_context.user_context
        assert '2500' not in str(provider.response_context)
        assert provider.response_context.case_summary['facts']['disputed_amount'] is None


def test_deleting_memory_does_not_delete_case_and_stale_note_cannot_prove_case():
    client, user_id = _client('deletion-consistency')
    saved = client.post('/api/v1/profile/memories', json={
        'category': 'explicit', 'text': 'I had a consumer case once',
    })
    assert saved.status_code == 201
    with SessionLocal() as db:
        user = db.get(UserModel, user_id)
        consumer = _case(db, user_id, 'CONSUMER', 'Old consumer matter')
        db.commit()
        assert chat_user_context(db, user, 'kya mera pehle koi consumer case tha?', None)[
            'previous_case_context']['cases']

    assert client.delete(f"/api/v1/profile/memories/{saved.json()['id']}").status_code == 204
    assert client.get('/api/v1/profile/memories').json() == []
    assert len(client.get('/api/v1/profile/case-history?category=CONSUMER').json()) == 1
    with SessionLocal() as db:
        user = db.get(UserModel, user_id)
        assert chat_user_context(db, user, 'what do you remember about me?', None)['saved_memory_context'] == []
        db.delete(db.get(CaseModel, consumer.id))
        db.commit()
        lookup = chat_user_context(db, user, 'kya mera pehle koi consumer case tha?', None)
        assert lookup['previous_case_context']['cases'] == []
        assert 'record nahi mila' in case_history_lookup_reply(lookup, 'hinglish')
    assert client.get('/api/v1/profile/case-history?category=CONSUMER').json() == []


def test_cross_user_profile_memory_history_and_case_detail_are_denied():
    alice, alice_id = _client('ownership-a')
    bob, bob_id = _client('ownership-b')
    saved = alice.post('/api/v1/profile/memories', json={
        'category': 'recurring', 'text': 'Use concise explanations',
    })
    with SessionLocal() as db:
        alice_case = _case(db, alice_id, 'CONSUMER', 'Alice private matter')
        bob_case = _case(db, bob_id, 'EMPLOYMENT', 'Bob private matter')
        profile = ConversationalLegalAgent()._init_case_profile(
            'Private consumer issue', case_id=alice_case.id, category_override='CONSUMER',
        )
        db.add(ChatCaseSessionModel(case_id=alice_case.id, user_id=alice_id, is_demo=False,
                                    profile_data=profile.model_dump(mode='json'), messages_data=[]))
        alice_case_id, bob_case_id = alice_case.id, bob_case.id
        db.commit()
    assert bob.get('/api/v1/profile').json()['id'] == bob_id
    assert bob.get('/api/v1/profile/memories').json() == []
    assert bob.delete(f"/api/v1/profile/memories/{saved.json()['id']}").status_code == 404
    assert bob.get('/api/v1/profile/case-history?category=CONSUMER').json() == []
    assert bob.get(f'/api/v1/cases/{alice_case_id}').status_code == 404
    assert bob.get(f'/api/v1/chat/cases/{alice_case_id}').status_code == 404
    assert alice.get(f'/api/v1/cases/{bob_case_id}').status_code == 404
