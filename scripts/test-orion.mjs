// "Test Orion.cmd" step 2: wait for Orion's brain, then ask the Orion window to open the Test Center and start the
// automatic checks (POST /tests/open {"run": true}; apps/daemon/test_center.py). Talks only to this computer.
//   exit 0 -> the Test Center was asked to open
//   exit 1 -> Orion didn't come up in time (message printed)
import { readFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const API = process.env.ORION_API_URL || 'http://127.0.0.1:9120';
const WAIT_MIN = Number(process.env.ORION_TEST_WAIT_MIN || 20); // the first launch compiles the window: be patient
const say = (msg) => console.log(`\x1b[36m[orion]\x1b[0m ${msg}`);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** Orion's per-start key (apps/daemon/api_token.py writes it, owner-only). '' while Orion is off. */
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

async function call(method, route, body) {
  const t = daemonToken();
  const headers = { 'content-type': 'application/json', ...(t ? { Authorization: `Bearer ${t}` } : {}) };
  const res = await fetch(`${API}${route}`, { method, headers, body: body ? JSON.stringify(body) : undefined, signal: AbortSignal.timeout(5000) });
  return { status: res.status, json: await res.json().catch(() => ({})) };
}

const deadline = Date.now() + WAIT_MIN * 60_000;
let told = false;
for (;;) {
  try {
    const r = await call('GET', '/tests');
    if (r.status === 200 && r.json.groups) break;
  } catch {
    /* not up yet */
  }
  if (Date.now() > deadline) {
    say(`Orion didn't start within ${WAIT_MIN} minutes. Look in logs\\orion-dev.log, or start it with Start Orion.cmd and try again.`);
    process.exit(1);
  }
  if (!told) {
    say('Waiting for Orion to start (the very first start can take several minutes)...');
    told = true;
  }
  await sleep(3000);
}

const r = await call('POST', '/tests/open', { run: true });
if (r.status !== 200) {
  say(`Orion answered but couldn't open the Test Center (${r.json.error || r.status}).`);
  process.exit(1);
}
say('The Test Center is open in the Orion window and the automatic checks are running. Then follow Next.');
process.exit(0);
