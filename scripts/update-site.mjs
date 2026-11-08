#!/usr/bin/env node
/**
 * Keep the website in step with the code. Rewrites the generated parts of docs/site/site.json:
 *   commands.menu   every menu item and its one-line description   (apps/desktop/src/lib/menu.ts)
 *   commands.quick  the built-in quick commands and server phrases  (apps/daemon/quick_commands.py)
 *   commands.keys   OrionOS keyboard shortcuts                      (os/lab/sway-config)
 *   commands.ctl    orion-ctl commands and what they do             (os/lab/bin/orion-ctl help text)
 *   meta.updated    today's date
 * Hand-written copy (headlines, features, commands.voice) is left alone.
 *
 *   node scripts/update-site.mjs              update site.json
 *   node scripts/update-site.mjs --snapshot   also refresh the OS preview's sample catalogues from the daemon
 *                                             (needs Python; runs apps/desktop/src/demo/snapshot_daemon.py)
 *   node scripts/update-site.mjs --shots      also re-capture the screenshots (scripts/capture-screenshots.mjs;
 *                                             build the preview first with npm run build:demo)
 *   node scripts/update-site.mjs --check      exit 1 if site.json is out of date (for CI)
 */
import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const SITE = path.join(ROOT, 'docs/site/site.json');
const args = new Set(process.argv.slice(2));
const read = (p) => fs.readFileSync(path.join(ROOT, p), 'utf8');

