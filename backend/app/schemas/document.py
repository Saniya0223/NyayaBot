from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field

class DocumentGenerateRequest(BaseModel):
    case_id: str
    doc_type: str  # FORMAL_LEGAL_NOTICE, EDAAKHIL_COMPLAINT, RTI_SEC6, TENANT_DEMAND_NOTICE
    custom_instructions: Optional[str] = None
    override_data: Optional[Dict[str, Any]] = None

class DocumentResponse(BaseModel):
    id: str
    case_id: str
    doc_type: str
    title: str
    content_html: str
    pdf_download_url: Optional[str] = None
    docx_download_url: Optional[str] = None
    statutory_citations: List[Dict[str, str]] = Field(default_factory=list)
    annexures: List[Dict[str, str]] = Field(default_factory=list)
    created_at: str

class DocumentDefinitionSchema(BaseModel):
    id: str
    name: str
    category: str
    applicable_workflows: List[str] = Field(default_factory=list)
    required_fields: List[str] = Field(default_factory=list)
    optional_fields: List[str] = Field(default_factory=list)
    evidence_suggestions: List[str] = Field(default_factory=list)
    requires_professional_review: bool = False
    template_id: str

class PortalDossierStep(BaseModel):
    step_number: int
    title: str
    description: str
    portal_url: Optional[str] = None
    portal_section: Optional[str] = None
    fields_to_fill: List[Dict[str, str]] = Field(default_factory=list)  # {field_label: value}
    documents_to_upload: List[str] = Field(default_factory=list)
    pro_tip: Optional[str] = None

class PortalFilingDossier(BaseModel):
    case_id: str
    portal_name: str  # e.g., e-Daakhil, RTI Online, NCH (National Consumer Helpline)
    portal_url: str
    forum_name: str
    prescribed_fees: str
    estimated_resolution_time: str
    steps: List[PortalDossierStep] = Field(default_factory=list)
    annexure_checklist: List[Dict[str, str]] = Field(default_factory=list)
