// The GitHub Pages preview answers every daemon route the HUD's windows call (src/demo/demoApi.ts).
// Run: node --test apps/desktop/tests/demo.test.ts
// The browser smoke test (every menu item opens with no console errors) is scripts/capture-screenshots.mjs --smoke.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const SRC = fileURLToPath(new URL('../src/', import.meta.url));
const read = (p: string) => readFileSync(join(SRC, p), 'utf8');

function files(dir: string): string[] {
  return readdirSync(join(SRC, dir)).flatMap((n) => {
    const p = join(dir, n);
    if (statSync(join(SRC, p)).isDirectory()) return files(p);
    return /\.tsx?$/.test(n) ? [p] : [];
  });
}

/** Every route prefix the HUD calls on the daemon, e.g. "/tests", "/privacy", "/self". */
function calledPrefixes(): Set<string> {
  const out = new Set<string>();
  for (const f of [...files('components'), ...files('lib')]) {
    const src = read(f);
    for (const m of src.matchAll(/ORION_HTTP_URL\}(\/[a-z][\w-]*)/g)) out.add(m[1]);
    for (const m of src.matchAll(/(?:call|api|post)(?:<[^>]*>)?\(\s*[`'"](\/[a-z][\w-]*)/g)) out.add(m[1]);
    for (const m of src.matchAll(/useDaemonPoll(?:<[^>]*>)?\(\s*[`'"](\/[a-z][\w-]*)/g)) out.add(m[1]);
  }
  return out;
}

const demo = read('demo/demoApi.ts');
const listed = [...(demo.match(/DEMO_ROUTES = \[([\s\S]*?)\] as const/)?.[1] ?? '').matchAll(/'(\/[\w/-]+)'/g)].map((m) => m[1]);
// Routes that only exist on the desktop (Tauri) or the daemon's own pages, never called by a window in the preview.
const DESKTOP_ONLY = new Set(['/daemon-token', '/linux', '/hardware']);

test('the preview lists a stub for every route a window calls', () => {
  const missing = [...calledPrefixes()].filter((p) => !DESKTOP_ONLY.has(p) && !listed.some((l) => l === p || l.startsWith(`${p}/`)));
  assert.deepEqual(missing, [], `add these to demo/demoApi.ts: ${missing.join(', ')}`);
});

test('each listed route has a handler', () => {
  // each prefix appears again after the list: in a handler (`p === '/x'`, `p.startsWith('/x')` or a /^\/x/ pattern),
  // or in demoDaemon.ts for the two routes it answers itself
  const daemon = read('demo/demoDaemon.ts');
  const body = demo.slice(demo.indexOf('] as const'));
  for (const r of listed) {
    const esc = r.replace(/\//g, '\\/');
    assert.ok(body.includes(`'${r}`) || body.includes(esc) || daemon.includes(r), r);
  }
});

test('every window in the menu has its routes stubbed', () => {
  const need = ['/tests', '/missions', '/projects', '/phones', '/privacy', '/self', '/presence', '/guide', '/browser-skills', '/desktop-skills', '/scout'];
  for (const r of need) assert.ok(listed.includes(r), r);
});

test('the preview sends nothing out', () => {
  assert.ok(!/https?:\/\/(?!www\.(youtube|tiktok|curseforge)\.com|orionopenwow\.github\.io)[\w.-]+\.[a-z]{2,}\//.test(demo.replace(/http:\/\/192\.168\.1\.20:9121/g, '')), 'no outside URLs in the stub');
  assert.ok(!/fetch\(\s*['"`]https?:/.test(demo), 'the stub never fetches the internet');
});
