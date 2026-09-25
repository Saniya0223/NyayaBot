from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text

from app.db.models import CaseModel, UserMemoryModel, UserModel
from app.db.session import SessionLocal
from app.db.migrations import apply_additive_migrations
from app.main import _document_validation_data, app
from app.schemas.fact_graph import FactGraphSchema, PartyInfo
from app.services.user_context import chat_user_context, explicit_memory_text


def signed_in(label: str) -> TestClient:
    client = TestClient(app)
    response = client.post('/api/v1/auth/signup', json={
        'full_name': f'{label} User', 'email': f'{label}@example.com', 'password': 'StrongPass123!',
    })
    assert response.status_code == 201
    return client


def test_profile_validation_and_owner_scoped_memory():
    alice = signed_in('profilealice')
    bob = signed_in('profilebob')
    assert alice.get('/api/v1/profile').json()['email'] == 'profilealice@example.com'
    payload = {
        'full_name': 'Alice Sharma', 'date_of_birth': '1999-04-03', 'phone': '9876543210',
        'state': 'Rajasthan', 'city': 'Jaipur', 'pin_code': '302001',
        'full_address': '12 Test Lane', 'preferred_language': 'hinglish',
    }
    updated = alice.put('/api/v1/profile', json=payload)
    assert updated.status_code == 200
    assert updated.json()['date_of_birth'] == '1999-04-03'
    assert updated.json()['email'] == 'profilealice@example.com'
    assert alice.put('/api/v1/profile', json={**payload, 'pin_code': '123'}).status_code == 422
    assert alice.put('/api/v1/profile', json={**payload, 'date_of_birth': '2099-01-01'}).status_code == 422

    created = alice.post('/api/v1/profile/memories', json={'category': 'preference', 'text': 'Explain simply'})
    assert created.status_code == 201
    memory_id = created.json()['id']
    assert len(alice.get('/api/v1/profile/memories').json()) == 1
    assert bob.get('/api/v1/profile/memories').json() == []
    assert bob.delete(f'/api/v1/profile/memories/{memory_id}').status_code == 404
    assert alice.delete(f'/api/v1/profile/memories/{memory_id}').status_code == 204
    assert alice.get('/api/v1/profile/memories').json() == []


def test_case_history_stays_owned_and_chat_context_is_selective():
    alice = signed_in('historyalice')
    bob = signed_in('historybob')
    db = SessionLocal()
    try:
        owner = db.query(UserModel).filter(UserModel.email == 'historyalice@example.com').one()
        other = db.query(UserModel).filter(UserModel.email == 'historybob@example.com').one()
        db.add(UserMemoryModel(user_id=owner.id, category='recurring', text='I use a mobility aid at work'))
        db.add(CaseModel(user_id=owner.id, case_number='HISTORY-A', title='Salary matter', category='EMPLOYMENT'))
        db.add(CaseModel(user_id=other.id, case_number='HISTORY-B', title='Private dispute', category='CONSUMER'))
        db.commit()
        history = alice.get('/api/v1/profile/case-history').json()
        assert any(item['title'] == 'Salary matter' for item in history)
        assert all(item['title'] != 'Private dispute' for item in history)
        ordinary = chat_user_context(db, owner, 'Hello', None)
        assert 'previous_case_context' not in ordinary
        assert 'profile_context' not in ordinary
        assert 'saved_memory_context' not in ordinary
        assert 'mobility aid' in str(chat_user_context(db, owner, 'Does my mobility aid affect work?', None))
        assert chat_user_context(db, owner, 'What is my name?', None)['profile_context']['full_name'] == 'historyalice User'
        assert 'Private dispute' not in str(chat_user_context(db, owner, 'Tell me about my previous case', None))
        assert 'Salary matter' in str(chat_user_context(db, owner, 'Tell me about my previous case', None))
    finally:
        db.close()
    assert bob.get('/api/v1/profile/case-history').json()[0]['title'] == 'Private dispute'


def test_document_prefill_respects_case_values_and_explicit_clear():
    user = UserModel(full_name='Profile Name', email='profile@example.com', city='Jaipur',
                     state='Rajasthan', full_address='Profile Address', pin_code='302001', phone='9999999999')
    facts = FactGraphSchema(complainant=PartyInfo(name='Complainant', city='Delhi', address=None))
    values = _document_validation_data(facts, {}, user)
    assert values['complainant_name'] == 'Profile Name'
    assert values['complainant_city'] == 'Delhi'
    assert values['complainant_address'] == 'Profile Address'
    assert values['complainant_pin_code'] == '302001'
    assert _document_validation_data(facts, {'complainant_address': ''}, user)['complainant_address'] == ''


def test_only_explicit_safe_memory_request_is_detected():
    assert explicit_memory_text('Please remember that I prefer concise explanations.') == 'I prefer concise explanations'
    assert explicit_memory_text('I mentioned this earlier.') is None
    assert explicit_memory_text('Remember my password is abc123') is None


def test_existing_user_table_gets_additive_profile_columns(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'old.db').as_posix()}")
    with engine.begin() as connection:
        connection.execute(text('CREATE TABLE users (id VARCHAR(36) PRIMARY KEY, full_name VARCHAR(255), email VARCHAR(255))'))
        connection.execute(text("INSERT INTO users VALUES ('legacy', 'Legacy User', 'legacy@example.com')"))
    apply_additive_migrations(engine)
    columns = {column['name'] for column in inspect(engine).get_columns('users')}
    assert {'date_of_birth', 'pin_code', 'full_address', 'preferred_language'} <= columns
    with engine.connect() as connection:
        assert connection.execute(text("SELECT full_name FROM users WHERE id='legacy'")).scalar() == 'Legacy User'


def test_explicit_chat_memory_is_saved_and_manageable():
    client = signed_in('memorychat')
    response = client.post('/api/v1/chat/message', json={
        'message': 'Remember that I prefer concise explanations.',
    })
    assert response.status_code == 200
    assert 'saved' in response.json()['reply_text'].lower()
    memories = client.get('/api/v1/profile/memories').json()
    assert len(memories) == 1
    assert memories[0]['category'] == 'explicit'
    assert memories[0]['text'] == 'I prefer concise explanations'
