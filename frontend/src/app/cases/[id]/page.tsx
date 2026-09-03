'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { ArrowLeft, CalendarDays, Check, FileText, MessageCircle, Scale } from 'lucide-react';
import CaseWorkspacePanel from '@/components/CaseWorkspacePanel';
import FactualConfirmModal from '@/components/FactualConfirmModal';
import { absoluteDocumentUrl, fetchChatCase, StructuredCaseProfile } from '@/lib/api';

const tabs = [
  ['overview', 'Overview', Scale],
  ['timeline', 'Timeline', CalendarDays],
  ['documents', 'Documents', FileText],
] as const;

export default function CaseDetailPage() {
  const params = useParams<{ id: string }>();
  const caseId = params.id;
  const [profile, setProfile] = useState<StructuredCaseProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<(typeof tabs)[number][0]>('overview');
  const [documentModal, setDocumentModal] = useState({ open: false, type: '', label: '' });

  useEffect(() => {
    let active = true;
    fetchChatCase(caseId)
      .then((session) => { if (active) setProfile(session.case_profile); })
      .catch((caught) => {
        if (active) setError(caught instanceof Error ? caught.message : 'Case could not be loaded.');
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [caseId]);

  if (loading) {
    return <div className="grid min-h-[60vh] place-items-center text-sm text-[#718078]">Loading case workspace…</div>;
  }

  if (!profile || error) {
    return (
      <div className="mx-auto max-w-xl px-4 py-20 text-center">
        <h1 className="text-xl font-bold text-[#26362f]">Case not found</h1>
        <p className="mt-2 text-sm text-[#718078]">{error || 'This case is unavailable.'}</p>
        <Link href="/cases" className="mt-5 inline-flex rounded-xl bg-[#174e3b] px-4 py-2.5 text-xs font-bold text-white">Back to cases</Link>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-6xl px-4 py-7 sm:px-6 lg:px-8">
      <Link href="/cases" className="inline-flex items-center gap-2 text-xs font-semibold text-[#617169] hover:text-[#174e3b]"><ArrowLeft className="size-4" />Back to cases</Link>
      <header className="mt-5 flex flex-col justify-between gap-4 sm:flex-row sm:items-end">
        <div>
          <span className="font-mono text-[10px] font-bold text-[#76837c]">{profile.case_number}</span>
          <h1 className="mt-2 text-3xl font-bold tracking-[-0.04em] text-[#17231f]">{profile.title}</h1>
          <p className="mt-2 text-sm text-[#6c7973]">{profile.current_stage_label}</p>
        </div>
        <Link href={`/?case=${encodeURIComponent(profile.case_id)}`} className="flex w-fit items-center gap-2 rounded-xl bg-[#174e3b] px-4 py-2.5 text-xs font-bold text-white"><MessageCircle className="size-4" />Continue conversation</Link>
      </header>

      <div className="mt-7 flex gap-1 rounded-2xl border border-[#dce4df] bg-white p-1">
        {tabs.map(([value, label, Icon]) => (
          <button key={value} type="button" onClick={() => setTab(value)} aria-pressed={tab === value} className={`flex flex-1 items-center justify-center gap-2 rounded-xl px-3 py-2.5 text-xs font-bold ${tab === value ? 'bg-[#e8f2ec] text-[#174e3b]' : 'text-[#6a7771] hover:bg-[#f5f7f5]'}`}>
            <Icon className="size-4" />{label}
          </button>
        ))}
      </div>

      <div className="mt-5">
        {tab === 'overview' ? (
          <div className="mx-auto max-w-xl">
            <CaseWorkspacePanel profile={profile} onTriggerDocumentModal={(type, label) => setDocumentModal({ open: true, type, label })} />
          </div>
        ) : null}

        {tab === 'timeline' ? (
          <div className="rounded-3xl border border-[#dde5e0] bg-white p-5 paper-shadow">
            <h2 className="text-sm font-bold text-[#2a3a33]">Chronological case activity</h2>
            <ol className="mt-5 space-y-4">
              {(profile.timeline || []).map((event) => (
                <li key={event.id} className="flex gap-3">
                  <span className="mt-0.5 grid size-6 shrink-0 place-items-center rounded-full bg-[#e8f2ec] text-[#2f755b]"><Check className="size-3.5" /></span>
                  <div><p className="text-xs font-bold text-[#34443c]">{event.label}</p><p className="mt-1 text-[10px] text-[#839088]">{new Date(event.date).toLocaleString('en-IN', { dateStyle: 'medium', timeStyle: 'short' })} · {event.source}</p></div>
                </li>
              ))}
            </ol>
          </div>
        ) : null}

        {tab === 'documents' ? (
          <div className="space-y-3">
            {(profile.documents || []).length ? (profile.documents || []).map((document) => (
              <article key={document.id} className="flex flex-col justify-between gap-3 rounded-2xl border border-[#dde5e0] bg-white p-4 sm:flex-row sm:items-center">
                <div><span className="text-[9px] font-bold uppercase tracking-wide text-[#718078]">{document.status}</span><h2 className="mt-1 text-sm font-bold text-[#2a3a33]">{document.title}</h2><p className="mt-1 text-[10px] text-[#839088]">{new Date(document.created_at).toLocaleDateString('en-IN')}</p></div>
                <div className="flex gap-2">
                  {document.docx_download_url ? <a href={absoluteDocumentUrl(document.docx_download_url)} className="rounded-lg border border-[#ccd8d1] px-3 py-2 text-[11px] font-bold text-[#174e3b]">DOCX</a> : null}
                  {document.pdf_download_url ? <a href={absoluteDocumentUrl(document.pdf_download_url)} className="rounded-lg bg-[#174e3b] px-3 py-2 text-[11px] font-bold text-white">PDF</a> : null}
                </div>
              </article>
            )) : <div className="rounded-3xl border border-dashed border-[#cbd8d0] bg-white p-10 text-center text-sm text-[#718078]">No document has been generated for this case yet.</div>}
          </div>
        ) : null}
      </div>

      {documentModal.open ? (
        <FactualConfirmModal profile={profile} docType={documentModal.type} docLabel={documentModal.label} onClose={() => setDocumentModal((current) => ({ ...current, open: false }))} />
      ) : null}
    </div>
  );
}
