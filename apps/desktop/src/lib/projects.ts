/** Calls for "Start a project with Claude" (daemon: apps/daemon/projects.py). Mission progress uses lib/missions.ts. */
import { ORION_HTTP_URL } from './assets';
import type { Mission } from './missions';

export interface Starter {
  id: 'web-game' | 'website' | 'python-tool' | 'empty';
  label: string;
  description: string;
  run: string;
}

export interface Project {
  name: string;
  path: string;
  starter: Starter['id'];
  idea: string;
  created: number;
  missions: string[];
  exists: boolean;
  playable: boolean;
  latest: Mission | null;
}

export interface ProjectsSnapshot {
  root: string;
  starters: Starter[];
  projects: Project[];
}

export interface Started {
  project: Project;
  mission: Mission | null;
  message: string;
}

async function call<T>(path: string, body?: unknown, post = body !== undefined): Promise<T> {
  const init: RequestInit | undefined = post
    ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body ?? {}) }
    : undefined;
  const res = await fetch(`${ORION_HTTP_URL}${path}`, init);
  const data = (await res.json().catch(() => ({}))) as T & { error?: string; message?: string };
  if (!res.ok) throw new Error(data.error || data.message || `Orion answered ${res.status}`);
  return data;
}

const at = (name: string) => `/projects/${encodeURIComponent(name)}`;

export const projectsApi = {
  list: () => call<ProjectsSnapshot>('/projects'),
  create: (p: { name: string; starter: Starter['id']; idea: string; agent?: string | null; budget_usd?: number | null }) =>
    call<Started>('/projects', p),
  change: (name: string, text: string) => call<Started>(`${at(name)}/change`, { text }),
  open: (name: string) => call<{ message: string }>(`${at(name)}/open`, {}, true),
  play: (name: string) => call<{ url: string; message: string }>(`${at(name)}/play`, {}, true),
};

/** The same cleaning the daemon does, so the wizard can show the real folder name as you type. */
export function folderName(name: string): string {
  return name.split(/\s+/).filter(Boolean).join(' ').replace(/[^A-Za-z0-9 _\-.]/g, '').replace(/^[ .]+|[ .]+$/g, '').slice(0, 60).replace(/[ .]+$/, '');
}
