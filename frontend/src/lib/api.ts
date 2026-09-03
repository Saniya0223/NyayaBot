export const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000/api/v1';

export interface PartyInfo {
  name: string;
  designation_or_role?: string;
  address?: string;
  city?: string;
  state?: string;
  phone?: string;
  email?: string;
}

export interface TimelineEvent {
  date: string;
  event_description: string;
  evidence_reference?: string;
}

export interface FinancialBreakdown {
  amount_paid: number;
  refund_claimed: number;
  compensation_claimed: number;
  litigation_costs_claimed: number;
  total_claim_amount: number;
}

export interface EvidenceItem {
  doc_type: string;
  doc_name: string;
  file_url?: string;
  is_available: boolean;
  annexure_label?: string;
}

export interface FactGraph {
  complainant: PartyInfo;
  opposite_party: PartyInfo;
  incident_narrative: string;
  incident_date?: string;
  category: string;
  sub_category?: string;
  timeline: TimelineEvent[];
  financials: FinancialBreakdown;
  evidence_inventory: EvidenceItem[];
  missing_facts: string[];
  clarification_questions: string[];
  is_complete: boolean;
  completion_score: number;
}

export interface StatutoryCitation {
  section: string;
  act: string;
  title: string;
  description: string;
  relevance_reason?: string;
  source_url?: string;
  source_authority?: string;
  effective_from?: string;
  effective_to?: string;
  document_type?: string;
}

export interface CaseTimelineMilestone {
  id: string;
  title: string;
  description?: string;
  event_type: string;
  target_date?: string;
  completed_at?: string;
  is_mandatory: boolean;
  status: 'PENDING' | 'COMPLETED' | 'OVERDUE';
}

export interface CaseData {
  id: string;
  case_number: string;
  title: string;
  category: string;
  status: string;
  severity_level: 'STANDARD' | 'ESCALATED_LAWYER';
  cause_of_action_date?: string;
  limitation_deadline?: string;
  limitation_days_remaining?: number;
  pecuniary_value: number;
  appropriate_forum?: string;
  fact_graph: FactGraph;
  timeline_events: CaseTimelineMilestone[];
  applicable_statutes: StatutoryCitation[];
  suggested_actions: string[];
  escalation_reason?: string;
  created_at: string;
  updated_at: string;
}

export interface DocumentResponse {
  id: string;
  case_id: string;
  doc_type: string;
  title: string;
  content_html: string;
  pdf_download_url?: string;
  docx_download_url?: string;
  statutory_citations: Array<{ act: string; section: string; title: string }>;
  annexures: Array<{ label: string; name: string }>;
  created_at: string;
}

export interface PortalDossierStep {
  step_number: number;
  title: string;
  description: string;
  portal_url?: string;
  portal_section?: string;
  fields_to_fill: Array<{ label: string; value: string }>;
  documents_to_upload: string[];
  pro_tip?: string;
}

export interface PortalFilingDossier {
  case_id: string;
  portal_name: string;
  portal_url: string;
  forum_name: string;
  prescribed_fees: string;
  estimated_resolution_time: string;
  steps: PortalDossierStep[];
  annexure_checklist: Array<{ label: string; title: string }>;
}

export async function submitIntake(payload: {
  user_narrative: string;
  case_id?: string;
  user_name?: string;
  user_city?: string;
  user_state?: string;
  user_phone?: string;
  user_email?: string;
}): Promise<CaseData> {
  const res = await fetch(`${API_BASE_URL}/intake`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.detail || 'Failed to process legal intake');
  }
  return res.json();
}

export async function submitClarifications(case_id: string, answers: Record<string, string>): Promise<CaseData> {
  const res = await fetch(`${API_BASE_URL}/clarifications`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ case_id, answers })
  });
  if (!res.ok) throw new Error('Failed to update case clarifications');
  return res.json();
}

export async function fetchCases(): Promise<CaseData[]> {
  const res = await fetch(`${API_BASE_URL}/cases`);
  if (!res.ok) throw new Error('Failed to fetch cases');
  return res.json();
}

