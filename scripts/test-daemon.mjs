// Runs the daemon test suite with the project virtualenv:  npm run test:daemon
import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const daemonDir = path.join(root, 'apps', 'daemon');
const isWin = process.platform === 'win32';
const venvPy = path.join(daemonDir, '.venv', isWin ? 'Scripts/python.exe' : 'bin/python');
const py = existsSync(venvPy) ? venvPy : isWin ? 'python' : 'python3';

const hasPytest = spawnSync(py, ['-c', 'import pytest'], { cwd: daemonDir }).status === 0;
if (!hasPytest) {
  spawnSync(py, ['-m', 'pip', 'install', '-r', 'requirements-dev.txt'], { cwd: daemonDir, stdio: 'inherit' });
}
const res = spawnSync(py, ['-m', 'pytest', 'tests', '-q', '-p', 'no:warnings'], { cwd: daemonDir, stdio: 'inherit' });
process.exit(res.status ?? 1);
