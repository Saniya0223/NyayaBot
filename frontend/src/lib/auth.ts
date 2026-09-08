export interface NyayaUser {
  name: string;
  email: string;
  phone?: string;
  city?: string;
  isGuest?: boolean;
}

const STORAGE_KEY = 'nyayabot_user';

export function getStoredUser(): NyayaUser | null {
  if (typeof window === 'undefined') return null;
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

export function setStoredUser(user: NyayaUser): void {
  if (typeof window === 'undefined') return;
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(user));
    window.dispatchEvent(new Event('nyayabot_auth_change'));
  } catch {}
}

export function removeStoredUser(): void {
  if (typeof window === 'undefined') return;
  try {
    localStorage.removeItem(STORAGE_KEY);
    window.dispatchEvent(new Event('nyayabot_auth_change'));
  } catch {}
}
