from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field

class PartyInfo(BaseModel):
    name: str = ""
    designation_or_role: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None

class TimelineEvent(BaseModel):
    date: str
    event_description: str
    evidence_reference: Optional[str] = None

class FinancialBreakdown(BaseModel):
    amount_paid: float = 0.0
    refund_claimed: float = 0.0
    compensation_claimed: float = 0.0
    litigation_costs_claimed: float = 0.0
    total_claim_amount: float = 0.0

class EvidenceItem(BaseModel):
    doc_type: str  # INVOICE, PAYMENT_RECEIPT, EMAIL_THREAD, WHATSAPP_CHAT, NOTICE, CONTRACT, PHOTO
    doc_name: str
    file_url: Optional[str] = None
    is_available: bool = True
    annexure_label: Optional[str] = None

class FactGraphSchema(BaseModel):
    complainant: PartyInfo = Field(default_factory=PartyInfo)
    opposite_party: PartyInfo = Field(default_factory=PartyInfo)
    incident_narrative: str = ""
    incident_date: Optional[str] = None
    category: str = "CONSUMER"  # CONSUMER, TENANCY, RTI, CYBER, CHEQUE_BOUNCE
    sub_category: Optional[str] = None  # e.g., DEFECTIVE_PRODUCT, ECOMMERCE_FRAUD, DEPOSIT_WITHHOLDING
    timeline: List[TimelineEvent] = Field(default_factory=list)
    financials: FinancialBreakdown = Field(default_factory=FinancialBreakdown)
    evidence_inventory: List[EvidenceItem] = Field(default_factory=list)
    missing_facts: List[str] = Field(default_factory=list)
    clarification_questions: List[str] = Field(default_factory=list)
    is_complete: bool = False
    completion_score: float = 0.0  # 0.0 to 1.0
