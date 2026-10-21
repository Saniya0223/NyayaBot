'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { ArrowRight, CheckCircle2, Clock3, Coins, FileText, FolderOpen, Plus, Scale, ShieldAlert, Trash2 } from 'lucide-react';
import { useAuth } from '@/components/AuthProvider';
import LoginGate from '@/components/LoginGate';
import { deleteChatCase, fetchChatCases, resolveChatCase, StructuredCaseProfile } from '@/lib/api';

export default function CasesPage() {
  const { user, loading: authLoading, setUser } = useAuth();
  const [cases, setCases] = useState<StructuredCaseProfile[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [resolvingId, setResolvingId] = useState<string | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  useEffect(() => {
    if (authLoading) return;
    if (!user) return;
    let active = true;
    fetchChatCases().then((data) => { if (active) setCases(data); }).catch((caught) => { if (active) setError(caught instanceof Error ? caught.message : 'Cases could not be loaded.'); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [authLoading, user]);

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

  async function removeCase(caseId: string) {
    if (deletingId) return;
    setDeletingId(caseId);
    setError(null);
    try {
      await deleteChatCase(caseId);
      setCases((current) => current.filter((item) => item.case_id !== caseId));
      setConfirmDeleteId(null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Could not delete this case.');
    } finally {
      setDeletingId(null);
    }
  }

  if (authLoading) {
    return <div className="grid min-h-[60vh] place-items-center text-sm text-[#817b72]">Checking your session…</div>;
  }

  if (!user) {
    return (
      <LoginGate
        onLoginSuccess={(nextUser) => {
          setCases([]);
          setError(null);
          setLoading(true);
          setUser(nextUser);
        }}
      />
    );
  }

  return (
    <div className="mx-auto max-w-[1320px] px-4 py-8 sm:px-6 sm:py-10 lg:px-8">
      <header className="flex flex-col justify-between gap-5 sm:flex-row sm:items-end">
        <div><p className="flex items-center gap-2 text-xs font-bold uppercase tracking-[0.13em] text-[#51483a]"><FolderOpen className="size-4" />Saved legal journeys</p><h1 className="mt-2 text-4xl font-extrabold tracking-[-0.05em] text-[#151515]">My Cases</h1><p className="mt-2 max-w-2xl text-sm leading-6 text-[#777169]">Continue the conversation, review evidence, and see the next action without starting over.</p></div>
        <Link href="/" className="flex w-fit items-center gap-2 rounded-xl bg-gradient-to-r from-[#ffe792] to-[#ffdf69] px-5 py-3 text-xs font-bold text-[#171717] shadow-sm hover:from-[#ffdf69] hover:to-[#ffd54d]"><Plus className="size-4" />Start a new case</Link>
      </header>

      {error ? <p role="alert" className="mt-6 rounded-2xl border border-[#efc8c2] bg-[#fff4f2] p-4 text-sm text-[#99483b]">{error}</p> : null}
      {loading ? <div className="mt-8 grid gap-4 md:grid-cols-2">{[1, 2, 3, 4].map((item) => <div key={item} className="h-56 animate-pulse rounded-2xl border border-[#ebe5da] bg-white" />)}</div> : null}

      {!loading && cases.length === 0 ? (
        <div className="mt-8 rounded-3xl border border-dashed border-[#ded7c9] bg-white p-12 text-center"><FolderOpen className="mx-auto size-10 text-[#a99d86]" /><h2 className="mt-4 text-lg font-bold text-[#292929]">No saved cases yet</h2><p className="mt-2 text-sm text-[#817b72]">Tell NyayaBot what happened to create your first legal journey.</p></div>
      ) : null}

      {!loading && cases.length > 0 ? (
        <div className="mt-8 grid gap-5 md:grid-cols-2">
          {cases.map((item) => {
            const resolved = item.current_stage_key === 'RESOLVED';
            return (
              <article key={item.case_id} className="flex flex-col rounded-3xl border border-[#ece7dc] bg-white p-5 paper-shadow sm:p-6">
                <div className="flex items-start justify-between gap-3"><div><span className="font-mono text-[10px] font-bold text-[#969088]">{item.case_number}</span><h2 className="mt-2 text-xl font-extrabold tracking-[-0.03em] text-[#181818]">{item.title}</h2><p className="mt-1 text-xs text-[#78736b]">{item.category_display_name}</p></div><div className="flex flex-col items-end gap-2"><span className="inline-flex items-center gap-2 rounded-full bg-[#fff4ca] px-3 py-1.5 text-[10px] font-bold text-[#28200c]"><span className="size-2 rounded-full bg-[#efb81e]" />{resolved ? 'Resolved' : 'In Progress'}</span>{item.risk_level === 'AMBER' || item.risk_level === 'RED' ? <span className="text-[10px] font-semibold text-[#a34e3b]">{item.risk_level} risk</span> : null}</div></div>
                <div className="mt-5 grid grid-cols-2 gap-3"><div className="flex items-center gap-3 rounded-xl bg-[#fcfaf5] p-3"><Coins className="size-5 shrink-0 text-[#9a720f]" /><div><span className="text-[10px] font-semibold uppercase tracking-wide text-[#8a857d]">Dispute value</span><p className="mt-1 text-sm font-bold text-[#202020]">{item.disputed_amount ? `₹${item.disputed_amount.toLocaleString('en-IN')}` : 'Not stated'}</p></div></div><div className="flex items-center gap-3 rounded-xl bg-[#fcfaf5] p-3"><FileText className="size-5 shrink-0 text-[#34302b]" /><div><span className="text-[10px] font-semibold uppercase tracking-wide text-[#8a857d]">Evidence</span><p className="mt-1 text-sm font-bold text-[#202020]">{item.evidence_checklist.filter((evidence) => evidence.is_available).length} confirmed</p></div></div></div>
                <div className="mt-4 flex items-center gap-3 rounded-2xl border border-[#eee9df] p-3.5"><span className="grid size-9 shrink-0 place-items-center rounded-full bg-[#fff7df]"><Clock3 className="size-4 text-[#29251c]" /></span><div><p className="text-[10px] font-bold uppercase tracking-wide text-[#88827a]">Current stage</p><p className="mt-1 text-sm font-bold text-[#1d1d1d]">{item.current_stage_label}</p></div></div>
                <div className="mt-5 flex flex-wrap gap-2">
                  <Link href={`/?case=${encodeURIComponent(item.case_id)}`} className="flex flex-1 items-center justify-center gap-2 rounded-xl bg-[#171717] px-3 py-2.5 text-xs font-bold text-white hover:bg-[#303030]">Continue conversation<ArrowRight className="size-4" /></Link>
                  <Link href={`/cases/${item.case_id}`} className="flex items-center gap-1.5 rounded-xl border border-[#ded8cb] px-3 text-xs font-semibold text-[#34312c] hover:bg-[#fff8df]" aria-label={`View details for ${item.title}`}><Scale className="size-4" /><span className="hidden sm:inline">View</span></Link>
                  {!resolved ? <button type="button" onClick={() => void markResolved(item.case_id)} disabled={resolvingId === item.case_id || deletingId === item.case_id} className="flex items-center gap-1.5 rounded-xl border border-[#ded8cb] px-3 text-xs font-semibold text-[#34312c] hover:bg-[#fff8df] disabled:opacity-50" aria-label={`Mark ${item.title} resolved`}><CheckCircle2 className="size-4" /><span className="hidden sm:inline">Resolve</span></button> : null}
                  <button type="button" onClick={() => setConfirmDeleteId(item.case_id)} disabled={deletingId !== null} className="flex items-center gap-1.5 rounded-xl border border-[#edc8c2] px-3 py-2.5 text-xs font-semibold text-[#a33f32] hover:bg-[#fff4f2] disabled:opacity-50" aria-label={`Delete ${item.title}`}><Trash2 className="size-4" />Delete</button>
                </div>
                {confirmDeleteId === item.case_id ? (
                  <div className="mt-3 rounded-xl border border-[#edc8c2] bg-[#fff8f6] p-3" role="group" aria-label={`Confirm deletion of ${item.title}`}>
                    <p className="text-xs leading-5 text-[#6d3c35]">Permanently delete this case, its chat, evidence, and generated documents? This cannot be undone.</p>
                    <div className="mt-3 flex justify-end gap-2">
                      <button type="button" onClick={() => setConfirmDeleteId(null)} disabled={deletingId === item.case_id} className="rounded-lg border border-[#ded8cb] px-3 py-2 text-xs font-semibold text-[#34312c] disabled:opacity-50">Cancel</button>
                      <button type="button" onClick={() => void removeCase(item.case_id)} disabled={deletingId === item.case_id} className="rounded-lg bg-[#a33f32] px-3 py-2 text-xs font-bold text-white disabled:opacity-50">{deletingId === item.case_id ? 'Deleting…' : 'Delete case'}</button>
                    </div>
                  </div>
                ) : null}
              </article>
            );
          })}
        </div>
      ) : null}

      <div className="mt-8 flex gap-2.5 rounded-2xl border border-[#efd9ac] bg-[#fff9ed] p-4 text-xs leading-5 text-[#76571f]"><ShieldAlert className="mt-0.5 size-4 shrink-0" /><span>Deadline cards are shown only when a source and date are available. NyayaBot does not guess statutory deadlines.</span></div>
    </div>
  );
}
