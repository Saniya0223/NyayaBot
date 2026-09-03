'use client';

import { useState } from 'react';
import {
  AlertTriangle,
  ArrowRight,
  Check,
  ChevronDown,
  Circle,
  FileCheck2,
  FileText,
  IndianRupee,
  MapPin,
  Scale,
  Sparkles,
  UserRound,
} from 'lucide-react';
import { StructuredCaseProfile } from '@/lib/api';

interface CaseWorkspacePanelProps {
  profile: StructuredCaseProfile | null;
  onTriggerDocumentModal: (docType: string, docLabel: string) => void;
}

export default function CaseWorkspacePanel({ profile, onTriggerDocumentModal }: CaseWorkspacePanelProps) {
  const [showRights, setShowRights] = useState(false);

  if (!profile) {
    return (
      <aside className="flex h-full min-h-[620px] flex-col rounded-[26px] border border-[#dbe4de] bg-white p-5 paper-shadow" aria-label="Live case workspace">
        <span className="text-[10px] font-bold uppercase tracking-[0.14em] text-[#74817a]">Live case workspace</span>
        <div className="flex flex-1 flex-col items-center justify-center px-5 text-center">
          <span className="grid size-12 place-items-center rounded-2xl bg-[#edf4ef] text-[#2f755b]"><Sparkles className="size-5" aria-hidden="true" /></span>
          <h2 className="mt-4 text-base font-bold text-[#25342e]">Your case will take shape here</h2>
          <p className="mt-2 max-w-xs text-xs leading-5 text-[#718078]">As you tell your story, NyayaBot will organise facts, evidence, your legal journey, and the next practical action.</p>
        </div>
      </aside>
    );
  }

  const knownEvidence = profile.evidence_checklist.filter((item) => item.is_available).length;
  const documentType = profile.recommended_doc_type || 'GENERAL_COMPLAINT_LETTER';
  const documentLabel = profile.recommended_doc_label || 'Prepare complaint letter';
  // Intake facts and document fields are gated separately, so counting only the
  // intake list showed "0 details still needed" while the case was not yet ready.
  const outstandingDetailCount = new Set([
    ...profile.missing_required_fields,
    ...(profile.missing_document_fields ?? []),
  ]).size;
  const nextStep = profile.legal_journey.find((step) => step.status === 'FUTURE');
  const displayAmount = profile.disputed_amount > 0 ? `₹${profile.disputed_amount.toLocaleString('en-IN')}` : 'Not provided';

  return (
    <aside className="soft-scrollbar h-full min-h-[620px] overflow-y-auto rounded-[26px] border border-[#dbe4de] bg-white p-4 paper-shadow sm:p-5" aria-label="Live case workspace">
      <div className="mb-4 flex items-center justify-between">
        <span className="text-[10px] font-bold uppercase tracking-[0.14em] text-[#74817a]">Live case workspace</span>
        <span className={`rounded-full px-2.5 py-1 text-[10px] font-bold ${profile.risk_level === 'RED' ? 'bg-[#fdeae7] text-[#a33f32]' : profile.risk_level === 'AMBER' ? 'bg-[#fff3dd] text-[#96631b]' : 'bg-[#e8f2ec] text-[#2d6d53]'}`}>
          {profile.risk_level || 'GREEN'} guidance
        </span>
      </div>

      {profile.safety_notice ? (
        <div className={`mb-4 flex gap-2.5 rounded-2xl border p-3 text-[11px] leading-5 ${profile.risk_level === 'RED' ? 'border-[#efc2bb] bg-[#fff4f2] text-[#853c32]' : 'border-[#efd9ac] bg-[#fff9ec] text-[#745319]'}`}>
          <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          <span>{profile.safety_notice}</span>
        </div>
      ) : null}

      <section className="rounded-2xl bg-[#174e3b] p-4 text-white">
        <div className="flex items-center justify-between gap-3">
          <span className="rounded-lg bg-white/12 px-2 py-1 font-mono text-[9px] font-bold tracking-[0.08em]">{profile.case_number}</span>
          <span className="text-[10px] font-semibold text-[#cde1d5]">{profile.category_display_name}</span>
        </div>
        <h2 className="mt-3 text-lg font-bold leading-6 tracking-[-0.02em]">{profile.title}</h2>
        <p className="mt-1.5 flex items-center gap-1.5 text-[11px] text-[#d3e2da]"><MapPin className="size-3.5" aria-hidden="true" />{profile.user_city ? `${profile.user_city}, ${profile.user_state || 'India'}` : 'Location not provided yet'}</p>
      </section>

      <section className="mt-4 grid grid-cols-2 gap-2" aria-label="Case facts">
        <div className="rounded-xl border border-[#e0e7e3] bg-[#fbfcfb] p-3">
          <span className="flex items-center gap-1 text-[10px] font-semibold uppercase tracking-wide text-[#7b8881]"><IndianRupee className="size-3" />Amount</span>
          <p className="mt-1 truncate text-sm font-bold text-[#24332d]">{displayAmount}</p>
        </div>
        <div className="rounded-xl border border-[#e0e7e3] bg-[#fbfcfb] p-3">
          <span className="flex items-center gap-1 text-[10px] font-semibold uppercase tracking-wide text-[#7b8881]"><UserRound className="size-3" />Other party</span>
          <p className="mt-1 truncate text-sm font-bold text-[#24332d]">{profile.opposite_party_name || profile.bank_name || 'Not provided'}</p>
        </div>
      </section>

      <details open className="group mt-4 rounded-2xl border border-[#e0e7e3] bg-white">
        <summary className="flex list-none items-center justify-between px-4 py-3 text-xs font-bold text-[#304039] marker:hidden">
          <span className="flex items-center gap-2"><FileCheck2 className="size-4 text-[#2f755b]" />Evidence <span className="font-medium text-[#87938d]">{knownEvidence}/{profile.evidence_checklist.length}</span></span>
          <ChevronDown className="size-4 text-[#7d8b84] transition group-open:rotate-180" />
        </summary>
        <div className="space-y-1.5 border-t border-[#edf1ee] px-3 py-3">
          {profile.evidence_checklist.map((item) => (
            <div key={item.id} className="flex items-start gap-2.5 rounded-xl px-2 py-2">
              {item.is_available ? <span className="mt-0.5 grid size-4 shrink-0 place-items-center rounded-full bg-[#dff0e6] text-[#287154]"><Check className="size-2.5 stroke-[3]" /></span> : <Circle className="mt-0.5 size-4 shrink-0 text-[#bdc8c2]" />}
              <div className="min-w-0"><p className={`text-[11px] font-semibold ${item.is_available ? 'text-[#2d3d36]' : 'text-[#718078]'}`}>{item.name}</p><p className="mt-0.5 text-[10px] leading-4 text-[#8a958f]">{item.why_needed}</p></div>
            </div>
          ))}
        </div>
      </details>

      <section className="mt-4 rounded-2xl border border-[#e0e7e3] p-4">
        <h3 className="text-xs font-bold text-[#304039]">Legal journey</h3>
        <ol className="mt-3 space-y-0">
          {profile.legal_journey.map((step, index) => {
            const completed = step.status === 'COMPLETED';
            const current = step.status === 'CURRENT' || step.is_current;
            return (
              <li key={step.id} className="relative flex gap-3 pb-4 last:pb-0">
                {index < profile.legal_journey.length - 1 ? <span className={`absolute left-[7px] top-4 h-[calc(100%-8px)] w-px ${completed ? 'bg-[#76a990]' : 'bg-[#dfe6e2]'}`} /> : null}
                <span className={`relative z-10 mt-0.5 grid size-[15px] shrink-0 place-items-center rounded-full ${completed ? 'bg-[#3c8a68] text-white' : current ? 'border-[4px] border-[#dbeadf] bg-[#174e3b]' : 'border-2 border-[#cbd5d0] bg-white'}`}>{completed ? <Check className="size-2.5 stroke-[3]" /> : null}</span>
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2"><p className={`text-[11px] font-bold ${current ? 'text-[#174e3b]' : completed ? 'text-[#526159]' : 'text-[#8a958f]'}`}>{step.title}</p>{current ? <span className="rounded bg-[#e8f2ec] px-1.5 py-0.5 text-[8px] font-bold uppercase tracking-wide text-[#2b6e52]">Current</span> : null}</div>
                  {current ? <p className="mt-1 text-[10px] leading-4 text-[#718078]">{step.description}</p> : null}
                </div>
              </li>
            );
          })}
        </ol>
      </section>

      <section className="mt-4 rounded-2xl border border-[#bdd2c6] bg-[#edf5f0] p-4">
        <div className="flex items-center gap-2 text-[10px] font-bold uppercase tracking-[0.1em] text-[#2d6d53]"><Sparkles className="size-3.5" />Next recommended action</div>
        <h3 className="mt-2 text-sm font-bold text-[#20332b]">{profile.is_ready_for_document ? documentLabel : 'Continue the short intake'}</h3>
        <p className="mt-1 text-[11px] leading-5 text-[#617168]">{profile.is_ready_for_document ? 'Review the facts NyayaBot already knows, add only missing document details, and generate a draft.' : `${outstandingDetailCount} detail${outstandingDetailCount === 1 ? '' : 's'} still needed. NyayaBot will ask conversationally.`}</p>
        {profile.is_ready_for_document ? (
          <button type="button" onClick={() => onTriggerDocumentModal(documentType, documentLabel)} className="mt-3 flex w-full items-center justify-between rounded-xl bg-[#174e3b] px-3.5 py-2.5 text-xs font-bold text-white transition hover:bg-[#103c2d]">
            <span className="flex items-center gap-2"><FileText className="size-4" />{documentLabel}</span><ArrowRight className="size-4" />
          </button>
        ) : null}
        {nextStep && profile.current_stage_key === 'RESOLVED' ? <p className="mt-2 text-[10px] text-[#718078]">Next if unresolved: {nextStep.title}</p> : null}
      </section>

      {profile.rights_summary ? (
        <section className="mt-4 overflow-hidden rounded-2xl border border-[#e0e7e3]">
          <button type="button" onClick={() => setShowRights((value) => !value)} aria-expanded={showRights} className="flex w-full items-center justify-between px-4 py-3 text-left text-xs font-bold text-[#304039] hover:bg-[#fafcfb]">
            <span className="flex items-center gap-2"><Scale className="size-4 text-[#2f755b]" />Rights & legal sources</span><ChevronDown className={`size-4 text-[#7d8b84] transition ${showRights ? 'rotate-180' : ''}`} />
          </button>
          {showRights ? (
            <div className="space-y-3 border-t border-[#edf1ee] px-4 py-3 text-[11px] leading-5 text-[#607068]">
              <p>{profile.rights_summary.what_this_means}</p>
              <ul className="space-y-1.5">{profile.rights_summary.possible_rights.map((right) => <li key={right} className="flex gap-2"><span className="text-[#2f755b]">•</span><span>{right}</span></li>)}</ul>
              {profile.rights_summary.sources?.map((source) => <a key={source.url} href={source.url} target="_blank" rel="noreferrer" className="block rounded-lg bg-[#f6f8f7] px-2.5 py-2 font-semibold text-[#2d6d53] hover:underline">{source.title}<span className="block text-[9px] font-medium text-[#839088]">{source.authority}</span></a>)}
            </div>
          ) : null}
        </section>
      ) : null}
    </aside>
  );
}