// ---------------------------------------------------------------- menu.ts
export function menuItems(src) {
  const out = [];
  const re = /\{\s*id:\s*'([\w-]+)',\s*label:\s*'((?:[^'\\]|\\.)*)'[^}]*?group:\s*'(main|more)'[^}]*?description:\s*'((?:[^'\\]|\\.)*)'/g;
  for (const m of src.matchAll(re)) out.push({ id: m[1], label: m[2].replace(/\\'/g, "'"), group: m[3], does: m[4].replace(/\\'/g, "'") });
  return out;
}

// ---------------------------------------------------------------- quick_commands.py
export function quickCommands(src) {
  const out = [];
  const defaults = src.match(/DEFAULTS[^=]*=\s*\{([\s\S]*?)\n\}/);
  if (defaults) {
    for (const m of defaults[1].matchAll(/"([\w-]+)":\s*\[([^\]]*)\]/g)) {
      const steps = [...m[2].matchAll(/"([^"]+)"/g)].map((x) => x[1]);
      out.push({ say: m[1], does: steps.map(stepWords).join(', then ') });
    }
  }
  const phrases = { start: 'start the server', stop: 'stop the server', restart: 'restart the server', status: 'is the server up?', logs: 'show the server logs' };
  for (const m of src.matchAll(/re\.compile\(r"[^"]+"\),\s*"(\w+)"\)/g)) {
    if (phrases[m[1]]) out.push({ say: phrases[m[1]], does: `OpenWoW server: ${m[1]}` });
  }
  return out;
}
function stepWords(step) {
  const [kind, arg] = step.split(':');
  if (kind === 'app') return `open ${arg}`;
  if (kind === 'wow') return `${arg} the OpenWoW server`;
  if (kind === 'station') return `open the ${arg} station`;
  if (step === 'say:status') return 'say a short status';
  return step;
}

// ---------------------------------------------------------------- sway-config
const KEY_WORDS = {
  'orion-rescue': 'Rescue panel', 'workspace number 1': 'Back to Orion', '$term': 'Terminal', '$menu': 'App launcher',
  'orion-files': 'Files', 'orion-settings': 'Settings', 'orion-ctl mic toggle': 'Hot mic on or off',
  'orion-ctl camera toggle': 'Camera presence on or off', 'orion-lock': 'Lock the screen', 'orion-share-scrub': 'Hide private things before sharing the screen',
  'orion-windows switch': 'Switch windows', 'orion-windows menu': 'Window menu', 'orion-shortcuts toggle': 'Show every shortcut',
  'orion-screenshot full': 'Screenshot', 'orion-screenshot area': 'Screenshot of an area', 'orion-screenshot markup': 'Screenshot and draw on it',
  'orion-clipboard pick': 'Clipboard history', 'orion-nightlight toggle': 'Night light', 'fullscreen toggle': 'Full screen', kill: 'Close the window',
};
export function keyBindings(src) {
  const byWhat = new Map();
  for (const line of src.split('\n')) {
    const m = line.match(/^\s*bindsym\s+(?:--[\w-]+\s+)*(\S+)\s+(?:exec\s+)?(.+)$/);
    if (!m) continue;
    const cmd = m[2].replace(/\s*[|>].*$/, '').trim();
    const what = KEY_WORDS[cmd];
    if (!what || /^button/.test(m[1])) continue;
    const key = m[1].replace(/\$mod/g, 'Super').replace(/Mod1/g, 'Alt').replace(/\+/g, ' + ').replace(/\bspace\b/, 'Space')
      .replace(/\bcomma\b/, ',').replace(/\bslash\b/, '/').replace(/\bReturn\b/, 'Enter').replace('XF86AudioMicMute', 'Mic mute key').replace(/\b([a-z])\b/g, (x) => x.toUpperCase());
    if (!byWhat.has(what)) byWhat.set(what, []);
    if (!byWhat.get(what).includes(key)) byWhat.get(what).push(key);
  }
  // the HUD's own keys (apps/desktop/src/lib/menu.ts isMenuShortcut, App.tsx)
  const hud = [{ keys: ['Super + Space', 'Alt + Space'], does: 'Open the Orion menu' }, { keys: ['Ctrl + Space'], does: 'Show or hide the chat' }];
  return hud.concat([...byWhat].map(([does, keys]) => ({ keys: keys.slice(0, 2), does })));
}

// ---------------------------------------------------------------- orion-ctl help
export function ctlCommands(src) {
  const help = src.match(/cat <<'HELP'\n([\s\S]*?)\nHELP/);
  if (!help) return [];
  const out = [];
  for (const line of help[1].split('\n')) {
    const m = line.match(/^(orion-ctl .+?)\s{2,}(.+)$/);
    if (m) out.push({ cmd: m[1].trim(), does: m[2].trim() });
    else if (out.length && /^\s{10,}\S/.test(line)) out[out.length - 1].does += ` ${line.trim()}`;
  }
  return out;
}

// ---------------------------------------------------------------- main
function main() {
  const site = JSON.parse(fs.readFileSync(SITE, 'utf8'));
  const before = JSON.stringify(site.commands);
  site.commands = {
    voice: site.commands?.voice ?? [],
    menu: menuItems(read('apps/desktop/src/lib/menu.ts')),
    quick: quickCommands(read('apps/daemon/quick_commands.py')),
    keys: keyBindings(read('os/lab/sway-config')),
    ctl: ctlCommands(read('os/lab/bin/orion-ctl')),
  };
  const changed = JSON.stringify(site.commands) !== before;
  if (args.has('--check')) {
    if (changed) { console.error('docs/site/site.json is out of date: run node scripts/update-site.mjs'); process.exit(1); }
    console.log('site.json is up to date.');
    return;
  }
  if (changed) site.meta.updated = new Date().toISOString().slice(0, 10);
  fs.writeFileSync(SITE, `${JSON.stringify(site, null, 2)}\n`);
  const c = site.commands;
  console.log(`site.json: ${c.menu.length} menu items, ${c.quick.length} quick commands, ${c.keys.length} shortcuts, ${c.ctl.length} orion-ctl commands${changed ? '' : ' (no change)'}`);

  if (args.has('--snapshot')) {
    const py = process.platform === 'win32' ? 'python' : 'python3';
    const r = spawnSync(py, [path.join(ROOT, 'apps/desktop/src/demo/snapshot_daemon.py')], { stdio: 'inherit' });
    if (r.status !== 0) console.warn('Could not refresh the preview catalogues (Python or a daemon module missing); kept the old ones.');
  }
  if (args.has('--shots')) {
    const r = spawnSync(process.execPath, [path.join(ROOT, 'scripts/capture-screenshots.mjs')], { stdio: 'inherit' });
    if (r.status !== 0) process.exit(r.status ?? 1);
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) main();
