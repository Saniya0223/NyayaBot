import mimetypes
import os
import uuid
import logging
from datetime import datetime
from typing import List

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.agents.conversation_agent import conversational_agent
from app.agents.intake_node import IntakeFactExtractor
from app.agents.orchestrator import legal_orchestrator
from app.agents.rag_node import statutory_rag
from app.config import settings
from app.llm.contracts import ProviderStatus
from app.llm.factory import get_llm_provider
from app.db.models import (
    CaseModel,
    CaseTimelineEventModel,
    ChatCaseSessionModel,
    EvidenceFileModel,
    FactGraphModel,
    GeneratedDocumentModel,
)
from app.db.session import Base, engine, get_db
from app.schemas.case import CaseResponse, ClarificationAnswer, IntakeRequest
from app.schemas.chat import (
    ChatMessage,
    ChatSessionResponse,
    ChatTurnRequest,
    ChatTurnResponse,
    DocumentUploadExtractionRequest,
    StructuredCaseProfile,
)
from app.schemas.document import (
    DocumentDefinitionSchema,
    DocumentGenerateRequest,
    DocumentResponse,
    PortalFilingDossier,
)
from app.schemas.fact_graph import EvidenceItem, FactGraphSchema, FinancialBreakdown, PartyInfo
from app.services.doc_generator import doc_generator
from app.services.document_registry import (
    list_document_definitions,
    validate_document_fields,
)
from app.services.dossier_generator import DossierGenerator
from app.services.upload_intelligence import extract_upload_text, validate_upload
from app.services.llm_conversation import gemini_conversation_service


Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="NyayaBot",
    version=settings.APP_VERSION,
    description="Conversation-first legal information, workflow, and document-assistance MVP for India",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

CHAT_CASE_SESSIONS: dict[str, StructuredCaseProfile] = {}
logger = logging.getLogger("uvicorn.error")
llm_status = get_llm_provider().status
logger.info(
    "provider=%s model=%s configured=%s mode=%s event=llm_initialized",
    llm_status.provider,
    llm_status.model,
    llm_status.configured,
    llm_status.mode,
)


def _safe_profile(record: ChatCaseSessionModel) -> StructuredCaseProfile:
    return StructuredCaseProfile.model_validate(record.profile_data)


def _load_chat_profile(case_id: str, db: Session) -> StructuredCaseProfile | None:
    if case_id in CHAT_CASE_SESSIONS:
        return CHAT_CASE_SESSIONS[case_id]
    record = db.query(ChatCaseSessionModel).filter(ChatCaseSessionModel.case_id == case_id).first()
    if not record:
        return None
    profile = _safe_profile(record)
    CHAT_CASE_SESSIONS[case_id] = profile
    return profile


def _profile_to_fact_graph(profile: StructuredCaseProfile, narrative: str) -> FactGraphSchema:
    evidence = [
        EvidenceItem(
            doc_type=item.id.upper(),
            doc_name=item.name,
            is_available=item.is_available,
            annexure_label=item.annexure_label,
        )
        for item in profile.evidence_checklist
        if item.is_available
    ]
    return FactGraphSchema(
        complainant=PartyInfo(
            name=profile.user_name or "",
            address=profile.key_facts.get("user_address"),
            city=profile.user_city,
            state=profile.user_state,
            phone=profile.user_phone,
        ),
        opposite_party=PartyInfo(
            name=profile.opposite_party_name or profile.bank_name or profile.police_station_name or "",
            address=profile.opposite_party_address or profile.property_address,
            city=profile.user_city,
            state=profile.user_state,
        ),
        incident_narrative=narrative,
        incident_date=profile.vacating_date or profile.incident_date,
        category=profile.category,
        sub_category=profile.issue_type,
        financials=FinancialBreakdown(
            amount_paid=profile.disputed_amount,
            refund_claimed=profile.disputed_amount,
            total_claim_amount=profile.disputed_amount,
        ),
        evidence_inventory=evidence,
        missing_facts=profile.missing_required_fields,
        is_complete=profile.is_ready_for_document,
        completion_score=max(0.2, 1 - (len(profile.missing_required_fields) * 0.12)),
    )


