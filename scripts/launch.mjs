// One-click launcher prep (called by "Start Orion.cmd").
// Installs/updates whatever is missing or changed, creates the desktop shortcut, then exits:
//   exit 0  -> ready, the .cmd starts Orion
//   exit 3  -> Orion is already running
//   other   -> setup failed (message printed)
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { appendFileSync, existsSync, mkdirSync, readdirSync, readFileSync, statSync, writeFileSync } from 'node:fs';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const isWin = process.platform === 'win32';
const daemonDir = path.join(root, 'apps', 'daemon');
const venvPython = isWin
  ? path.join(daemonDir, '.venv', 'Scripts', 'python.exe')
  : path.join(daemonDir, '.venv', 'bin', 'python');
const stampFile = path.join(root, 'node_modules', '.orion-launch.json');

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

const logFile = path.join(root, 'logs', 'launcher.log');
mkdirSync(path.dirname(logFile), { recursive: true });
const say = (msg) => {
  console.log(`\x1b[36m[orion]\x1b[0m ${msg}`);
  try { appendFileSync(logFile, `${new Date().toISOString()} ${msg}\n`); } catch { /* ignore */ }
};

function hash(...files) {
  const h = createHash('sha256');
  for (const f of files) {
    const p = path.join(root, f);
    h.update(existsSync(p) ? readFileSync(p) : Buffer.from('missing'));
  }
  return h.digest('hex').slice(0, 16);
}

function run(cmd, args, { cwd = root, fatal = true } = {}) {
  // npm is a .cmd shim on Windows and needs a shell; python/node are real exes (keeps spaced paths safe)
  const res = spawnSync(cmd, args, { cwd, stdio: 'inherit', shell: isWin && cmd === 'npm' });
  if (res.status !== 0 && fatal) {
    say(`Setup step failed (exit ${res.status}): ${cmd} ${args.join(' ')}`);
    process.exit(1);
  }
  return res.status === 0;
}

function portOpen(port) {
  return new Promise((resolve) => {
    const s = net.connect({ port, host: '127.0.0.1' });
    s.once('connect', () => { s.destroy(); resolve(true); });
    s.once('error', () => resolve(false));
    s.setTimeout(800, () => { s.destroy(); resolve(false); });
  });
}

function readStamp() {
  try { return JSON.parse(readFileSync(stampFile, 'utf8')); } catch { return {}; }
}

function ensureShortcut() {
  if (!isWin) return;
  const desktop = path.join(os.homedir(), 'Desktop');
  const oneDriveDesktop = path.join(os.homedir(), 'OneDrive', 'Desktop');
  const target = existsSync(oneDriveDesktop) && !existsSync(desktop) ? oneDriveDesktop : desktop;
  const lnk = path.join(target, 'Orion.lnk');
  if (existsSync(lnk)) return;
  const cmdPath = path.join(root, 'Start Orion.cmd');
  const icon = path.join(root, 'apps', 'desktop', 'src-tauri', 'icons', 'icon.ico');
  const ps = [
    '$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:ORION_LNK)',
    '$s.TargetPath = $env:ORION_CMD',
    '$s.WorkingDirectory = $env:ORION_ROOT',
    '$s.IconLocation = $env:ORION_ICON',
    '$s.Description = "Start Orion"',
    '$s.Save()',
  ].join('; ');
  const res = spawnSync('powershell.exe', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', ps], {
    stdio: 'ignore',
    env: { ...process.env, ORION_LNK: lnk, ORION_CMD: cmdPath, ORION_ROOT: root, ORION_ICON: icon },
  });
  if (res.status === 0) say(`Added an "Orion" shortcut to your desktop.`);
}

