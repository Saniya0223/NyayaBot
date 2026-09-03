from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime
from app.schemas.fact_graph import FactGraphSchema

class IntakeRequest(BaseModel):
    user_narrative: str
    case_id: Optional[str] = None
    user_name: Optional[str] = "Complainant"
    user_city: Optional[str] = "New Delhi"
    user_state: Optional[str] = "Delhi"
    user_phone: Optional[str] = None
    user_email: Optional[str] = None

class ClarificationAnswer(BaseModel):
    case_id: str
    answers: Dict[str, str]  # question -> answer

class CaseTimelineEventSchema(BaseModel):
    id: Optional[str] = None
    title: str
    description: Optional[str] = None
    event_type: str  # INCIDENT, NOTICE_SENT, PORTAL_FILED, HEARING, DEADLINE
    target_date: Optional[str] = None
    completed_at: Optional[datetime] = None
    is_mandatory: bool = True
    status: str = "PENDING"  # PENDING, COMPLETED, OVERDUE

class StatutoryCitation(BaseModel):
    section: str
    act: str
    title: str
    description: str
    relevance_reason: Optional[str] = None
    source_url: Optional[str] = None
    source_authority: Optional[str] = None
    effective_from: Optional[str] = None
    effective_to: Optional[str] = None
    document_type: Optional[str] = None

class CaseResponse(BaseModel):
    id: str
    case_number: str
    title: str
    category: str
    status: str
    severity_level: str
    cause_of_action_date: Optional[str] = None
    limitation_deadline: Optional[str] = None
    limitation_days_remaining: Optional[int] = None
    pecuniary_value: float = 0.0
    appropriate_forum: Optional[str] = None
    fact_graph: Optional[FactGraphSchema] = None
    timeline_events: List[CaseTimelineEventSchema] = Field(default_factory=list)
    applicable_statutes: List[StatutoryCitation] = Field(default_factory=list)
    suggested_actions: List[str] = Field(default_factory=list)
    escalation_reason: Optional[str] = None
    created_at: datetime
    updated_at: datetime
