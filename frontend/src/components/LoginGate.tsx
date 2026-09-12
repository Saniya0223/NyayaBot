'use client';

import React, { useState } from 'react';
import { Eye, EyeOff, Lock, Mail, Scale, ShieldCheck, User, ArrowRight, LoaderCircle } from 'lucide-react';
import { ApiError } from '@/lib/api';
import { loginAccount, NyayaUser, signupAccount } from '@/lib/auth';

interface LoginGateProps {
  onLoginSuccess: (user: NyayaUser) => void;
}

export default function LoginGate({ onLoginSuccess }: LoginGateProps) {
  const [tab, setTab] = useState<'signin' | 'signup'>('signin');
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!email.trim()) {
      setError('Please enter your email address.');
      return;
    }
    if (!password.trim()) {
      setError('Please enter your password.');
      return;
    }
    if (tab === 'signup' && !name.trim()) {
      setError('Please enter your full name.');
      return;
    }
    if (tab === 'signup' && password.length < 8) {
      setError('Choose a password with at least 8 characters.');
      return;
    }

    setError('');
    setSubmitting(true);
    try {
      const user = tab === 'signup'
        ? await signupAccount(name.trim(), email.trim(), password)
        : await loginAccount(email.trim(), password);
      onLoginSuccess(user);
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 0) {
        setError('The NyayaBot backend is unavailable. Check that it is running and try again.');
      } else if (caught instanceof Error) {
        setError(caught.message);
      } else {
        setError('Authentication failed. Please try again.');
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex min-h-[calc(100vh-10rem)] items-center justify-center px-4 py-8 sm:px-6">
      <div className="w-full max-w-[460px] overflow-hidden rounded-[28px] border border-[#dbe4de] bg-white p-6 paper-shadow sm:p-8">
        {/* Brand Header */}
        <div className="text-center">
          <div className="mx-auto grid size-12 place-items-center rounded-2xl bg-[#174e3b] text-white shadow-md">
            <Scale className="size-6" aria-hidden="true" />
          </div>
          <h1 className="mt-4 text-2xl font-bold tracking-tight text-[#17231f]">
            Welcome to NyayaBot
          </h1>
          <p className="mt-1.5 text-xs text-[#6b7872]">
            Turn your story into an actionable legal plan & official notices
          </p>
        </div>

        {/* Tab Switcher */}
        <div className="mt-6 flex rounded-xl border border-[#dde5e0] bg-[#f5f8f6] p-1">
          <button
            type="button"
            onClick={() => { setTab('signin'); setError(''); }}
            className={`flex-1 rounded-lg py-2 text-xs font-bold transition-all ${
              tab === 'signin'
                ? 'bg-white text-[#174e3b] shadow-sm'
                : 'text-[#6b7872] hover:text-[#17231f]'
            }`}
          >
            Sign In
          </button>
          <button
            type="button"
            onClick={() => { setTab('signup'); setError(''); }}
            className={`flex-1 rounded-lg py-2 text-xs font-bold transition-all ${
              tab === 'signup'
                ? 'bg-white text-[#174e3b] shadow-sm'
                : 'text-[#6b7872] hover:text-[#17231f]'
            }`}
          >
            Create Account
          </button>
        </div>

        {/* Error message */}
        {error ? (
          <div className="mt-4 rounded-xl border border-[#f3c2c2] bg-[#fdf2f2] px-3.5 py-2 text-xs font-semibold text-[#b82a2a]">
            {error}
          </div>
        ) : null}

        {/* Form */}
        <form onSubmit={handleSubmit} className="mt-5 space-y-3.5">
          {tab === 'signup' && (
            <div>
              <label className="block text-xs font-semibold text-[#293931]">Full Name</label>
              <div className="relative mt-1">
                <span className="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-3 text-[#87968e]">
                  <User className="size-4" />
                </span>
                <input
                  type="text"
                  placeholder="e.g. Rahul Sharma"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  className="w-full rounded-xl border border-[#dbe4de] bg-[#fbfdfc] py-2.5 pl-9 pr-3 text-xs text-[#17231f] transition placeholder:text-[#9bb0a5] focus:border-[#174e3b] focus:bg-white focus:outline-none"
                />
              </div>
            </div>
          )}

          <div>
            <label className="block text-xs font-semibold text-[#293931]">Email Address</label>
            <div className="relative mt-1">
              <span className="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-3 text-[#87968e]">
                <Mail className="size-4" />
              </span>
              <input
                type="email"
                autoComplete="email"
                placeholder="name@example.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="w-full rounded-xl border border-[#dbe4de] bg-[#fbfdfc] py-2.5 pl-9 pr-3 text-xs text-[#17231f] transition placeholder:text-[#9bb0a5] focus:border-[#174e3b] focus:bg-white focus:outline-none"
              />
            </div>
          </div>

          <div>
            <label className="block text-xs font-semibold text-[#293931]">Password</label>
            <div className="relative mt-1">
              <span className="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-3 text-[#87968e]">
                <Lock className="size-4" />
              </span>
              <input
                type={showPassword ? 'text' : 'password'}
                autoComplete={tab === 'signin' ? 'current-password' : 'new-password'}
                placeholder="••••••••••••"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="w-full rounded-xl border border-[#dbe4de] bg-[#fbfdfc] py-2.5 pl-9 pr-10 text-xs text-[#17231f] transition placeholder:text-[#9bb0a5] focus:border-[#174e3b] focus:bg-white focus:outline-none"
              />
              <button
                type="button"
                onClick={() => setShowPassword(!showPassword)}
                className="absolute inset-y-0 right-0 flex items-center pr-3 text-[#87968e] hover:text-[#293931]"
                aria-label={showPassword ? 'Hide password' : 'Show password'}
              >
                {showPassword ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
              </button>
            </div>
          </div>

          <button
            type="submit"
            disabled={submitting}
            className="mt-2 flex w-full items-center justify-center gap-2 rounded-xl bg-[#174e3b] py-3 text-xs font-bold text-white shadow-sm transition hover:bg-[#103c2d] active:scale-[0.99] disabled:cursor-wait disabled:opacity-70"
          >
            <span>{submitting ? 'Checking securely…' : tab === 'signin' ? 'Sign In to NyayaBot' : 'Create My Account'}</span>
            {submitting ? <LoaderCircle className="size-3.5 animate-spin" /> : <ArrowRight className="size-3.5" />}
          </button>
        </form>

        <p className="mt-5 rounded-xl border border-[#dce5df] bg-[#f5f8f6] px-3.5 py-2.5 text-center text-[11px] leading-5 text-[#68766f]">
          Guest case storage is temporarily disabled so no private cases can be shared between visitors.
        </p>

        {/* Security badge */}
        <div className="mt-5 flex items-center justify-center gap-1.5 text-[11px] text-[#6b7872]">
          <ShieldCheck className="size-3.5 text-[#2f755b]" />
          <span>Secure server session · Passwords stay out of browser storage</span>
        </div>
      </div>
    </div>
  );
}
