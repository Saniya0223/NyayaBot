'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { ArrowRight, CheckCircle2, Clock3, FolderOpen, Plus, Scale, ShieldAlert } from 'lucide-react';
import { fetchChatCases, resolveChatCase, StructuredCaseProfile } from '@/lib/api';

export default function CasesPage() {
  const [cases, setCases] = useState<StructuredCaseProfile[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [resolvingId, setResolvingId] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    fetchChatCases().then((data) => { if (active) setCases(data); }).catch((caught) => { if (active) setError(caught instanceof Error ? caught.message : 'Cases could not be loaded.'); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  async function markResolved(caseId: string) {
    setResolvingId(caseId);
    setError(null);
    try {
      const updated = await resolveChatCase(caseId);
      setCases((current) => current.map((item) => item.case_id === caseId ? updated : item));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Could not update this case.');
    } finally {
      setResolvingId(null);
    }
  }

  return (
    <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6 sm:py-10 lg:px-8">
      <header className="flex flex-col justify-between gap-5 sm:flex-row sm:items-end">
        <div><p className="flex items-center gap-2 text-xs font-bold uppercase tracking-[0.13em] text-[#2f755b]"><FolderOpen className="size-4" />Saved legal journeys</p><h1 className="mt-2 text-3xl font-bold tracking-[-0.04em] text-[#17231f]">My cases</h1><p className="mt-2 max-w-xl text-sm leading-6 text-[#6b7872]">Continue the conversation, review evidence, and see the next action without starting over.</p></div>
        <Link href="/" className="flex w-fit items-center gap-2 rounded-xl bg-[#174e3b] px-4 py-2.5 text-xs font-bold text-white hover:bg-[#103c2d]"><Plus className="size-4" />Start a new case</Link>
      </header>

      {error ? <p role="alert" className="mt-6 rounded-2xl border border-[#efc8c2] bg-[#fff4f2] p-4 text-sm text-[#99483b]">{error}</p> : null}
      {loading ? <div className="mt-8 grid gap-4 md:grid-cols-2">{[1, 2, 3, 4].map((item) => <div key={item} className="h-56 animate-pulse rounded-2xl border border-[#e0e7e3] bg-white" />)}</div> : null}

      {!loading && cases.length === 0 ? (
        <div className="mt-8 rounded-3xl border border-dashed border-[#cbd8d0] bg-white p-12 text-center"><FolderOpen className="mx-auto size-10 text-[#8da197]" /><h2 className="mt-4 text-lg font-bold text-[#2a3933]">No saved cases yet</h2><p className="mt-2 text-sm text-[#718078]">Tell NyayaBot what happened to create your first legal journey.</p></div>
      ) : null}

      {!loading && cases.length > 0 ? (
        <div className="mt-8 grid gap-4 md:grid-cols-2">
          {cases.map((item) => {
            const resolved = item.current_stage_key === 'RESOLVED';
            return (
              <article key={item.case_id} className="flex flex-col rounded-3xl border border-[#dde5e0] bg-white p-5 paper-shadow">
                <div className="flex items-start justify-between gap-3"><div><span className="font-mono text-[10px] font-bold text-[#738079]">{item.case_number}</span><h2 className="mt-2 text-lg font-bold tracking-[-0.02em] text-[#21312a]">{item.title}</h2><p className="mt-1 text-xs text-[#718078]">{item.category_display_name}</p></div><span className={`rounded-full px-2.5 py-1 text-[10px] font-bold ${item.risk_level === 'AMBER' ? 'bg-[#fff3dd] text-[#93611b]' : item.risk_level === 'RED' ? 'bg-[#fee9e5] text-[#9d4235]' : 'bg-[#e8f2ec] text-[#2f7055]'}`}>{resolved ? 'Resolved' : item.risk_level || 'Active'}</span></div>
                <div className="mt-5 grid grid-cols-2 gap-2"><div className="rounded-xl bg-[#f6f8f6] p-3"><span className="text-[10px] font-semibold uppercase tracking-wide text-[#839088]">Dispute value</span><p className="mt-1 text-sm font-bold text-[#2b3a34]">{item.disputed_amount ? `₹${item.disputed_amount.toLocaleString('en-IN')}` : 'Not stated'}</p></div><div className="rounded-xl bg-[#f6f8f6] p-3"><span className="text-[10px] font-semibold uppercase tracking-wide text-[#839088]">Evidence</span><p className="mt-1 text-sm font-bold text-[#2b3a34]">{item.evidence_checklist.filter((evidence) => evidence.is_available).length} confirmed</p></div></div>
                <div className="mt-4 rounded-2xl border border-[#dfe7e2] p-3.5"><p className="flex items-center gap-2 text-[10px] font-bold uppercase tracking-wide text-[#7b8881]"><Clock3 className="size-3.5" />Current stage</p><p className="mt-1.5 text-sm font-bold text-[#174e3b]">{item.current_stage_label}</p></div>
                <div className="mt-5 flex flex-wrap gap-2"><Link href={`/?case=${encodeURIComponent(item.case_id)}`} className="flex flex-1 items-center justify-center gap-2 rounded-xl bg-[#174e3b] px-3 py-2.5 text-xs font-bold text-white hover:bg-[#103c2d]">Continue conversation<ArrowRight className="size-4" /></Link><Link href={`/cases/${item.case_id}`} className="grid size-10 place-items-center rounded-xl border border-[#cfdad3] text-[#53635a] hover:bg-[#f2f6f3]" aria-label={`View details for ${item.title}`}><Scale className="size-4" /></Link>{!resolved ? <button type="button" onClick={() => void markResolved(item.case_id)} disabled={resolvingId === item.case_id} className="grid size-10 place-items-center rounded-xl border border-[#cfdad3] text-[#53635a] hover:bg-[#f2f6f3] disabled:opacity-50" aria-label={`Mark ${item.title} resolved`}><CheckCircle2 className="size-4" /></button> : null}</div>
              </article>
            );
          })}
        </div>
      ) : null}

      <div className="mt-8 flex gap-2.5 rounded-2xl border border-[#efd9ac] bg-[#fff9ed] p-4 text-xs leading-5 text-[#76571f]"><ShieldAlert className="mt-0.5 size-4 shrink-0" /><span>Deadline cards are shown only when a source and date are available. NyayaBot does not guess statutory deadlines.</span></div>
    </div>
  );
}
