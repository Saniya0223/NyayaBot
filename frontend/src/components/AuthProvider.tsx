'use client';

import { createContext, useContext, useEffect, useMemo, useState } from 'react';
import { ApiError } from '@/lib/api';
import { fetchCurrentUser, logoutAccount, NyayaUser } from '@/lib/auth';

interface AuthContextValue {
  user: NyayaUser | null;
  loading: boolean;
  setUser: (user: NyayaUser) => void;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export default function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUserState] = useState<NyayaUser | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    fetchCurrentUser()
      .then((currentUser) => { if (active) setUserState(currentUser); })
      .catch((error: unknown) => {
        if (active && !(error instanceof ApiError && error.status === 401)) {
          const detail = error instanceof Error ? error.message : 'Unknown connection error';
          console.warn(`[NyayaBot] Could not verify the backend session: ${detail}`);
        }
      })
      .finally(() => { if (active) setLoading(false); });

    function handleExpiredSession() {
      setUserState(null);
      setLoading(false);
    }
    window.addEventListener('nyayabot_auth_expired', handleExpiredSession);
    return () => {
      active = false;
      window.removeEventListener('nyayabot_auth_expired', handleExpiredSession);
    };
  }, []);

  const value = useMemo<AuthContextValue>(() => ({
    user,
    loading,
    setUser: setUserState,
    logout: async () => {
      await logoutAccount();
      setUserState(null);
    },
  }), [loading, user]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) throw new Error('useAuth must be used inside AuthProvider');
  return context;
}
