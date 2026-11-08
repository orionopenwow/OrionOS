// Turns on Secret Shield's pre-commit check for this repo: points git at the committed .githooks/ folder.
//   node scripts/secret-shield/install-hooks.mjs [--quiet] [--force]
// Runs automatically from `npm install` (the "prepare" script) and "Publish to GitHub.cmd".
// Safe to run again. It never changes anything outside this repo's own git settings, and it leaves a
// different hooks folder alone unless you pass --force.
import { spawnSync } from 'node:child_process';
import { chmodSync, existsSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { ROOT, exeOnPath, gitleaksExe } from './scanner.mjs';

const args = process.argv.slice(2);
const quiet = args.includes('--quiet');
const force = args.includes('--force');
const say = (m) => { if (!quiet) console.log(m); };
const git = (a) => spawnSync('git', ['-C', ROOT, ...a], { encoding: 'utf8' });

if (!exeOnPath('git')) { say('Secret Shield: git is not installed, nothing to set up.'); process.exit(0); }
const top = git(['rev-parse', '--show-toplevel']);
if (top.status !== 0) { say('Secret Shield: not a git repo yet; run this again after git init.'); process.exit(0); }
const hook = path.join(ROOT, '.githooks', 'pre-commit');
if (!existsSync(hook)) { console.error('Secret Shield: .githooks/pre-commit is missing.'); process.exit(1); }

const current = (git(['config', '--get', 'core.hooksPath']).stdout || '').trim();
if (current && current.replace(/\/$/, '') !== '.githooks' && !force) {
  console.log(`Secret Shield: this repo already uses its own hooks folder (${current}), so it was left alone.`);
  console.log('Run with --force to switch to .githooks (then add your other hooks there).');
  process.exit(0);
}
if (current.replace(/\/$/, '') !== '.githooks') {
  const r = git(['config', 'core.hooksPath', '.githooks']);
  if (r.status !== 0) { console.error(`Secret Shield: could not set core.hooksPath: ${r.stderr}`); process.exit(1); }
  console.log('Secret Shield is on: every commit in this repo is now checked for passwords, tokens and keys.');
} else {
  say('Secret Shield is already on for this repo.');
}
if (process.platform !== 'win32') { try { chmodSync(hook, 0o755); } catch { /* read-only checkout */ } }

const old = path.join(ROOT, '.git', 'hooks', 'pre-commit');
if (existsSync(old) && !readFileSync(old, 'utf8').includes('Secret Shield')) {
  console.log('Note: .git/hooks/pre-commit is no longer used (git now reads .githooks/). Move it there if you still need it.');
}
if (gitleaksExe()) {
  say('Commits are checked with gitleaks.');
} else {
  say('gitleaks is not installed, so the built-in scanner checks commits. For the strongest check, install it:');
  say(process.platform === 'win32' ? '  winget install gitleaks.gitleaks' : '  brew install gitleaks   (or download from https://github.com/gitleaks/gitleaks/releases)');
}
