// Dev-server helper for `npm run dev:web` (the HUD in a plain browser tab). Kept as plain JS so the HUD's
// TypeScript check doesn't need Node's type definitions. See vite.config.ts and src/lib/daemonAuth.ts.
import { readFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';

/** Orion's per-start API key (apps/daemon/api_token.py writes it, owner-only). '' when Orion isn't running. */
export function daemonToken() {
  const env = process.env;
  const home = os.homedir();
  const files = [env.ORION_TOKEN_FILE];
  if (process.platform !== 'win32') {
    files.push(path.join(env.XDG_RUNTIME_DIR || `/run/user/${process.getuid ? process.getuid() : ''}`, 'orion', 'daemon.token'));
  }
  files.push(path.join(env.ORION_DATA_DIR ? env.ORION_DATA_DIR.replace(/^~/, home) : path.join(home, '.orion'), 'daemon.token'));
  for (const f of files) {
    if (!f) continue;
    try {
      const t = readFileSync(f, 'utf8').trim();
      if (t) return t;
    } catch {
      /* not there */
    }
  }
  return '';
}

/**
 * The page in a plain browser has no Tauri bridge to read the daemon's key, so the dev server hands it to its own
 * page only. Refused for any other site: the Host must be this dev server by name (no DNS rebinding), and a
 * cross-site Origin / Sec-Fetch-Site is turned away. No CORS headers are ever sent. Dev only (apply: 'serve').
 */
export function daemonTokenForDevPage(port) {
  const hosts = new Set([`localhost:${port}`, `127.0.0.1:${port}`]);
  const origins = new Set([`http://localhost:${port}`, `http://127.0.0.1:${port}`]);
  return {
    name: 'orion-daemon-token',
    apply: 'serve',
    configureServer(server) {
      server.middlewares.use('/__orion/daemon-token', (req, res) => {
        const site = String(req.headers['sec-fetch-site'] || 'same-origin');
        const origin = req.headers.origin;
        if (!hosts.has(String(req.headers.host || '')) || site !== 'same-origin' || (origin && !origins.has(origin))) {
          res.statusCode = 403;
          res.end();
          return;
        }
        const t = daemonToken();
        res.statusCode = t ? 200 : 404;
        res.setHeader('Cache-Control', 'no-store');
        res.setHeader('Content-Type', 'text/plain');
        res.end(t);
      });
    },
  };
}
