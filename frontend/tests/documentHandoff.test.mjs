import assert from 'node:assert/strict';
import test from 'node:test';

import { knownDocumentValues, routeDocumentAction } from '../src/lib/documentHandoff.ts';

test('PREPARE_DOC opens the confirmation modal even when document fields are missing', () => {
  const opened = [];
  const sent = [];
  routeDocumentAction(
    { type: 'PREPARE_DOC', doc_type: 'FORMAL_LEGAL_NOTICE', label: 'Consumer Grievance Letter' },
    {
      isLLMActive: true,
      missingDocumentFields: ['user_name', 'user_city'],
      openModal: (type, label) => opened.push({ type, label }),
      sendMessage: (message) => sent.push(message),
    },
  );
  assert.deepEqual(opened, [{ type: 'FORMAL_LEGAL_NOTICE', label: 'Consumer Grievance Letter' }]);
  assert.deepEqual(sent, []);
});

test('known values prefill the correct document fields without inventing missing values', () => {
  const profile = {
    user_name: 'Saniya Sharma',
    user_city: 'Pune',
    opposite_party_name: 'Seller',
    bank_name: 'Example Bank',
    police_station_name: 'Central Police Station',
    disputed_amount: 38000,
    incident_date: '10 September 2026',
    transaction_id: 'UTR123456',
    key_facts: {},
  };
  const consumer = knownDocumentValues(profile, 'FORMAL_LEGAL_NOTICE');
  assert.equal(consumer.fullName, 'Saniya Sharma');
  assert.equal(consumer.otherParty, 'Seller');
  assert.equal(consumer.amount, '38000');
  assert.equal(consumer.city, 'Pune');
  assert.equal(consumer.propertyAddress, '');
  assert.equal(knownDocumentValues(profile, 'CYBERCRIME_BANK_FREEZE').otherParty, 'Example Bank');
  assert.equal(knownDocumentValues(profile, 'POLICE_COMPLAINT_BNSS').otherParty, 'Central Police Station');
  assert.equal(knownDocumentValues({ ...profile, opposite_party_name: '', key_facts: { recipient_name: 'Public Authority' } }, 'RTI_SEC6').otherParty, 'Public Authority');
});

test('ordinary non-document actions retain their existing chat behavior', () => {
  const opened = [];
  const sent = [];
  routeDocumentAction(
    { type: 'OTHER_ACTION', label: 'Continue' },
    {
      isLLMActive: true,
      missingDocumentFields: ['user_name'],
      openModal: (type, label) => opened.push({ type, label }),
      sendMessage: (message) => sent.push(message),
    },
  );
  assert.deepEqual(opened, []);
  assert.equal(sent.length, 1);
});
