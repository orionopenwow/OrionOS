// Secret Shield pre-commit check (run by .githooks/pre-commit for every commit, from you or an agent).
// Uses gitleaks with .gitleaks.toml when it's installed, otherwise the built-in scanner. Files that hold keys
// by design (.env, .orion-signing/, *.key ...) are blocked by name without being opened.
// Exit 0 = commit goes ahead; exit 1 = blocked, with the exact file:line and how to fix it.
import { spawnSync } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { git, gitleaksExe, isKeyFile, keyFileFinding, loadRules, norm, stagedFindings, REDACTED } from './scanner.mjs';

const top = git(process.cwd(), ['rev-parse', '--show-toplevel']);
if (top.code !== 0) process.exit(0);
const root = top.out.trim();
const rules = loadRules();

// gitleaks rule id -> the kind used for rotation guides (same mapping as secret_shield.py)
const KINDS = [['anthropic', 'anthropic'], ['openai', 'openai'], ['github', 'github'], ['gitlab', 'gitlab'],
  ['discord', 'discord'], ['telegram', 'telegram'], ['slack', 'slack'], ['stripe', 'stripe'], ['aws', 'aws'],
  ['gcp', 'google'], ['google', 'google'], ['huggingface', 'huggingface'], ['npm', 'npm'], ['private-key', 'private_key']];
const gitleaksKind = (id) => (KINDS.find(([p]) => id.toLowerCase().includes(p)) ?? [null, 'generic'])[1];

function runGitleaks(exe) {
  const tmp = mkdtempSync(path.join(os.tmpdir(), 'orion-shield-'));
  const report = path.join(tmp, 'report.json');
  const args = ['git', '--pre-commit', '--staged', '--redact', '--no-banner', '--log-level', 'error',
    '--exit-code', '1', '--report-format', 'json', '--report-path', report];
  const cfg = path.join(root, '.gitleaks.toml');
  if (existsSync(cfg)) args.push('--config', cfg);
  args.push(root);
  const r = spawnSync(exe, args, { cwd: root, encoding: 'utf8' });
  try {
    if (r.status !== 0 && r.status !== 1) return null; // gitleaks itself failed: use the built-in scanner
    const rows = existsSync(report) ? JSON.parse(readFileSync(report, 'utf8') || '[]') : [];
    return rows.map((x) => {
      const id = String(x.RuleID || 'secret');
      const kind = gitleaksKind(id);
      const label = kind === 'generic' ? `a secret (${id.replace(/-/g, ' ')})` : rules.rules.find((rr) => rr.kind === kind)?.label;
      return { rule: id, kind, what: label,
        file: norm(String(x.File || '')), line: x.StartLine || 0,
        preview: String(x.Match || '').replace(/REDACTED/g, REDACTED).slice(0, 160), where: 'staged' };
    });
  } catch {
    return null;
  } finally {
    rmSync(tmp, { recursive: true, force: true });
  }
}

const staged = git(root, ['diff', '--cached', '--name-only', '-z', '--diff-filter=ACMR']).out.split('\0').filter(Boolean);
let findings = staged.filter(isKeyFile).map((f) => keyFileFinding(f, 'staged'));
let engine = 'built-in scanner';
const exe = gitleaksExe();
const fromGitleaks = exe ? runGitleaks(exe) : null;
if (fromGitleaks) {
  engine = 'gitleaks';
  findings.push(...fromGitleaks.filter((f) => !isKeyFile(f.file)));
} else {
  findings.push(...stagedFindings(root, rules).filter((f) => f.kind !== 'key_file'));
}

if (!findings.length) process.exit(0);

const out = [];
out.push('', 'Secret Shield stopped this commit: it contains something that looks like a password, token or key.', '');
for (const f of findings) {
  if (f.kind === 'key_file') {
    out.push(`  ${f.file}`, `      ${f.what}.`, `      Fix: git rm --cached "${f.file}"   (keeps your copy) and make sure .gitignore lists it.`, '');
  } else {
    out.push(`  ${f.file}:${f.line}   ${f.what}`, `      ${f.preview}`, '');
  }
}
out.push('How to fix it:',
  '  1. Take the secret out of that line. Put it in apps/daemon/.env (that file is never committed) and read it from there.',
  '  2. Stage the fix (git add <file>) and commit again.',
  '  3. If the secret was ever pushed or shared, make a new one and kill the old one. Step-by-step for each service:',
  '       node scripts/secret-shield/scan-history.mjs --guide ' + [...new Set(findings.map((f) => f.kind))].join(' '),
  '',
  'A deliberate fake (for example in a test)? Add the comment  gitleaks:allow  at the end of that line.',
  `(checked with ${engine})`, '');
process.stderr.write(out.join('\n') + '\n');
process.exit(1);
