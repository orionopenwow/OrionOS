/**
 * Orion's brain (the daemon on localhost:9120) only answers programs that hold its per-start key (review B1).
 *
 *  - Desktop app: the Tauri command `daemon_token` reads the key from its owner-only file.
 *  - Plain browser (`npm run dev:web`): the Vite dev server hands it out on its own origin only (vite.config.ts).
 *  - Guest demo: there is no daemon, so nothing is needed.
 *
 * installDaemonAuth() wraps window.fetch so every call to the daemon carries `Authorization: Bearer <key>`, and
 * retries once with a fresh key after a 401 (the key changes whenever the daemon restarts). The WebSocket can't
 * carry headers, so its URL gets `?token=` (withToken).
 */
import { isTauri } from './tauri';

const DAEMON_PREFIXES = ['http://localhost:9120', 'http://127.0.0.1:9120'];
let cached: string | null = null;
let inflight: Promise<string | null> | null = null;

async function readToken(): Promise<string | null> {
  if (isTauri()) {
    const { invoke } = await import('@tauri-apps/api/core');
    return (await invoke<string | null>('daemon_token')) ?? null;
  }
  try {
    const r = await fetch('/__orion/daemon-token', { cache: 'no-store' });
    return r.ok ? (await r.text()).trim() || null : null;
  } catch {
    return null;
  }
}

/** The daemon's key (cached; `fresh` re-reads it, e.g. after the daemon restarted). */
export async function daemonToken(fresh = false): Promise<string | null> {
  if (!fresh && cached) return cached;
  if (!inflight) {
    inflight = readToken()
      .catch(() => null)
      .then((t) => {
        cached = t;
        inflight = null;
        return t;
      });
  }
  return inflight;
}

/** ws://localhost:9120 -> ws://localhost:9120/?token=... */
export async function withToken(url: string, fresh = false): Promise<string> {
  const t = await daemonToken(fresh);
  if (!t) return url;
  const u = new URL(url);
  u.searchParams.set('token', t);
  return u.toString();
}

function isDaemonUrl(url: string): boolean {
  return DAEMON_PREFIXES.some((p) => url === p || url.startsWith(`${p}/`) || url.startsWith(`${p}?`));
}

function resendable(init?: RequestInit): boolean {
  const b = init?.body;
  return b == null || typeof b === 'string' || b instanceof Blob || b instanceof FormData || b instanceof URLSearchParams
    || b instanceof ArrayBuffer || ArrayBuffer.isView(b);
}

/** Make every fetch to the daemon carry its key. Call once at start-up (not in the guest demo). */
export function installDaemonAuth(): void {
  const real = window.fetch.bind(window);
  const send = async (input: RequestInfo | URL, init: RequestInit | undefined, fresh: boolean) => {
    const headers = new Headers(init?.headers ?? (input instanceof Request ? input.headers : undefined));
    const t = await daemonToken(fresh);
    if (t) headers.set('Authorization', `Bearer ${t}`);
    return real(input, { ...init, headers });
  };
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
    if (!isDaemonUrl(url)) return real(input, init);
    const res = await send(input, init, false);
    if (res.status !== 401 || !resendable(init) || input instanceof Request) return res;
    return send(input, init, true);
  };
}
