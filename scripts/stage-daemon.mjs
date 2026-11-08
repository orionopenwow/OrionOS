// Copies the Python daemon sources into apps/desktop/src-tauri/resources/daemon so the
// desktop installer can bundle them. Runs automatically before `tauri build`.
// Skips the venv, caches, tests and secrets (.env) — the app builds its own venv on first run.
import { cpSync, existsSync, mkdirSync, readdirSync, rmSync, statSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const src = path.join(root, 'apps', 'daemon');
const dest = path.join(root, 'apps', 'desktop', 'src-tauri', 'resources', 'daemon');
const INCLUDE = /\.(py|txt)$|^\.env\.example$|^preload_seed\.json$|^secret_shield_rules\.json$|^package_top_lists\.json$/; // + Secret Shield patterns, Package Reality Check names
const SKIP = new Set(['.venv', '__pycache__', 'tests', '.pytest_cache', '.env']);

rmSync(dest, { recursive: true, force: true });
mkdirSync(dest, { recursive: true });
let count = 0;
for (const name of readdirSync(src)) {
  if (SKIP.has(name)) continue;
  const from = path.join(src, name);
  if (statSync(from).isFile() && INCLUDE.test(name)) {
    cpSync(from, path.join(dest, name));
    count += 1;
  }
}
if (!existsSync(path.join(dest, 'main.py'))) {
  console.error('stage-daemon: main.py missing — refusing to build an app without its brain.');
  process.exit(1);
}
console.log(`stage-daemon: bundled ${count} daemon files -> ${path.relative(root, dest)}`);
