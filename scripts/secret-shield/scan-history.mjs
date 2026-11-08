// Secret Shield one-time full history scan, with a plain-words report and rotation steps.
//   node scripts/secret-shield/scan-history.mjs [repo]          scan every commit on every branch
//   node scripts/secret-shield/scan-history.mjs --guide openai  print the rotation steps for one kind of secret
// Uses gitleaks (git mode) and trufflehog when they're installed, otherwise the built-in scanner. trufflehog runs
// with --no-verification, so no secret is ever sent anywhere to "check" it. Secrets are never printed.
// The report is saved to logs/secret-shield/ (git ignores logs/). Exit code 1 when something was found.
import { spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { ROOT, REDACTED, exeOnPath, formatReport, gitleaksExe, guidesFor, historyFindings, loadRules, norm } from './scanner.mjs';

const args = process.argv.slice(2);
const rules = loadRules();

if (args.includes('--guide')) {
  const kinds = args.filter((a) => !a.startsWith('--'));
  const known = Object.keys(rules.guides).filter((k) => !k.startsWith('_'));
  if (!kinds.length) { console.log(`Kinds: ${known.join(', ')}, key_file`); process.exit(0); }
  for (const [kind, steps] of Object.entries(guidesFor(kinds, rules))) {
    console.log(`\n${kind}:`);
    steps.forEach((s, i) => console.log(`  ${i + 1}. ${s}`));
  }
  console.log('\nThen:');
  for (const s of rules.guides._after ?? []) console.log(`  - ${s}`);
  process.exit(0);
}

const repo = path.resolve(args.find((a) => !a.startsWith('--')) ?? ROOT);
const KINDS = [['anthropic', 'anthropic'], ['openai', 'openai'], ['github', 'github'], ['gitlab', 'gitlab'],
  ['discord', 'discord'], ['telegram', 'telegram'], ['slack', 'slack'], ['stripe', 'stripe'], ['aws', 'aws'],
  ['gcp', 'google'], ['google', 'google'], ['huggingface', 'huggingface'], ['npm', 'npm'], ['private', 'private_key'], ['uri', 'url_password']];
const kindOf = (id) => (KINDS.find(([p]) => String(id).toLowerCase().includes(p)) ?? [null, 'generic'])[1];
const labelOf = (kind, id) => (kind === 'generic' ? `a secret (${String(id).replace(/-/g, ' ')})` : rules.rules.find((r) => r.kind === kind)?.label);
const notes = [];
const all = [];

function gitleaksScan(exe) {
  const tmp = mkdtempSync(path.join(os.tmpdir(), 'orion-shield-'));
  const report = path.join(tmp, 'gitleaks.json');
  const a = ['git', repo, '--no-banner', '--redact', '--exit-code', '0', '--log-level', 'error', '--log-opts', '--all',
    '--report-format', 'json', '--report-path', report];
  if (existsSync(path.join(repo, '.gitleaks.toml'))) a.push('--config', path.join(repo, '.gitleaks.toml'));
  const r = spawnSync(exe, a, { cwd: repo, encoding: 'utf8' });
  try {
    if (r.status !== 0 || !existsSync(report)) return null;
    return JSON.parse(readFileSync(report, 'utf8') || '[]').map((x) => {
      const kind = kindOf(x.RuleID);
      return { rule: x.RuleID, kind, what: labelOf(kind, x.RuleID), file: norm(x.File || ''), line: x.StartLine || 0,
        where: 'history', commit: String(x.Commit || '').slice(0, 10), preview: String(x.Match || '').replace(/REDACTED/g, REDACTED).slice(0, 160) };
    });
  } finally { rmSync(tmp, { recursive: true, force: true }); }
}

function lineHasMarker(g) {
  const r = spawnSync('git', ['-C', repo, 'show', `${g.commit}:${g.file}`], { encoding: 'utf8', maxBuffer: 1024 * 1024 * 64 });
  return (r.stdout || '').split('\n')[g.line - 1]?.includes(rules.marker) ?? false;
}

function trufflehogScan() {
  const r = spawnSync('trufflehog', ['git', `file://${repo}`, '--json', '--no-verification', '--no-update', '--results=verified,unknown,unverified'],
    { cwd: repo, encoding: 'utf8', maxBuffer: 1024 * 1024 * 256 });
  if (r.status !== 0 && r.status !== 183) return null;
  const out = [];
  for (const line of (r.stdout || '').split('\n')) {
    if (!line.trim().startsWith('{')) continue;
    try {
      const x = JSON.parse(line); // Raw / RawV2 hold the secret: only checked against the allowlist, never copied
      const g = x.SourceMetadata?.Data?.Git ?? {};
      const raw = String(x.RawV2 || x.Raw || '');
      const allowed = rules.allow.some((a) => (!a.path || a.path.test(norm(g.file || ''))) && (!a.secret || a.secret.test(raw)));
      if (allowed || (g.line && rules.marker && lineHasMarker(g))) continue;
      const kind = kindOf(x.DetectorName);
      out.push({ rule: `trufflehog:${x.DetectorName}`, kind, what: labelOf(kind, x.DetectorName), file: norm(g.file || ''),
        line: g.line || 0, where: 'history', commit: String(g.commit || '').slice(0, 10), preview: '(found by trufflehog)' });
    } catch { /* not a finding line */ }
  }
  return out;
}

const gl = gitleaksExe();
const glFindings = gl ? gitleaksScan(gl) : null;
if (glFindings) { notes.push('- Checked with gitleaks (every commit on every branch).'); all.push(...glFindings); }
if (exeOnPath('trufflehog')) {
  const th = trufflehogScan();
  if (th) {
    notes.push('- Also checked with trufflehog (offline: nothing was sent anywhere to verify).');
    const seen = new Set(all.map((f) => `${f.file}:${f.line}:${f.commit}`));
    all.push(...th.filter((f) => !seen.has(`${f.file}:${f.line}:${f.commit}`)));
  }
}
if (!glFindings) {
  notes.push(gl ? '- gitleaks failed, so the built-in scanner was used.' : '- gitleaks is not installed, so the built-in scanner was used (install gitleaks for more patterns).');
  all.push(...historyFindings(repo, rules));
}
const commits = spawnSync('git', ['-C', repo, 'rev-list', '--all', '--count'], { encoding: 'utf8' }).stdout?.trim();
notes.unshift(`Repo: ${path.basename(repo)}, ${commits || '?'} commits, scanned ${new Date().toISOString().slice(0, 16).replace('T', ' ')}.`, '');
notes.push('- Also turn on GitHub push protection as a second net: repo Settings > Code security > Secret Protection > Push protection.');

const text = formatReport('Secret Shield: full history scan', all, rules, notes);
const dir = path.join(ROOT, 'logs', 'secret-shield');
mkdirSync(dir, { recursive: true });
const file = path.join(dir, `history-scan-${path.basename(repo)}-${new Date().toISOString().slice(0, 10)}.md`);
writeFileSync(file, text);
process.stdout.write(text);
console.log(`\nReport saved to ${path.relative(process.cwd(), file) || file}`);
process.exit(all.length ? 1 : 0);
