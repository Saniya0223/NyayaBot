from abc import ABC, abstractmethod
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.domains.contracts import ActionId, DomainId, EvidenceId


CaseCategory = DomainId


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
    order_reference_id: Optional[str] = None
    payment_transaction_id: Optional[str] = None
    shipment_tracking_id: Optional[str] = None
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

    type: ActionId
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
    evidence_detected: List[EvidenceId] = Field(default_factory=list)
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
    language_style: str = "english"
    script_style: str = "roman"
    domain_catalog: List[Dict[str, Any]] = Field(default_factory=list)


class LLMResponseContext(BaseModel):
    user_message: str
    recent_messages: List[Dict[str, str]] = Field(default_factory=list)
    case_summary: Dict[str, Any]
    workflow: Dict[str, Any]
    missing_information: List[str] = Field(default_factory=list)
    legal_sources: List[Dict[str, Any]] = Field(default_factory=list)
    language_style: str = "english"
    script_style: str = "roman"
    conflict: Optional[Dict[str, Any]] = None
    # Safety triage result and readiness stage. The model is told when a case is
    # still being understood so it does not push a document prematurely.
    safety: Optional[Dict[str, Any]] = None
    readiness: str = "UNDERSTANDING_CASE"
    domain_context: Dict[str, Any] = Field(default_factory=dict)


class ProviderStatus(BaseModel):
    provider: str
    model: str
    configured: bool
    mode: Literal["gemini", "groq", "limited_demo"]
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