def _sync_profile_to_legacy_case(profile: StructuredCaseProfile, narrative: str, db: Session) -> None:
    case = db.query(CaseModel).filter(CaseModel.id == profile.case_id).first()
    if not case:
        case = CaseModel(
            id=profile.case_id,
            case_number=profile.case_number,
            title=profile.title,
            category=profile.category,
        )
        db.add(case)
        db.flush()
    case.title = profile.title
    case.category = profile.category
    case.status = profile.current_stage_label
    case.pecuniary_value = profile.disputed_amount
    case.cause_of_action_date = profile.vacating_date or profile.incident_date
    case.appropriate_forum = f"Authority competent for {profile.user_city or 'the relevant local area'}"
    case.severity_level = "ESCALATED_LAWYER" if profile.risk_level == "RED" else "STANDARD"

    fact_graph = _profile_to_fact_graph(profile, narrative)
    if not case.fact_graph:
        case.fact_graph = FactGraphModel(case_id=profile.case_id)
    case.fact_graph.complainant_data = fact_graph.complainant.model_dump()
    case.fact_graph.opposite_party_data = fact_graph.opposite_party.model_dump()
    case.fact_graph.incident_narrative = narrative
    case.fact_graph.financial_breakdown = fact_graph.financials.model_dump()
    case.fact_graph.evidence_inventory = [item.model_dump() for item in fact_graph.evidence_inventory]
    case.fact_graph.missing_facts = profile.missing_required_fields
    case.fact_graph.is_complete = profile.is_ready_for_document
    case.fact_graph.completion_score = fact_graph.completion_score


def _save_chat_session(
    profile: StructuredCaseProfile,
    messages: List[ChatMessage],
    db: Session,
) -> None:
    record = db.query(ChatCaseSessionModel).filter(ChatCaseSessionModel.case_id == profile.case_id).first()
    if not record:
        record = ChatCaseSessionModel(case_id=profile.case_id, profile_data={}, messages_data=[])
        db.add(record)
    record.profile_data = profile.model_dump(mode="json")
    record.messages_data = [message.model_dump(mode="json") for message in messages]
    record.updated_at = datetime.utcnow()
    narrative = "\n".join(message.text for message in messages if message.sender == "user") or profile.title
    _sync_profile_to_legacy_case(profile, narrative, db)
    db.commit()
    CHAT_CASE_SESSIONS[profile.case_id] = profile


def _messages_for_session(case_id: str, db: Session) -> List[ChatMessage]:
    record = db.query(ChatCaseSessionModel).filter(ChatCaseSessionModel.case_id == case_id).first()
    if not record:
        return []
    return [ChatMessage.model_validate(message) for message in record.messages_data]


def _seed_demo_sessions(db: Session) -> None:
    demos = {
        "demo-tenant": [
            "My landlord isn't returning my ₹50,000 deposit.",
            "Noida, Uttar Pradesh. I moved out on 10 August 2026.",
            "Yes, both",
            "He just says he'll return it later.",
        ],
        "demo-consumer": [
            "Amazon won't refund ₹20,000 for a defective laptop.",
            "I bought a laptop on 12 August 2026 and I live in Delhi.",
            "I have the invoice and Amazon rejected the refund in writing.",
            "I sent the grievance today by email.",
        ],
        "demo-salary": [
            "My company hasn't paid July and August salary totalling ₹90,000.",
            "I work in Delhi for ABC Pvt Ltd. My monthly salary is ₹45,000.",
            "Yes, both",
        ],
        "demo-cyber": [
            "I lost ₹35,000 in a UPI fraud.",
            "It happened on 30 August 2026. UTR ABC123456789 and my bank is HDFC.",
            "I reported it to the bank and called 1930.",
        ],
    }
    for case_id, turns in demos.items():
        if db.query(ChatCaseSessionModel).filter(ChatCaseSessionModel.case_id == case_id).first():
            continue
        profile = None
        messages: List[ChatMessage] = []
        for turn in turns:
            response = conversational_agent.process_turn(
                ChatTurnRequest(message=turn, case_id=case_id), profile
            )
            profile = response.case_profile
            messages.extend(
                [
                    ChatMessage(id=str(uuid.uuid4()), sender="user", text=turn),
                    ChatMessage(
                        id=response.message_id,
                        sender="bot",
                        text=response.reply_text,
                        quick_replies=response.quick_replies,
                        suggested_action=response.suggested_action,
                    ),
                ]
            )
        if profile:
            _save_chat_session(profile, messages, db)


