import { API_BASE_URL, ApiError, apiErrorMessage, apiFetch } from '@/lib/api';

export interface NyayaUser {
  id: string;
  name: string;
  email: string;
  phone?: string;
  city?: string;
  state?: string;
  createdAt: string;
}

interface BackendUser {
  id: string;
  full_name: string;
  email: string;
  phone?: string;
  city?: string;
  state?: string;
  created_at: string;
}

function toNyayaUser(user: BackendUser): NyayaUser {
  return {
    id: user.id,
    name: user.full_name,
    email: user.email,
    phone: user.phone,
    city: user.city,
    state: user.state,
    createdAt: user.created_at,
  };
}

async function authRequest(path: string, init?: RequestInit): Promise<NyayaUser> {
  const response = await apiFetch(`${API_BASE_URL}/auth/${path}`, init);
  if (!response.ok) {
    throw new ApiError(await apiErrorMessage(response, 'Authentication failed.'), response.status);
  }
  return toNyayaUser(await response.json());
}

export function fetchCurrentUser(): Promise<NyayaUser> {
  return authRequest('me');
}

export function loginAccount(email: string, password: string): Promise<NyayaUser> {
  return authRequest('login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  });
}

export function signupAccount(fullName: string, email: string, password: string): Promise<NyayaUser> {
  return authRequest('signup', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ full_name: fullName, email, password }),
  });
}

export async function logoutAccount(): Promise<void> {
  const response = await apiFetch(`${API_BASE_URL}/auth/logout`, { method: 'POST' });
  if (!response.ok) {
    throw new ApiError(await apiErrorMessage(response, 'Could not log out.'), response.status);
  }
}
