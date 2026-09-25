import assert from 'node:assert/strict';
import test from 'node:test';

import { confirmedDocumentValues, initialDocumentValues, requiredDocumentValuesComplete } from '../src/lib/documentRequirements.ts';

const assessment = {
  document_type: 'SALARY_DEMAND_NOTICE', intent: 'USER_REQUESTED', status: 'OPTIONAL_FIELDS_AVAILABLE',
  ready_to_generate: true, blockers: [], missing_required_fields: [], missing_optional_fields: ['employee_role'],
  fields: [
    { key: 'complainant_name', label: 'Name', required: true, data_type: 'text', value: 'Example Employee' },
    { key: 'disputed_amount', label: 'Amount', required: true, data_type: 'number', value: 400000 },
    { key: 'employee_role', label: 'Role', required: false, data_type: 'text', value: null },
  ],
};

test('known values prefill, while missing optional fields do not block confirmation', () => {
  const values = initialDocumentValues(assessment);
  assert.deepEqual(values, { complainant_name: 'Example Employee', disputed_amount: '400000', employee_role: '' });
  assert.equal(requiredDocumentValuesComplete(assessment, values), true);
  assert.deepEqual(confirmedDocumentValues(assessment, values), { complainant_name: 'Example Employee', disputed_amount: 400000 });
});

test('only required fields and backend blockers disable confirmation', () => {
  const values = initialDocumentValues(assessment);
  assert.equal(requiredDocumentValuesComplete(assessment, { ...values, complainant_name: '' }), false);
  assert.equal(requiredDocumentValuesComplete({ ...assessment, blockers: ['Safety first'] }, values), false);
  assert.equal(requiredDocumentValuesComplete(assessment, { ...values, disputed_amount: '0' }), false);
});

test('a cleared prefilled personal field is sent as blank for document review', () => {
  const personal = { ...assessment, fields: [
    ...assessment.fields,
    { key: 'complainant_phone', label: 'Phone', required: false, data_type: 'text', value: '9876543210' },
  ] };
  const values = { ...initialDocumentValues(personal), complainant_phone: '' };
  assert.equal(confirmedDocumentValues(personal, values).complainant_phone, '');
});
