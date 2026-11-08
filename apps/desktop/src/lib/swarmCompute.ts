/** Types and polling for the agent swarm robots and the compute meter (daemon: swarm_compute_routes.py). */
import { useCallback, useEffect, useRef, useState } from 'react';
import { ORION_HTTP_URL } from './assets';

export type SwarmState = 'working' | 'waiting' | 'stuck' | 'failed' | 'done';

export interface SwarmAgent {
  id: string;
  tool: string;
  project: string;
  name: string;
  cwd: string;
  state: SwarmState;
  status: string;
  reason: string;
  last_activity: number;
  started: number;
  events: Array<{ at: number | null; text: string }>;
  files: string[];
  controllable: boolean;
  run_id: string | null;
  paused: boolean;
  can_open: boolean;
  control_note: string;
  /** Mission Control (missions.py): set on robots that are missions; cost_usd is null for agents Orion didn't start. */
  mission_id?: string;
  cost_usd?: number | null;
  cost_estimated?: boolean;
  budget_usd?: number | null;
}

export interface SwarmSnapshot {
  agents: SwarmAgent[];
  at: number;
  watching: string[];
}

export interface ComputeModelRow {
  model: string;
  provider: string;
  local: boolean;
  calls: number;
  tokens_in: number;
  tokens_out: number;
  cost: number;
  price_in: number;
  price_out: number;
  price_known: boolean;
}

export interface ComputePeriod {
  cost: number;
  tokens_in: number;
  tokens_out: number;
  calls: number;
  models: ComputeModelRow[];
}

export interface ComputeSnapshot {
  today: ComputePeriod & { date: string };
  session: ComputePeriod & { since: number };
  history: Array<{ date: string; cost: number }>;
  tips: string[];
  note: string;
}

/** Polls a daemon GET route. Pauses while the window is hidden; returns null until the first answer. */
export function useDaemonPoll<T>(path: string, everyMs: number): [T | null, () => void] {
  const [data, setData] = useState<T | null>(null);
  const alive = useRef(true);
  const load = useCallback(async () => {
    if (typeof document !== 'undefined' && document.hidden) return;
    try {
      const res = await fetch(`${ORION_HTTP_URL}${path}`);
      if (!res.ok) return;
      const json = (await res.json()) as T;
      if (alive.current) setData(json);
    } catch {
      /* daemon offline: keep the last answer */
    }
  }, [path]);
  useEffect(() => {
    alive.current = true;
    void load();
    const t = window.setInterval(() => void load(), everyMs);
    return () => {
      alive.current = false;
      window.clearInterval(t);
    };
  }, [load, everyMs]);
  return [data, load];
}

export async function swarmAction(id: string, action: 'open' | 'pause' | 'resume' | 'stop'): Promise<{ ok: boolean; message: string }> {
  try {
    const res = await fetch(`${ORION_HTTP_URL}/agents/swarm/${encodeURIComponent(id)}/${action}`, { method: 'POST' });
    const body = (await res.json()) as { ok?: boolean; message?: string };
    return { ok: !!body.ok, message: body.message ?? '' };
  } catch {
    return { ok: false, message: 'Orion is not running.' };
  }
}

export function usd(x: number): string {
  if (x <= 0) return '$0.00';
  if (x < 0.01) return '<$0.01';
  return x < 100 ? `$${x.toFixed(2)}` : `$${Math.round(x).toLocaleString()}`;
}

export function tokens(n: number): string {
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(n < 10_000 ? 1 : 0)}k`;
  return `${(n / 1_000_000).toFixed(1)}M`;
}

export function ago(epochS: number, nowS = Date.now() / 1000): string {
  const s = Math.max(0, Math.round(nowS - epochS));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  return m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${m % 60} min`;
}
