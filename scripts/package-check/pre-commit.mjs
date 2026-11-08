// Package Reality Check pre-commit check (run by .githooks/pre-commit after Secret Shield, for every commit).
// When a commit adds dependencies (package.json, lockfiles, requirements*.txt, pyproject.toml, Cargo.toml ...),
// each new package is checked by Orion (apps/daemon/package_check.py): does it exist, how old is it, how used,
// who publishes it, and is its name a look-alike of a popular package?
// Orion running: the daemon checks the registries and puts a card in the Queue for anything suspicious.
// Orion not running: only the look-alike test runs here, against apps/daemon/package_top_lists.json.
// Exit 0 = commit goes ahead; exit 1 = blocked, with the reason in plain words.
import { spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import os from 'node:os';

/** Orion's per-start API key (apps/daemon/api_token.py writes it, owner-only; review B1). '' when Orion is off. */
function daemonToken() {
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
const authHeaders = (extra = {}) => {
  const t = daemonToken();
  return t ? { ...extra, Authorization: `Bearer ${t}` } : { ...extra };
};

function git(cwd, args) {
  const r = spawnSync('git', args, { cwd, encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 });
  return { code: r.status ?? 1, out: r.stdout || '' };
}

const top = git(process.cwd(), ['rev-parse', '--show-toplevel']);
if (top.code !== 0) process.exit(0);
const root = top.out.trim();

function manifestKind(p) {
  const n = path.posix.basename(p.replace(/\\/g, '/')).toLowerCase();
  if (n === 'package.json') return 'package.json';
  if (n === 'package-lock.json' || n === 'npm-shrinkwrap.json') return 'package-lock';
  if (n === 'yarn.lock') return 'yarn.lock';
  if (n === 'pnpm-lock.yaml') return 'pnpm-lock';
  if (/^requirements[\w.-]*\.(txt|in)$/.test(n) || n === 'constraints.txt') return 'requirements';
  if (n === 'pyproject.toml') return 'pyproject';
  if (n === 'pipfile') return 'pipfile';
  if (['poetry.lock', 'uv.lock', 'pdm.lock'].includes(n)) return 'pylock';
  if (n === 'cargo.toml') return 'cargo.toml';
  if (n === 'cargo.lock') return 'cargo.lock';
  return null;
}

const staged = git(root, ['diff', '--cached', '--name-only', '-z', '--diff-filter=ACMR']).out.split('\0').filter(Boolean);
const changed = staged.filter((f) => manifestKind(f));
if (!changed.length) process.exit(0);

const hasHead = git(root, ['rev-parse', '--verify', '-q', 'HEAD']).code === 0;
const files = changed.map((p) => ({
  path: p,
  old: hasHead ? git(root, ['show', `HEAD:${p}`]).out : '',
  new: git(root, ['show', `:${p}`]).out,
}));

async function askOrion() {
  const port = process.env.ORION_PORT || '9120';
  const base = (process.env.ORION_DAEMON_URL || `http://127.0.0.1:${port}`).replace(/\/$/, '');
  try {
    const res = await fetch(`${base}/package-check/manifests`, {
      method: 'POST', headers: authHeaders({ 'content-type': 'application/json' }),
      body: JSON.stringify({ files, origin: `a commit in ${path.basename(root)}` }),
      signal: AbortSignal.timeout(120000),
    });
    if (res.status === 404) return null; // an Orion without Package Reality Check
    return await res.json();
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------- offline look-alike test (direct manifests only)
const norm = (eco, n) => (eco === 'pypi' ? n.replace(/[-_.]+/g, '-').toLowerCase()
  : eco === 'crates' ? n.toLowerCase().replace(/_/g, '-') : n.toLowerCase());

function distance(a, b, limit) {
  if (a === b) return 0;
  if (Math.abs(a.length - b.length) > limit) return limit + 1;
  let prev2 = [];
  let prev = Array.from({ length: b.length + 1 }, (_, j) => j);
  for (let i = 1; i <= a.length; i++) {
    const cur = [i];
    for (let j = 1; j <= b.length; j++) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost);
      if (i > 1 && j > 1 && a[i - 1] === b[j - 2] && a[i - 2] === b[j - 1]) cur[j] = Math.min(cur[j], prev2[j - 2] + 1);
    }
    if (Math.min(...cur) > limit) return limit + 1;
    prev2 = prev; prev = cur;
  }
  return prev[b.length];
}

function names(kind, text) {
  const out = [];
  if (!text) return out;
  if (kind === 'package.json') {
    try {
      const d = JSON.parse(text);
      for (const s of ['dependencies', 'devDependencies', 'optionalDependencies', 'peerDependencies']) {
        for (const [n, v] of Object.entries(d[s] || {})) {
          const real = String(v).startsWith('npm:') ? String(v).slice(4).replace(/(.)@.*$/, '$1') : n;
          out.push(['npm', real]);
        }
      }
    } catch { /* not JSON yet */ }
  } else if (kind === 'requirements') {
    for (const line of text.split(/\r?\n/)) {
      const m = /^\s*([A-Za-z0-9][A-Za-z0-9._-]*)/.exec(line.split(' #')[0]);
      if (m && !line.trim().startsWith('-')) out.push(['pypi', m[1]]);
    }
  } else if (kind === 'cargo.toml') {
    let inDeps = false;
    for (const line of text.split(/\r?\n/)) {
      const h = /^\s*\[([^\]]+)\]/.exec(line);
      if (h) { inDeps = /(^|\.)(dev-|build-)?dependencies$/.test(h[1].trim()); continue; }
      const m = inDeps && /^\s*([A-Za-z0-9_-]+)\s*=/.exec(line);
      if (m) {
        const pk = /package\s*=\s*"([^"]+)"/.exec(line);
        out.push(['crates', pk ? pk[1] : m[1]]);
      }
    }
  } else if (kind === 'pyproject') {
    const block = /dependencies\s*=\s*\[([\s\S]*?)\]/g;
    let m;
    while ((m = block.exec(text))) {
      for (const q of m[1].matchAll(/["']\s*([A-Za-z0-9][A-Za-z0-9._-]*)/g)) out.push(['pypi', q[1]]);
    }
  }
  return out;
}

function offlineCheck() {
  let lists = {};
  try {
    lists = JSON.parse(readFileSync(path.join(root, 'apps', 'daemon', 'package_top_lists.json'), 'utf8')).lists || {};
  } catch { return []; }
  const sets = Object.fromEntries(Object.entries(lists).map(([e, l]) => [e, l.map((n) => norm(e, n))]));
  const bad = [];
  for (const f of files) {
    const kind = manifestKind(f.path);
    const before = new Set(names(kind, f.old).map(([e, n]) => `${e}:${norm(e, n)}`));
    for (const [eco, raw] of names(kind, f.new)) {
      const n = norm(eco, raw);
      const list = sets[eco] || [];
      if (before.has(`${eco}:${n}`) || n.length < 4 || list.includes(n)) continue;
      const squash = (s) => s.replace(/[-_.@/]/g, '');
      const twin = list.find((p) => squash(p) === squash(n) || distance(n, p, p.length < 9 ? 1 : 2) <= (p.length < 9 ? 1 : 2));
      if (twin) bad.push(`  ${raw} (${eco}) in ${f.path}: its name is very close to "${twin}", a popular package.`);
    }
  }
  return bad;
}

const answer = await askOrion();
if (answer && typeof answer.allowed === 'boolean') {
  if (answer.allowed) {
    if (answer.verdict === 'warn') process.stderr.write(`${answer.summary}\n`);
    process.exit(0);
  }
  process.stderr.write(['', String(answer.summary || 'Package Reality Check stopped this commit.'), '',
    'Approve the card in Orion\'s Queue if you trust these packages, then commit again.',
    'Otherwise fix the name in the file, stage it (git add <file>) and commit again.', ''].join('\n'));
  process.exit(1);
}
const bad = offlineCheck();
if (!bad.length) process.exit(0);
process.stderr.write(['', 'Package Reality Check stopped this commit: a new package looks like a copy of a popular one.',
  ...bad, '',
  'Attackers register names one letter off popular packages and wait for a typo.',
  'Orion isn\'t running, so only the names were checked. Fix the spelling, or start Orion and commit again',
  'to have the package checked properly (and approve it there if you trust it).', ''].join('\n'));
process.exit(1);
