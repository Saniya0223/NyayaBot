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
    <div className="fixed inset-0 z-50 grid place-items-center bg-[#171717]/50 p-3 backdrop-blur-sm" role="dialog" aria-modal="true" aria-labelledby="upload-title">
      <div className="w-full max-w-xl overflow-hidden rounded-[24px] border border-[#e9e3d7] bg-white shadow-2xl">
        <header className="flex items-center justify-between border-b border-[#eee8dd] px-5 py-4">
          <div className="flex items-center gap-3"><span className="grid size-10 place-items-center rounded-xl bg-[#fff3c6] text-[#181818]"><FileSearch className="size-5" /></span><div><h2 id="upload-title" className="text-sm font-bold text-[#222222]">Add evidence to this case</h2><p className="mt-0.5 text-[11px] text-[#8a837a]">Upload first; extracted facts always require confirmation.</p></div></div>
          <button type="button" onClick={onClose} aria-label="Close upload dialog" className="grid size-9 place-items-center rounded-xl text-[#817b72] hover:bg-[#fcf9ef]"><X className="size-5" /></button>
        </header>

        <div className="space-y-5 p-5 sm:p-6">
          <label className="block text-[11px] font-bold text-[#48443d]">Document type<select value={documentType} onChange={(event) => setDocumentType(event.target.value)} className="mt-1.5 w-full rounded-xl border border-[#ded7c9] bg-white px-3.5 py-2.5 text-sm text-[#252525] focus:border-[#bb9134] focus:outline-none">{types.map((type) => <option key={type.value} value={type.value}>{type.label}</option>)}</select></label>

          <label className={`flex min-h-28 flex-col items-center justify-center rounded-2xl border-2 border-dashed px-5 py-5 text-center transition ${selectedFile ? 'border-[#d8b54d] bg-[#fff8e5]' : 'border-[#e8e0d3] bg-[#fefcf8] hover:border-[#dec88e]'}`}>
            <input type="file" accept=".pdf,.doc,.docx,.png,.jpg,.jpeg,.txt,.eml" onChange={chooseFile} className="sr-only" />
            {selectedFile ? <Paperclip className="size-5 text-[#9d6b00]" /> : <Upload className="size-5 text-[#7c8a83]" />}
            <span className="mt-2 text-xs font-bold text-[#35453e]">{selectedFile?.name || 'Choose a document or screenshot'}</span>
            <span className="mt-1 text-[10px] text-[#999187]">PDF, DOCX, JPG, PNG, or TXT · up to 10 MB</span>
          </label>

          <label className="block text-[11px] font-bold text-[#48443d]">Relevant text excerpt <span className="font-medium text-[#9e968c]">(optional MVP fallback)</span><textarea rows={4} value={excerpt} onChange={(event) => setExcerpt(event.target.value)} placeholder="Paste a paragraph if you want NyayaBot to identify candidate facts from it." className="mt-1.5 w-full resize-y rounded-xl border border-[#ded7c9] bg-white px-3.5 py-2.5 text-sm leading-6 text-[#252525] placeholder:text-[#9ba6a0] focus:border-[#bb9134] focus:outline-none" /></label>

          <div className="flex gap-2.5 rounded-2xl bg-[#fcfaf4] p-3 text-[11px] leading-5 text-[#777169]"><ShieldCheck className="mt-0.5 size-4 shrink-0 text-[#9d6b00]" /><span>Your original file is kept as uploaded. NyayaBot reads its text (scanned pages and images with OCR) and shows what it found; nothing is added to your case until you confirm it. Reading a file does not verify it is authentic.</span></div>
          {error ? <p role="alert" className="rounded-xl bg-[#fff0ed] p-3 text-xs text-[#a2473a]">{error}</p> : null}
        </div>

        <footer className="flex items-center justify-between border-t border-[#eee8dd] bg-[#fefcf8] px-5 py-4"><button type="button" onClick={onClose} className="rounded-xl px-3 py-2 text-xs font-semibold text-[#716c64] hover:bg-[#eef2ef]">Cancel</button><button type="button" onClick={() => void processUpload()} disabled={!selectedFile || processing} className="flex items-center gap-2 rounded-xl bg-[#181818] px-4 py-2.5 text-xs font-bold text-white hover:bg-[#080808] disabled:bg-[#ded7c9]">{processing ? <LoaderCircle className="size-4 animate-spin" /> : <Upload className="size-4" />}{processing ? 'Uploading…' : 'Attach & inspect'}</button></footer>
      </div>
    </div>
  );
}
