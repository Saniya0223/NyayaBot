from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def authenticated_client(label: str) -> TestClient:
    authenticated = TestClient(app)
    response = authenticated.post(
        "/api/v1/auth/signup",
        json={
            "full_name": f"{label} User",
            "email": f"{label.lower()}@example.com",
            "password": "StrongPass123!",
        },
    )
    assert response.status_code == 201
    return authenticated


def test_llm_status_is_explicit_when_key_is_missing():
    response = client.get("/api/v1/llm/status")
    assert response.status_code == 200
    body = response.json()
    assert body["provider"] in {"gemini", "groq"}
    assert body["mode"] in {"gemini", "groq", "limited_demo"}
    assert body["configured"] == (body["mode"] != "limited_demo")

def test_api_root():
    res = client.get("/")
    assert res.status_code == 200
    data = res.json()
    assert data["app"] == "NyayaBot"

def test_api_intake_and_case_lifecycle():
    auth_client = authenticated_client("Lifecycle")
    # 1. Post Intake
    payload = {
        "user_narrative": "I purchased a Samsung Smart TV on 12-01-2026 for Rs. 42,000 from Croma. The screen was cracked on delivery. They refuse to replace it.",
        "user_name": "Saniya Sharma",
        "user_city": "Pune",
        "user_state": "Maharashtra"
    }
    res = auth_client.post("/api/v1/intake", json=payload)
    assert res.status_code == 200
    case_data = res.json()
    
    case_id = case_data["id"]
    assert case_data["category"] == "CONSUMER"
    assert "DCDRC" in case_data["appropriate_forum"] or "District" in case_data["appropriate_forum"]
    assert case_data["limitation_days_remaining"] is not None
    assert case_data["limitation_days_remaining"] > 300
    assert len(case_data["applicable_statutes"]) > 0

    # 2. Get Case by ID
    get_res = auth_client.get(f"/api/v1/cases/{case_id}")
    assert get_res.status_code == 200
    assert get_res.json()["id"] == case_id

    # 3. Generate Legal Document
    doc_payload = {
        "case_id": case_id,
        "doc_type": "FORMAL_LEGAL_NOTICE"
    }
    doc_res = auth_client.post("/api/v1/documents/generate", json=doc_payload)
    assert doc_res.status_code == 200
    doc_data = doc_res.json()
    assert "Consumer Grievance Letter" in doc_data["title"]
    assert doc_data["pdf_download_url"] is not None
    assert doc_data["docx_download_url"] is not None

    pdf_res = auth_client.get(doc_data["pdf_download_url"])
    assert pdf_res.status_code == 200
    assert pdf_res.content.startswith(b"%PDF")

    docx_res = auth_client.get(doc_data["docx_download_url"])
    assert docx_res.status_code == 200
    assert docx_res.content.startswith(b"PK")

    # 4. Get Government Portal Dossier
    dossier_res = auth_client.get(f"/api/v1/cases/{case_id}/dossier")
    assert dossier_res.status_code == 200
    dossier_data = dossier_res.json()
    assert "e-Daakhil" in dossier_data["portal_name"]
    assert len(dossier_data["steps"]) >= 4

    # 5. Get Statutes
    statutes_res = client.get("/api/v1/statutes")
    assert statutes_res.status_code == 200
    assert "CONSUMER" in statutes_res.json()


def test_chat_case_is_persisted_and_retrievable():
    auth_client = authenticated_client("ChatPersistence")
    response = auth_client.post(
        "/api/v1/chat/message",
        json={"message": "A seller in Jaipur has not refunded Rs 12,500 for a defective phone."},
    )
    assert response.status_code == 200
    case_id = response.json()["case_profile"]["case_id"]

    session = auth_client.get(f"/api/v1/chat/cases/{case_id}")
    assert session.status_code == 200
    payload = session.json()
    assert payload["case_profile"]["case_id"] == case_id
    assert len(payload["messages"]) == 2

    cases = auth_client.get("/api/v1/chat/cases")
    assert cases.status_code == 200
    assert any(item["case_id"] == case_id for item in cases.json())
    assert all(not item["case_id"].startswith("demo-") for item in cases.json())


def test_case_summary_is_on_demand_cached_and_owner_only(monkeypatch):
    from types import SimpleNamespace
    from app import main as main_module

    owner = authenticated_client("SummaryOwner")
    other = authenticated_client("SummaryOther")
    started = owner.post("/api/v1/chat/message", json={
        "message": "I paid Rs 25,000 for a defective phone from Example Seller."
    })
    assert started.status_code == 200
    case_id = started.json()["case_profile"]["case_id"]

    class FakeProvider:
        def __init__(self):
            self.status = SimpleNamespace(configured=True)
            self.calls = 0

        async def chat(self, context):
            self.calls += 1
            assert context.case_summary["category"] == "CONSUMER"
            return "Situation\nThe user reported a phone dispute."

    provider = FakeProvider()
    monkeypatch.setattr(main_module, "get_llm_provider", lambda: provider)
    assert provider.calls == 0
    assert other.post(f"/api/v1/chat/cases/{case_id}/summary").status_code == 404
    first = owner.post(f"/api/v1/chat/cases/{case_id}/summary")
    assert first.status_code == 200, first.text
    assert first.json()["cached"] is False
    second = owner.post(f"/api/v1/chat/cases/{case_id}/summary")
    assert second.status_code == 200
    assert second.json()["cached"] is True
    assert provider.calls == 1


