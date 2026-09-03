'use client';

import { ChangeEvent, useState } from 'react';
import { FileSearch, LoaderCircle, Paperclip, ShieldCheck, Upload, X } from 'lucide-react';
import { StructuredCaseProfile, uploadEvidenceFile } from '@/lib/api';

interface Props {
  caseId: string;
  onExtracted: (profile: StructuredCaseProfile, replyText: string, quickReplies: string[]) => void;
  onClose: () => void;
}

const types = [
  { value: 'RENTAL_AGREEMENT', label: 'Rental / tenancy agreement' },
  { value: 'SALARY_SLIP', label: 'Salary slip / appointment letter' },
  { value: 'INVOICE', label: 'Invoice / order receipt' },
  { value: 'REJECTION_REPLY', label: 'Reply, rejection, or prior complaint' },
];

export default function DocumentIntelligenceModal({ caseId, onExtracted, onClose }: Props) {
  const [documentType, setDocumentType] = useState(types[0].value);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [excerpt, setExcerpt] = useState('');
  const [processing, setProcessing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function chooseFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    setSelectedFile(file || null);
  }

  async function processUpload() {
    if (!selectedFile || processing) return;
    setProcessing(true);
    setError(null);
    try {
      const response = await uploadEvidenceFile({
        case_id: caseId,
        doc_type: documentType,
        file: selectedFile,
        excerpt: excerpt.trim() || undefined,
      });
      onExtracted(response.case_profile, response.reply_text, response.quick_replies);
      onClose();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'The upload could not be processed. Your case is unchanged.');
    } finally {
      setProcessing(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-[#10241d]/50 p-3 backdrop-blur-sm" role="dialog" aria-modal="true" aria-labelledby="upload-title">
      <div className="w-full max-w-xl overflow-hidden rounded-[24px] border border-[#dbe4de] bg-white shadow-2xl">
        <header className="flex items-center justify-between border-b border-[#e3e9e5] px-5 py-4">
          <div className="flex items-center gap-3"><span className="grid size-10 place-items-center rounded-xl bg-[#e8f2ec] text-[#174e3b]"><FileSearch className="size-5" /></span><div><h2 id="upload-title" className="text-sm font-bold text-[#21312a]">Add evidence to this case</h2><p className="mt-0.5 text-[11px] text-[#74817a]">Upload first; extracted facts always require confirmation.</p></div></div>
          <button type="button" onClick={onClose} aria-label="Close upload dialog" className="grid size-9 place-items-center rounded-xl text-[#6f7c75] hover:bg-[#f1f5f2]"><X className="size-5" /></button>
        </header>

        <div className="space-y-5 p-5 sm:p-6">
          <label className="block text-[11px] font-bold text-[#405048]">Document type<select value={documentType} onChange={(event) => setDocumentType(event.target.value)} className="mt-1.5 w-full rounded-xl border border-[#ccd8d1] bg-white px-3.5 py-2.5 text-sm text-[#24322c] focus:border-[#5d8c73] focus:outline-none">{types.map((type) => <option key={type.value} value={type.value}>{type.label}</option>)}</select></label>

          <label className={`flex min-h-28 flex-col items-center justify-center rounded-2xl border-2 border-dashed px-5 py-5 text-center transition ${selectedFile ? 'border-[#76a98f] bg-[#f1f7f3]' : 'border-[#d3ddd7] bg-[#fbfcfb] hover:border-[#98b5a5]'}`}>
            <input type="file" accept=".pdf,.doc,.docx,.png,.jpg,.jpeg,.txt,.eml" onChange={chooseFile} className="sr-only" />
            {selectedFile ? <Paperclip className="size-5 text-[#2f755b]" /> : <Upload className="size-5 text-[#7c8a83]" />}
            <span className="mt-2 text-xs font-bold text-[#35453e]">{selectedFile?.name || 'Choose a document or screenshot'}</span>
            <span className="mt-1 text-[10px] text-[#87938d]">PDF, DOCX, JPG, PNG, or TXT · up to 10 MB</span>
          </label>

          <label className="block text-[11px] font-bold text-[#405048]">Relevant text excerpt <span className="font-medium text-[#8a958f]">(optional MVP fallback)</span><textarea rows={4} value={excerpt} onChange={(event) => setExcerpt(event.target.value)} placeholder="Paste a paragraph if you want NyayaBot to identify candidate facts from it." className="mt-1.5 w-full resize-y rounded-xl border border-[#ccd8d1] bg-white px-3.5 py-2.5 text-sm leading-6 text-[#24322c] placeholder:text-[#9ba6a0] focus:border-[#5d8c73] focus:outline-none" /></label>

          <div className="flex gap-2.5 rounded-2xl bg-[#f3f7f4] p-3 text-[11px] leading-5 text-[#627168]"><ShieldCheck className="mt-0.5 size-4 shrink-0 text-[#2f755b]" /><span>If extraction is unavailable or uncertain, NyayaBot attaches the file without inventing facts. This MVP does not claim that an upload has been legally verified.</span></div>
          {error ? <p role="alert" className="rounded-xl bg-[#fff0ed] p-3 text-xs text-[#a2473a]">{error}</p> : null}
        </div>

        <footer className="flex items-center justify-between border-t border-[#e3e9e5] bg-[#fbfcfb] px-5 py-4"><button type="button" onClick={onClose} className="rounded-xl px-3 py-2 text-xs font-semibold text-[#66736d] hover:bg-[#eef2ef]">Cancel</button><button type="button" onClick={() => void processUpload()} disabled={!selectedFile || processing} className="flex items-center gap-2 rounded-xl bg-[#174e3b] px-4 py-2.5 text-xs font-bold text-white hover:bg-[#103c2d] disabled:bg-[#bdc9c2]">{processing ? <LoaderCircle className="size-4 animate-spin" /> : <Upload className="size-4" />}{processing ? 'Processing…' : 'Attach & inspect'}</button></footer>
      </div>
    </div>
  );
}
