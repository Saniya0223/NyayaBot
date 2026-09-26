'use client';

import { useEffect, useState } from 'react';
import { AlertTriangle, FileSearch, LoaderCircle, X } from 'lucide-react';
import { absoluteDocumentUrl, EvidenceDetail, EvidenceSourceRef, fetchEvidence } from '@/lib/api';

interface Props {
  evidenceId: string;
  fileName: string;
  onClose: () => void;
}

function sourceLabel(source: EvidenceSourceRef | null): string {
  if (!source) return 'Source location not identified';
  const parts = [source.page_number ? `Page ${source.page_number}` : source.method === 'user_excerpt' ? 'Your pasted excerpt' : 'Document'];
  if (source.method === 'ocr') parts.push('OCR');
  return parts.join(' · ');
}

function fieldLabel(field: string): string {
  return field.replace(/_/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function methodLabel(method: string): string {
  return method === 'ocr' ? 'OCR' : method === 'text_extraction' ? 'Direct text' : 'Not read';
}

export default function EvidenceDetailModal({ evidenceId, fileName, onClose }: Props) {
  const [detail, setDetail] = useState<EvidenceDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    fetchEvidence(evidenceId)
      .then((value) => { if (active) setDetail(value); })
      .catch((caught) => { if (active) setError(caught instanceof Error ? caught.message : 'Could not load this file.'); });
    return () => { active = false; };
  }, [evidenceId]);

  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose(); };
    window.addEventListener('keydown', closeOnEscape);
    return () => window.removeEventListener('keydown', closeOnEscape);
  }, [onClose]);

  const analysis = detail?.analysis;
  const candidates = analysis ? Object.entries(analysis.candidate_facts ?? {}) : [];

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-[#10241d]/50 p-3 backdrop-blur-sm" role="dialog" aria-modal="true" aria-labelledby="evidence-detail-title">
      <div className="flex max-h-[90vh] w-full max-w-2xl flex-col overflow-hidden rounded-[24px] border border-[#dbe4de] bg-white shadow-2xl">
        <header className="flex items-center justify-between gap-3 border-b border-[#e3e9e5] px-5 py-4">
          <div className="flex min-w-0 items-center gap-3">
            <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-[#e8f2ec] text-[#174e3b]"><FileSearch className="size-5" /></span>
            <div className="min-w-0">
              <h2 id="evidence-detail-title" className="truncate text-sm font-bold text-[#21312a]">{fileName}</h2>
              <p className="mt-0.5 text-[11px] text-[#74817a]">Extracted information · Provided by you</p>
            </div>
          </div>
          <button type="button" onClick={onClose} aria-label="Close extracted information" className="grid size-9 place-items-center rounded-xl text-[#6f7c75] hover:bg-[#f1f5f2]"><X className="size-5" /></button>
        </header>

        <div className="soft-scrollbar space-y-4 overflow-y-auto p-5 text-[12px] leading-5 text-[#405048]">
          {error ? <p role="alert" className="rounded-xl bg-[#fff0ed] p-3 text-xs text-[#a2473a]">{error}</p> : null}
          {!detail && !error ? <p className="flex items-center gap-2 text-[#74817a]"><LoaderCircle className="size-4 animate-spin" />Loading…</p> : null}

          {detail ? (
            <>
              <p className="rounded-xl bg-[#f3f7f4] p-3 text-[11px] text-[#627168]">
                NyayaBot identified the information below in the uploaded file. It has not been verified, it does not show that the document is authentic or admissible, and nothing here is added to your case until you confirm it.
              </p>

              <section aria-label="Extraction">
                <h3 className="text-xs font-bold text-[#304039]">Extraction</h3>
                <p className="mt-1">
                  Status: <strong>{detail.processing_status === 'COMPLETED' ? 'Processed' : detail.processing_status === 'FAILED' ? 'Processing failed' : 'Processing…'}</strong>
                  {detail.page_count ? ` · ${detail.page_count} page${detail.page_count === 1 ? '' : 's'}` : ''}
                  {detail.direct_pages ? ` · ${detail.direct_pages} read directly` : ''}
                  {detail.ocr_pages ? ` · ${detail.ocr_pages} read with OCR` : ''}
                </p>
                {detail.error_message ? <p className="mt-1 text-[#a2473a]">{detail.error_message}</p> : null}
                {detail.has_readable_text === false && detail.processing_status === 'COMPLETED' ? <p className="mt-1 text-[#74817a]">No readable text was extracted from this file.</p> : null}
                {detail.failed_pages?.length ? <p className="mt-1 text-[#a2473a]">Could not read page{detail.failed_pages.length === 1 ? '' : 's'} {detail.failed_pages.join(', ')}.</p> : null}
                <a href={absoluteDocumentUrl(detail.download_url)} className="mt-1 inline-block font-semibold text-[#2f755b] hover:underline">View original document</a>
              </section>

              {analysis ? (
                <section aria-label="Findings" className="space-y-3">
                  <h3 className="text-xs font-bold text-[#304039]">What the document appears to contain</h3>
                  {analysis.mode === 'rule_based' ? <p className="text-[11px] text-[#8a6d1f]">AI analysis was unavailable, so only basic rule-based extraction was used.</p> : null}
                  {analysis.document_type ? <p>Document type (AI reading): {analysis.document_type}</p> : null}
                  {analysis.summary ? <p><span className="font-semibold">AI summary of the text:</span> {analysis.summary}</p> : null}

                  {analysis.conflicts?.length ? (
                    <div className="rounded-xl border border-[#efc2bb] bg-[#fff4f2] p-3">
                      <p className="flex items-center gap-1.5 font-semibold text-[#853c32]"><AlertTriangle className="size-3.5" />Differs from your case — nothing was changed</p>
                      <ul className="mt-1 space-y-1">{analysis.conflicts.map((item) => (
                        <li key={item.field}>{fieldLabel(item.field)}: your case has <strong>{String(item.current_value)}</strong>; the document appears to show <strong>{String(item.evidence_value)}</strong> <span className="text-[#8b6b64]">({sourceLabel(item.source)})</span></li>
                      ))}</ul>
                    </div>
                  ) : null}

                  {candidates.length ? (
                    <div>
                      <p className="font-semibold">Details awaiting your confirmation</p>
                      <ul className="mt-1 space-y-1">{candidates.map(([field, item]) => (
                        <li key={field}>{fieldLabel(field)}: <strong>{String(item.value)}</strong> <span className="text-[#87938d]">({sourceLabel(item.source)})</span></li>
                      ))}</ul>
                    </div>
                  ) : null}

                  {analysis.findings?.length ? (
                    <div>
                      <p className="font-semibold">Findings</p>
                      <ul className="mt-1 space-y-1.5">{analysis.findings.map((finding) => (
                        <li key={finding.id} className="rounded-lg bg-[#f7faf8] px-3 py-2">
                          <span className="text-[10px] font-bold uppercase tracking-wider text-[#87938d]">{finding.type.replace(/_/g, ' ')}</span>
                          <p className="font-semibold text-[#2c3b34]">{finding.value}</p>
                          {finding.statement ? <p className="text-[11px] text-[#617168]">{finding.statement}</p> : null}
                          <p className="text-[10px] text-[#87938d]">{sourceLabel(finding.source)}{finding.clarity === 'unclear' ? ' · unclear' : ''}{finding.ocr_derived ? ' · OCR text may contain recognition errors' : ''}</p>
                        </li>
                      ))}</ul>
                    </div>
                  ) : null}

                  {analysis.notes?.length ? <ul className="space-y-1 text-[11px] text-[#74817a]">{analysis.notes.map((note, index) => <li key={index}>• {note}</li>)}</ul> : null}
                </section>
              ) : null}

              {detail.pages?.some((page) => page.text) ? (
                <section aria-label="Extracted text">
                  <h3 className="text-xs font-bold text-[#304039]">Extracted text</h3>
                  <div className="mt-1 space-y-1.5">{detail.pages.map((page, index) => (
                    <details key={`${page.page_number ?? 'doc'}-${index}`} className="rounded-lg bg-[#f7faf8] px-3 py-2">
                      <summary className="cursor-pointer text-[11px] font-semibold text-[#34443c]">
                        {page.page_number ? `Page ${page.page_number}` : 'Document text'} · {methodLabel(page.method)}{page.status !== 'ok' ? ` · ${page.status.replace(/_/g, ' ')}` : ''}
                      </summary>
                      {page.method === 'ocr' ? <p className="mt-1 text-[10px] text-[#8a6d1f]">Recognised with OCR; may contain errors.</p> : null}
                      <pre className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap break-words font-sans text-[11px] text-[#526159]">{page.text || 'No readable text.'}</pre>
                    </details>
                  ))}</div>
                </section>
              ) : null}
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}