def test_explicit_salary_document_generates_from_understanding_case_with_optional_skipped():
    auth_client = authenticated_client("ExplicitSalary")
    started = auth_client.post("/api/v1/chat/message", json={"message": "My employer has not paid my salary for four months."})
    assert started.status_code == 200
    case_id = started.json()["case_profile"]["case_id"]
    assert started.json()["case_profile"]["recommended_doc_type"] is None

    requested = auth_client.post("/api/v1/chat/message", json={"case_id": case_id, "message": "create salary notice pdf"})
    assert requested.status_code == 200
    body = requested.json()
    assert body["case_profile"]["readiness"] == "UNDERSTANDING_CASE"
    assert body["case_profile"]["recommended_doc_type"] is None
    assert body["suggested_action"]["type"] == "PREPARE_DOC"
    assert body["suggested_action"]["intent"] == "USER_REQUESTED"
    assert body["suggested_action"]["open_confirmation_modal"] is True
    assert body["case_profile"]["documents"] == []
    assert "queued" not in body["reply_text"].lower()

    assessment = auth_client.get("/api/v1/documents/assessment", params={"case_id": case_id, "doc_type": "SALARY_DEMAND_NOTICE"})
    assert assessment.status_code == 200
    assert "employee_role" in assessment.json()["missing_optional_fields"]
    assert not assessment.json()["ready_to_generate"]

    created = auth_client.post("/api/v1/documents/generate", json={
        "case_id": case_id, "doc_type": "SALARY_DEMAND_NOTICE",
        "override_data": {
            "complainant_name": "Example Employee", "opposite_party_name": "Example Employer",
            "complainant_city": "Jaipur", "disputed_amount": 400000,
        },
    })
    assert created.status_code == 200, created.text
    assert "N/A" not in created.json()["content_html"]
    assert auth_client.get(created.json()["pdf_download_url"]).content.startswith(b"%PDF")
    assert auth_client.get(created.json()["docx_download_url"]).content.startswith(b"PK")
    assert auth_client.post("/api/v1/documents/generate", json={
        "case_id": case_id, "doc_type": "UNSUPPORTED_CONTRACT", "override_data": {},
    }).status_code == 422


def test_real_evidence_upload_extracts_candidates_and_requires_confirmation():
    auth_client = authenticated_client("Evidence")
    started = auth_client.post(
        "/api/v1/chat/message",
        json={"message": "My landlord has not returned my deposit."},
    )
    case_id = started.json()["case_profile"]["case_id"]
    agreement = (
        b"Tenant: Rahul Sharma\nLandlord: Raj Verma\n"
        b"Security Deposit: Rs 50,000\nProperty Address: Sector 62 Noida\n"
    )
    uploaded = auth_client.post(
        "/api/v1/chat/upload-file",
        data={"case_id": case_id, "doc_type": "RENTAL_AGREEMENT"},
        files={"upload": ("rental-agreement.txt", agreement, "text/plain")},
    )
    assert uploaded.status_code == 200
    upload_profile = uploaded.json()["case_profile"]
    # Upload only stores the original; extraction runs as a background task
    # (the test client runs it before returning) and nothing is applied yet.
    assert "pending_document_extraction" not in upload_profile["key_facts"]
    evidence_id = upload_profile["key_facts"]["last_upload"]["evidence_id"]
    processed = auth_client.get(f"/api/v1/evidence/{evidence_id}").json()
    assert processed["processing_status"] == "COMPLETED"
    assert processed["pages"][0]["method"] == "text_extraction"
    reviewed = auth_client.post(f"/api/v1/evidence/{evidence_id}/review")
    assert reviewed.status_code == 200
    upload_profile = reviewed.json()["case_profile"]
    candidates = upload_profile["key_facts"]["pending_document_extraction"]["facts"]
    assert candidates["user_name"] == "Rahul Sharma"
    assert candidates["opposite_party_name"] == "Raj Verma"
    assert candidates["disputed_amount"] == 50000
    assert upload_profile["user_name"] is None
    assert any(item["id"] == "rental_agreement" and item["is_available"] for item in upload_profile["evidence_checklist"])
    assert any(item["name"] == "rental-agreement.txt" for item in upload_profile["provided_documents"])
    assert any(item["name"] == "rental-agreement.txt" for item in auth_client.get(f"/api/v1/chat/cases/{case_id}").json()["case_profile"]["provided_documents"])

    confirmed = auth_client.post(
        "/api/v1/chat/message",
        json={"case_id": case_id, "message": "Details are correct"},
    )
    assert confirmed.status_code == 200
    confirmed_profile = confirmed.json()["case_profile"]
    assert confirmed_profile["user_name"] == "Rahul Sharma"
    assert confirmed_profile["opposite_party_name"] == "Raj Verma"
    assert confirmed_profile["fact_metadata"]["user_name"]["confirmed"] is True


def test_resolve_case_is_idempotent():
    auth_client = authenticated_client("Resolve")
    started = auth_client.post(
        "/api/v1/chat/message",
        json={"message": "My company has not paid my salary in Delhi."},
    )
    case_id = started.json()["case_profile"]["case_id"]
    first = auth_client.post(f"/api/v1/chat/cases/{case_id}/resolve")
    second = auth_client.post(f"/api/v1/chat/cases/{case_id}/resolve")
    assert first.status_code == 200
    assert second.status_code == 200
    profile = second.json()
    assert profile["current_stage_key"] == "RESOLVED"
    assert len([event for event in profile["timeline"] if event["type"] == "case_resolved"]) == 1
