import { test } from 'node:test';
import assert from 'node:assert/strict';
import { canGenerateSummary, caseTypeLabel, journeyEvents, keyFacts, suggestedActions } from '../src/lib/caseWorkspace.ts';

function profile(overrides = {}) {
  return {
    category: 'CONSUMER', category_display_name: 'Consumer complaint', readiness: 'UNDERSTANDING_CASE',
    key_facts: {}, fact_metadata: {}, timeline: [], documents: [], actions_completed: [],
    ...overrides,
  };
}

test('unknown case and unrecorded values stay out of key facts', () => {
  const value = profile({ category: 'GENERAL', disputed_amount: 25000, key_facts: { product_name: 'Phone' } });
  assert.equal(caseTypeLabel(value), 'Case type not identified yet');
  assert.deepEqual(keyFacts(value), []);
  assert.equal(canGenerateSummary(value), false);
});

test('only provenance-backed matching facts are shown, including explicit no', () => {
  const value = profile({
    disputed_amount: 25000,
    key_facts: { seller_contacted: false, product_name: 'Phone', issue_description: 'old' },
    fact_metadata: {
      disputed_amount: { value: 25000, source: 'groq_chat', confidence: 0.95 },
      seller_contacted: { value: false, source: 'groq_chat', confidence: 0.9 },
      product_name: { value: 'Tablet', source: 'groq_chat', confidence: 0.9 },
      issue_description: { value: 'old', source: 'system_derived', confidence: 1 },
    },
  });
  assert.deepEqual(keyFacts(value).map((item) => item.key), ['disputed_amount', 'seller_contacted']);
  assert.equal(canGenerateSummary(value), true);
});

test('journey contains actual recorded events and generated files, never planned stages or uploads', () => {
  const value = profile({
    legal_journey: [{ id: 'COURT', status: 'COMPLETED', title: 'Court filing' }],
    timeline: [
      { id: 'start', type: 'case_started', date: '2026-01-01', label: 'Started', source: 'chat' },
      { id: 'upload', type: 'document_uploaded', date: '2026-01-02', label: 'Uploaded', source: 'upload' },
      { id: 'sent', type: 'formal_demand_sent', date: '2026-01-03', label: 'Notice sent', source: 'chat' },
    ],
    documents: [{ id: 'doc', title: 'Notice', created_at: '2026-01-04', pdf_download_url: '/notice.pdf' }],
  });
  assert.deepEqual(journeyEvents(value).map((item) => item.label), ['Notice sent', 'Notice generated']);
  assert.equal(journeyEvents(value)[0].origin, 'reported');
});

test('suggestions come from backend state and do not imply completed actions', () => {
  const value = profile({
    key_facts: { product_name: 'Phone' },
    fact_metadata: { product_name: { value: 'Phone', source: 'groq_chat', confidence: 0.95 } },
    next_action_plan: { action_id: 'send_grievance', label: 'Send grievance', status: 'READY' },
    professional_help: { level: 'CONSIDER_LEGAL_HELP' },
  });
  assert.deepEqual(suggestedActions(value).map((item) => item.key), ['plan', 'legal-help']);
  assert.deepEqual(suggestedActions(profile()), []);
  assert.deepEqual(suggestedActions({ ...value, safety_status: { is_safety_case: true, immediate_danger: true } }), []);
});