@app.get("/")
def root():
    return {
        "app": "NyayaBot",
        "version": settings.APP_VERSION,
        "status": "online",
        "jurisdiction": "India",
        "supported_domains": ["consumer", "employment", "tenant", "cybercrime", "police"],
    }


@app.post("/api/v1/intake", response_model=CaseResponse)
def handle_intake(req: IntakeRequest, db: Session = Depends(get_db)):
    if not req.user_narrative or len(req.user_narrative.strip()) < 10:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Please describe the issue in at least 10 characters.",
        )
    return legal_orchestrator.process_intake(req, db)


@app.post("/api/v1/clarifications", response_model=CaseResponse)
def submit_clarifications(payload: ClarificationAnswer, db: Session = Depends(get_db)):
    case = db.query(CaseModel).filter(CaseModel.id == payload.case_id).first()
    if not case or not case.fact_graph:
        raise HTTPException(status_code=404, detail="Case facts not found")
    facts = case.fact_graph
    answers_text = " ".join(f"{question}: {answer}" for question, answer in payload.answers.items())
    return legal_orchestrator.process_intake(
        IntakeRequest(
            user_narrative=f"{facts.incident_narrative}\n\nAdditional clarifications: {answers_text}",
            case_id=case.id,
            user_name=facts.complainant_data.get("name", ""),
            user_city=facts.complainant_data.get("city", ""),
            user_state=facts.complainant_data.get("state", ""),
            user_phone=facts.complainant_data.get("phone"),
            user_email=facts.complainant_data.get("email"),
        ),
        db,
    )


@app.get("/api/v1/cases", response_model=List[CaseResponse])
def list_cases(db: Session = Depends(get_db)):
    results = []
    for case in db.query(CaseModel).order_by(CaseModel.updated_at.desc()).all():
        facts = case.fact_graph
        request = IntakeRequest(
            user_narrative=facts.incident_narrative if facts else case.title,
            case_id=case.id,
            user_name=facts.complainant_data.get("name", "") if facts else "",
            user_city=facts.complainant_data.get("city", "") if facts else "",
            user_state=facts.complainant_data.get("state", "") if facts else "",
        )
        results.append(legal_orchestrator.process_intake(request, db))
    return results


