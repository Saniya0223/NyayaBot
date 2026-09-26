// Resolve the API origin at runtime so one build works both on localhost and
// over the LAN (a phone on the same Wi-Fi), whose IP changes with DHCP. Baking
// a fixed host into the bundle strands the app whenever that address moves.
// An explicit NEXT_PUBLIC_API_URL always wins, for deployed environments.
function resolveApiBaseUrl(): string {
  if (process.env.NEXT_PUBLIC_API_URL) return process.env.NEXT_PUBLIC_API_URL;
  if (typeof window !== 'undefined') return `http://${window.location.hostname}:8000/api/v1`;
  return 'http://localhost:8000/api/v1';
}

export const API_BASE_URL = resolveApiBaseUrl();

export class ApiError extends Error {
  constructor(message: string, public readonly status: number) {
    super(message);
    this.name = 'ApiError';
  }
}

export async function apiFetch(input: string, init: RequestInit = {}): Promise<Response> {
  let response: Response;
  try {
    response = await fetch(input, { ...init, credentials: 'include' });
  } catch {
    throw new ApiError(`Cannot reach the NyayaBot backend at ${API_BASE_URL}.`, 0);
  }
  if (response.status === 401 && typeof window !== 'undefined') {
    window.dispatchEvent(new Event('nyayabot_auth_expired'));
  }
  return response;
}

export async function apiErrorMessage(response: Response, fallback: string): Promise<string> {
  const payload = await response.json().catch(() => null);
  const detail = payload?.detail;
  return typeof detail === 'string' ? detail : fallback;
}

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

export interface DocumentAssessment {
  document_type: string;
  intent: string;
  status: string;
  ready_to_generate: boolean;
  fields: Array<{ key: string; label: string; required: boolean; data_type: string; value: unknown }>;
  missing_required_fields: string[];
  missing_optional_fields: string[];
  blockers: string[];
}

export async function fetchDocumentAssessment(case_id: string, doc_type: string): Promise<DocumentAssessment> {
  const query = new URLSearchParams({ case_id, doc_type });
  const res = await apiFetch(`${API_BASE_URL}/documents/assessment?${query}`);
  if (!res.ok) throw new Error('Could not load document requirements. Please retry.');
  return res.json();
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
  const res = await apiFetch(`${API_BASE_URL}/intake`, {
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
  const res = await apiFetch(`${API_BASE_URL}/clarifications`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ case_id, answers })
  });
  if (!res.ok) throw new Error('Failed to update case clarifications');
  return res.json();
}

export async function fetchCases(): Promise<CaseData[]> {
  const res = await apiFetch(`${API_BASE_URL}/cases`);
  if (!res.ok) throw new Error('Failed to fetch cases');
  return res.json();
}

export async function fetchCaseById(case_id: string): Promise<CaseData> {
  const res = await apiFetch(`${API_BASE_URL}/cases/${case_id}`);
  if (!res.ok) throw new Error('Failed to fetch case details');
  return res.json();
}

export async function toggleTimelineEvent(case_id: string, event_id: string): Promise<void> {
  const res = await apiFetch(`${API_BASE_URL}/cases/${case_id}/timeline/${event_id}/toggle`, {
    method: 'POST'
  });
  if (!res.ok) throw new Error('Failed to update timeline milestone');
}

