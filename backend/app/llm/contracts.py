from abc import ABC, abstractmethod
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


CaseCategory = Literal[
    "CONSUMER",
    "EMPLOYMENT",
    "HOUSING_TENANT",
    "CYBER_FRAUD",
    "POLICE_COMPLAINT",
    "GENERAL",
]


class IssueClassification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: CaseCategory
    issue_type: str = Field(min_length=2, max_length=100)
    confidence: float = Field(ge=0, le=1)


class ExtractedCaseFacts(BaseModel):
    """Closed schema: Gemini cannot introduce arbitrary persisted fields."""

    model_config = ConfigDict(extra="forbid")

    user_name: Optional[str] = None
    user_city: Optional[str] = None
    user_state: Optional[str] = None
    opposite_party_name: Optional[str] = None
    opposite_party_address: Optional[str] = None
    property_address: Optional[str] = None
    disputed_amount: Optional[float] = Field(default=None, ge=0)
    incident_date: Optional[str] = None
    vacating_date: Optional[str] = None
    unpaid_months: Optional[List[str]] = None
    monthly_salary: Optional[float] = Field(default=None, ge=0)
    transaction_id: Optional[str] = None
    bank_name: Optional[str] = None
    police_station_name: Optional[str] = None
    product_name: Optional[str] = None
    employee_role: Optional[str] = None
    rental_agreement_available: Optional[bool] = None
    deposit_payment_proof_available: Optional[bool] = None
    landlord_contacted: Optional[bool] = None
    landlord_reason: Optional[str] = None
    invoice_available: Optional[bool] = None
    seller_contacted: Optional[bool] = None
    seller_response: Optional[str] = None
    hr_contacted: Optional[bool] = None
    employment_proof_available: Optional[bool] = None
    bank_reported: Optional[bool] = None
    cyber_reported: Optional[bool] = None
    police_approached: Optional[bool] = None
    written_complaint_available: Optional[bool] = None


class DetectedAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal[
        "informal_request_made",
        "formal_demand_sent",
        "response_rejected",
        "response_accepted",
        "case_resolved",
        "bank_reported",
        "cybercrime_reported",
        "police_complaint_submitted",
    ]
    completed: bool = True
    date_reference: Optional[str] = None
    confidence: float = Field(ge=0, le=1)


class FieldConfidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str = Field(min_length=2, max_length=80)
    confidence: float = Field(ge=0, le=1)


class CaseExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_intent: str = Field(min_length=2, max_length=200)
    language_style: Literal["english", "hindi", "hinglish", "other"] = "english"
    classification: IssueClassification
    facts: ExtractedCaseFacts = Field(default_factory=ExtractedCaseFacts)
    confidence_by_field: List[FieldConfidence] = Field(default_factory=list)
    actions_detected: List[DetectedAction] = Field(default_factory=list)
    evidence_detected: List[Literal[
        "rental_agreement",
        "deposit_payment_proof",
        "move_out_photos",
        "landlord_chat",
        "invoice",
        "defect_photos",
        "support_tickets",
        "seller_rejection",
        "offer_letter",
        "salary_slips",
        "hr_emails",
        "upi_receipt",
        "scammer_chat",
        "bank_complaint_ack",
        "incident_proof",
        "complaint_copy",
        "speed_post_receipt",
    ]] = Field(default_factory=list)
    clarification_needed: bool = False
    ambiguity_note: Optional[str] = None


class DocumentAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_type: str
    summary: str
    outcome: Literal["REJECTED", "ACCEPTED", "PENDING", "INFORMATION_ONLY", "UNCLEAR"] = "UNCLEAR"
    facts: ExtractedCaseFacts = Field(default_factory=ExtractedCaseFacts)
    confidence_by_field: List[FieldConfidence] = Field(default_factory=list)
    explicit_deadlines: List[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)


class LLMExtractionContext(BaseModel):
    user_message: str
    recent_messages: List[Dict[str, str]] = Field(default_factory=list)
    case_summary: Optional[Dict[str, Any]] = None


class LLMResponseContext(BaseModel):
    user_message: str
    recent_messages: List[Dict[str, str]] = Field(default_factory=list)
    case_summary: Dict[str, Any]
    workflow: Dict[str, Any]
    missing_information: List[str] = Field(default_factory=list)
    legal_sources: List[Dict[str, Any]] = Field(default_factory=list)
    language_style: str = "english"
    conflict: Optional[Dict[str, Any]] = None


class ProviderStatus(BaseModel):
    provider: str
    model: str
    configured: bool
    mode: Literal["gemini", "limited_demo"]
    message: str


class LLMProviderError(RuntimeError):
    """Base exception for recoverable provider failures."""


class LLMNotConfiguredError(LLMProviderError):
    pass


class LLMProvider(ABC):
    @property
    @abstractmethod
    def status(self) -> ProviderStatus:
        raise NotImplementedError

    @abstractmethod
    async def extract_case_updates(self, context: LLMExtractionContext) -> CaseExtraction:
        raise NotImplementedError

    @abstractmethod
    async def classify_issue(self, context: LLMExtractionContext) -> IssueClassification:
        raise NotImplementedError

    @abstractmethod
    async def chat(self, context: LLMResponseContext) -> str:
        raise NotImplementedError

    @abstractmethod
    async def analyze_document(self, text: str, document_type_hint: str) -> DocumentAnalysis:
        raise NotImplementedError