export async function fetchCaseById(case_id: string): Promise<CaseData> {
  const res = await fetch(`${API_BASE_URL}/cases/${case_id}`);
  if (!res.ok) throw new Error('Failed to fetch case details');
  return res.json();
}

export async function toggleTimelineEvent(case_id: string, event_id: string): Promise<void> {
  const res = await fetch(`${API_BASE_URL}/cases/${case_id}/timeline/${event_id}/toggle`, {
    method: 'POST'
  });
  if (!res.ok) throw new Error('Failed to update timeline milestone');
}

export async function generateDocument(
  case_id: string,
  doc_type: string,
  override_data?: Record<string, unknown>
): Promise<DocumentResponse> {
  const res = await fetch(`${API_BASE_URL}/documents/generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ case_id, doc_type, override_data })
  });
  if (!res.ok) {
    const payload = await res.json().catch(() => null);
    const detail = payload?.detail;
    const message = typeof detail === 'string' ? detail : detail?.message;
    const missing = Array.isArray(detail?.missing_fields) ? ` Missing: ${detail.missing_fields.join(', ')}.` : '';
    throw new Error(`${message || 'Failed to generate legal document.'}${missing}`);
  }
  return res.json();
}

export async function fetchPortalDossier(case_id: string): Promise<PortalFilingDossier> {
  const res = await fetch(`${API_BASE_URL}/cases/${case_id}/dossier`);
  if (!res.ok) throw new Error('Failed to load portal filing dossier');
  return res.json();
}

export interface EvidenceStatusItem {
  id: string;
  name: string;
  is_available: boolean;
  why_needed: string;
  annexure_label?: string;
}

export interface LegalStageMilestone {
  id: string;
  title: string;
  description: string;
  status: 'COMPLETED' | 'CURRENT' | 'FUTURE';
  is_current: boolean;
}

export interface StructuredCaseProfile {
  case_id: string;
  case_number: string;
  title: string;
  category: string;
  category_display_name: string;
  issue_type: string;
  current_stage_key: string;
  current_stage_label: string;
  user_name?: string;
  user_city?: string;
  user_state?: string;
  user_phone?: string;
  opposite_party_name?: string;
  opposite_party_address?: string;
  property_address?: string;
  disputed_amount: number;
  incident_date?: string;
  vacating_date?: string;
  unpaid_months: string[];
  transaction_id?: string;
  bank_name?: string;
  police_station_name?: string;
  key_facts: Record<string, unknown>;
  fact_metadata?: Record<string, { value: unknown; source: string; confidence: number; confirmed: boolean }>;
  evidence_checklist: EvidenceStatusItem[];
  legal_journey: LegalStageMilestone[];
  actions_completed?: Array<{ type: string; date: string; label: string }>;
  timeline?: Array<{ id: string; type: string; date: string; label: string; source: string }>;
  deadlines?: Array<{ date: string; source: string; reason: string; confidence: number; confirmed: boolean }>;
  documents?: Array<{
    id: string;
    type: string;
    title: string;
    status: string;
    created_at: string;
    pdf_download_url?: string;
    docx_download_url?: string;
  }>;
  recommended_next_action?: { type: string; doc_type?: string; label: string };
  rights_summary?: {
    what_this_means: string;
    possible_rights: string[];
    useful_evidence: string[];
    legal_source: string;
    sources?: Array<{ title: string; authority: string; url: string }>;
  };
  risk_level?: 'GREEN' | 'AMBER' | 'RED';
  safety_notice?: string;
  is_ready_for_document: boolean;
  recommended_doc_type?: string;
  recommended_doc_label?: string;
  missing_required_fields: string[];
  missing_document_fields: string[];
  created_at?: string;
  updated_at?: string;
}

export interface ChatMessageItem {
  id: string;
  sender: 'user' | 'bot' | 'system';
  text: string;
  timestamp?: string;
  quick_replies?: string[];
  suggested_action?: { type: string; doc_type?: string; label: string };
  extracted_badge?: string;
}

export interface ChatTurnResponse {
  reply_text: string;
  case_profile: StructuredCaseProfile;
  quick_replies: string[];
  suggested_action?: { type: string; doc_type?: string; label: string };
  message_id: string;
  llm_provider: string;
  llm_model?: string;
  llm_mode: 'gemini' | 'limited_demo';
}

export interface LLMStatus {
  provider: string;
  model: string;
  configured: boolean;
  mode: 'gemini' | 'limited_demo';
  message: string;
}

export async function fetchLLMStatus(): Promise<LLMStatus> {
  const res = await fetch(`${API_BASE_URL}/llm/status`);
  if (!res.ok) throw new Error('Failed to load AI provider status');
  return res.json();
}

export async function sendChatMessage(payload: {
  message: string;
  case_id?: string;
  history?: ChatMessageItem[];
}): Promise<ChatTurnResponse> {
  const res = await fetch(`${API_BASE_URL}/chat/message`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });
  if (!res.ok) {
    // A non-JSON body (proxy error, HTML 500) must not mask the real status code.
    const detail = await res.text().catch(() => '');
    let parsed = '';
    try { parsed = JSON.parse(detail)?.detail ?? ''; } catch { parsed = ''; }
    throw new Error(`Chat request failed (HTTP ${res.status}): ${parsed || detail.slice(0, 200) || res.statusText}`);
  }
  return res.json();
}

export async function uploadDocumentForExtraction(payload: {
  case_id?: string;
  doc_type: string;
  file_name: string;
  simulated_content?: string;
}): Promise<ChatTurnResponse> {
  const res = await fetch(`${API_BASE_URL}/chat/upload-document`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.detail || 'Failed to process document extraction');
  }
  return res.json();
}

export async function uploadEvidenceFile(payload: {
  case_id: string;
  doc_type: string;
  file: File;
  excerpt?: string;
}): Promise<ChatTurnResponse> {
  const form = new FormData();
  form.append('case_id', payload.case_id);
  form.append('doc_type', payload.doc_type);
  form.append('upload', payload.file);
  if (payload.excerpt) form.append('excerpt', payload.excerpt);
  const res = await fetch(`${API_BASE_URL}/chat/upload-file`, { method: 'POST', body: form });
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new Error(typeof body?.detail === 'string' ? body.detail : 'Failed to upload and inspect the document');
  }
  return res.json();
}

export async function fetchStatutes(): Promise<Record<string, StatutoryCitation[]>> {
  const res = await fetch(`${API_BASE_URL}/statutes`);
  if (!res.ok) throw new Error('Failed to load statutes');
  return res.json();
}

export interface ChatSessionResponse {
  case_profile: StructuredCaseProfile;
  messages: ChatMessageItem[];
}

export async function fetchChatCases(): Promise<StructuredCaseProfile[]> {
  const res = await fetch(`${API_BASE_URL}/chat/cases`);
  if (!res.ok) throw new Error('Failed to fetch conversational cases');
  return res.json();
}

export async function fetchChatCase(caseId: string): Promise<ChatSessionResponse> {
  const res = await fetch(`${API_BASE_URL}/chat/cases/${caseId}`);
  if (!res.ok) throw new Error('Failed to reopen this case');
  return res.json();
}

export async function resolveChatCase(caseId: string): Promise<StructuredCaseProfile> {
  const res = await fetch(`${API_BASE_URL}/chat/cases/${caseId}/resolve`, { method: 'POST' });
  if (!res.ok) throw new Error('Failed to mark case resolved');
  return res.json();
}

export interface DocumentListItem {
  id: string;
  case_id: string;
  case_title: string;
  doc_type: string;
  title: string;
  status: string;
  pdf_download_url?: string;
  docx_download_url?: string;
  created_at: string;
}

export async function fetchDocuments(): Promise<DocumentListItem[]> {
  const res = await fetch(`${API_BASE_URL}/documents`);
  if (!res.ok) throw new Error('Failed to fetch documents');
  return res.json();
}

export function absoluteDocumentUrl(path?: string): string | undefined {
  if (!path) return undefined;
  if (/^https?:\/\//.test(path)) return path;
  return `${API_BASE_URL.replace(/\/api\/v1\/?$/, '')}${path}`;
}
