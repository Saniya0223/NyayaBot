'use client';

import { useState } from 'react';
import { Check, Copy, Download, FileText, LoaderCircle, PencilLine, RefreshCw, ShieldCheck, X } from 'lucide-react';
import { absoluteDocumentUrl, DocumentResponse, generateDocument, StructuredCaseProfile } from '@/lib/api';

interface Props {
  profile: StructuredCaseProfile;
  docType: string;
  docLabel: string;
  onClose: () => void;
}

export default function FactualConfirmModal({ profile, docType, docLabel, onClose }: Props) {
  const [fullName, setFullName] = useState(profile.user_name || '');
  const [otherParty, setOtherParty] = useState(profile.opposite_party_name || profile.bank_name || profile.police_station_name || '');
  const [city, setCity] = useState(profile.user_city || '');
  const [amount, setAmount] = useState(profile.disputed_amount ? String(profile.disputed_amount) : '');
  const [propertyAddress, setPropertyAddress] = useState(profile.property_address || '');
  const [relevantDate, setRelevantDate] = useState(profile.vacating_date || profile.incident_date || '');
  const [transactionId, setTransactionId] = useState(profile.transaction_id || '');
  const [isGenerating, setIsGenerating] = useState(false);
  const [document, setDocument] = useState<DocumentResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const needsAmount = !['POLICE_COMPLAINT_BNSS', 'RTI_SEC6', 'GENERAL_COMPLAINT_LETTER'].includes(docType);
  const needsProperty = docType === 'TENANT_DEMAND_NOTICE';
  const needsDate = ['TENANT_DEMAND_NOTICE', 'CYBERCRIME_BANK_FREEZE', 'EDAAKHIL_COMPLAINT'].includes(docType);
  const needsTransaction = docType === 'CYBERCRIME_BANK_FREEZE';
  const otherPartyLabel = docType === 'POLICE_COMPLAINT_BNSS' ? 'Police station' : docType === 'CYBERCRIME_BANK_FREEZE' ? 'Bank / payment app' : docType === 'RTI_SEC6' ? 'Public authority' : 'Other party';
  const canGenerate = Boolean(fullName.trim() && otherParty.trim() && city.trim() && (!needsAmount || Number(amount) > 0) && (!needsProperty || propertyAddress.trim()) && (!needsDate || relevantDate.trim()) && (!needsTransaction || transactionId.trim()));

  async function createDocument() {
    if (!canGenerate || isGenerating) return;
    setIsGenerating(true);
    setError(null);
    try {
      const result = await generateDocument(profile.case_id, docType, {
        complainant_name: fullName.trim(),
        complainant_city: city.trim(),
        opposite_party_name: otherParty.trim(),
        recipient_name: otherParty.trim(),
        police_station_name: otherParty.trim(),
        bank_name: otherParty.trim(),
        disputed_amount: needsAmount ? Number(amount) : undefined,
        property_address: needsProperty ? propertyAddress.trim() : undefined,
        vacating_date: docType === 'TENANT_DEMAND_NOTICE' ? relevantDate.trim() : undefined,
        incident_date: docType !== 'TENANT_DEMAND_NOTICE' && needsDate ? relevantDate.trim() : undefined,
        transaction_id: needsTransaction ? transactionId.trim() : undefined,
      });
      setDocument(result);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Document generation failed. Your case is still saved.');
    } finally {
      setIsGenerating(false);
    }
  }

  async function copyText() {
    if (!document) return;
    const plainText = document.content_html.replace(/<style[\s\S]*?<\/style>/gi, '').replace(/<[^>]+>/g, '\n').replace(/\n\s*\n/g, '\n\n').trim();
    await navigator.clipboard.writeText(plainText);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  }

  function openDownload(path?: string) {
    const url = absoluteDocumentUrl(path);
    if (url) window.open(url, '_blank', 'noopener,noreferrer');
  }

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-[#10241d]/50 p-3 backdrop-blur-sm" role="dialog" aria-modal="true" aria-labelledby="document-dialog-title">
      <div className="flex max-h-[92vh] w-full max-w-3xl flex-col overflow-hidden rounded-[24px] border border-[#dbe4de] bg-white shadow-2xl">
        <header className="flex items-center justify-between border-b border-[#e3e9e5] px-5 py-4">
          <div className="flex items-center gap-3">
            <span className="grid size-10 place-items-center rounded-xl bg-[#e8f2ec] text-[#174e3b]"><FileText className="size-5" /></span>
            <div><h2 id="document-dialog-title" className="text-sm font-bold text-[#21312a]">{document ? document.title : `Confirm details for ${docLabel}`}</h2><p className="mt-0.5 text-[11px] text-[#74817a]">Nothing is filed or sent automatically.</p></div>
          </div>
          <button type="button" onClick={onClose} className="grid size-9 place-items-center rounded-xl text-[#6f7c75] hover:bg-[#f1f5f2]" aria-label="Close document dialog"><X className="size-5" /></button>
        </header>

        <div className="soft-scrollbar flex-1 overflow-y-auto p-5 sm:p-6">
          {!document ? (
            <div className="space-y-5">
              <div className="flex gap-2.5 rounded-2xl border border-[#cfe0d6] bg-[#f1f7f3] p-3.5 text-[11px] leading-5 text-[#52655b]"><ShieldCheck className="mt-0.5 size-4 shrink-0 text-[#2f755b]" /><span>Important names, amounts, dates, and addresses must be confirmed by you. NyayaBot will not fill missing personal details with guesses.</span></div>
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <Field label="Your full legal name" value={fullName} onChange={setFullName} placeholder="As it should appear in the letter" />
                <Field label={otherPartyLabel} value={otherParty} onChange={setOtherParty} placeholder={`Enter ${otherPartyLabel.toLowerCase()}`} />
                <Field label="City / jurisdiction" value={city} onChange={setCity} placeholder="City and State" />
                {needsAmount ? <Field label="Disputed amount (₹)" value={amount} onChange={setAmount} placeholder="50000" inputMode="decimal" /> : null}
                {needsProperty ? <div className="sm:col-span-2"><Field label="Full rented property address" value={propertyAddress} onChange={setPropertyAddress} placeholder="House/flat, street, locality, city, PIN" /></div> : null}
                {needsDate ? <Field label={docType === 'TENANT_DEMAND_NOTICE' ? 'Vacating / handover date' : 'Incident / transaction date'} value={relevantDate} onChange={setRelevantDate} placeholder="DD Month YYYY" /> : null}
                {needsTransaction ? <Field label="UTR / transaction ID" value={transactionId} onChange={setTransactionId} placeholder="Enter the exact reference" /> : null}
              </div>
              <div className="rounded-2xl border border-[#e1e7e3] p-4"><p className="text-[10px] font-bold uppercase tracking-[0.1em] text-[#75827b]">Evidence currently noted</p><div className="mt-2 flex flex-wrap gap-2">{profile.evidence_checklist.filter((item) => item.is_available).map((item) => <span key={item.id} className="rounded-full bg-[#e8f2ec] px-2.5 py-1 text-[10px] font-semibold text-[#2e6d53]">✓ {item.name}</span>)}{profile.evidence_checklist.every((item) => !item.is_available) ? <span className="text-[11px] text-[#86918c]">No evidence confirmed yet</span> : null}</div></div>
              {error ? <p role="alert" className="rounded-xl bg-[#fff0ed] p-3 text-xs text-[#a2473a]">{error}</p> : null}
            </div>
          ) : (
            <div className="space-y-4">
              <div className="flex flex-wrap items-center justify-between gap-2 rounded-2xl border border-[#dfe6e2] bg-[#f8faf8] p-3">
                <span className="flex items-center gap-2 text-xs font-bold text-[#2e6d53]"><Check className="size-4" />Draft generated</span>
                <div className="flex flex-wrap gap-2">
                  <button type="button" onClick={() => setDocument(null)} className="flex items-center gap-1.5 rounded-lg border border-[#d6e0da] bg-white px-3 py-2 text-[11px] font-semibold text-[#526159]"><PencilLine className="size-3.5" />Edit details</button>
                  <button type="button" onClick={() => void createDocument()} disabled={isGenerating} className="flex items-center gap-1.5 rounded-lg border border-[#d6e0da] bg-white px-3 py-2 text-[11px] font-semibold text-[#526159]"><RefreshCw className="size-3.5" />Regenerate</button>
                  <button type="button" onClick={() => void copyText()} className="flex items-center gap-1.5 rounded-lg border border-[#d6e0da] bg-white px-3 py-2 text-[11px] font-semibold text-[#526159]">{copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}{copied ? 'Copied' : 'Copy'}</button>
                </div>
              </div>
              <div className="min-h-[420px] overflow-x-auto rounded-xl border border-[#d9dedb] bg-white p-7 font-serif text-xs leading-6 text-black shadow-inner sm:p-10" dangerouslySetInnerHTML={{ __html: document.content_html }} />
            </div>
          )}
        </div>

        <footer className="flex items-center justify-between gap-3 border-t border-[#e3e9e5] bg-[#fbfcfb] px-5 py-4">
          <button type="button" onClick={onClose} className="rounded-xl px-3 py-2 text-xs font-semibold text-[#66736d] hover:bg-[#eef2ef]">{document ? 'Done' : 'Cancel'}</button>
          {!document ? (
            <button type="button" onClick={() => void createDocument()} disabled={!canGenerate || isGenerating} className="flex items-center gap-2 rounded-xl bg-[#174e3b] px-4 py-2.5 text-xs font-bold text-white hover:bg-[#103c2d] disabled:bg-[#bdc9c2]">{isGenerating ? <LoaderCircle className="size-4 animate-spin" /> : <Check className="size-4" />}{isGenerating ? 'Generating safely…' : 'Confirm & generate'}</button>
          ) : (
            <div className="flex gap-2"><button type="button" onClick={() => openDownload(document.docx_download_url)} className="flex items-center gap-2 rounded-xl border border-[#b9c9bf] bg-white px-3.5 py-2.5 text-xs font-bold text-[#174e3b]"><Download className="size-4" />DOCX</button><button type="button" onClick={() => openDownload(document.pdf_download_url)} className="flex items-center gap-2 rounded-xl bg-[#174e3b] px-3.5 py-2.5 text-xs font-bold text-white"><Download className="size-4" />PDF</button></div>
          )}
        </footer>
      </div>
    </div>
  );
}

function Field({ label, value, onChange, placeholder, inputMode }: { label: string; value: string; onChange: (value: string) => void; placeholder: string; inputMode?: 'decimal' }) {
  return (
    <label className="block text-[11px] font-bold text-[#405048]">{label}<input value={value} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} inputMode={inputMode} className="mt-1.5 w-full rounded-xl border border-[#ccd8d1] bg-white px-3.5 py-2.5 text-sm font-medium text-[#24322c] placeholder:font-normal placeholder:text-[#a0aaa5] focus:border-[#5d8c73] focus:outline-none" /></label>
  );
}
