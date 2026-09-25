'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import LoginGate from '@/components/LoginGate';
import { useAuth } from '@/components/AuthProvider';
import { addMemory, deleteMemory, fetchCaseHistory, fetchMemories, fetchProfile, updateProfile, CaseHistoryItem, SavedMemory, UserProfile } from '@/lib/profile';

const fieldStyle = 'mt-1 w-full rounded-xl border border-[#ccd8d1] bg-white px-3 py-2 text-sm';

export default function ProfilePage() {
  const { user, loading, setUser, logout } = useAuth();
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [memories, setMemories] = useState<SavedMemory[]>([]);
  const [cases, setCases] = useState<CaseHistoryItem[]>([]);
  const [memoryCategory, setMemoryCategory] = useState<SavedMemory['category']>('preference');
  const [memoryText, setMemoryText] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!user) return;
    let active = true;
    void Promise.all([fetchProfile(), fetchMemories(), fetchCaseHistory()])
      .then(([personal, saved, history]) => { if (active) { setProfile(personal); setMemories(saved); setCases(history); } })
      .catch((caught) => { if (active) setError(caught instanceof Error ? caught.message : 'Could not load profile.'); });
    return () => { active = false; };
  }, [user]);

  function change<K extends keyof UserProfile>(field: K, value: UserProfile[K]) {
    setProfile((current) => current ? { ...current, [field]: value } : current);
  }

  async function saveProfile(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!profile || !user) return;
    setBusy(true); setError(''); setNotice('');
    try {
      const updated = await updateProfile({
        full_name: profile.full_name, date_of_birth: profile.date_of_birth,
        phone: profile.phone?.trim() || null, state: profile.state?.trim() || null,
        city: profile.city?.trim() || null, pin_code: profile.pin_code?.trim() || null,
        full_address: profile.full_address?.trim() || null,
        preferred_language: profile.preferred_language,
      });
      setProfile(updated);
      setUser({ ...user, name: updated.full_name, phone: updated.phone ?? undefined,
        city: updated.city ?? undefined, state: updated.state ?? undefined });
      setNotice('Profile saved. Document details will still be shown for review.');
    } catch (caught) { setError(caught instanceof Error ? caught.message : 'Could not save profile.'); }
    finally { setBusy(false); }
  }

  async function saveMemory(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true); setError(''); setNotice('');
    try {
      const saved = await addMemory(memoryCategory, memoryText.trim());
      setMemories((current) => [saved, ...current]); setMemoryText('');
      setNotice('Memory saved. You can delete it at any time.');
    } catch (caught) { setError(caught instanceof Error ? caught.message : 'Could not save memory.'); }
    finally { setBusy(false); }
  }

  async function removeMemory(id: string) {
    setBusy(true); setError(''); setNotice('');
    try { await deleteMemory(id); setMemories((current) => current.filter((item) => item.id !== id)); }
    catch (caught) { setError(caught instanceof Error ? caught.message : 'Could not delete memory.'); }
    finally { setBusy(false); }
  }

  if (loading) return <div className="mx-auto max-w-3xl p-8 text-sm">Loading profile…</div>;
  if (!user) return <LoginGate onLoginSuccess={setUser} />;

  return <main className="mx-auto max-w-3xl space-y-6 px-4 py-8 sm:px-6">
    <div className="flex items-start justify-between gap-3"><div><h1 className="text-2xl font-bold text-[#17231f]">Profile</h1><p className="mt-1 text-sm text-[#66766d]">Personal details, saved memory, and separate cases.</p></div><button type="button" onClick={() => void logout()} className="rounded-xl border px-3 py-2 text-xs">Log out</button></div>
    {error && <p role="alert" className="rounded-xl bg-[#fff0ed] p-3 text-sm text-[#a2473a]">{error}</p>}
    {notice && <p role="status" className="rounded-xl bg-[#f1f7f3] p-3 text-sm text-[#285b44]">{notice}</p>}
    <section className="rounded-2xl border border-[#dde5e0] bg-white p-5"><h2 className="text-lg font-semibold">Personal information</h2><p className="mt-1 text-xs text-[#718078]">Your email comes from your account. Case-specific facts stay in each case.</p>
      {!profile ? <p className="mt-4 text-sm">Loading…</p> : <form onSubmit={(event) => void saveProfile(event)} className="mt-4 grid gap-4 sm:grid-cols-2">
        <label className="text-xs font-semibold">Full name *<input required minLength={2} maxLength={255} className={fieldStyle} value={profile.full_name} onChange={(e) => change('full_name', e.target.value)} /></label>
        <label className="text-xs font-semibold">Date of birth *<input required type="date" max={new Date().toISOString().slice(0, 10)} className={fieldStyle} value={profile.date_of_birth ?? ''} onChange={(e) => change('date_of_birth', e.target.value)} /></label>
        <label className="text-xs font-semibold">Email<input readOnly className={`${fieldStyle} bg-[#f5f7f5]`} value={profile.email} /></label>
        <label className="text-xs font-semibold">Phone<input type="tel" maxLength={20} className={fieldStyle} value={profile.phone ?? ''} onChange={(e) => change('phone', e.target.value)} /></label>
        <label className="text-xs font-semibold">State<input maxLength={100} className={fieldStyle} value={profile.state ?? ''} onChange={(e) => change('state', e.target.value)} /></label>
        <label className="text-xs font-semibold">City<input maxLength={100} className={fieldStyle} value={profile.city ?? ''} onChange={(e) => change('city', e.target.value)} /></label>
        <label className="text-xs font-semibold">PIN code<input inputMode="numeric" pattern="[1-9][0-9]{5}" maxLength={6} className={fieldStyle} value={profile.pin_code ?? ''} onChange={(e) => change('pin_code', e.target.value)} /></label>
        <label className="text-xs font-semibold">Preferred language<select className={fieldStyle} value={profile.preferred_language ?? ''} onChange={(e) => change('preferred_language', (e.target.value || null) as UserProfile['preferred_language'])}><option value="">Follow my messages</option><option value="english">English</option><option value="hindi">Hindi</option><option value="hinglish">Roman Hinglish</option></select></label>
        <label className="text-xs font-semibold sm:col-span-2">Full address<textarea rows={3} maxLength={500} className={fieldStyle} value={profile.full_address ?? ''} onChange={(e) => change('full_address', e.target.value)} /></label>
        <button disabled={busy} className="rounded-xl bg-[#174e3b] px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-50 sm:col-span-2">Save profile</button>
      </form>}
    </section>
    <section className="rounded-2xl border border-[#dde5e0] bg-white p-5"><h2 className="text-lg font-semibold">Long-term memory</h2><p className="mt-1 text-xs text-[#718078]">Save useful preferences or recurring information. Chat messages are not saved here automatically. Avoid passwords, OTPs, and ID numbers.</p>
      <form onSubmit={(event) => void saveMemory(event)} className="mt-4 space-y-3"><select aria-label="Memory type" className={fieldStyle} value={memoryCategory} onChange={(e) => setMemoryCategory(e.target.value as SavedMemory['category'])}><option value="preference">Preference</option><option value="recurring">Recurring information</option><option value="explicit">Remember this</option></select><textarea aria-label="Memory to save" required minLength={3} maxLength={500} rows={2} className={fieldStyle} value={memoryText} onChange={(e) => setMemoryText(e.target.value)} placeholder="For example: Explain legal terms in simple language." /><button disabled={busy} className="rounded-xl bg-[#174e3b] px-4 py-2 text-xs font-semibold text-white disabled:opacity-50">Save memory</button></form>
      <div className="mt-4 space-y-2">{memories.map((item) => <div key={item.id} className="flex items-start justify-between gap-3 rounded-xl bg-[#f6f8f6] p-3"><div><span className="text-[10px] font-bold uppercase text-[#39705a]">{item.category}</span><p className="text-sm">{item.text}</p></div><button type="button" disabled={busy} aria-label="Delete memory" onClick={() => void removeMemory(item.id)} className="text-xs text-[#a2473a]">Delete</button></div>)}{!memories.length && <p className="text-sm text-[#718078]">No saved memories yet.</p>}</div>
    </section>
    <section className="rounded-2xl border border-[#dde5e0] bg-white p-5"><h2 className="text-lg font-semibold">Cases</h2><p className="mt-1 text-xs text-[#718078]">Detailed facts and documents stay in each case. Brief history is used only when relevant.</p><div className="mt-4 space-y-2">{cases.map((item) => <Link key={item.case_id} href={`/?case=${encodeURIComponent(item.case_id)}`} className="block rounded-xl border border-[#e2e9e4] p-3 hover:bg-[#f6f8f6]"><span className="text-sm font-semibold">{item.title}</span><span className="ml-2 text-xs text-[#718078]">{item.category} · {item.status}</span>{item.summary && <p className="mt-1 line-clamp-2 text-xs text-[#718078]">{item.summary}</p>}</Link>)}{!cases.length && <p className="text-sm text-[#718078]">No cases yet.</p>}</div></section>
  </main>;
}
