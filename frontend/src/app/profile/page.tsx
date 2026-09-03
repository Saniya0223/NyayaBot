import { Bell, Languages, LockKeyhole, ShieldCheck, UserRound } from 'lucide-react';

export default function ProfilePage() {
  return (
    <div className="mx-auto max-w-3xl px-4 py-8 sm:px-6 sm:py-10">
      <p className="flex items-center gap-2 text-xs font-bold uppercase tracking-[0.13em] text-[#2f755b]"><UserRound className="size-4" />Preferences</p>
      <h1 className="mt-2 text-3xl font-bold tracking-[-0.04em] text-[#17231f]">Profile</h1>
      <p className="mt-2 text-sm leading-6 text-[#6b7872]">This local MVP does not require an account. Your saved demo data remains on this device&apos;s backend database.</p>
      <div className="mt-8 space-y-3">
        <section className="flex gap-4 rounded-2xl border border-[#dde5e0] bg-white p-5"><span className="grid size-10 shrink-0 place-items-center rounded-xl bg-[#e8f2ec] text-[#174e3b]"><Languages className="size-5" /></span><div><h2 className="text-sm font-bold text-[#293931]">Language</h2><p className="mt-1 text-xs leading-5 text-[#718078]">NyayaBot accepts English, Hindi, and Hinglish in the same conversation. Interface translation is planned for a later release.</p></div></section>
        <section className="flex gap-4 rounded-2xl border border-[#dde5e0] bg-white p-5"><span className="grid size-10 shrink-0 place-items-center rounded-xl bg-[#e8f2ec] text-[#174e3b]"><LockKeyhole className="size-5" /></span><div><h2 className="text-sm font-bold text-[#293931]">Privacy</h2><p className="mt-1 text-xs leading-5 text-[#718078]">Aadhaar, PAN, and card numbers are masked during intake. Avoid sharing passwords, OTPs, or full payment credentials.</p></div></section>
        <section className="flex gap-4 rounded-2xl border border-[#dde5e0] bg-white p-5"><span className="grid size-10 shrink-0 place-items-center rounded-xl bg-[#e8f2ec] text-[#174e3b]"><Bell className="size-5" /></span><div><h2 className="text-sm font-bold text-[#293931]">Reminders</h2><p className="mt-1 text-xs leading-5 text-[#718078]">Notification delivery is not enabled in this MVP. Any date shown must include its source and confirmation status.</p></div></section>
      </div>
      <div className="mt-6 flex gap-2.5 rounded-2xl bg-[#f1f7f3] p-4 text-xs leading-5 text-[#5d6d64]"><ShieldCheck className="mt-0.5 size-4 shrink-0 text-[#2f755b]" />NyayaBot never files a complaint, sends a notice, or contacts another person unless a future integration clearly asks for your authorization.</div>
    </div>
  );
}
