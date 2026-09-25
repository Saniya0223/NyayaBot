import type { StructuredCaseProfile } from './api';

const factualLabels: Record<string, string> = {
  disputed_amount: 'Amount in dispute', incident_date: 'Incident or transaction date',
  vacating_date: 'Vacating date', unpaid_months: 'Unpaid months',
  opposite_party_name: 'Other party', property_address: 'Property',
  user_city: 'City', user_state: 'State', product_name: 'Product or service',
  seller_platform: 'Seller or platform', purchase_timing: 'Purchase timing',
  seller_contacted: 'Seller contacted', seller_response_received: 'Seller responded',
  seller_response: 'Seller response', landlord_contacted: 'Landlord contacted',
  landlord_reason: 'Landlord response', hr_contacted: 'Employer contacted',
  bank_reported: 'Bank notified', cyber_reported: 'Cybercrime report made',
  termination_occurred: 'Employment terminated',
  eviction_proceeding_started: 'Eviction proceeding started',
  formal_proceeding_started: 'Formal proceeding started',
  court_notice_received: 'Court or tribunal notice received',
};

function recordedSource(source: string): boolean {
  return source === 'chat' || source.endsWith('_chat') || source === 'document_confirmation'
    || source === 'user_conflict_confirmation' || source.startsWith('upload:');
}

export function caseTypeLabel(profile: StructuredCaseProfile): string {
  return profile.category === 'GENERAL' || profile.readiness === 'PRE_INTAKE'
    ? 'Case type not identified yet'
    : profile.category_display_name;
}

export function keyFacts(profile: StructuredCaseProfile): Array<{ key: string; text: string }> {
  return Object.entries(factualLabels).flatMap(([key, label]) => {
    const metadata = profile.fact_metadata?.[key];
    if (!metadata || !recordedSource(metadata.source) || ((metadata.confidence ?? 0) < 0.75 && !metadata.confirmed)) return [];
    const value = key in profile ? profile[key as keyof StructuredCaseProfile] : profile.key_facts?.[key];
    if (value === null || value === undefined || value === '' || (Array.isArray(value) && !value.length)) return [];
    if (JSON.stringify(value) !== JSON.stringify(metadata.value)) return [];
    let rendered: string;
    if (typeof value === 'boolean') rendered = value ? 'Yes' : 'No';
    else if (key === 'disputed_amount' && typeof value === 'number') rendered = `₹${value.toLocaleString('en-IN')}`;
    else if (Array.isArray(value)) rendered = value.join(', ');
    else if (typeof value === 'string' || typeof value === 'number') rendered = String(value);
    else return [];
    return [{ key, text: `${label}: ${rendered}` }];
  });
}

export interface JourneyEvent {
  id: string;
  label: string;
  recordedAt?: string;
  origin: 'reported' | 'generated' | 'recorded';
  downloadUrl?: string;
}

export function journeyEvents(profile: StructuredCaseProfile): JourneyEvent[] {
  const reported = (profile.timeline ?? [])
    .filter((event) => !['case_started', 'document_uploaded', 'document_prepared'].includes(event.type))
    .map((event) => ({
      id: event.id, label: event.label, recordedAt: event.date,
      origin: event.type === 'case_resolved' ? 'recorded' as const : 'reported' as const,
    }));
  const generated = (profile.documents ?? []).map((document) => ({
    id: `document-${document.id}`, label: `${document.title} generated`,
    recordedAt: document.created_at, origin: 'generated' as const,
    downloadUrl: document.pdf_download_url || document.docx_download_url,
  }));
  return [...reported, ...generated].sort((left, right) =>
    (left.recordedAt ?? '').localeCompare(right.recordedAt ?? ''));
}

export function suggestedActions(profile: StructuredCaseProfile): Array<{ key: string; label: string; docType?: string }> {
  const safety = profile.safety_status;
  if (safety?.is_safety_case && (safety.immediate_danger !== false || !profile.key_facts?.safety_triage_complete)) return [];
  const suggestions: Array<{ key: string; label: string; docType?: string }> = [];
  const caseUnderstood = profile.category !== 'GENERAL'
    && Boolean(keyFacts(profile).length || profile.actions_completed?.length);
  const document = profile.recommended_next_action;
  if (document?.type === 'PREPARE_DOC' && document.doc_type) {
    suggestions.push({ key: 'document', label: document.label, docType: document.doc_type });
  } else if (caseUnderstood && profile.next_action_plan?.status === 'READY' && profile.next_action_plan.action_id !== 'PREPARE_DOC') {
    suggestions.push({ key: 'plan', label: profile.next_action_plan.label });
  }
  if (profile.professional_help?.level === 'CONSIDER_LEGAL_HELP') {
    suggestions.push({ key: 'legal-help', label: 'Consider consulting a legal professional' });
  } else if (['LEGAL_HELP_RECOMMENDED', 'URGENT_LEGAL_HELP'].includes(profile.professional_help?.level ?? '')) {
    suggestions.push({ key: 'legal-help', label: 'Legal assistance may be useful before the next significant step' });
  }
  return suggestions;
}

export function canGenerateSummary(profile: StructuredCaseProfile): boolean {
  const safety = profile.safety_status;
  if (safety?.is_safety_case && (safety.immediate_danger !== false || !profile.key_facts?.safety_triage_complete)) return false;
  return profile.category !== 'GENERAL'
    && Boolean(keyFacts(profile).length || profile.actions_completed?.length || profile.documents?.length);
}