function ps(command) {
  const r = spawnSync('powershell.exe', ['-NoProfile', '-Command', command], { encoding: 'utf8' });
  return (r.stdout || '').trim();
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

/**
 * Ports 1420 (UI dev server) and 9120 (Python brain). A leftover process from this repo is closed so the
 * fresh code runs; anything else holding the port is reported.
 */
async function freePort(port, label) {
  if (!(await portOpen(port))) return;
  if (!isWin) {
    say(`Port ${port} (${label}) is busy — close the other process and try again.`);
    process.exit(1);
  }
  const pid = ps(`(Get-NetTCPConnection -LocalPort ${port} -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1).OwningProcess`);
  const info = pid
    ? ps(`$p = Get-CimInstance Win32_Process -Filter "ProcessId=${Number(pid)}"; "$($p.Name)|$($p.CommandLine)"`)
    : '';
  const [name = 'unknown', cmdline = ''] = info.split('|');
  let ours = cmdline.toLowerCase().includes(root.toLowerCase()) || /wonderful-meitner|run_daemon|orion/i.test(cmdline);
  if (!ours && port === 9120) ours = await isOrionBrain(); // e.g. started from another copy of the repo
  if (!ours) say(`Process on port ${port}: ${name} — ${cmdline || '(command line unavailable)'}`);
  if (ours) {
    say(`Closing a leftover Orion ${label} (pid ${pid}).`);
    spawnSync('taskkill', ['/PID', String(pid), '/T', '/F'], { stdio: 'ignore' });
    for (let i = 0; i < 20 && (await portOpen(port)); i++) await sleep(250);
    if (!(await portOpen(port))) return;
  }
  say(`Port ${port} is being used by another program: ${name} (pid ${pid || '?'}). Close it, then start Orion again.`);
  process.exit(1);
}

// ------------------------------------------------------------------ main
say(`Launcher started in ${root}`);
/** Something on :9120 answering like Orion's daemon (/health returns its agents block). */
async function isOrionBrain() {
  try {
    const res = await fetch('http://127.0.0.1:9120/health', { headers: authHeaders(), signal: AbortSignal.timeout(2000) });
    const body = await res.json();
    // Without a readable key Orion only says {"ok": true}; that's still Orion's brain.
    return Boolean(body && ((body.status === 'ok' && body.agents) || (res.status === 200 && body.ok === true)));
  } catch {
    return false;
  }
}

/** Same fingerprint the daemon computes at startup (main.py _build_id): name, size and mtime of every .py file. */
function localBuildId() {
  const dir = path.join(root, 'apps', 'daemon');
  const h = createHash('sha1');
  for (const name of readdirSync(dir).filter((n) => n.endsWith('.py')).sort()) {
    const st = statSync(path.join(dir, name));
    h.update(`${name}:${st.size}:${Math.floor(st.mtimeMs / 1000)}\n`);
  }
  return h.digest('hex').slice(0, 12);
}

/** True when the brain on :9120 runs different code than this checkout (e.g. files were updated). */
async function brainIsStale() {
  if (!(await portOpen(9120))) return false;
  try {
    const res = await fetch('http://127.0.0.1:9120/health', { headers: authHeaders(), signal: AbortSignal.timeout(2500) });
    const body = await res.json();
    return Boolean(body.build) && body.build !== localBuildId(); // no key: can't tell, so leave it running
  } catch {
    return false;
  }
}

if (isWin) {
  const running = spawnSync('tasklist', ['/FI', 'IMAGENAME eq orion.exe', '/NH'], { encoding: 'utf8' }).stdout || '';
  if (/orion\.exe/i.test(running)) {
    if (await brainIsStale()) {
      say('Orion is running outdated code — restarting it.');
      spawnSync('taskkill', ['/IM', 'orion.exe', '/T', '/F'], { stdio: 'ignore' });
      await sleep(1000);
    } else {
      // Closing the window only hides Orion to the tray: a second launch hands focus to the running one
      // (single-instance plugin), so the click always shows Orion.
      const exe = path.join(root, 'apps', 'desktop', 'src-tauri', 'target', 'debug', 'orion.exe');
      if (existsSync(exe)) spawnSync(exe, [], { stdio: 'ignore', timeout: 5000 });
      say('Orion is already running — bringing it to the front.');
      process.exit(3);
    }
  }
}
await freePort(1420, 'UI server');
await freePort(9120, 'brain (Python daemon)');

const stamp = readStamp();
const next = { ...stamp };

// 1. JavaScript dependencies
const jsHash = hash('package.json', 'package-lock.json', 'apps/desktop/package.json');
if (!existsSync(path.join(root, 'node_modules')) || stamp.js !== jsHash) {
  say('Installing app dependencies (first run or after an update)…');
  run('npm', ['install', '--no-audit', '--no-fund']);
  next.js = hash('package.json', 'package-lock.json', 'apps/desktop/package.json');
}

// 2. Python daemon (base requirements are required; optional extras are best-effort)
const pyHash = hash('apps/daemon/requirements.txt');
if (!existsSync(venvPython) || stamp.py !== pyHash) {
  say('Setting up the Orion brain (Python)… this takes a few minutes the first time.');
  run('node', [path.join('scripts', 'setup-daemon.mjs')]);
  next.py = pyHash;
}
const optHash = hash('apps/daemon/requirements-optional.txt');
if (stamp.opt !== optHash) {
  say('Installing optional extras (macro recorder, PDFs, background removal)…');
  if (run(venvPython, ['-m', 'pip', 'install', '-r', 'requirements-optional.txt'], { cwd: daemonDir, fatal: false })) {
    next.opt = optHash;
  } else {
    say('Some optional extras failed to install — Orion still works without them. Will retry next launch.');
  }
}

mkdirSync(path.dirname(stampFile), { recursive: true });
writeFileSync(stampFile, JSON.stringify(next, null, 2));

// The Tauri build requires its bundled-resources folder to exist even in dev mode.
if (!run('node', [path.join('scripts', 'stage-daemon.mjs')], { fatal: false })) {
  mkdirSync(path.join(root, 'apps', 'desktop', 'src-tauri', 'resources', 'daemon'), { recursive: true });
}

if (!existsSync(path.join(root, 'apps', 'desktop', 'src-tauri', 'target'))) {
  say('First launch builds the desktop window (Rust) — give it a few minutes. Later launches are fast.');
}
ensureShortcut();
say('Setup OK — starting Orion.');
process.exit(0);
