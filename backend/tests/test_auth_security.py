import hashlib
import uuid
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from app.db.models import AuthSessionModel, CaseModel, ChatCaseSessionModel, UserModel
from app.db.session import SessionLocal
from app.main import app


PASSWORD = "CorrectHorse123!"


def signup_client(label: str) -> tuple[TestClient, dict]:
    client = TestClient(app)
    email = f"{label.lower()}-{uuid.uuid4().hex[:8]}@example.com"
    response = client.post(
        "/api/v1/auth/signup",
        json={"full_name": f"{label} Citizen", "email": email, "password": PASSWORD},
    )
    assert response.status_code == 201
    return client, response.json()


def test_signup_hashes_password_and_me_returns_safe_user():
    client, created = signup_client("Hash")
    assert "password" not in created
    assert "password_hash" not in created

    with SessionLocal() as db:
        user = db.query(UserModel).filter(UserModel.id == created["id"]).one()
        assert user.password_hash != PASSWORD
        assert user.password_hash.startswith("$argon2id$")

    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["id"] == created["id"]
    assert "password_hash" not in me.json()


def test_session_cookie_is_httponly_and_only_its_hash_is_stored():
    client = TestClient(app)
    email = f"cookie-{uuid.uuid4().hex[:8]}@example.com"
    response = client.post(
        "/api/v1/auth/signup",
        json={"full_name": "Cookie Citizen", "email": email, "password": PASSWORD},
    )
    assert response.status_code == 201
    set_cookie = response.headers["set-cookie"]
    assert "HttpOnly" in set_cookie
    assert "SameSite=lax" in set_cookie
    assert "Path=/api/v1" in set_cookie
    raw_token = client.cookies.get("nyayabot_session")
    assert raw_token
    with SessionLocal() as db:
        stored = db.query(AuthSessionModel).filter_by(user_id=response.json()["id"]).one()
        assert stored.token_hash != raw_token
        assert stored.token_hash == hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def test_login_wrong_password_duplicate_email_and_unauthenticated_access():
    signed_up, created = signup_client("Login")
    assert signed_up.post("/api/v1/auth/logout").status_code == 204
    assert signed_up.get("/api/v1/auth/me").status_code == 401

    login_client = TestClient(app)
    success = login_client.post(
        "/api/v1/auth/login",
        json={"email": created["email"].upper(), "password": PASSWORD},
    )
    assert success.status_code == 200
    assert login_client.get("/api/v1/auth/me").status_code == 200

    wrong = TestClient(app).post(
        "/api/v1/auth/login",
        json={"email": created["email"], "password": "wrong-password"},
    )
    assert wrong.status_code == 401

    duplicate = TestClient(app).post(
        "/api/v1/auth/signup",
        json={"full_name": "Duplicate", "email": created["email"].upper(), "password": PASSWORD},
    )
    assert duplicate.status_code == 409
    assert TestClient(app).get("/api/v1/chat/cases").status_code == 401


def test_logout_revokes_the_server_side_session():
    client, created = signup_client("Logout")
    with SessionLocal() as db:
        assert db.query(AuthSessionModel).filter(AuthSessionModel.user_id == created["id"]).count() == 1
    assert client.post("/api/v1/auth/logout").status_code == 204
    with SessionLocal() as db:
        assert db.query(AuthSessionModel).filter(AuthSessionModel.user_id == created["id"]).count() == 0
    assert client.get("/api/v1/auth/me").status_code == 401


def test_expired_session_and_untrusted_write_origin_are_rejected():
    client, created = signup_client("Expiry")
    blocked = client.post(
        "/api/v1/chat/message",
        headers={"Origin": "https://attacker.example"},
        json={"message": "This request must not create a case."},
    )
    assert blocked.status_code == 403

    with SessionLocal() as db:
        session = db.query(AuthSessionModel).filter_by(user_id=created["id"]).one()
        session.expires_at = datetime.utcnow() - timedelta(seconds=1)
        db.commit()
    assert client.get("/api/v1/auth/me").status_code == 401


