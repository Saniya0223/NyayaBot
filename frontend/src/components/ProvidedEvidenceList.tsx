'use client';

import { useEffect, useMemo, useState } from 'react';
import { FileText, LoaderCircle } from 'lucide-react';
import { absoluteDocumentUrl, ChatTurnResponse, fetchEvidence, ProvidedDocument, retryEvidence, reviewEvidence } from '@/lib/api';
import EvidenceDetailModal from './EvidenceDetailModal';

interface Props {
  documents: ProvidedDocument[];
  compact?: boolean;
  onReviewed?: (response: ChatTurnResponse) => void;
}

const POLL_MS = 3000;
const inFlight = (status?: string) => status === 'UPLOADED' || status === 'PROCESSING';

function statusLine(document: ProvidedDocument): { text: string; tone: 'muted' | 'busy' | 'error' | 'ok' } {
  const status = document.processing_status;
  if (!status) return { text: '', tone: 'muted' };
  if (status === 'NOT_PROCESSED') return { text: 'Not processed yet', tone: 'muted' };
  if (inFlight(status)) return { text: 'Processing…', tone: 'busy' };
  if (status === 'FAILED') return { text: 'Processing failed', tone: 'error' };
  if (document.has_readable_text === false) return { text: 'Processed · No readable text extracted', tone: 'muted' };
  const methods = document.methods ?? [];
  const read = methods.includes('ocr') && methods.includes('text_extraction') ? 'Text extracted + OCR'
    : methods.includes('ocr') ? 'Text recognised (OCR)' : 'Text extracted';
  const pages = document.page_count ? ` · ${document.page_count} page${document.page_count === 1 ? '' : 's'}` : '';
  return { text: `Processed${pages} · ${read}`, tone: 'ok' };
}

export default function ProvidedEvidenceList({ documents, compact = false, onReviewed }: Props) {
  const [overrides, setOverrides] = useState<Record<string, ProvidedDocument>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [openDetail, setOpenDetail] = useState<ProvidedDocument | null>(null);

  const items = useMemo(() => documents.map((document) => ({ ...document, ...overrides[document.id] })), [documents, overrides]);
  const pendingKey = items.filter((item) => inFlight(item.processing_status)).map((item) => item.id).join(',');

  // Poll only while something is processing; the backend turns abandoned runs into
  // a retryable failure, so this never spins forever.
  useEffect(() => {
    if (!pendingKey) return;
    const ids = pendingKey.split(',');
    const timer = window.setInterval(() => {
      ids.forEach((id) => {
        fetchEvidence(id)
          .then((detail) => setOverrides((current) => ({ ...current, [id]: detail })))
          .catch(() => {});
      });
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [pendingKey]);

  async function retry(document: ProvidedDocument) {
    setBusy(document.id);
    setActionError(null);
    try {
      const summary = await retryEvidence(document.id);
      setOverrides((current) => ({ ...current, [document.id]: { ...summary, processing_status: 'UPLOADED' } }));
    } catch (caught) {
      setActionError(caught instanceof Error ? caught.message : 'Could not retry processing.');
    } finally {
      setBusy(null);
    }
  }

  async function review(document: ProvidedDocument) {
    if (!onReviewed) return;
    setBusy(document.id);
    setActionError(null);
    try {
      const response = await reviewEvidence(document.id);
      setOverrides((current) => ({ ...current, [document.id]: { ...document, review_status: 'OFFERED' } }));
      onReviewed(response);
    } catch (caught) {
      setActionError(caught instanceof Error ? caught.message : 'Could not open these details for review.');
    } finally {
      setBusy(null);
    }
  }

  return (
    <>
      <ul className={compact ? 'mt-1 space-y-2' : 'mt-2 space-y-2'}>
        {items.map((document) => {
          const line = statusLine(document);
          const reviewable = (document.candidate_count ?? 0) + (document.conflict_count ?? 0);
          const hasDetail = document.processing_status === 'COMPLETED' || document.processing_status === 'FAILED';
          const analysisFailed = document.processing_status === 'COMPLETED' && document.analysis_status === 'FAILED';
          return (
            <li key={document.id} className={compact ? 'text-[11px] text-[#526159]' : 'rounded-lg bg-[#f7faf8] px-3 py-2 text-xs text-[#526159]'}>
              <div className="flex items-center gap-2">
                <FileText className={`${compact ? 'size-3.5' : 'size-4'} shrink-0 text-[#2f755b]`} />
                <span className="min-w-0 flex-1 truncate font-semibold text-[#34443c]" title={document.name}>{document.name}</span>
                <span className="text-[9px] uppercase text-[#96a19b]">{document.name.split('.').pop()}</span>
              </div>
              {line.text ? (
                <p className={`mt-0.5 flex items-center gap-1 pl-5 text-[10px] ${line.tone === 'error' ? 'text-[#a2473a]' : line.tone === 'busy' ? 'text-[#2f755b]' : 'text-[#87938d]'}`}>
                  {line.tone === 'busy' ? <LoaderCircle className="size-3 animate-spin" /> : null}{line.text}
                </p>
              ) : null}
              {document.processing_status === 'FAILED' && document.error_message ? <p className="pl-5 text-[10px] text-[#a2473a]">{document.error_message}</p> : null}
              {analysisFailed ? <p className="pl-5 text-[10px] text-[#a2473a]">AI analysis failed; the extracted text is kept.</p> : null}
              {document.processing_status === 'COMPLETED' && document.failed_pages?.length ? <p className="pl-5 text-[10px] text-[#a2473a]">Could not read page{document.failed_pages.length === 1 ? '' : 's'} {document.failed_pages.join(', ')}.</p> : null}
              <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 pl-5 text-[10px] font-semibold">
                <a href={absoluteDocumentUrl(document.download_url)} className="text-[#2f755b] hover:underline">View document</a>
                {hasDetail ? <button type="button" onClick={() => setOpenDetail(document)} className="text-[#2f755b] hover:underline">View extracted information</button> : null}
                {document.retryable ? (
                  <button type="button" disabled={busy === document.id} onClick={() => void retry(document)} className="text-[#a2473a] hover:underline disabled:opacity-50">
                    {document.processing_status === 'NOT_PROCESSED' ? 'Process' : analysisFailed || document.analysis_status === 'LIMITED' ? 'Retry analysis' : 'Retry'}
                  </button>
                ) : null}
                {onReviewed && reviewable && document.review_status === 'PENDING' ? (
                  <button type="button" disabled={busy === document.id} onClick={() => void review(document)} className="text-[#174e3b] hover:underline disabled:opacity-50">Review {reviewable} detail{reviewable === 1 ? '' : 's'} in chat</button>
                ) : null}
              </div>
            </li>
          );
        })}
      </ul>
      {actionError ? <p role="alert" className="mt-1 text-[10px] text-[#a2473a]">{actionError}</p> : null}
      {openDetail ? <EvidenceDetailModal evidenceId={openDetail.id} fileName={openDetail.name} onClose={() => setOpenDetail(null)} /> : null}
    </>
  );
}
