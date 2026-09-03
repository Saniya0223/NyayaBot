from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_llm_status_is_explicit_when_key_is_missing():
    response = client.get("/api/v1/llm/status")
    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == "gemini"
    assert body["mode"] in {"gemini", "limited_demo"}
    assert body["configured"] == (body["mode"] == "gemini")

def test_api_root():
    res = client.get("/")
    assert res.status_code == 200
    data = res.json()
    assert data["app"] == "NyayaBot"

def test_api_intake_and_case_lifecycle():
    # 1. Post Intake
    payload = {
        "user_narrative": "I purchased a Samsung Smart TV on 12-01-2026 for Rs. 42,000 from Croma. The screen was cracked on delivery. They refuse to replace it.",
        "user_name": "Saniya Sharma",
        "user_city": "Pune",
        "user_state": "Maharashtra"
    }
    res = client.post("/api/v1/intake", json=payload)
    assert res.status_code == 200
    case_data = res.json()
    
    case_id = case_data["id"]
    assert case_data["category"] == "CONSUMER"
    assert "DCDRC" in case_data["appropriate_forum"] or "District" in case_data["appropriate_forum"]
    assert case_data["limitation_days_remaining"] is not None
    assert case_data["limitation_days_remaining"] > 300
    assert len(case_data["applicable_statutes"]) > 0

    # 2. Get Case by ID
    get_res = client.get(f"/api/v1/cases/{case_id}")
    assert get_res.status_code == 200
    assert get_res.json()["id"] == case_id

    # 3. Generate Legal Document
    doc_payload = {
        "case_id": case_id,
        "doc_type": "FORMAL_LEGAL_NOTICE"
    }
    doc_res = client.post("/api/v1/documents/generate", json=doc_payload)
    assert doc_res.status_code == 200
    doc_data = doc_res.json()
    assert "Consumer Grievance Letter" in doc_data["title"]
    assert doc_data["pdf_download_url"] is not None
    assert doc_data["docx_download_url"] is not None

    pdf_res = client.get(doc_data["pdf_download_url"])
    assert pdf_res.status_code == 200
    assert pdf_res.content.startswith(b"%PDF")

    docx_res = client.get(doc_data["docx_download_url"])
    assert docx_res.status_code == 200
    assert docx_res.content.startswith(b"PK")

    # 4. Get Government Portal Dossier
    dossier_res = client.get(f"/api/v1/cases/{case_id}/dossier")
    assert dossier_res.status_code == 200
    dossier_data = dossier_res.json()
    assert "e-Daakhil" in dossier_data["portal_name"]
    assert len(dossier_data["steps"]) >= 4

    # 5. Get Statutes
    statutes_res = client.get("/api/v1/statutes")
    assert statutes_res.status_code == 200
    assert "CONSUMER" in statutes_res.json()


def test_chat_case_is_persisted_and_retrievable():
    response = client.post(
        "/api/v1/chat/message",
        json={"message": "A seller in Jaipur has not refunded Rs 12,500 for a defective phone."},
    )
    assert response.status_code == 200
    case_id = response.json()["case_profile"]["case_id"]

    session = client.get(f"/api/v1/chat/cases/{case_id}")
    assert session.status_code == 200
    payload = session.json()
    assert payload["case_profile"]["case_id"] == case_id
    assert len(payload["messages"]) == 2

    cases = client.get("/api/v1/chat/cases")
    assert cases.status_code == 200
    assert any(item["case_id"] == case_id for item in cases.json())
    case_ids = {item["case_id"] for item in cases.json()}
    assert {"demo-tenant", "demo-consumer", "demo-salary", "demo-cyber"}.issubset(case_ids)


def test_real_evidence_upload_extracts_candidates_and_requires_confirmation():
    started = client.post(
        "/api/v1/chat/message",
        json={"message": "My landlord has not returned my deposit."},
    )
    case_id = started.json()["case_profile"]["case_id"]
    agreement = (
        b"Tenant: Rahul Sharma\nLandlord: Raj Verma\n"
        b"Security Deposit: Rs 50,000\nProperty Address: Sector 62 Noida\n"
    )
    uploaded = client.post(
        "/api/v1/chat/upload-file",
        data={"case_id": case_id, "doc_type": "RENTAL_AGREEMENT"},
        files={"upload": ("rental-agreement.txt", agreement, "text/plain")},
    )
    assert uploaded.status_code == 200
    upload_profile = uploaded.json()["case_profile"]
    candidates = upload_profile["key_facts"]["pending_document_extraction"]["facts"]
    assert candidates["user_name"] == "Rahul Sharma"
    assert candidates["opposite_party_name"] == "Raj Verma"
    assert candidates["disputed_amount"] == 50000
    assert upload_profile["user_name"] is None
    assert any(item["id"] == "rental_agreement" and item["is_available"] for item in upload_profile["evidence_checklist"])

    confirmed = client.post(
        "/api/v1/chat/message",
        json={"case_id": case_id, "message": "Details are correct"},
    )
    assert confirmed.status_code == 200
    confirmed_profile = confirmed.json()["case_profile"]
    assert confirmed_profile["user_name"] == "Rahul Sharma"
    assert confirmed_profile["opposite_party_name"] == "Raj Verma"
    assert confirmed_profile["fact_metadata"]["user_name"]["confirmed"] is True


def test_resolve_case_is_idempotent():
    started = client.post(
        "/api/v1/chat/message",
        json={"message": "My company has not paid my salary in Delhi."},
    )
    case_id = started.json()["case_profile"]["case_id"]
    first = client.post(f"/api/v1/chat/cases/{case_id}/resolve")
    second = client.post(f"/api/v1/chat/cases/{case_id}/resolve")
    assert first.status_code == 200
    assert second.status_code == 200
    profile = second.json()
    assert profile["current_stage_key"] == "RESOLVED"
    assert len([event for event in profile["timeline"] if event["type"] == "case_resolved"]) == 1
