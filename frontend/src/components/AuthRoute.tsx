'use client';

import { useEffect } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useAuth } from '@/components/AuthProvider';
import LoginGate from '@/components/LoginGate';

function postAuthPath() {
  const requestedNext = new URLSearchParams(window.location.search).get('next');
  return requestedNext && /^\/\?case=[0-9a-f-]{36}$/i.test(requestedNext) ? requestedNext : '/';
}

export default function AuthRoute({ mode }: { mode: 'signin' | 'signup' }) {
  const { user, loading, setUser } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!loading && user) router.replace(postAuthPath());
  }, [loading, router, user]);

  if (loading || user) {
    return <div className="grid min-h-[60vh] place-items-center px-4 text-sm text-[#817b72]">Opening NyayaBot…</div>;
  }

  return (
    <div>
      <LoginGate
        key={mode}
        initialTab={mode}
        onLoginSuccess={setUser}
      />
      <p className="-mt-7 pb-10 text-center text-xs text-[#817b72]"><Link href="/" className="font-semibold text-[#171717] hover:underline">Back to home</Link></p>
    </div>
  );
}
