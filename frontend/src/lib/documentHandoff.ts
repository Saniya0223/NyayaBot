import type { StructuredCaseProfile } from './api';

type DocumentAction = { type: string; doc_type?: string; label: string; open_confirmation_modal?: boolean };

type HandoffOptions = {
  isLLMActive: boolean;
  missingDocumentFields?: readonly string[];
  recommendedDocType?: string;
  openModal: (docType: string, docLabel: string) => void;
  sendMessage: (message: string) => void;
};

export function routeDocumentAction(action: DocumentAction, options: HandoffOptions): void {
  if (action.type === 'PREPARE_DOC') {
    const docType = action.doc_type || options.recommendedDocType;
    if (docType) options.openModal(docType, action.label || 'Prepare document');
    return;
  }

  if (options.isLLMActive && options.missingDocumentFields?.length) {
    options.sendMessage(`I want to prepare the ${action.label}. Please ask me for the missing details.`);
    return;
  }
  options.openModal(action.doc_type || 'GENERAL_COMPLAINT_LETTER', action.label || 'Prepare document');
}

export function openEligibleDocumentHandoff(
  action: DocumentAction | undefined,
  recommendedDocType: string | undefined,
  openModal: (docType: string, docLabel: string) => void,
): boolean {
  if (action?.type !== 'PREPARE_DOC' || !action.open_confirmation_modal) return false;
  const docType = action.doc_type || recommendedDocType;
  if (!docType) return false;
  openModal(docType, action.label || 'Prepare document');
  return true;
}

function knownText(profile: StructuredCaseProfile, key: string): string {
  const value = profile.key_facts?.[key] || profile.fact_metadata?.[key]?.value;
  return typeof value === 'string' ? value : '';
}

export function knownDocumentValues(profile: StructuredCaseProfile, docType: string) {
  const otherParty = docType === 'POLICE_COMPLAINT_BNSS'
    ? profile.police_station_name || knownText(profile, 'police_station_name')
    : docType === 'CYBERCRIME_BANK_FREEZE'
      ? profile.bank_name || knownText(profile, 'bank_name')
      : profile.opposite_party_name || knownText(profile, 'opposite_party_name')
        || knownText(profile, 'recipient_name')
        || (docType === 'GENERAL_COMPLAINT_LETTER' ? profile.bank_name || profile.police_station_name || '' : '');

  return {
    fullName: profile.user_name || knownText(profile, 'user_name'),
    otherParty,
    city: profile.user_city || knownText(profile, 'user_city'),
    amount: profile.disputed_amount > 0 ? String(profile.disputed_amount) : '',
    propertyAddress: profile.property_address || knownText(profile, 'property_address'),
    relevantDate: docType === 'TENANT_DEMAND_NOTICE'
      ? profile.vacating_date || knownText(profile, 'vacating_date')
      : profile.incident_date || knownText(profile, 'incident_date'),
    transactionId: profile.transaction_id || knownText(profile, 'transaction_id'),
  };
}
