'use client';

import { useEffect, useState } from 'react';
import { Check, Download, FileText, LoaderCircle, X } from 'lucide-react';
import { absoluteDocumentUrl, DocumentAssessment, DocumentResponse, fetchDocumentAssessment, generateDocument, StructuredCaseProfile } from '@/lib/api';
import { confirmedDocumentValues, initialDocumentValues, requiredDocumentValuesComplete } from '@/lib/documentRequirements';

interface Props {
  profile: StructuredCaseProfile;
  docType: string;
  docLabel: string;
  onClose: () => void;
}

export default function FactualConfirmModal({ profile, docType, docLabel, onClose }: Props) {
  const [assessment, setAssessment] = useState<DocumentAssessment | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [isLoading, setIsLoading] = useState(true);
  const [isGenerating, setIsGenerating] = useState(false);
  const [document, setDocument] = useState<DocumentResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    void fetchDocumentAssessment(profile.case_id, docType)
      .then((result) => {
        if (!active) return;
        setAssessment(result);
        setValues(initialDocumentValues(result));
      })
      .catch((caught) => { if (active) setError(caught instanceof Error ? caught.message : 'Could not load document requirements.'); })
      .finally(() => { if (active) setIsLoading(false); });
    return () => { active = false; };
  }, [profile.case_id, docType]);

  const requiredFields = assessment?.fields.filter((field) => field.required) ?? [];
  const optionalFields = assessment?.fields.filter((field) => !field.required) ?? [];
  const requiredComplete = requiredDocumentValuesComplete(assessment, values);

  async function createDocument() {
    if (!requiredComplete || isGenerating) return;
    setIsGenerating(true);
    setError(null);
    try {
      const overrideData = confirmedDocumentValues(assessment!, values);
      const result = await generateDocument(profile.case_id, docType, overrideData);
      if (!result.pdf_download_url || !result.docx_download_url) throw new Error('The document files were not created. Please retry.');
      setDocument(result);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Document generation failed. Your case is still saved.');
    } finally {
      setIsGenerating(false);
    }
  }

  function openDownload(path?: string) {
    const url = absoluteDocumentUrl(path);
    if (url) window.open(url, '_blank', 'noopener,noreferrer');
  }

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-[#10241d]/50 p-3 backdrop-blur-sm" role="dialog" aria-modal="true" aria-labelledby="document-dialog-title">
      <div className="flex max-h-[92vh] w-full max-w-3xl flex-col overflow-hidden rounded-[24px] border border-[#dbe4de] bg-white shadow-2xl">
        <header className="flex items-center justify-between border-b border-[#e3e9e5] px-5 py-4">
          <div className="flex items-center gap-3"><FileText className="size-5 text-[#174e3b]" /><div><h2 id="document-dialog-title" className="text-sm font-bold text-[#21312a]">{document ? document.title : `Confirm details for ${docLabel}`}</h2><p className="text-[11px] text-[#74817a]">Nothing is filed or sent automatically.</p></div></div>
          <button type="button" onClick={onClose} aria-label="Close document dialog"><X className="size-5" /></button>
        </header>
        <div className="soft-scrollbar flex-1 overflow-y-auto space-y-5 p-5 sm:p-6">
          {isLoading ? <p role="status">Loading document requirements…</p> : null}
          {!document && assessment ? <>
            <p className="rounded-xl bg-[#f1f7f3] p-3 text-xs text-[#52655b]">Review all details, including any personal information prefilled from your profile. Change anything that is not right before generating. Nothing is sent or filed automatically.</p>
            {assessment.blockers.length ? <p role="alert" className="text-xs text-[#a2473a]">{assessment.blockers.join(' ')}</p> : null}
            <section aria-label="Required document details"><h3 className="mb-3 text-xs font-bold">Required details</h3><div className="grid grid-cols-1 gap-4 sm:grid-cols-2">{requiredFields.map((field) => <Field key={field.key} label={field.label} value={values[field.key] ?? ''} required dataType={field.data_type} onChange={(value) => setValues((current) => ({ ...current, [field.key]: value }))} />)}</div></section>
            {optionalFields.length ? <section aria-label="Optional document details"><h3 className="mb-1 text-xs font-bold">Optional details</h3><p className="mb-3 text-xs text-[#6b7870]">Review any prefilled values, or leave optional details blank.</p><div className="grid grid-cols-1 gap-4 sm:grid-cols-2">{optionalFields.map((field) => <Field key={field.key} label={field.label} value={values[field.key] ?? ''} required={false} dataType={field.data_type} onChange={(value) => setValues((current) => ({ ...current, [field.key]: value }))} />)}</div></section> : null}
          </> : null}
          {error ? <p role="alert" className="rounded-xl bg-[#fff0ed] p-3 text-xs text-[#a2473a]">{error}</p> : null}
          {document ? <div className="space-y-3"><p className="flex items-center gap-2 text-xs font-bold text-[#2e6d53]"><Check className="size-4" />Draft generated</p><div className="min-h-[350px] overflow-x-auto rounded-xl border border-[#d9dedb] bg-white p-7 font-serif text-xs leading-6 text-black" dangerouslySetInnerHTML={{ __html: document.content_html }} /></div> : null}
        </div>
        <footer className="flex items-center justify-between gap-3 border-t border-[#e3e9e5] bg-[#fbfcfb] px-5 py-4">
          <button type="button" onClick={onClose} className="text-xs font-semibold text-[#66736d]">{document ? 'Done' : 'Cancel'}</button>
          {!document ? <button type="button" onClick={() => void createDocument()} disabled={!requiredComplete || isGenerating} className="flex items-center gap-2 rounded-xl bg-[#174e3b] px-4 py-2.5 text-xs font-bold text-white disabled:bg-[#bdc9c2]">{isGenerating ? <LoaderCircle className="size-4 animate-spin" /> : <Check className="size-4" />}{isGenerating ? 'Generating…' : 'Confirm & generate'}</button> : <div className="flex gap-2"><button type="button" onClick={() => openDownload(document.docx_download_url)} className="flex items-center gap-2 rounded-xl border px-3 py-2 text-xs font-bold"><Download className="size-4" />DOCX</button><button type="button" onClick={() => openDownload(document.pdf_download_url)} className="flex items-center gap-2 rounded-xl bg-[#174e3b] px-3 py-2 text-xs font-bold text-white"><Download className="size-4" />PDF</button></div>}
        </footer>
      </div>
    </div>
  );
}

function Field({ label, value, required, dataType, onChange }: { label: string; value: string; required: boolean; dataType: string; onChange: (value: string) => void }) {
  const className = 'mt-1.5 w-full rounded-xl border border-[#ccd8d1] bg-white px-3.5 py-2.5 text-sm font-medium text-[#24322c] focus:border-[#5d8c73] focus:outline-none';
  return <label className="block text-[11px] font-bold text-[#405048]">{label}{required ? ' *' : ' (optional)'}{dataType === 'multiline' ? <textarea value={value} onChange={(event) => onChange(event.target.value)} required={required} rows={4} className={className} /> : <input value={value} onChange={(event) => onChange(event.target.value)} required={required} inputMode={dataType === 'number' ? 'decimal' : undefined} className={className} />}</label>;
}
