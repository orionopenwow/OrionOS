// One-command Orion release:  npm run release [-- patch|minor|major|1.2.3] [--notes "What changed"]
//
// 1. bumps the version everywhere it lives (app, Tauri, Cargo, daemon, shared types, extension)
// 2. runs the daemon tests (skip with --skip-tests)
// 3. writes .github/release-notes.md, commits, tags vX.Y.Z and pushes
// GitHub Actions (.github/workflows/release.yml) then builds the signed Windows installer and
// publishes it; every installed Orion offers "Restart to update" within a few hours.
import { execSync } from 'node:child_process';
import { copyFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const p = (...parts) => path.join(root, ...parts);
const args = process.argv.slice(2);
const flag = (name) => args.includes(name);
const option = (name) => {
  const i = args.indexOf(name);
  return i >= 0 ? args[i + 1] : undefined;
};
const sh = (cmd, opts = {}) => execSync(cmd, { cwd: root, stdio: 'inherit', ...opts });
const shOut = (cmd) => execSync(cmd, { cwd: root, encoding: 'utf8' }).trim();

const current = JSON.parse(readFileSync(p('apps/desktop/src-tauri/tauri.conf.json'), 'utf8')).version;
const bump = args.find((a) => !a.startsWith('--') && a !== option('--notes')) || 'patch';
function nextVersion(v, kind) {
  if (/^\d+\.\d+\.\d+$/.test(kind)) return kind;
  const [maj, min, pat] = v.split('.').map(Number);
  if (kind === 'major') return `${maj + 1}.0.0`;
  if (kind === 'minor') return `${maj}.${min + 1}.0`;
  if (kind === 'patch') return `${maj}.${min}.${pat + 1}`;
  throw new Error(`Unknown bump "${kind}" (use patch, minor, major or x.y.z)`);
}
const version = nextVersion(current, bump);
const tag = `v${version}`;
console.log(`\n🌌 Releasing Orion ${current} -> ${version}\n`);

if (!existsSync(p('.git'))) throw new Error('Not a git repository.');
let remote = '';
try {
  remote = shOut('git remote get-url origin');
} catch {
  /* no remote yet */
}
if (!remote) {
  console.error('No git remote "origin". Create the GitHub repo, then:\n  git remote add origin https://github.com/<you>/<repo>.git');
  process.exit(1);
}
if (shOut('git tag --list ' + tag)) throw new Error(`Tag ${tag} already exists.`);

// The CI workflow template lives in scripts/ci/ (tools can't write .github/workflows directly);
// make sure the live copy matches it before every release.
const wfSrc = p('scripts/ci/release.yml');
const wfDest = p('.github/workflows/release.yml');
if (existsSync(wfSrc)) {
  mkdirSync(path.dirname(wfDest), { recursive: true });
  if (!existsSync(wfDest) || readFileSync(wfDest, 'utf8') !== readFileSync(wfSrc, 'utf8')) {
    copyFileSync(wfSrc, wfDest);
    console.log('  ✓ installed .github/workflows/release.yml');
  }
}

// ---------------------------------------------------------------- bump versions
const editJson = (rel, fn) => {
  const file = p(rel);
  const data = JSON.parse(readFileSync(file, 'utf8'));
  fn(data);
  writeFileSync(file, JSON.stringify(data, null, 2) + '\n');
};
const editText = (rel, re, replacement) => {
  const file = p(rel);
  const before = readFileSync(file, 'utf8');
  const after = before.replace(re, replacement);
  if (after === before) console.warn(`  ! no version found in ${rel}`);
  writeFileSync(file, after);
};
editJson('package.json', (d) => (d.version = version));
editJson('apps/desktop/package.json', (d) => (d.version = version));
editJson('packages/shared-types/package.json', (d) => (d.version = version));
editJson('apps/desktop/src-tauri/tauri.conf.json', (d) => (d.version = version));
editJson('apps/extension/manifest.json', (d) => (d.version = version));
editText('apps/desktop/src-tauri/Cargo.toml', /^version = "[^"]+"/m, `version = "${version}"`);
editText('packages/shared-types/src/index.ts', /ORION_VERSION = '[^']+'/, `ORION_VERSION = '${version}'`);
editText('apps/daemon/main.py', /os\.getenv\("ORION_APP_VERSION"\) or "[^"]+"/, `os.getenv("ORION_APP_VERSION") or "${version}"`);
console.log('  ✓ versions bumped');

// ---------------------------------------------------------------- tests
if (!flag('--skip-tests')) {
  try {
    sh('node scripts/test-daemon.mjs');
  } catch {
    console.error('\nDaemon tests failed — fix them or rerun with --skip-tests. Versions were bumped but nothing was committed.');
    process.exit(1);
  }
}

// ---------------------------------------------------------------- notes, commit, tag, push
let notes = option('--notes');
if (!notes) {
  let lastTag = '';
  try {
    lastTag = shOut('git describe --tags --abbrev=0');
  } catch {
    /* first release */
  }
  const range = lastTag ? `${lastTag}..HEAD` : 'HEAD';
  let log = '';
  try {
    log = shOut(`git log ${range} --pretty=format:"- %s" -n 40`);
  } catch {
    /* no commits yet */
  }
  notes = log || 'Orion update.';
}
writeFileSync(p('.github/release-notes.md'), `## Orion ${version}\n\n${notes}\n`);

sh('git add -A');
sh(`git commit -m "release: Orion ${tag}"`);
sh(`git tag -a ${tag} -m "Orion ${tag}"`);
sh('git push origin HEAD');
sh(`git push origin ${tag}`);
console.log(`\n✓ Pushed ${tag}. GitHub Actions is building the installer:`);
console.log(`  ${remote.replace(/\.git$/, '')}/actions`);
console.log('  When it finishes, every installed Orion will offer "Restart to update".\n');
