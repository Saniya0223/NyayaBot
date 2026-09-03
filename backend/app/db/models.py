import uuid
from datetime import datetime
from sqlalchemy import Column, String, Text, Boolean, Integer, Float, DateTime, ForeignKey, JSON
from sqlalchemy.orm import relationship
from app.db.session import Base

def generate_uuid():
    return str(uuid.uuid4())

class UserModel(Base):
    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    full_name = Column(String(255), nullable=False)
    email = Column(String(255), unique=True, nullable=False)
    phone = Column(String(20), nullable=True)
    city = Column(String(100), nullable=True)
    state = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    cases = relationship("CaseModel", back_populates="user", cascade="all, delete-orphan")


class CaseModel(Base):
    __tablename__ = "cases"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=True)
    case_number = Column(String(50), unique=True, index=True)
    title = Column(String(255), nullable=False)
    category = Column(String(50), nullable=False)  # CONSUMER, TENANCY, RTI, CYBER, CHEQUE_BOUNCE
    status = Column(String(50), default="INTAKE_IN_PROGRESS")  # INTAKE_IN_PROGRESS, FACTS_EXTRACTED, READY_TO_FILE, FILED, PENDING_HEARING, RESOLVED
    severity_level = Column(String(30), default="STANDARD")  # STANDARD, ESCALATED_LAWYER
    cause_of_action_date = Column(String(50), nullable=True)
    limitation_deadline = Column(String(50), nullable=True)
    pecuniary_value = Column(Float, default=0.0)
    appropriate_forum = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("UserModel", back_populates="cases")
    fact_graph = relationship("FactGraphModel", back_populates="case", uselist=False, cascade="all, delete-orphan")
    timeline_events = relationship("CaseTimelineEventModel", back_populates="case", cascade="all, delete-orphan")
    documents = relationship("GeneratedDocumentModel", back_populates="case", cascade="all, delete-orphan")
    evidence_files = relationship("EvidenceFileModel", back_populates="case", cascade="all, delete-orphan")


class FactGraphModel(Base):
    __tablename__ = "fact_graphs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    case_id = Column(String(36), ForeignKey("cases.id"), unique=True, nullable=False)
    complainant_data = Column(JSON, default=dict)
    opposite_party_data = Column(JSON, default=dict)
    incident_narrative = Column(Text, default="")
    structured_timeline = Column(JSON, default=list)  # List of {date, event}
    financial_breakdown = Column(JSON, default=dict)  # {amount_paid, refund_claimed, compensation_claimed}
    evidence_inventory = Column(JSON, default=list)  # List of {doc_type, doc_name, file_url}
    missing_facts = Column(JSON, default=list)
    is_complete = Column(Boolean, default=False)
    completion_score = Column(Float, default=0.0)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    case = relationship("CaseModel", back_populates="fact_graph")


class CaseTimelineEventModel(Base):
    __tablename__ = "case_timeline_events"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    case_id = Column(String(36), ForeignKey("cases.id"), nullable=False)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    event_type = Column(String(50), nullable=False)  # INCIDENT, NOTICE_SENT, PORTAL_FILED, HEARING, DEADLINE
    target_date = Column(String(50), nullable=True)
    completed_at = Column(DateTime, nullable=True)
    is_mandatory = Column(Boolean, default=True)
    status = Column(String(30), default="PENDING")  # PENDING, COMPLETED, OVERDUE

    case = relationship("CaseModel", back_populates="timeline_events")


class GeneratedDocumentModel(Base):
    __tablename__ = "generated_documents"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    case_id = Column(String(36), ForeignKey("cases.id"), nullable=False)
    doc_type = Column(String(50), nullable=False)  # FORMAL_LEGAL_NOTICE, EDAAKHIL_COMPLAINT, RTI_SEC6, TENANT_DEMAND_NOTICE
    version = Column(Integer, default=1)
    title = Column(String(255), nullable=False)
    content_html = Column(Text, nullable=True)
    pdf_filename = Column(String(255), nullable=True)
    pdf_download_url = Column(String(500), nullable=True)
    statutory_citations = Column(JSON, default=list)
    created_at = Column(DateTime, default=datetime.utcnow)

    case = relationship("CaseModel", back_populates="documents")


class EvidenceFileModel(Base):
    __tablename__ = "evidence_files"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    case_id = Column(String(36), ForeignKey("cases.id"), nullable=False)
    file_name = Column(String(255), nullable=False)
    file_type = Column(String(50), nullable=True)
    file_path = Column(String(500), nullable=False)
    annexure_label = Column(String(20), nullable=True)  # e.g., Annexure A-1
    uploaded_at = Column(DateTime, default=datetime.utcnow)

    case = relationship("CaseModel", back_populates="evidence_files")


class ChatCaseSessionModel(Base):
    """Durable conversational state kept separate from the legacy fact-graph tables."""

    __tablename__ = "chat_case_sessions"

    case_id = Column(String(36), ForeignKey("cases.id"), primary_key=True)
    profile_data = Column(JSON, default=dict, nullable=False)
    messages_data = Column(JSON, default=list, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
