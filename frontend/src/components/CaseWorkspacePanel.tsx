'use client';

import { useEffect, useState } from 'react';
import { ArrowRight, BookOpen, ChevronDown, FileText, FolderOpen, ListChecks, Scale, Sparkles, X } from 'lucide-react';
import { absoluteDocumentUrl, ChatTurnResponse, generateCaseSummary, StructuredCaseProfile } from '@/lib/api';
import { canGenerateSummary, caseTypeLabel, journeyEvents, keyFacts, suggestedActions } from '@/lib/caseWorkspace';
import { helpLevelLabels } from '@/lib/professionalHelp';
import FillOnPortal from './FillOnPortal';
import FormattedMessage from './FormattedMessage';
import ProvidedEvidenceList from './ProvidedEvidenceList';

interface Props {
  profile: StructuredCaseProfile | null;
  onTriggerDocumentModal: (docType: string, docLabel: string) => void;
  viewportScroll?: boolean;
  /** Evidence details handed to the chat for confirmation (the existing confirm flow). */
  onEvidenceReviewed?: (response: ChatTurnResponse) => void;
}

function dateLabel(value?: string): string | null {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date.toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' });
}

export default function CaseWorkspacePanel(props: Props) {
  const profile = props.profile;
  return <CaseWorkspaceContent key={`${profile?.case_id ?? 'new'}-${profile?.updated_at ?? ''}-${profile?.documents?.length ?? 0}-${profile?.provided_documents?.length ?? 0}`} {...props} />;
}