export async function generateDocument(
  case_id: string,
  doc_type: string,
  override_data?: Record<string, unknown>
): Promise<DocumentResponse> {
  const res = await apiFetch(`${API_BASE_URL}/documents/generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ case_id, doc_type, override_data })
  });
  if (!res.ok) {
    const payload = await res.json().catch(() => null);
    const detail = payload?.detail;
    const message = typeof detail === 'string' ? detail : detail?.message;
    throw new Error(message || 'Failed to generate legal document. Review the required details and retry.');
  }
  return res.json();
}

export async function fetchPortalDossier(case_id: string): Promise<PortalFilingDossier> {
  const res = await apiFetch(`${API_BASE_URL}/cases/${case_id}/dossier`);
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
  fact_metadata?: Record<string, { value: unknown; source: string; confidence?: number; confirmed?: boolean }>;
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
  provided_documents?: ProvidedDocument[];
  legal_sources?: Array<{
    act: string;
    section: string;
    title: string;
    description: string;
    relevance_reason?: string;
    source_url?: string;
    source_authority?: string;
    document_type?: string;
  }>;
  next_action_plan?: {
    action_id: string;
    label: string;
    description: string;
    status: 'READY' | 'BLOCKED' | 'COMPLETED' | 'NOT_APPLICABLE';
    doc_type?: string;
  } | null;
  recommended_next_action?: { type: string; doc_type?: string; label: string; open_confirmation_modal?: boolean };
  document_request?: {
    intent: 'USER_REQUESTED';
    document_type?: string;
    status: string;
    missing_required_fields?: string[];
    missing_optional_fields?: string[];
    optional_skipped?: boolean;
  } | null;
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
  // Readiness ladder mirrored from the backend; the workspace gates the
  // document CTA on it so the UI never claims a case is further along.
  readiness?: 'PRE_INTAKE' | 'UNDERSTANDING_CASE' | 'READY_FOR_LEGAL_GUIDANCE' | 'READY_FOR_ACTION' | 'READY_FOR_DOCUMENT';
  intake_missing_facts?: string[];
  safety_status?: { is_safety_case: boolean; severity: string; immediate_danger?: boolean | null; triage_question?: string; guidance?: string } | null;
  professional_help?: {
    level: 'SELF_HELP_REASONABLE' | 'CONSIDER_LEGAL_HELP' | 'LEGAL_HELP_RECOMMENDED' | 'URGENT_LEGAL_HELP';
    reason_codes: string[];
    professional_types: string[];
    urgency: 'ROUTINE' | 'PROMPT';
    reassess_on: string[];
    relevant_known_signals: string[];
    unknown_relevant_signals: string[];
    assessment_version: string;
  } | null;
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
  suggested_action?: { type: string; doc_type?: string; label: string; open_confirmation_modal?: boolean };
  extracted_badge?: string;
}

export interface ChatTurnResponse {
  reply_text: string;
  case_profile: StructuredCaseProfile;
  quick_replies: string[];
  suggested_action?: { type: string; doc_type?: string; label: string; open_confirmation_modal?: boolean };
  message_id: string;
  llm_provider: string;
  llm_model?: string;
  llm_mode: 'groq' | 'gemini' | 'limited_demo';
}

export interface LLMStatus {
  provider: string;
  model: string;
  configured: boolean;
  mode: 'groq' | 'gemini' | 'limited_demo';
  message: string;
}

export async function fetchLLMStatus(): Promise<LLMStatus> {
  const res = await apiFetch(`${API_BASE_URL}/llm/status`);
  if (!res.ok) throw new Error('Failed to load AI provider status');
  return res.json();
}

export async function sendChatMessage(payload: {
  message: string;
  case_id?: string;
  history?: ChatMessageItem[];
}): Promise<ChatTurnResponse> {
  const res = await apiFetch(`${API_BASE_URL}/chat/message`, {
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
  const res = await apiFetch(`${API_BASE_URL}/chat/upload-document`, {
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
  const res = await apiFetch(`${API_BASE_URL}/chat/upload-file`, { method: 'POST', body: form });
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new Error(typeof body?.detail === 'string' ? body.detail : 'Failed to upload and inspect the document');
  }
  return res.json();
}

export async function fetchStatutes(): Promise<Record<string, StatutoryCitation[]>> {
  const res = await apiFetch(`${API_BASE_URL}/statutes`);
  if (!res.ok) throw new Error('Failed to load statutes');
  return res.json();
}

export interface ChatSessionResponse {
  case_profile: StructuredCaseProfile;
  messages: ChatMessageItem[];
}

export type EvidenceProcessingStatus = 'NOT_PROCESSED' | 'UPLOADED' | 'PROCESSING' | 'COMPLETED' | 'FAILED';

/** An uploaded ("Provided by you") file and its processing state. Never a generated document. */
export interface ProvidedDocument {
  id: string;
  name: string;
  file_type?: string;
  uploaded_at?: string;
  download_url: string;
  processing_status?: EvidenceProcessingStatus;
  error_code?: string | null;
  error_message?: string | null;
  retryable?: boolean;
  analysis_status?: 'COMPLETED' | 'LIMITED' | 'FAILED' | 'SKIPPED' | null;
  analysis_mode?: 'llm' | 'rule_based' | null;
  page_count?: number | null;
  has_readable_text?: boolean | null;
  methods?: string[];
  direct_pages?: number;
  ocr_pages?: number;
  failed_pages?: number[];
  findings_count?: number;
  candidate_count?: number;
  conflict_count?: number;
  review_status?: 'NONE' | 'PENDING' | 'OFFERED';
}

export interface EvidenceSourceRef {
  evidence_id: string;
  file_name: string;
  page_number: number | null;
  method: string;
}

export interface EvidenceDetail extends ProvidedDocument {
  pages: Array<{
    page_number: number | null;
    method: string;
    status: string;
    error_code?: string | null;
    has_readable_text: boolean;
    text: string;
  }>;
  warnings: string[];
  analysis: {
    mode: string | null;
    document_type: string | null;
    summary: string;
    findings: Array<{ id: string; type: string; value: string; statement: string; clarity: string; ocr_derived: boolean; source: EvidenceSourceRef | null }>;
    candidate_facts: Record<string, { value: unknown; source: EvidenceSourceRef | null }>;
    corroborated: Array<{ field: string; value: unknown; source: EvidenceSourceRef | null }>;
    conflicts: Array<{ field: string; current_value: unknown; evidence_value: unknown; current_confirmed: boolean; source: EvidenceSourceRef | null }>;
    deadlines: Array<{ text: string; source: EvidenceSourceRef | null }>;
    notes: string[];
    unanalyzed_pages: number[];
  } | null;
}

export async function fetchEvidence(evidenceId: string): Promise<EvidenceDetail> {
  const res = await apiFetch(`${API_BASE_URL}/evidence/${encodeURIComponent(evidenceId)}`);
  if (!res.ok) throw new ApiError(await apiErrorMessage(res, 'Could not load this file.'), res.status);
  return res.json();
}

export async function retryEvidence(evidenceId: string): Promise<ProvidedDocument> {
  const res = await apiFetch(`${API_BASE_URL}/evidence/${encodeURIComponent(evidenceId)}/retry`, { method: 'POST' });
  if (!res.ok) throw new ApiError(await apiErrorMessage(res, 'Could not retry processing.'), res.status);
  return res.json();
}

export async function reviewEvidence(evidenceId: string): Promise<ChatTurnResponse> {
  const res = await apiFetch(`${API_BASE_URL}/evidence/${encodeURIComponent(evidenceId)}/review`, { method: 'POST' });
  if (!res.ok) throw new ApiError(await apiErrorMessage(res, 'Could not open these details for review.'), res.status);
  return res.json();
}

export async function fetchChatCases(): Promise<StructuredCaseProfile[]> {
  const res = await apiFetch(`${API_BASE_URL}/chat/cases`);
  if (!res.ok) throw new Error('Failed to fetch conversational cases');
  return res.json();
}

export async function fetchChatCase(caseId: string): Promise<ChatSessionResponse> {
  const res = await apiFetch(`${API_BASE_URL}/chat/cases/${caseId}`);
  if (!res.ok) throw new Error('Failed to reopen this case');
  return res.json();
}

export interface CaseSummaryResponse { text: string; cached: boolean }

export async function generateCaseSummary(caseId: string): Promise<CaseSummaryResponse> {
  const res = await apiFetch(`${API_BASE_URL}/chat/cases/${encodeURIComponent(caseId)}/summary`, { method: 'POST' });
  if (!res.ok) throw new ApiError(await apiErrorMessage(res, 'Could not generate the case summary.'), res.status);
  return res.json();
}

export async function resolveChatCase(caseId: string): Promise<StructuredCaseProfile> {
  const res = await apiFetch(`${API_BASE_URL}/chat/cases/${caseId}/resolve`, { method: 'POST' });
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
  const res = await apiFetch(`${API_BASE_URL}/documents`);
  if (!res.ok) throw new Error('Failed to fetch documents');
  return res.json();
}

export function absoluteDocumentUrl(path?: string): string | undefined {
  if (!path) return undefined;
  if (/^https?:\/\//.test(path)) return path;
  return `${API_BASE_URL.replace(/\/api\/v1\/?$/, '')}${path}`;
}
