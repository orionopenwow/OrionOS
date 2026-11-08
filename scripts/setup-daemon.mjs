// Creates apps/daemon/.venv and installs Python dependencies (cross-platform).
import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const daemonDir = path.join(root, 'apps', 'daemon');
const venv = path.join(daemonDir, '.venv');
const isWin = process.platform === 'win32';
const venvPython = isWin ? path.join(venv, 'Scripts', 'python.exe') : path.join(venv, 'bin', 'python');

function run(cmd, args) {
  console.log(`> ${cmd} ${args.join(' ')}`);
  const res = spawnSync(cmd, args, { cwd: daemonDir, stdio: 'inherit' });
  if (res.status !== 0) {
    console.error(`Command failed (${res.status}): ${cmd} ${args.join(' ')}`);
    process.exit(res.status ?? 1);
  }
}

if (!existsSync(venvPython)) {
  run(process.env.PYTHON || (isWin ? 'python' : 'python3'), ['-m', 'venv', venv]);
}
run(venvPython, ['-m', 'pip', 'install', '--upgrade', 'pip']);
run(venvPython, ['-m', 'pip', 'install', '-r', 'requirements.txt']);
if (process.argv.includes('--optional')) {
  run(venvPython, ['-m', 'pip', 'install', '-r', 'requirements-optional.txt']);
}
console.log('\nOrion daemon environment ready.');