function CaseWorkspaceContent({ profile, onTriggerDocumentModal, onEvidenceReviewed, viewportScroll = false }: Props) {
  const [documentsOpen, setDocumentsOpen] = useState(false);
  const [summaryOpen, setSummaryOpen] = useState(false);
  const [summaryText, setSummaryText] = useState<string | null>(null);
  const [summaryLoading, setSummaryLoading] = useState(false);
  const [summaryError, setSummaryError] = useState<string | null>(null);

  useEffect(() => {
    if (!documentsOpen && !summaryOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setDocumentsOpen(false);
        setSummaryOpen(false);
      }
    };
    window.addEventListener('keydown', closeOnEscape);
    return () => window.removeEventListener('keydown', closeOnEscape);
  }, [documentsOpen, summaryOpen]);

  async function openSummary() {
    if (!profile || !canGenerateSummary(profile)) return;
    setSummaryOpen(true);
    if (summaryText || summaryLoading) return;
    setSummaryError(null);
    setSummaryLoading(true);
    try {
      setSummaryText((await generateCaseSummary(profile.case_id)).text);
    } catch (error) {
      setSummaryError(error instanceof Error ? error.message : 'Could not generate the summary.');
    } finally {
      setSummaryLoading(false);
    }
  }

  const facts = profile ? keyFacts(profile) : [];
  const laws = profile?.legal_sources ?? [];
  const provided = profile?.provided_documents ?? [];
  const generated = profile?.documents ?? [];
  const documentCount = provided.length + generated.length;
  const journey = profile ? journeyEvents(profile) : [];
  const suggestions = profile ? suggestedActions(profile) : [];
  const safety = profile?.safety_status;
  const showHelp = profile?.professional_help && !(safety?.is_safety_case && (safety.immediate_danger !== false || !profile?.key_facts?.safety_triage_complete));
  const limit = 3;

  // The backend names a case as soon as it knows enough, so show that name
  // rather than gating on category alone - a case can be meaningfully titled
  // before its workflow stage means anything to the user.
  const PLACEHOLDER_TITLES = ['legal information request', 'new case', 'untitled case'];
  const hasRealTitle = Boolean(
    profile?.title && !PLACEHOLDER_TITLES.includes(profile.title.trim().toLowerCase())
  );
  const caseHeading = hasRealTitle ? profile!.title : 'Your case is taking shape';
  const caseSubheading = hasRealTitle && profile?.current_stage_label
    ? profile.current_stage_label
    : 'Describe your situation to get started.';

  return (
    <aside className={`soft-scrollbar h-full min-h-[620px] overflow-y-auto rounded-[26px] border border-[#ebe6dc] bg-white p-4 paper-shadow sm:p-5 ${viewportScroll ? 'md:h-auto md:max-h-[calc(100dvh-102px)] md:min-h-0 md:overscroll-contain' : ''}`} aria-label="Live case workspace">
      <div className="mb-4 flex items-center justify-between gap-3">
        <span className="text-[10px] font-bold uppercase tracking-[0.14em] text-[#8a837a]">Live case workspace</span>
        {profile?.case_number ? <span className="font-mono text-[9px] text-[#9e968c]">{profile.case_number}</span> : null}
      </div>

      <div className="rounded-2xl bg-[#1b1b1b] p-5 text-white shadow-[0_12px_24px_rgba(44,32,9,0.13)]">
        <span className="inline-flex items-center gap-2 rounded-full bg-[#f3d776]/15 px-2.5 py-1 text-[10px] font-semibold text-[#fff2c7]"><FolderOpen className="size-3.5 text-[#ffe17a]" />{profile ? caseTypeLabel(profile) : 'Case type not identified yet'}</span>
        <h2 className="mt-3 text-lg font-bold leading-6">{caseHeading}</h2>
        <p className="mt-1 text-[11px] text-[#eee4cc]">{caseSubheading}</p>
        {profile?.updated_at ? <p className="mt-4 text-[10px] text-[#d4cbb6]">Last updated {dateLabel(profile.updated_at)}</p> : null}
      </div>

      {profile?.safety_notice ? <p className="mt-3 rounded-xl border border-[#efc2bb] bg-[#fff4f2] p-3 text-[11px] leading-5 text-[#853c32]">{profile.safety_notice}</p> : null}

      <details open className="group mt-4 rounded-2xl border border-[#ebe5da] bg-white">
        <summary className="flex cursor-pointer list-none items-center justify-between px-4 py-3 marker:hidden">
          <span className="flex items-center gap-2 text-xs font-bold text-[#303030]"><ListChecks className="size-4 text-[#9d6b00]" />Key facts{facts.length ? <span className="font-medium text-[#999187]">{facts.length}</span> : null}</span>
          <ChevronDown className="size-4 text-[#928a80] transition group-open:rotate-180" aria-hidden="true" />
        </summary>
        <div className="border-t border-[#edf1ee] px-4 py-3">
          {facts.length ? <ul className="space-y-2">{facts.map((fact) => <li key={fact.key} className="flex gap-2 text-[11px] leading-5 text-[#5e5a53]"><span className="text-[#9d6b00]">•</span><span>{fact.text}</span></li>)}</ul>
            : <p className="text-[11px] leading-5 text-[#999187]">Describe your situation to build the case summary.</p>}
        </div>
      </details>

      <section className="mt-3 rounded-2xl border border-[#ebe5da] p-4" aria-label="Laws and rights">
        <h3 className="flex items-center gap-2 text-xs font-bold text-[#303030]"><Scale className="size-4 text-[#9d6b00]" />Laws & rights{laws.length ? <span className="font-medium text-[#999187]">{laws.length}</span> : null}</h3>
        {laws.length ? <div className="mt-2 space-y-1.5">{laws.map((law, index) => (
          <details key={`${law.act}-${law.section}-${index}`} className="group rounded-xl bg-[#fdfbf6] px-3 py-2">
            <summary className="flex cursor-pointer list-none items-center justify-between gap-2 text-[11px] font-semibold text-[#383530] marker:hidden"><span>{law.act} — {law.section}</span><ChevronDown className="size-3.5 shrink-0 text-[#928a80] group-open:rotate-180" /></summary>
            <div className="mt-2 border-t border-[#e3ebe6] pt-2 text-[10px] leading-5 text-[#6a655e]">
              <p>{law.title}{law.description ? ` — ${law.description}` : ''}</p>
              {law.document_type?.toLowerCase().includes('model law') ? <p className="mt-1 font-semibold">Model guidance; State adoption must be checked.</p> : null}
              {law.source_url ? <a href={law.source_url} target="_blank" rel="noreferrer" className="mt-1 inline-block font-semibold text-[#2d6d53] hover:underline">View source{law.source_authority ? ` · ${law.source_authority}` : ''}</a> : null}
            </div>
          </details>
        ))}</div> : <p className="mt-2 text-[11px] leading-5 text-[#999187]">Relevant laws will appear once identified for this case.</p>}
      </section>

      <section className="mt-3 rounded-2xl border border-[#ebe5da] p-4" aria-label="Case documents">
        <div className="flex items-center justify-between gap-2"><h3 className="flex items-center gap-2 text-xs font-bold text-[#303030]"><FolderOpen className="size-4 text-[#9d6b00]" />Documents <span className="font-medium text-[#999187]">{documentCount}</span></h3>
          {provided.length > 3 || generated.length > 3 ? <button type="button" onClick={() => setDocumentsOpen(true)} className="text-[10px] font-semibold text-[#9d6b00] hover:underline">View all</button> : null}
        </div>
        {documentCount === 0 ? <p className="mt-2 text-[11px] text-[#999187]">No documents yet.</p> : <div className="mt-2 space-y-3">
          {provided.length ? <div><p className="text-[9px] font-bold uppercase tracking-wider text-[#999187]">Provided by you</p><ProvidedEvidenceList documents={provided.slice(0, limit)} compact onReviewed={onEvidenceReviewed} /></div> : null}
          {generated.length ? <div><p className="text-[9px] font-bold uppercase tracking-wider text-[#999187]">Generated by NyayaBot</p><ul className="mt-1 space-y-1.5">{generated.slice(0, limit).map((document) => <li key={document.id} className="flex items-center gap-2 text-[11px] text-[#5e5a53]"><FileText className="size-3.5 shrink-0 text-[#9d6b00]" /><span className="min-w-0 flex-1 truncate" title={document.title}>{document.title}</span>{document.pdf_download_url ? <a href={absoluteDocumentUrl(document.pdf_download_url)} className="font-semibold text-[#9d6b00] hover:underline">PDF</a> : null}{document.docx_download_url ? <a href={absoluteDocumentUrl(document.docx_download_url)} className="font-semibold text-[#9d6b00] hover:underline">DOCX</a> : null}</li>)}</ul></div> : null}
        </div>}
      </section>

      <section className="mt-3 rounded-2xl border border-[#ebe5da] p-4" aria-label="Legal journey">
        <h3 className="flex items-center gap-2 text-xs font-bold text-[#303030]"><BookOpen className="size-4 text-[#9d6b00]" />Legal journey{journey.length ? <span className="font-medium text-[#999187]">{journey.length}</span> : null}</h3>
        {journey.length ? <ol className="mt-3 space-y-2">{journey.map((event) => (
          <li key={event.id} className="border-l-2 border-[#d8b54d] pl-3">
            <details>
              <summary className="cursor-pointer list-none text-[11px] font-semibold text-[#383530] marker:hidden">{event.label}</summary>
              <div className="mt-1 text-[10px] leading-4 text-[#999187]">
                {event.origin === 'reported' ? 'Reported in chat' : event.origin === 'generated' ? 'Generated by NyayaBot' : 'Recorded in case'}
                {dateLabel(event.recordedAt) ? ` · ${event.origin === 'generated' ? 'Generated' : 'Recorded'} ${dateLabel(event.recordedAt)}` : ''}
                {event.downloadUrl ? <a href={absoluteDocumentUrl(event.downloadUrl)} className="ml-2 font-semibold text-[#9d6b00] hover:underline">Open document</a> : null}
              </div>
            </details>
          </li>
        ))}</ol>
          : <p className="mt-2 text-[11px] text-[#999187]">Legal actions will appear here as they happen.</p>}
      </section>

      {suggestions.length ? <section className="mt-3 rounded-2xl border border-[#ead5a0] bg-[#fff8e2] p-4" aria-label="Suggested actions"><h3 className="flex items-center gap-2 text-xs font-bold text-[#303030]"><ArrowRight className="size-4 text-[#9d6b00]" />Suggested actions</h3><ul className="mt-2 space-y-2">{suggestions.map((action) => <li key={action.key} className="text-[11px] leading-5 text-[#5e5a53]">{action.docType ? <button type="button" onClick={() => onTriggerDocumentModal(action.docType!, action.label)} className="flex items-center gap-2 font-semibold text-[#181818] hover:underline"><ArrowRight className="size-3.5" />{action.label}</button> : <span className="flex items-start gap-2"><ArrowRight className="mt-1 size-3.5 shrink-0 text-[#9d6b00]" />{action.label}</span>}</li>)}</ul></section> : null}

      {showHelp ? <p className="mt-3 rounded-xl bg-[#fdfbf6] px-3 py-2 text-[10px] leading-4 text-[#6a655e]">Professional help: {helpLevelLabels[profile!.professional_help!.level]}. Based on currently known facts; this can change.</p> : null}

      <section className="mt-4 border-t border-[#eee8dd] pt-4" aria-label="AI case summary"><button type="button" onClick={openSummary} disabled={!profile || !canGenerateSummary(profile) || summaryLoading} className="flex w-full items-center justify-center gap-2 rounded-xl bg-[#181818] px-3 py-2.5 text-xs font-bold text-white transition hover:bg-[#080808] disabled:cursor-not-allowed disabled:bg-[#dcd4c4]"><Sparkles className="size-4" />{summaryText ? 'View AI Summary' : 'Generate AI Summary'}</button>{!profile || !canGenerateSummary(profile) ? <p className="mt-1.5 text-center text-[10px] text-[#999187]">Available after some case facts are recorded.</p> : null}</section>

      {profile?.case_id ? <FillOnPortal profile={profile} /> : null}

      {documentsOpen ? <div className="fixed inset-0 z-50 flex items-center justify-center bg-[#171717]/50 p-4" role="presentation" onClick={() => setDocumentsOpen(false)}>
        <div role="dialog" aria-modal="true" aria-labelledby="case-documents-title" onClick={(event) => event.stopPropagation()} className="soft-scrollbar max-h-[85vh] w-full max-w-lg overflow-y-auto rounded-3xl bg-white p-5 shadow-2xl sm:p-6">
          <div className="flex items-center justify-between gap-3"><h2 id="case-documents-title" className="text-lg font-bold text-[#292929]">Case documents ({documentCount})</h2><button type="button" onClick={() => setDocumentsOpen(false)} aria-label="Close documents" className="rounded-lg p-1 text-[#817b72] hover:bg-[#fcf9ef]"><X className="size-5" /></button></div>
          {provided.length ? <section className="mt-4"><h3 className="text-[10px] font-bold uppercase tracking-wider text-[#999187]">Provided by you</h3><ProvidedEvidenceList documents={provided} onReviewed={onEvidenceReviewed} /></section> : null}
          {generated.length ? <section className="mt-4"><h3 className="text-[10px] font-bold uppercase tracking-wider text-[#999187]">Generated by NyayaBot</h3><ul className="mt-2 space-y-2">{generated.map((document) => <li key={document.id} className="flex items-center gap-2 rounded-lg bg-[#fdfbf6] px-3 py-2 text-xs text-[#5e5a53]"><FileText className="size-4 shrink-0 text-[#9d6b00]" /><span className="min-w-0 flex-1 break-words">{document.title}</span>{document.pdf_download_url ? <a href={absoluteDocumentUrl(document.pdf_download_url)} className="font-bold text-[#9d6b00] hover:underline">PDF</a> : null}{document.docx_download_url ? <a href={absoluteDocumentUrl(document.docx_download_url)} className="font-bold text-[#9d6b00] hover:underline">DOCX</a> : null}</li>)}</ul></section> : null}
        </div>
      </div> : null}

      {summaryOpen ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-[#171717]/50 p-4" role="presentation" onClick={() => setSummaryOpen(false)}>
          <div role="dialog" aria-modal="true" aria-labelledby="case-summary-title" onClick={(event) => event.stopPropagation()} className="soft-scrollbar max-h-[85vh] w-full max-w-2xl overflow-y-auto rounded-3xl bg-white p-5 shadow-2xl sm:p-7">
            <div className="flex items-start justify-between gap-4 border-b border-[#eee8dd] pb-4">
              <div>
                <p className="text-[10px] font-bold uppercase tracking-[0.14em] text-[#9d6b00]">On-demand case brief</p>
                <h2 id="case-summary-title" className="mt-1 text-xl font-bold text-[#171717]">AI Case Summary</h2>
                <p className="mt-1 text-xs text-[#817b72]">A structured overview of the information recorded in this case.</p>
              </div>
              <button type="button" onClick={() => setSummaryOpen(false)} aria-label="Close summary" className="rounded-lg p-1 text-[#817b72] hover:bg-[#fcf9ef]"><X className="size-5" /></button>
            </div>
            {summaryLoading ? <p className="mt-5 text-sm text-[#817b72]">Preparing your case brief…</p> : null}
            {summaryError ? <div className="mt-5"><p role="alert" className="text-sm text-[#a33f32]">{summaryError}</p><button type="button" onClick={openSummary} className="mt-3 text-xs font-bold text-[#9d6b00] hover:underline">Try again</button></div> : null}
            {summaryText ? <div className="mt-5"><FormattedMessage text={summaryText} variant="summary" /></div> : null}
            <p className="mt-6 border-t border-[#eee8dd] pt-3 text-[10px] leading-5 text-[#999187]">This overview uses recorded case information and is not legal representation. Verify important legal steps with an appropriate professional or authority.</p>
          </div>
        </div>
      ) : null}

    </aside>
  );
}
