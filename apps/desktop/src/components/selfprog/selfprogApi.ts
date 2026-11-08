/**
 * Living Orion (after-1.0 plan §7, Phase 8): talks to the daemon's /self/changes routes (apps/daemon/self_program.py).
 * Every call carries the daemon's key through lib/daemonAuth.ts.
 */
import { daemonToken } from '../../lib/daemonAuth';
import { ORION_HTTP_URL } from '../../lib/assets';

export interface SelfCheck {
  name: string;
  label: string;
  ok: boolean;
  skipped: boolean;
  detail: string;
}

export interface SelfChange {
  id: string;
  request: string;
  summary: string;
  reason: string;
  kind: 'ui_code' | 'daemon_code' | 'protected_core';
  status: 'building' | 'ready' | 'failed' | 'applied' | 'rejected' | 'rolled_back';
  mode: 'checkout' | 'layer';
  files: { path: string; action: string; additions: number; deletions: number }[];
  checks: SelfCheck[];
  screens: { before?: boolean; after?: boolean };
  protected_words?: string;
  touches_ui: boolean;
  touches_daemon: boolean;
  preview_dir?: string;
  decision?: string;
  error?: string | null;
  diff?: string;
}

export interface SelfSwap {
  id: string;
  change_id: string;
  summary: string;
  status: 'probation' | 'kept' | 'rolled_back';
  at: number;
  touches_ui: boolean;
  touches_daemon: boolean;
  rollback_reason?: string;
  rolled_back_at?: number;
}

export interface SelfState {
  mode: 'checkout' | 'layer';
  changes: SelfChange[];
  probation: { swap_id: string; deadline: number; needs_ui: boolean; ui_ok: boolean } | null;
  swaps: SelfSwap[];
}

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const t = await daemonToken();
  if (t) headers.set('Authorization', `Bearer ${t}`);
  if (init.body) headers.set('Content-Type', 'application/json');
  const r = await fetch(`${ORION_HTTP_URL}${path}`, { ...init, headers });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error((data as { message?: string }).message || `Orion said no (${r.status})`);
  return data as T;
}

export const selfprog = {
  state: () => call<SelfState>('/self/changes'),
  change: (id: string) => call<{ change: SelfChange }>(`/self/changes/${id}`).then((d) => d.change),
  approve: (id: string) => call<{ ok: boolean }>(`/self/changes/${id}/approve`, { method: 'POST' }),
  reject: (id: string) => call<{ ok: boolean }>(`/self/changes/${id}/reject`, { method: 'POST' }),
  askPhone: (id: string) => call<{ message: string }>(`/self/changes/${id}/ask-signature`, { method: 'POST' }),
  upstream: (id: string) => call<{ ok: boolean; message: string }>(`/self/changes/${id}/upstream`, { method: 'POST' }),
  preview: (id: string) => call<{ url: string | null }>(`/self/changes/${id}/preview`),
  undo: () => call<{ message: string }>('/self/undo', { method: 'POST', body: '{}' }),
  uiOk: () => call<{ ok: boolean }>('/self/probation/ui-ok', { method: 'POST' }),
  /** A screenshot as an object URL (an <img> can't send the daemon's key). */
  async screen(id: string, which: 'before' | 'after'): Promise<string | null> {
    const t = await daemonToken();
    const r = await fetch(`${ORION_HTTP_URL}/self/changes/${id}/screens/${which}`, {
      headers: t ? { Authorization: `Bearer ${t}` } : {},
    });
    return r.ok ? URL.createObjectURL(await r.blob()) : null;
  },
};
