from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field

class ChatMessage(BaseModel):
    id: Optional[str] = None
    sender: str  # "user" | "bot" | "system"
    text: str
    timestamp: Optional[str] = None
    quick_replies: Optional[List[str]] = None
    suggested_action: Optional[Dict[str, Any]] = None  # e.g., {"type": "PREPARE_DOC", "doc_type": "...", "label": "..."}
    extracted_badge: Optional[str] = None

class EvidenceStatusItem(BaseModel):
    id: str
    name: str
    is_available: bool = False
    why_needed: str
    annexure_label: Optional[str] = None

class LegalStageMilestone(BaseModel):
    id: str
    title: str
    description: str
    status: str  # "COMPLETED" | "CURRENT" | "FUTURE"
    is_current: bool = False

class StructuredCaseProfile(BaseModel):
    case_id: str
    case_number: str
    title: str
    category: str  # "CONSUMER" | "EMPLOYMENT" | "HOUSING_TENANT" | "CYBER_FRAUD" | "POLICE_COMPLAINT" | "GENERAL"
    category_display_name: str
    issue_type: str
    current_stage_key: str
    current_stage_label: str
    user_name: Optional[str] = None
    user_city: Optional[str] = None
    user_state: Optional[str] = None
    user_phone: Optional[str] = None
    opposite_party_name: Optional[str] = None
    opposite_party_address: Optional[str] = None
    property_address: Optional[str] = None
    disputed_amount: float = 0.0
    incident_date: Optional[str] = None
    vacating_date: Optional[str] = None
    unpaid_months: List[str] = Field(default_factory=list)
    transaction_id: Optional[str] = None
    bank_name: Optional[str] = None
    police_station_name: Optional[str] = None
    key_facts: Dict[str, Any] = Field(default_factory=dict)
    fact_metadata: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    evidence_checklist: List[EvidenceStatusItem] = Field(default_factory=list)
    legal_journey: List[LegalStageMilestone] = Field(default_factory=list)
    actions_completed: List[Dict[str, Any]] = Field(default_factory=list)
    timeline: List[Dict[str, Any]] = Field(default_factory=list)
    deadlines: List[Dict[str, Any]] = Field(default_factory=list)
    documents: List[Dict[str, Any]] = Field(default_factory=list)
    recommended_next_action: Optional[Dict[str, Any]] = None
    rights_summary: Optional[Dict[str, Any]] = None  # {what_this_means, possible_rights: [], useful_evidence: [], legal_source}
    risk_level: str = "GREEN"  # GREEN | AMBER | RED
    safety_notice: Optional[str] = None
    is_ready_for_document: bool = False
    recommended_doc_type: Optional[str] = None
    recommended_doc_label: Optional[str] = None
    missing_required_fields: List[str] = Field(default_factory=list)
    missing_document_fields: List[str] = Field(default_factory=list)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

class ChatTurnRequest(BaseModel):
    message: str
    case_id: Optional[str] = None
    history: List[ChatMessage] = Field(default_factory=list)
    context_overrides: Optional[Dict[str, Any]] = None

class ChatTurnResponse(BaseModel):
    reply_text: str
    case_profile: StructuredCaseProfile
    quick_replies: List[str] = Field(default_factory=list)
    suggested_action: Optional[Dict[str, Any]] = None
    message_id: str
    llm_provider: str = "gemini"
    llm_model: Optional[str] = None
    llm_mode: str = "limited_demo"  # "gemini" | "limited_demo"

class ChatSessionResponse(BaseModel):
    case_profile: StructuredCaseProfile
    messages: List[ChatMessage] = Field(default_factory=list)

class DocumentUploadExtractionRequest(BaseModel):
    case_id: Optional[str] = None
    doc_type: str  # "RENTAL_AGREEMENT" | "SALARY_SLIP" | "INVOICE" | "REJECTION_REPLY"
    file_name: str
    simulated_content: Optional[str] = None
