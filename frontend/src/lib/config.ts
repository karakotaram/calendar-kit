/**
 * Application configuration
 *
 * The API URL is `api.base_url` in calendar.config.yaml. VITE_API_BASE_URL
 * overrides it, which is how you point a local dev server at a local API
 * (`VITE_API_BASE_URL=http://localhost:8199 npm run dev`).
 */
import { SITE } from '@/lib/site';

export const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || SITE.api.baseUrl).replace(/\/+$/, '');

/** Fetch JSON from the API, failing clearly when no API is configured. */
export async function fetchApi<T>(pathAndQuery: string): Promise<T> {
  if (!API_BASE_URL) {
    throw new Error('No API configured: set api.base_url in calendar.config.yaml (or VITE_API_BASE_URL).');
  }
  const response = await fetch(`${API_BASE_URL}${pathAndQuery}`);
  if (!response.ok) {
    throw new Error(`API returned ${response.status}`);
  }
  return response.json() as Promise<T>;
}
