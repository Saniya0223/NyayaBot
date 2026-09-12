'use client';

import { useState } from 'react';
import { Bell, Languages, LockKeyhole, LogOut, Mail, ShieldCheck, User, UserRound } from 'lucide-react';
import LoginGate from '@/components/LoginGate';
import { useAuth } from '@/components/AuthProvider';

export default function ProfilePage() {
  const { user: currentUser, loading, setUser, logout } = useAuth();
  const [logoutError, setLogoutError] = useState<string | null>(null);
  const [loggingOut, setLoggingOut] = useState(false);

  async function handleLogout() {
    setLoggingOut(true);
    setLogoutError(null);
    try {
      await logout();
    } catch (error) {
      setLogoutError(error instanceof Error ? error.message : 'Could not log out.');
    } finally {
      setLoggingOut(false);
    }
  }

  if (loading) {
    return (
      <div className="mx-auto grid min-h-[calc(100vh-9rem)] max-w-3xl place-items-center px-4 text-sm text-[#718078]">
        Loading profile…
      </div>
    );
  }

  if (!currentUser) {
    return <LoginGate onLoginSuccess={setUser} />;
  }

  return (
    <div className="mx-auto max-w-3xl px-4 py-8 sm:px-6 sm:py-10">
      <div className="flex items-center justify-between">
        <div>
          <p className="flex items-center gap-2 text-xs font-bold uppercase tracking-[0.13em] text-[#2f755b]">
            <UserRound className="size-4" />Profile & Preferences
          </p>
          <h1 className="mt-2 text-3xl font-bold tracking-[-0.04em] text-[#17231f]">
            {currentUser.name}
          </h1>
          <p className="mt-1 flex items-center gap-1.5 text-xs text-[#6b7872]">
            <Mail className="size-3.5" />
            {currentUser.email}
          </p>
        </div>
        <button
          type="button"
          onClick={() => void handleLogout()}
          disabled={loggingOut}
          className="flex items-center gap-2 rounded-xl border border-[#e2cdc6] bg-[#fdf7f6] px-3.5 py-2 text-xs font-bold text-[#a03422] transition hover:border-[#cfaba2] hover:bg-[#faeeea]"
          aria-label="Log out"
        >
          <LogOut className="size-3.5" />
          <span>{loggingOut ? 'Logging out…' : 'Log Out'}</span>
        </button>
      </div>

      {logoutError ? <p role="alert" className="mt-4 rounded-xl bg-[#fff1ef] p-3 text-xs text-[#97473b]">{logoutError}</p> : null}

      <div className="mt-8 space-y-3">
        {/* Account Info Card */}
        <section className="flex gap-4 rounded-2xl border border-[#dde5e0] bg-white p-5">
          <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-[#e8f2ec] text-[#174e3b]">
            <User className="size-5" />
          </span>
          <div>
            <h2 className="text-sm font-bold text-[#293931]">Account Details</h2>
            <p className="mt-1 text-xs leading-5 text-[#718078]">
              Signed in as <strong className="font-semibold text-[#1c2b25]">{currentUser.name}</strong> ({currentUser.email}).
              Your case history and drafts are linked to this account.
            </p>
          </div>
        </section>

        {/* Existing Preference Cards */}
        <section className="flex gap-4 rounded-2xl border border-[#dde5e0] bg-white p-5">
          <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-[#e8f2ec] text-[#174e3b]">
            <Languages className="size-5" />
          </span>
          <div>
            <h2 className="text-sm font-bold text-[#293931]">Language</h2>
            <p className="mt-1 text-xs leading-5 text-[#718078]">
              NyayaBot accepts English, Hindi, and Hinglish in the same conversation. Interface translation is planned for a later release.
            </p>
          </div>
        </section>

        <section className="flex gap-4 rounded-2xl border border-[#dde5e0] bg-white p-5">
          <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-[#e8f2ec] text-[#174e3b]">
            <LockKeyhole className="size-5" />
          </span>
          <div>
            <h2 className="text-sm font-bold text-[#293931]">Privacy</h2>
            <p className="mt-1 text-xs leading-5 text-[#718078]">
              Aadhaar, PAN, and card numbers are masked during intake. Avoid sharing passwords, OTPs, or full payment credentials.
            </p>
          </div>
        </section>

        <section className="flex gap-4 rounded-2xl border border-[#dde5e0] bg-white p-5">
          <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-[#e8f2ec] text-[#174e3b]">
            <Bell className="size-5" />
          </span>
          <div>
            <h2 className="text-sm font-bold text-[#293931]">Reminders</h2>
            <p className="mt-1 text-xs leading-5 text-[#718078]">
              Notification delivery is not enabled in this MVP. Any date shown must include its source and confirmation status.
            </p>
          </div>
        </section>
      </div>

      <div className="mt-6 flex gap-2.5 rounded-2xl bg-[#f1f7f3] p-4 text-xs leading-5 text-[#5d6d64]">
        <ShieldCheck className="mt-0.5 size-4 shrink-0 text-[#2f755b]" />
        NyayaBot never files a complaint, sends a notice, or contacts another person unless a future integration clearly asks for your authorization.
      </div>
    </div>
  );
}
