// Secret Shield built-in scanner (used when gitleaks isn't installed, and for key-file checks).
// Patterns come from apps/daemon/secret_shield_rules.json, the same file the daemon uses (secret_shield.py),
// so the hook and "Orion, is anything secret in my repos?" catch the same things.
// The secret itself is never printed: findings carry the file, line, kind and a redacted preview.
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { readFileSync, existsSync, statSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
export const RULES_PATH = path.join(ROOT, 'apps', 'daemon', 'secret_shield_rules.json');
export const REDACTED = '[redacted]';
const MAX_FILE_BYTES = 1_000_000;

// Files that hold keys by design: never opened, and committing one is blocked by name alone.
const KEY_FILE_RE =
  /(^|\/)(\.env(\.(?!example$|sample$|template$)[\w.-]+)?|\.orion-signing\/.*|id_(rsa|dsa|ecdsa|ed25519)|[^/]+\.(key|p12|pfx|keystore|jks))$/i;

export function loadRules(file = RULES_PATH) {
  const data = JSON.parse(readFileSync(file, 'utf8'));
  const flags = (f) => (f && f.includes('i') ? 'gdi' : 'gd'); // d: exact group positions
  return {
    rules: data.rules.map((r) => ({
      id: r.id, kind: r.kind, label: r.label, regex: new RegExp(r.regex, flags(r.flags)),
      group: r.group ?? 0, entropy: r.entropy ?? 0, needsDigitAndLetter: !!r.needsDigitAndLetter,
    })),
    allow: (data.allow ?? []).map((a) => ({
      path: a.path ? new RegExp(a.path) : null,
      secret: a.secret ? new RegExp(a.secret, a.flags && a.flags.includes('i') ? 'i' : '') : null,
    })),
    skipPaths: (data.skipPaths ?? []).map((p) => new RegExp(p)),
    placeholders: new Set((data.placeholders ?? []).map((p) => p.toLowerCase())),
    marker: data.inlineAllowMarker ?? 'gitleaks:allow',
    guides: data.guides ?? {},
  };
}

export function shannon(s) {
  if (!s) return 0;
  const counts = {};
  for (const c of s) counts[c] = (counts[c] ?? 0) + 1;
  return -Object.values(counts).reduce((t, n) => t + (n / s.length) * Math.log2(n / s.length), 0);
}

export function fingerprint(ruleId, secret) {
  return createHash('sha256').update(`orion-secret-shield:${ruleId}:${secret}`).digest('hex').slice(0, 12);
}

function placeholder(secret, rules) {
  const s = secret.trim().replace(/^["']+|["']+$/g, '');
  const low = s.toLowerCase();
  if (rules.placeholders.has(low) || /^(x+|\*+|\.+|_+|-+|0+)$/.test(low)) return true;
  if (/^(\{[^{}]*\}|%s|%\([^)]*\)s)$/.test(s)) return true; // template fields: {pw}, %s, %(pw)s
  return ['${', '{{', '%(', '<', '>', '$('].some((t) => s.includes(t)) || low.startsWith('process.env') || low.startsWith('os.environ');
}

export const norm = (p) => p.replace(/\\/g, '/');
export const isKeyFile = (p) => KEY_FILE_RE.test(norm(p));
export const skipPath = (p, rules) => rules.skipPaths.some((r) => r.test(norm(p)));

export function scanLine(line, file, lineNo, rules) {
  if (rules.marker && line.includes(rules.marker)) return [];
  if (line.length > 4000) line = line.slice(0, 4000);
  const p = norm(file);
  const found = [];
  const taken = [];
  for (const rule of rules.rules) {
    rule.regex.lastIndex = 0;
    for (const m of line.matchAll(rule.regex)) {
      const g = rule.group < m.length ? rule.group : 0;
      const secret = m[g];
      if (!secret) continue;
      const span = m.indices[g];
      if (taken.some(([a, b]) => a < span[1] && span[0] < b)) continue;
      if (placeholder(secret, rules)) continue;
      if (rule.entropy && shannon(secret) < rule.entropy) continue;
      if (rule.needsDigitAndLetter && !(/\d/.test(secret) && /[A-Za-z]/.test(secret))) continue;
      if (rules.allow.some((a) => (!a.path || a.path.test(p)) && (!a.secret || a.secret.test(secret)))) continue;
      taken.push(span);
      found.push({ rule, secret, span });
    }
  }
  if (!found.length) return [];
  let preview = line;
  for (const { span } of [...found].sort((x, y) => y.span[0] - x.span[0])) {
    preview = preview.slice(0, span[0]) + REDACTED + preview.slice(span[1]);
  }
  preview = preview.trim();
  if (preview.length > 160) preview = preview.slice(0, 157) + '...';
  return found.map(({ rule, secret }) => ({
    rule: rule.id, kind: rule.kind, what: rule.label, file: p, line: lineNo, preview,
    fingerprint: fingerprint(rule.id, secret),
  }));
}

export function scanText(text, file, rules) {
  if (skipPath(file, rules)) return [];
  const out = [];
  text.split(/\r?\n/).forEach((l, i) => out.push(...scanLine(l, file, i + 1, rules)));
  return out;
}

export function keyFileFinding(file, where, commit = null) {
  return {
    rule: 'key-file-tracked', kind: 'key_file', what: 'a file that holds keys (it should never be in git)',
    file: norm(file), line: 0, preview: '(file not opened)', where, commit, fingerprint: fingerprint('key-file', file),
  };
}

export function git(root, args, opts = {}) {
  const r = spawnSync('git', ['-C', root, '-c', 'core.quotepath=off', ...args], {
    encoding: 'utf8', maxBuffer: 1024 * 1024 * 512, env: { ...process.env, GIT_TERMINAL_PROMPT: '0' }, ...opts,
  });
  return { code: r.status ?? 1, out: r.stdout ?? '', err: r.stderr ?? '' };
}

// Parse `git diff/log -p -U0` output: findings on added lines, with their line numbers in the new file.
export function scanPatch(patch, rules, where) {
  const findings = new Map();
  let commit = null;
  let file = '';
  let lineNo = 0;
  for (const line of patch.split('\n')) {
    if (line.startsWith('\0commit ')) {
      commit = line.slice(8).trim();
      file = '';
    } else if (line.startsWith('+++ ')) {
      file = line.startsWith('+++ b/') ? line.slice(6) : '';
      if (file && isKeyFile(file)) {
        const f = keyFileFinding(file, where, commit && commit.slice(0, 10));
        findings.set(f.fingerprint + file, f);
        file = '';
      } else if (file && skipPath(file, rules)) file = '';
    } else if (line.startsWith('@@')) {
      const m = /^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(line);
      lineNo = m ? Number(m[1]) : 0;
    } else if (line.startsWith('+') && file) {
      for (const f of scanLine(line.slice(1).replace(/\r$/, ''), file, lineNo, rules)) {
        f.where = where;
        f.commit = commit ? commit.slice(0, 10) : null;
        findings.set(f.fingerprint + file, f); // git log is newest first: the oldest commit wins
      }
      lineNo += 1;
    }
  }
  return [...findings.values()];
}

export function stagedFindings(root, rules = loadRules()) {
  const names = git(root, ['diff', '--cached', '--name-only', '-z', '--diff-filter=ACMR']).out.split('\0').filter(Boolean);
  const keyFiles = names.filter(isKeyFile).map((f) => keyFileFinding(f, 'staged'));
  const patch = git(root, ['diff', '--cached', '-U0', '--no-color', '--no-ext-diff', '--no-renames', '--diff-filter=ACMR']);
  return [...keyFiles, ...scanPatch(patch.out, rules, 'staged').filter((f) => f.kind !== 'key_file')];
}

export function historyFindings(root, rules = loadRules(), maxCommits = null) {
  const args = ['log', '--all', '-p', '-U0', '--no-color', '--no-ext-diff', '--no-renames', '--format=%x00commit %H'];
  if (maxCommits) args.splice(1, 0, `-n${maxCommits}`);
  const r = git(root, args);
  if (r.code !== 0) throw new Error(`git log failed: ${r.err.trim()}`);
  return scanPatch(r.out, rules, 'history');
}

export function treeFindings(root, rules = loadRules()) {
  const r = git(root, ['ls-files', '-z', '--cached', '--others', '--exclude-standard']);
  if (r.code !== 0) throw new Error(`${root} isn't a git repo.`);
  const tracked = new Set(git(root, ['ls-files', '-z']).out.split('\0'));
  const out = [];
  for (const rel of [...new Set(r.out.split('\0').filter(Boolean))].sort()) {
    if (isKeyFile(rel)) { out.push(keyFileFinding(rel, tracked.has(rel) ? 'current files' : 'not ignored')); continue; }
    if (skipPath(rel, rules)) continue;
    const f = path.join(root, rel);
    try {
      const st = statSync(f);
      if (!st.isFile() || st.size > MAX_FILE_BYTES) continue;
      const raw = readFileSync(f);
      if (raw.subarray(0, 4096).includes(0)) continue;
      for (const x of scanText(raw.toString('utf8'), rel, rules)) out.push({ ...x, where: 'current files', commit: null });
    } catch { /* unreadable: skip */ }
  }
  return out;
}

export function where(f) {
  const loc = f.line ? `${f.file}:${f.line}` : f.file;
  if (f.where === 'history') return `${loc} (past commit ${f.commit})`;
  if (f.where === 'staged') return loc;
  if (f.where === 'not ignored') return `${loc} (not in .gitignore)`;
  return `${loc} (current files)`;
}

export function guidesFor(kinds, rules) {
  const out = {};
  for (const k of kinds) {
    out[k] = k === 'key_file'
      ? ['Stop tracking the file: git rm --cached <file> (this keeps your copy) and make sure .gitignore lists it.',
         'If the repo was ever pushed, treat every key in that file as leaked and rotate each one using the steps for its service.']
      : rules.guides[k] ?? rules.guides.generic;
  }
  return out;
}

// Plain-words Markdown report with rotation steps for each kind of secret found.
export function formatReport(title, findings, rules, notes = []) {
  const lines = [`# ${title}`, '', ...notes, ''];
  if (!findings.length) {
    lines.push('Nothing secret found.');
    return lines.join('\n') + '\n';
  }
  lines.push(`Found ${findings.length} possible secret${findings.length === 1 ? '' : 's'}. The values are hidden; only where they are is shown.`, '');
  for (const f of findings) lines.push(`- ${f.what} at \`${where(f)}\`: \`${f.preview}\``);
  lines.push('', '## What to do', '',
    'Anything that was ever pushed to GitHub counts as leaked, so rotate it: make a new one and kill the old one.', '');
  const kinds = [...new Set(findings.map((f) => f.kind))].sort();
  for (const [kind, steps] of Object.entries(guidesFor(kinds, rules))) {
    const label = findings.find((f) => f.kind === kind)?.what ?? kind;
    lines.push(`### ${label[0].toUpperCase()}${label.slice(1)}`, '');
    steps.forEach((s, i) => lines.push(`${i + 1}. ${s}`));
    lines.push('');
  }
  lines.push('### Then', '');
  for (const s of rules.guides._after ?? []) lines.push(`- ${s}`);
  lines.push('- If a finding is a deliberate fake (for example in a test), add the comment `gitleaks:allow` at the end of that line.');
  return lines.join('\n') + '\n';
}

// gitleaks, if installed (ORION_GITLEAKS points at it, or it's on PATH).
export function gitleaksExe() {
  const env = (process.env.ORION_GITLEAKS || '').trim();
  if (env) return existsSync(env) ? env : null;
  const r = spawnSync('gitleaks', ['version'], { encoding: 'utf8', shell: false });
  return r.status === 0 ? 'gitleaks' : null;
}

export function exeOnPath(name, args = ['--version']) {
  const r = spawnSync(name, args, { encoding: 'utf8', shell: false });
  return r.status === 0;
}

// CLI: node scripts/secret-shield/scanner.mjs [--staged | --tree | --history] [repo]
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const args = process.argv.slice(2);
  const repo = path.resolve(args.find((a) => !a.startsWith('--')) ?? ROOT);
  const rules = loadRules();
  const mode = args.includes('--staged') ? 'staged' : args.includes('--history') ? 'history' : 'tree';
  const findings = mode === 'staged' ? stagedFindings(repo, rules) : mode === 'history' ? historyFindings(repo, rules) : treeFindings(repo, rules);
  if (args.includes('--json')) process.stdout.write(JSON.stringify(findings, null, 1) + '\n');
  else process.stdout.write(formatReport(`Secret Shield: ${mode} scan of ${path.basename(repo)}`, findings, rules));
  process.exit(findings.length ? 1 : 0);
}