@app.get("/api/v1/cases/{case_id}", response_model=CaseResponse)
def get_case(case_id: str, db: Session = Depends(get_db)):
    case = db.query(CaseModel).filter(CaseModel.id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    facts = case.fact_graph
    return legal_orchestrator.process_intake(
        IntakeRequest(
            user_narrative=facts.incident_narrative if facts else case.title,
            case_id=case.id,
            user_name=facts.complainant_data.get("name", "") if facts else "",
            user_city=facts.complainant_data.get("city", "") if facts else "",
            user_state=facts.complainant_data.get("state", "") if facts else "",
        ),
        db,
    )


@app.post("/api/v1/cases/{case_id}/timeline/{event_id}/toggle")
def toggle_timeline_event(case_id: str, event_id: str, db: Session = Depends(get_db)):
    event = db.query(CaseTimelineEventModel).filter(
        CaseTimelineEventModel.case_id == case_id,
        CaseTimelineEventModel.id == event_id,
    ).first()
    if not event:
        raise HTTPException(status_code=404, detail="Timeline event not found")
    if event.status == "COMPLETED":
        event.status, event.completed_at = "PENDING", None
    else:
        event.status, event.completed_at = "COMPLETED", datetime.utcnow()
    db.commit()
    return {"status": "success", "new_event_status": event.status}


def _document_validation_data(facts: FactGraphSchema, overrides: dict) -> dict:
    return {
        "complainant_name": overrides.get("complainant_name") or facts.complainant.name,
        "recipient_name": overrides.get("recipient_name") or facts.opposite_party.name,
        "opposite_party_name": overrides.get("opposite_party_name") or facts.opposite_party.name,
        "complainant_city": overrides.get("complainant_city") or facts.complainant.city,
        "property_address": overrides.get("property_address") or facts.opposite_party.address,
        "disputed_amount": overrides.get("disputed_amount", facts.financials.amount_paid),
        "vacating_date": overrides.get("vacating_date") or facts.incident_date,
        "incident_date": overrides.get("incident_date") or facts.incident_date,
        "incident_narrative": overrides.get("incident_narrative") or facts.incident_narrative,
        "police_station_name": overrides.get("police_station_name") or facts.opposite_party.name,
        "bank_name": overrides.get("bank_name") or facts.opposite_party.name,
        "transaction_id": overrides.get("transaction_id"),
    }


@app.post("/api/v1/documents/generate", response_model=DocumentResponse)
def generate_document(req: DocumentGenerateRequest, db: Session = Depends(get_db)):
    case = db.query(CaseModel).filter(CaseModel.id == req.case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    profile = _load_chat_profile(req.case_id, db)
    if profile:
        messages = _messages_for_session(req.case_id, db)
        narrative = "\n".join(message.text for message in messages if message.sender == "user") or profile.title
        fact_graph = _profile_to_fact_graph(profile, narrative)
    elif case.fact_graph:
        stored = case.fact_graph
        fact_graph = IntakeFactExtractor.extract_facts(
            stored.incident_narrative,
            {
                "user_name": stored.complainant_data.get("name"),
                "user_city": stored.complainant_data.get("city"),
                "user_state": stored.complainant_data.get("state"),
                "user_phone": stored.complainant_data.get("phone"),
                "user_email": stored.complainant_data.get("email"),
                "opposite_party_name": stored.opposite_party_data.get("name"),
                "amount_paid": stored.financial_breakdown.get("amount_paid", 0),
                "incident_date": case.cause_of_action_date,
                "category": case.category,
            },
        )
    else:
        raise HTTPException(status_code=404, detail="Case facts not found")

    overrides = req.override_data or {}
    missing = validate_document_fields(req.doc_type, _document_validation_data(fact_graph, overrides))
    if missing:
        raise HTTPException(
            status_code=422,
            detail={"message": "Confirm the required facts before generating this document.", "missing_fields": missing},
        )
    response = doc_generator.generate_document(
        case_id=case.id,
        doc_type=req.doc_type,
        fact_graph=fact_graph,
        appropriate_forum=case.appropriate_forum or "Competent authority",
        custom_data=overrides,
    )
    db.add(
        GeneratedDocumentModel(
            id=response.id,
            case_id=case.id,
            doc_type=response.doc_type,
            title=response.title,
            content_html=response.content_html,
            pdf_filename=os.path.basename(response.pdf_download_url) if response.pdf_download_url else None,
            pdf_download_url=response.pdf_download_url,
            statutory_citations=response.statutory_citations,
        )
    )
    if profile:
        confirmed_override_fields = {
            "complainant_name": "user_name",
            "complainant_city": "user_city",
            "opposite_party_name": "opposite_party_name",
            "property_address": "property_address",
            "disputed_amount": "disputed_amount",
            "vacating_date": "vacating_date",
            "incident_date": "incident_date",
            "bank_name": "bank_name",
            "transaction_id": "transaction_id",
            "police_station_name": "police_station_name",
        }
        for override_key, profile_field in confirmed_override_fields.items():
            value = overrides.get(override_key)
            if value is not None and value != "":
                setattr(profile, profile_field, value)
                profile.fact_metadata[profile_field] = {
                    "value": value,
                    "source": "document_confirmation",
                    "confidence": 1.0,
                    "confirmed": True,
                }
        profile.documents.append(
            {
                "id": response.id,
                "type": response.doc_type,
                "title": response.title,
                "status": "DRAFT",
                "created_at": response.created_at,
                "pdf_download_url": response.pdf_download_url,
                "docx_download_url": response.docx_download_url,
            }
        )
        if not any(action.get("type") == "document_prepared" for action in profile.actions_completed):
            conversational_agent._add_action(profile, "document_prepared", f"Prepared {response.title}")
        _save_chat_session(profile, _messages_for_session(req.case_id, db), db)
    else:
        db.commit()
    logger.info("case_id=%s document_type=%s event=document_generated", case.id, req.doc_type)
    return response


@app.get("/api/v1/documents/download/{filename}")
def download_document(filename: str):
    if filename != os.path.basename(filename):
        raise HTTPException(status_code=400, detail="Invalid filename")
    file_path = os.path.join(settings.STORAGE_DIR, "documents", filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
    media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return FileResponse(file_path, media_type=media_type, filename=filename)


@app.get("/api/v1/documents")
def list_documents(db: Session = Depends(get_db)):
    records = db.query(GeneratedDocumentModel).order_by(GeneratedDocumentModel.created_at.desc()).all()
    result = []
    for record in records:
        case = db.query(CaseModel).filter(CaseModel.id == record.case_id).first()
        docx_url = None
        if record.pdf_download_url:
            candidate = record.pdf_download_url.rsplit(".", 1)[0] + ".docx"
            candidate_path = os.path.join(settings.STORAGE_DIR, "documents", os.path.basename(candidate))
            if os.path.exists(candidate_path):
                docx_url = candidate
        result.append(
            {
                "id": record.id,
                "case_id": record.case_id,
                "case_title": case.title if case else "Case",
                "doc_type": record.doc_type,
                "title": record.title,
                "status": "DRAFT",
                "pdf_download_url": record.pdf_download_url,
                "docx_download_url": docx_url,
                "created_at": record.created_at.isoformat(),
            }
        )
    return result


@app.get("/api/v1/document-definitions", response_model=List[DocumentDefinitionSchema])
def document_definitions():
    return list_document_definitions()


@app.get("/api/v1/cases/{case_id}/dossier", response_model=PortalFilingDossier)
def get_filing_dossier(case_id: str, db: Session = Depends(get_db)):
    case = db.query(CaseModel).filter(CaseModel.id == case_id).first()
    if not case or not case.fact_graph:
        raise HTTPException(status_code=404, detail="Case facts not found")
    facts = case.fact_graph
    schema = IntakeFactExtractor.extract_facts(
        facts.incident_narrative,
        {
            "user_name": facts.complainant_data.get("name"),
            "user_city": facts.complainant_data.get("city"),
            "user_state": facts.complainant_data.get("state"),
            "opposite_party_name": facts.opposite_party_data.get("name"),
            "amount_paid": facts.financial_breakdown.get("amount_paid", 0),
            "incident_date": case.cause_of_action_date,
            "category": case.category,
        },
    )
    return DossierGenerator.generate_dossier(
        case_id=case.id,
        category=case.category,
        fact_graph=schema,
        appropriate_forum=case.appropriate_forum or "Competent authority",
    )


@app.get("/api/v1/statutes")
def get_all_statutes():
    return statutory_rag.corpus


@app.get("/api/v1/llm/status", response_model=ProviderStatus)
def get_llm_status():
    return get_llm_provider().status


@app.post("/api/v1/chat/message", response_model=ChatTurnResponse)
async def handle_chat_message(req: ChatTurnRequest, db: Session = Depends(get_db)):
    existing_profile = _load_chat_profile(req.case_id, db) if req.case_id else None
    recent_messages = _messages_for_session(req.case_id, db) if existing_profile and req.case_id else req.history
    response = await gemini_conversation_service.process_turn(req, existing_profile, recent_messages)
    stored_messages = _messages_for_session(response.case_profile.case_id, db)
    stored_messages.extend(
        [
            ChatMessage(id=str(uuid.uuid4()), sender="user", text=req.message),
            ChatMessage(
                id=response.message_id,
                sender="bot",
                text=response.reply_text,
                quick_replies=response.quick_replies,
                suggested_action=response.suggested_action,
            ),
        ]
    )
    _save_chat_session(response.case_profile, stored_messages, db)
    logger.info(
        "case_id=%s classification=%s workflow_stage=%s provider=%s model=%s mode=%s event=chat_processed",
        response.case_profile.case_id,
        response.case_profile.category,
        response.case_profile.current_stage_key,
        response.llm_provider,
        response.llm_model,
        response.llm_mode,
    )
    return response


@app.get("/api/v1/chat/cases", response_model=List[StructuredCaseProfile])
def list_chat_cases(db: Session = Depends(get_db)):
    _seed_demo_sessions(db)
    records = db.query(ChatCaseSessionModel).order_by(ChatCaseSessionModel.updated_at.desc()).all()
    return [_safe_profile(record) for record in records]


@app.get("/api/v1/chat/cases/{case_id}", response_model=ChatSessionResponse)
def get_chat_case(case_id: str, db: Session = Depends(get_db)):
    profile = _load_chat_profile(case_id, db)
    if not profile:
        raise HTTPException(status_code=404, detail="Conversational case not found")
    return ChatSessionResponse(case_profile=profile, messages=_messages_for_session(case_id, db))


@app.post("/api/v1/chat/cases/{case_id}/resolve", response_model=StructuredCaseProfile)
def resolve_chat_case(case_id: str, db: Session = Depends(get_db)):
    profile = _load_chat_profile(case_id, db)
    if not profile:
        raise HTTPException(status_code=404, detail="Case not found")
    if profile.current_stage_key != "RESOLVED":
        profile.current_stage_key = "RESOLVED"
        profile.current_stage_label = "Resolved"
        conversational_agent._add_action(profile, "case_resolved", "Case marked resolved")
    _save_chat_session(profile, _messages_for_session(case_id, db), db)
    return profile


@app.post("/api/v1/chat/upload-document", response_model=ChatTurnResponse)
async def handle_document_upload_extraction(
    req: DocumentUploadExtractionRequest,
    db: Session = Depends(get_db),
):
    if not req.case_id:
        raise HTTPException(status_code=400, detail="Start a case before uploading a document")
    profile = _load_chat_profile(req.case_id, db)
    if not profile:
        raise HTTPException(status_code=404, detail="Case not found")
    response = await gemini_conversation_service.process_document_upload(req, profile)
    messages = _messages_for_session(profile.case_id, db)
    messages.append(
        ChatMessage(
            id=response.message_id,
            sender="bot",
            text=response.reply_text,
            quick_replies=response.quick_replies,
        )
    )
    _save_chat_session(response.case_profile, messages, db)
    logger.info("case_id=%s document_type=%s event=evidence_metadata_processed", profile.case_id, req.doc_type)
    return response


@app.post("/api/v1/chat/upload-file", response_model=ChatTurnResponse)
async def handle_evidence_file_upload(
    case_id: str = Form(...),
    doc_type: str = Form(...),
    upload: UploadFile = File(...),
    excerpt: str = Form(""),
    db: Session = Depends(get_db),
):
    profile = _load_chat_profile(case_id, db)
    if not profile:
        raise HTTPException(status_code=404, detail="Case not found")

    original_name = os.path.basename(upload.filename or "evidence")
    content = await upload.read()
    try:
        extension = validate_upload(original_name, content)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    evidence_id = str(uuid.uuid4())
    stored_name = f"{case_id[:8]}_{evidence_id}{extension}"
    storage_path = os.path.join(settings.STORAGE_DIR, "evidence", stored_name)
    with open(storage_path, "wb") as handle:
        handle.write(content)

    extracted_text, extraction_mode = extract_upload_text(original_name, content)
    combined_text = "\n".join(value for value in [extracted_text.strip(), excerpt.strip()] if value)[:50000]
    evidence_map = {
        "RENTAL_AGREEMENT": "rental_agreement",
        "SALARY_SLIP": "salary_slips",
        "INVOICE": "invoice",
        "REJECTION_REPLY": "seller_rejection",
    }
    checklist_item = next(
        (item for item in profile.evidence_checklist if item.id == evidence_map.get(doc_type)),
        None,
    )
    db.add(
        EvidenceFileModel(
            id=evidence_id,
            case_id=case_id,
            file_name=original_name,
            file_type=upload.content_type or extension.lstrip("."),
            file_path=storage_path,
            annexure_label=checklist_item.annexure_label if checklist_item else None,
        )
    )
    profile.key_facts["last_upload"] = {
        "evidence_id": evidence_id,
        "file_name": original_name,
        "stored_name": stored_name,
        "extraction_mode": extraction_mode,
    }
    response = await gemini_conversation_service.process_document_upload(
        DocumentUploadExtractionRequest(
            case_id=case_id,
            doc_type=doc_type,
            file_name=original_name,
            simulated_content=combined_text or None,
        ),
        profile,
    )
    messages = _messages_for_session(case_id, db)
    messages.append(
        ChatMessage(
            id=response.message_id,
            sender="bot",
            text=response.reply_text,
            quick_replies=response.quick_replies,
        )
    )
    _save_chat_session(response.case_profile, messages, db)
    logger.info(
        "case_id=%s document_type=%s extraction_mode=%s event=evidence_file_processed",
        case_id,
        doc_type,
        extraction_mode,
    )
    return response
