"""Deleting a saved case must remove only the signed-in user's case data."""

from datetime import datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient

from app.config import settings
from app.db.models import (
    AutofillSessionModel, CaseModel, ChatCaseSessionModel, EvidenceFileModel,
    GeneratedDocumentModel, UserModel,
)
from app.db.session import SessionLocal
from app.main import app
from app.schemas.chat import StructuredCaseProfile


def _signed_in(label: str) -> tuple[TestClient, str]:
    client = TestClient(app)
    email = f"{label.replace(' ', '').lower()}-{uuid4().hex[:8]}@example.com"
    response = client.post("/api/v1/auth/signup", json={
        "full_name": label, "email": email, "password": "StrongPass123!",
    })
    assert response.status_code == 201
    with SessionLocal() as db:
        user_id = db.query(UserModel.id).filter(UserModel.email == email).scalar()
    return client, user_id


def _add_chat_case(db, user_id: str) -> str:
    case_id = str(uuid4())
    case_number = f"NYA-{uuid4().hex[:10]}"
    profile = StructuredCaseProfile(
        case_id=case_id, case_number=case_number, title="Saved test case",
        category="GENERAL", category_display_name="General", issue_type="general",
        current_stage_key="INTAKE", current_stage_label="Understanding your situation",
    )
    db.add(CaseModel(
        id=case_id, user_id=user_id, case_number=case_number,
        title=profile.title, category=profile.category,
    ))
    db.add(ChatCaseSessionModel(
        case_id=case_id, user_id=user_id, is_demo=False,
        profile_data=profile.model_dump(mode="json"), messages_data=[],
    ))
    db.commit()
    return case_id


def test_delete_chat_case_is_owner_scoped_and_removes_attachments(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "STORAGE_DIR", str(tmp_path))
    evidence_dir = tmp_path / "evidence"
    document_dir = tmp_path / "documents"
    evidence_dir.mkdir()
    document_dir.mkdir()

    owner, owner_id = _signed_in("Case owner")
    other, other_id = _signed_in("Other owner")
    with SessionLocal() as db:
        case_id = _add_chat_case(db, owner_id)
        other_case_id = _add_chat_case(db, other_id)
        evidence_path = evidence_dir / f"{uuid4()}.txt"
        outside_path = tmp_path / "unrelated.txt"
        pdf_path = document_dir / f"notice_{uuid4().hex}.pdf"
        docx_path = pdf_path.with_suffix(".docx")
        for path in (evidence_path, outside_path, pdf_path, docx_path):
            path.write_bytes(b"test")
        db.add_all([
            EvidenceFileModel(case_id=case_id, file_name="evidence.txt", file_path=str(evidence_path)),
            EvidenceFileModel(case_id=case_id, file_name="unrelated.txt", file_path=str(outside_path)),
            GeneratedDocumentModel(
                case_id=case_id, doc_type="NOTICE", title="Notice",
                pdf_filename=pdf_path.name,
                pdf_download_url=f"/api/v1/documents/download/{pdf_path.name}",
            ),
            AutofillSessionModel(
                user_id=owner_id, case_id=case_id, portal="test",
                code_hash=uuid4().hex, expires_at=datetime.utcnow() + timedelta(hours=1),
            ),
        ])
        db.commit()

    url = f"/api/v1/chat/cases/{case_id}"
    assert TestClient(app).delete(url).status_code == 401
    assert other.delete(url).status_code == 404
    assert owner.get(url).status_code == 200
    assert owner.delete(url).status_code == 204
    assert owner.delete(url).status_code == 404
    assert owner.get(url).status_code == 404
    assert all(item["case_id"] != case_id for item in owner.get("/api/v1/chat/cases").json())
    assert other.get(f"/api/v1/chat/cases/{other_case_id}").status_code == 200

    with SessionLocal() as db:
        assert db.query(CaseModel).filter(CaseModel.id == case_id).count() == 0
        for model in (ChatCaseSessionModel, EvidenceFileModel, GeneratedDocumentModel, AutofillSessionModel):
            assert db.query(model).filter(model.case_id == case_id).count() == 0
    assert not evidence_path.exists()
    assert not pdf_path.exists()
    assert not docx_path.exists()
    assert outside_path.exists()
