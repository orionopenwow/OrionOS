/** Types and calls for Mission Control (daemon: missions.py / missions_routes.py). Uses the daemonAuth fetch wrapper. */
import { ORION_HTTP_URL } from './assets';

export type MissionState = 'queued' | 'running' | 'needs_you' | 'review' | 'done' | 'failed' | 'stopped';
export type MissionOp = 'pause' | 'resume' | 'stop' | 'approve_review' | 'reject';

export interface Verdict {
  verdict: string;
  reason?: string;
  reviewer?: string | null;
  notes?: string;
  failing_tests?: string[];
}

export interface Mission {
  id: string;
  title: string;
  description: string;
  repo: string;
  repo_name: string;
  agent: string;
  agent_label: string;
  model: string | null;
  task: string;
  why: string;
  state: MissionState;
  phase: string;
  paused: boolean;
  cost_usd: number;
  cost_estimated: boolean;
  cost_note: string;
  budget_usd: number;
  time_limit_min: number;
  active_s: number;
  created: number;
  started: number | null;
  finished: number | null;
  branch: string;
  summary: string;
  source: string;
  race_group: string | null;
  swarm_id: string | null;
  changed_files: string[];
  review: Verdict | null;
  checks: Verdict | null;
  merge: string | null;
}

export interface MissionDetail extends Mission {
  diffstat: string;
  diff: string;
  log_tail: string[];
}

export interface AgentInfo {
  key: string;
  label: string;
  installed: boolean;
  mode: 'headless' | 'watch';
  note: string;
}

export interface Template {
  id: string;
  label: string;
  title: string;
  description: string;
  task: string;
  repo?: string;
}

export interface MissionBoard {
  missions: Mission[];
  agents: AgentInfo[];
  templates: Template[];
  routing: { version: number; source: string; tasks: string[] };
}

export interface NewMission {
  title: string;
  description?: string;
  repo?: string;
  agent?: string;
  budget_usd?: number;
  time_limit_min?: number;
  race?: number;
  template?: string;
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${ORION_HTTP_URL}${path}`, init);
  const data = (await res.json().catch(() => ({}))) as T & { error?: string; message?: string };
  if (!res.ok) throw new Error(data.error || data.message || `Orion answered ${res.status}`);
  return data;
}

const json = (body: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});

export const missionsApi = {
  board: () => call<MissionBoard>('/missions'),
  detail: (id: string) => call<MissionDetail>(`/missions/${encodeURIComponent(id)}`),
  log: (id: string, since: number) =>
    call<{ lines: string[]; next: number; state: MissionState }>(`/missions/${encodeURIComponent(id)}/log?since=${since}`),
  create: (m: NewMission) => call<Mission & { race?: string[] }>('/missions', json(m)),
  act: (id: string, op: MissionOp) =>
    call<{ ok: boolean; message: string; mission: Mission }>(`/missions/${encodeURIComponent(id)}/${op}`, { method: 'POST' })
      .catch((e: Error) => ({ ok: false, message: e.message, mission: null as unknown as Mission })),
};

export const STATE_LABEL: Record<MissionState, string> = {
  queued: 'Queued',
  running: 'Running',
  needs_you: 'Needs you',
  review: 'Review',
  done: 'Done',
  failed: 'Failed',
  stopped: 'Stopped',
};

/** The five board columns from the plan; failed and stopped missions sit in Done with their own tag. */
export const COLUMNS: Array<{ id: string; label: string; states: MissionState[] }> = [
  { id: 'queued', label: 'Queued', states: ['queued'] },
  { id: 'running', label: 'Running', states: ['running'] },
  { id: 'needs_you', label: 'Needs you', states: ['needs_you'] },
  { id: 'review', label: 'Review', states: ['review'] },
  { id: 'done', label: 'Done', states: ['done', 'failed', 'stopped'] },
];
