/** Focus: the few things that matter right now (daemon: focus.py). Uses the daemonAuth fetch wrapper. */
import { ORION_HTTP_URL } from './assets';
import type { FocusAction, FocusView } from './focusView';

export * from './focusView';

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${ORION_HTTP_URL}${path}`, init);
  const data = (await res.json().catch(() => ({}))) as T & { error?: string };
  if (!res.ok) throw new Error(data.error || `Orion answered ${res.status}`);
  return data;
}

const post = (body: unknown): RequestInit => ({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

export const focusApi = {
  view: () => call<FocusView>('/focus'),
  feedback: (id: string, action: FocusAction) => call<{ ok: boolean }>('/focus/feedback', post({ id, action })),
  /** A draft only: it is never sent. Sending stays a tap in Messages. */
  draft: (id: string) => call<{ draft: string; thread: string }>('/focus/draft', post({ id })),
};

