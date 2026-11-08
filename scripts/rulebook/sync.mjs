// One Rulebook: rewrite CLAUDE.md, AGENTS.md, GEMINI.md and .cursor/rules/orion.mdc from .orion/rules.md.
// Works without the daemon running.
//   node scripts/rulebook/sync.mjs                  sync
//   node scripts/rulebook/sync.mjs check            exit 1 if a generated file is out of date
//   node scripts/rulebook/sync.mjs list
//   node scripts/rulebook/sync.mjs add "Never push without asking." [--section Approvals]
// The work is done by apps/daemon/rulebook.py (Python standard library only), so the daemon and this command
// always produce identical files.
import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const script = path.join(root, 'apps', 'daemon', 'rulebook.py');
const isWin = process.platform === 'win32';
const venv = path.join(root, 'apps', 'daemon', '.venv', isWin ? 'Scripts/python.exe' : 'bin/python');
const candidates = [[venv, []], ['python3', []], ['python', []], ['py', ['-3']]].filter(([c]) => c !== venv || existsSync(venv));

const args = process.argv.slice(2);
if (!args.length || args[0].startsWith('--')) args.unshift('sync');
for (const [cmd, pre] of candidates) {
  const ok = spawnSync(cmd, [...pre, '-c', 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'], { stdio: 'ignore' });
  if (ok.status !== 0) continue;
  // -I: isolated mode, so nothing from the current folder or environment is imported by accident
  const r = spawnSync(cmd, [...pre, '-I', script, ...args, '--repo', root], { stdio: 'inherit' });
  process.exit(r.status ?? 1);
}
console.error('One Rulebook needs Python 3.9 or newer. Install it from https://www.python.org and run this again.');
process.exit(1);
