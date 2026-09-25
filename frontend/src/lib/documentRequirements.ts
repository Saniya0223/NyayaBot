import type { DocumentAssessment } from './api';

export function initialDocumentValues(assessment: DocumentAssessment): Record<string, string> {
  return Object.fromEntries(assessment.fields.map((field) => [
    field.key, Array.isArray(field.value) ? field.value.join(', ') : String(field.value ?? ''),
  ]));
}

export function requiredDocumentValuesComplete(assessment: DocumentAssessment | null, values: Record<string, string>): boolean {
  return Boolean(assessment && !assessment.blockers.length && assessment.fields.filter((field) => field.required).every((field) => {
    const value = values[field.key]?.trim() ?? '';
    return field.data_type === 'number' ? Number(value) > 0 : Boolean(value);
  }));
}

export function confirmedDocumentValues(assessment: DocumentAssessment, values: Record<string, string>): Record<string, string | number> {
  return Object.fromEntries(assessment.fields.filter((field) => field.required || field.key.startsWith('complainant_') || values[field.key]?.trim()).map((field) => [
    field.key, field.data_type === 'number' ? Number(values[field.key]) : values[field.key].trim(),
  ]));
}
