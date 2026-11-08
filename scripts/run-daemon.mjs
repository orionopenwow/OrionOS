// Runs the Orion daemon using the project virtualenv.
// Exit code 75 means "hot-reload me" (used after an approved self-evolve patch touches
// daemon code), so the daemon is relaunched instead of the launcher exiting. If the
// relaunched daemon then crashes within CRASH_WINDOW_MS, the last applied patch is
// rolled back automatically and the daemon is started once more.
import { spawn, spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const RESTART_EXIT_CODE = 75;
const CRASH_WINDOW_MS = 90_000;
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const daemonDir = path.join(root, 'apps', 'daemon');
const isWin = process.platform === 'win32';
const py = isWin
  ? path.join(daemonDir, '.venv', 'Scripts', 'python.exe')
  : path.join(daemonDir, '.venv', 'bin', 'python');

if (!existsSync(py)) {
  console.error('Daemon virtualenv missing. Run `npm run setup:daemon` first.');
  process.exit(1);
}

let child = null;
let stopping = false;
let hotReloadedAt = 0;
let rolledBack = false;

function launch() {
  const startedAt = Date.now();
  child = spawn(py, ['main.py'], { cwd: daemonDir, stdio: 'inherit' });
  child.on('exit', (code) => {
    if (stopping) process.exit(code ?? 0);
    if (code === RESTART_EXIT_CODE) {
      console.log('\n[orion] Daemon code changed by a self-evolve patch — hot-reloading...\n');
      hotReloadedAt = Date.now();
      setTimeout(launch, 500);
      return;
    }
    const crashedAfterReload = code !== 0 && hotReloadedAt && startedAt - hotReloadedAt < 5_000
      && Date.now() - startedAt < CRASH_WINDOW_MS;
    if (crashedAfterReload && !rolledBack) {
      rolledBack = true;
      console.error('\n[orion] Daemon crashed right after a self-evolve hot reload — rolling the patch back...\n');
      const res = spawnSync(py, ['self_evolve.py', '--revert-last'], { cwd: daemonDir, stdio: 'inherit' });
      if (res.status === 0) {
        setTimeout(launch, 500);
        return;
      }
    }
    process.exit(code ?? 0);
  });
}

// On Windows the console already delivers Ctrl+C to the Python child, and child.kill()
// would TerminateProcess it (skipping a clean shutdown), so only forward on POSIX.
const forward = (sig) => () => {
  stopping = true;
  if (!isWin) child?.kill(sig);
};
process.on('SIGINT', forward('SIGINT'));
process.on('SIGTERM', forward('SIGTERM'));
launch();
