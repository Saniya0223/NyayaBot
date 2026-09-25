import { API_BASE_URL, ApiError, apiErrorMessage, apiFetch } from '@/lib/api';

export interface UserProfile {
  id: string;
  full_name: string;
  date_of_birth: string | null;
  email: string;
  phone: string | null;
  state: string | null;
  city: string | null;
  pin_code: string | null;
  full_address: string | null;
  preferred_language: 'english' | 'hindi' | 'hinglish' | null;
}

export interface SavedMemory {
  id: string;
  category: 'preference' | 'recurring' | 'explicit';
  text: string;
  created_at: string;
}

export interface CaseHistoryItem {
  case_id: string;
  title: string;
  category: string;
  status: string;
  summary: string | null;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await apiFetch(`${API_BASE_URL}/profile${path}`, init);
  if (!response.ok) throw new ApiError(await apiErrorMessage(response, 'Could not update profile.'), response.status);
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const fetchProfile = () => request<UserProfile>('');
export const updateProfile = (profile: Omit<UserProfile, 'id' | 'email'>) => request<UserProfile>('', {
  method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(profile),
});
export const fetchMemories = () => request<SavedMemory[]>('/memories');
export const addMemory = (category: SavedMemory['category'], text: string) => request<SavedMemory>('/memories', {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ category, text }),
});
export const deleteMemory = (id: string) => request<void>(`/memories/${encodeURIComponent(id)}`, { method: 'DELETE' });
export const fetchCaseHistory = () => request<CaseHistoryItem[]>('/case-history');