def test_case_data_is_fully_isolated_between_users():
    user_a, account_a = signup_client("OwnerA")
    user_b, _account_b = signup_client("OwnerB")

    chat = user_a.post(
        "/api/v1/chat/message",
        json={"message": "My landlord in Pune has not returned my Rs 25,000 deposit."},
    )
    assert chat.status_code == 200
    chat_case_id = chat.json()["case_profile"]["case_id"]

    assert any(
        item["case_id"] == chat_case_id for item in user_a.get("/api/v1/chat/cases").json()
    )
    assert all(
        item["case_id"] != chat_case_id for item in user_b.get("/api/v1/chat/cases").json()
    )
    assert user_b.get(f"/api/v1/chat/cases/{chat_case_id}").status_code == 404
    assert user_b.post(
        "/api/v1/chat/message",
        json={"case_id": chat_case_id, "message": "Show me this case"},
    ).status_code == 404
    assert user_b.post(
        "/api/v1/chat/upload-document",
        json={"case_id": chat_case_id, "doc_type": "INVOICE", "file_name": "invoice.txt"},
    ).status_code == 404
    assert user_b.post(
        "/api/v1/chat/upload-file",
        data={"case_id": chat_case_id, "doc_type": "INVOICE"},
        files={"upload": ("invoice.txt", b"Invoice total Rs 25,000", "text/plain")},
    ).status_code == 404

    owner_upload = user_a.post(
        "/api/v1/chat/upload-file",
        data={"case_id": chat_case_id, "doc_type": "INVOICE"},
        files={"upload": ("invoice.txt", b"Invoice total Rs 25,000", "text/plain")},
    )
    assert owner_upload.status_code == 200
    evidence_id = owner_upload.json()["case_profile"]["key_facts"]["last_upload"]["evidence_id"]
    assert user_a.get(f"/api/v1/evidence/{evidence_id}/download").status_code == 200
    assert user_b.get(f"/api/v1/evidence/{evidence_id}/download").status_code == 404

    intake = user_a.post(
        "/api/v1/intake",
        json={
            "user_narrative": "I purchased a phone on 12-01-2026 for Rs. 42,000 from Croma and it arrived broken. Croma refused my written refund request.",
            "user_name": "Owner A",
            "user_city": "Pune",
            "user_state": "Maharashtra",
        },
    )
    assert intake.status_code == 200
    legacy_case_id = intake.json()["id"]
    assert user_b.get(f"/api/v1/cases/{legacy_case_id}").status_code == 404
    assert user_b.get(f"/api/v1/cases/{legacy_case_id}/dossier").status_code == 404
    assert user_b.post(
        "/api/v1/documents/generate",
        json={"case_id": legacy_case_id, "doc_type": "FORMAL_LEGAL_NOTICE"},
    ).status_code == 404

    generated = user_a.post(
        "/api/v1/documents/generate",
        json={"case_id": legacy_case_id, "doc_type": "FORMAL_LEGAL_NOTICE"},
    )
    assert generated.status_code == 200
    download_url = generated.json()["pdf_download_url"]
    assert user_a.get(download_url).status_code == 200
    assert user_b.get(download_url).status_code == 404
    assert all(item["case_id"] != legacy_case_id for item in user_b.get("/api/v1/documents").json())

    dashboard_a = user_a.get("/api/v1/dashboard").json()
    dashboard_b = user_b.get("/api/v1/dashboard").json()
    assert dashboard_a["total_cases"] == 2
    assert dashboard_a["documents"] == 1
    assert dashboard_a["evidence_files"] == 1
    assert dashboard_b == {
        "total_cases": 0,
        "active_cases": 0,
        "documents": 0,
        "evidence_files": 0,
    }

    profile = chat.json()["case_profile"]
    with SessionLocal() as db:
        legacy_id = f"legacy-{uuid.uuid4().hex}"
        demo_id = f"demo-{uuid.uuid4().hex}"
        for case_id, is_demo in ((legacy_id, False), (demo_id, True)):
            case_profile = {**profile, "case_id": case_id, "case_number": f"NYA-{case_id[-8:]}"}
            db.add(
                CaseModel(
                    id=case_id,
                    case_number=case_profile["case_number"],
                    title="Unowned case",
                    category="CONSUMER",
                    user_id=None,
                )
            )
            db.add(
                ChatCaseSessionModel(
                    case_id=case_id,
                    user_id=None,
                    is_demo=is_demo,
                    profile_data=case_profile,
                    messages_data=[],
                )
            )
        db.commit()

    visible_ids = {item["case_id"] for item in user_a.get("/api/v1/chat/cases").json()}
    assert legacy_id not in visible_ids
    assert demo_id not in visible_ids
    assert user_a.get(f"/api/v1/chat/cases/{legacy_id}").status_code == 404
    assert user_a.get(f"/api/v1/chat/cases/{demo_id}").status_code == 404

    with SessionLocal() as db:
        owned_chat = db.query(ChatCaseSessionModel).filter_by(case_id=chat_case_id).one()
        owned_case = db.query(CaseModel).filter_by(id=chat_case_id).one()
        assert owned_chat.user_id == account_a["id"]
        assert owned_case.user_id == account_a["id"]
