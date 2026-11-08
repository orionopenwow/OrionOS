#!/usr/bin/env node
/**
 * Screenshots and a smoke test for the GitHub Pages site (docs/), using Playwright's headless Chromium.
 *
 *   node scripts/capture-screenshots.mjs            capture every picture listed in docs/site/site.json
 *   node scripts/capture-screenshots.mjs --smoke    open every menu item in the OS preview and fail on any
 *                                                   console error or any route the preview doesn't answer
 *   node scripts/capture-screenshots.mjs --only phone,tests   just those pictures
 *
 * Needs the preview built first (npm run build:demo) and Playwright with Chromium:
 *   npm i --no-save playwright && npx playwright install chromium       (Linux CI, Windows or macOS)
 * It serves docs/ on a free local port; nothing is fetched from the internet (the site's web fonts are blocked
 * while capturing, so the system fallback is used if they aren't cached).
 */
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { execSync } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const DOCS = path.join(ROOT, 'docs');
const args = process.argv.slice(2);
const SMOKE = args.includes('--smoke');
const only = (args[args.indexOf('--only') + 1] || '').split(',').filter(Boolean);
const OUT = path.join(DOCS, 'screenshots');

// ---------------------------------------------------------------- find Playwright (local, NODE_PATH or global)
async function loadPlaywright() {
  try {
    return await import('playwright');
  } catch {
    /* not next to this script */
  }
  const dirs = (process.env.NODE_PATH || '').split(path.delimiter).filter(Boolean);
  try {
    dirs.push(execSync('npm root -g', { encoding: 'utf8' }).trim());
  } catch {
    /* no npm */
  }
  for (const d of dirs) {
    try {
      const req = createRequire(path.join(d, 'noop.js'));
      return await import(pathToFileURL(req.resolve('playwright')).href);
    } catch {
      /* try the next one */
    }
  }
  console.error('Playwright is not installed. Run: npm i --no-save playwright && npx playwright install chromium');
  process.exit(2);
}

// ---------------------------------------------------------------- a tiny static server for docs/
const TYPES = { '.html': 'text/html', '.js': 'text/javascript', '.mjs': 'text/javascript', '.css': 'text/css', '.json': 'application/json',
  '.png': 'image/png', '.jpg': 'image/jpeg', '.svg': 'image/svg+xml', '.woff2': 'font/woff2', '.glb': 'model/gltf-binary', '.ico': 'image/x-icon' };
function serve() {
  return new Promise((resolve) => {
    const srv = http.createServer((req, res) => {
      let f = path.join(DOCS, decodeURIComponent(req.url.split('?')[0].split('#')[0]));
      if (!f.startsWith(DOCS)) { res.writeHead(403); return res.end(); }
      if (f.endsWith(path.sep) || f.endsWith('/')) f = path.join(f, 'index.html');
      fs.readFile(f, (err, data) => {
        if (err) { res.writeHead(404); return res.end(); }
        res.writeHead(200, { 'content-type': TYPES[path.extname(f)] || 'application/octet-stream' });
        res.end(data);
      });
    }).listen(0, '127.0.0.1', () => resolve(srv));
  });
}

// ---------------------------------------------------------------- what each picture needs
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const open = (page, id) => page.evaluate((x) => window.dispatchEvent(new CustomEvent('orion:open', { detail: x })), id);
const closeChat = async (page) => {
  // the Orion chat window opens by default on desktop; close it so the picture shows one window at a time
  const btn = page.locator('.orion-window [data-kind="close"]').first();
  if (await btn.count()) await btn.click({ timeout: 1500, force: true }).catch(() => undefined);
  await wait(700);
};
const clickText = (page, text) => page.getByText(text, { exact: false }).first().click({ timeout: 4000 });

/** Steps that set up each picture in the OS preview (captions live in docs/site/site.json). */
const RECIPES = {
  async site(page, base) {
    await page.goto(`${base}/index.html`, { waitUntil: 'networkidle' });
    await wait(1200);
  },
  async desktop(page) {
    await closeChat(page);
    await open(page, 'menu');
    await wait(1500);
  },
  async tests(page) {
    await closeChat(page);
    await open(page, 'tests');
    await page.waitForFunction(() => /\d+ to go/.test(document.body.innerText) && !document.body.innerText.includes('Checking…'), null, { timeout: 30000 }).catch(() => console.warn('the checks did not finish in time'));
    await wait(1500); // the automatic checks run, then the Next card shows
  },
  async projects(page) {
    await closeChat(page);
    await open(page, 'projects');
    await wait(1200);
    // the wizard's last step with a sample idea, as the Test Center's "Open with a sample idea" does
    await page.evaluate(() => window.dispatchEvent(new CustomEvent('orion:projects-prefill', {
      detail: { name: 'Comet Dodge', starter: 'web-game', idea: 'Dodge comets with the arrow keys. Survive 60 seconds to win. Add a high score.' },
    })));
    await wait(1500);
  },
  async missions(page) {
    await closeChat(page);
    await open(page, 'missions');
    await wait(2500);
    await page.locator('button', { hasText: 'Star catcher' }).first().dispatchEvent('click').catch((e) => console.warn('click failed', e.message)); // its live log, changes and cost
    await wait(3000);
  },
  async phone(page) {
    await closeChat(page);
    await open(page, 'phone');
    await wait(2200);
  },
  async privacy(page) {
    await closeChat(page);
    await open(page, 'privacy');
    await wait(1800);
  },
  async social(page, base) {
    await page.goto(`${base}/index.html?social=1`, { waitUntil: 'networkidle' });
    await wait(1200);
  },
};

async function main() {
  const pw = await loadPlaywright();
  const chromium = pw.chromium ?? pw.default?.chromium;
  const srv = await serve();
  const base = `http://127.0.0.1:${srv.address().port}`;
  const browser = await chromium.launch({ args: ['--enable-unsafe-swiftshader'] });
  let failed = false;
  try {
    if (SMOKE) failed = await smoke(browser, base);
    else await capture(browser, base);
  } finally {
    await browser.close();
    srv.close();
  }
  process.exit(failed ? 1 : 0);
}

async function newPage(browser, w, h, base) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h }, deviceScaleFactor: 1, reducedMotion: 'no-preference' });
  ctx.base = base;
  // offline: block anything that isn't the local server. The site's Google Fonts are answered with the same
  // OFL fonts the OS preview bundles (docs/app/assets), so pictures use the real type without the internet.
  await ctx.route(/^https?:\/\/(?!127\.0\.0\.1)/, (r) => {
    if (r.request().url().startsWith('https://fonts.googleapis.com/')) return r.fulfill({ status: 200, contentType: 'text/css', body: localFontCss(ctx.base) });
    return r.abort();
  });
  await ctx.addInitScript(() => { try { localStorage.setItem('orion-demo-tour-seen', '1'); } catch { /* */ } });
  return ctx.newPage();
}

function localFontCss(base) {
  const dir = path.join(DOCS, 'app', 'assets');
  const files = fs.existsSync(dir) ? fs.readdirSync(dir) : [];
  const face = (family, prefix, weight) => {
    const f = files.find((x) => x.startsWith(prefix) && x.endsWith('.woff2'));
    return f ? `@font-face{font-family:'${family}';font-weight:${weight};font-style:normal;font-display:block;src:url(${base}/app/assets/${f}) format('woff2');}` : '';
  };
  return face('Figtree', 'figtree-latin-wght', '300 900') + face('Unbounded', 'unbounded-latin-wght', '200 900') + face('Martian Mono', 'martian-mono-latin-standard', '100 800');
}

async function capture(browser, base) {
  const site = JSON.parse(fs.readFileSync(path.join(DOCS, 'site', 'site.json'), 'utf8'));
  fs.mkdirSync(OUT, { recursive: true });
  for (const shot of site.screenshots) {
    if (only.length && !only.includes(shot.id)) continue;
    const recipe = RECIPES[shot.id];
    if (!recipe) { console.warn(`no recipe for ${shot.id}, skipped`); continue; }
    const [w, h] = shot.size ?? [1600, 1000];
    const page = await newPage(browser, w, h, base);
    const errors = [];
    page.on('pageerror', (e) => errors.push(e.message));
    if (shot.id !== 'site' && shot.id !== 'social') {
      await page.goto(`${base}/app/?tour=0&shot=1`, { waitUntil: 'load' });
      await wait(3500);
    }
    await recipe(page, base);
    const file = path.join(OUT, shot.file);
    await page.screenshot({ path: file });
    console.log(`${shot.file}  ${w}x${h}${errors.length ? `  (page errors: ${errors.join(' | ')})` : ''}`);
    await page.context().close();
  }
}

/** Every menu item opens in the preview with no console errors and no unanswered daemon route. */
async function smoke(browser, base) {
  const src = fs.readFileSync(path.join(ROOT, 'apps/desktop/src/lib/menu.ts'), 'utf8');
  const ids = [...src.matchAll(/\{\s*id:\s*'([\w-]+)'/g)].map((m) => m[1]);
  const page = await newPage(browser, 1600, 1000, base);
  const errors = [];
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', (e) => errors.push(`page error: ${e.message}`));
  page.on('requestfailed', (r) => { if (r.url().startsWith(base)) errors.push(`request failed: ${r.url()}`); });
  page.on('response', (r) => { if (r.url().startsWith(base) && r.status() >= 400) errors.push(`${r.status()} ${r.url()}`); });
  await page.goto(`${base}/app/?tour=0`, { waitUntil: 'load' });
  await wait(3500);
  const report = [];
  for (const id of ['menu', ...ids]) {
    const before = errors.length;
    await open(page, id);
    await wait(1300);
    report.push(`${errors.length > before ? 'FAIL' : 'ok  '} ${id}${errors.length > before ? `: ${errors.slice(before).join(' | ')}` : ''}`);
    await page.keyboard.press('Escape');
  }
  // a few actions animate to a result: Do it for me, pairing a phone, Guide Mode
  const step = async (name, fn) => {
    const before = errors.length;
    try { await fn(); report.push(`${errors.length > before ? 'FAIL' : 'ok  '} action: ${name}`); }
    catch (e) { errors.push(`${name}: ${e.message.split('\n')[0]}`); report.push(`FAIL action: ${name}: ${e.message.split('\n')[0]}`); }
  };
  await step('Test Center: Do it for me', async () => {
    await open(page, 'tests');
    await page.getByRole('button', { name: /Do it for me/ }).first().dispatchEvent('click');
    await page.waitForFunction(() => /\?|Opened|worked/.test(document.body.innerText), null, { timeout: 15000 });
  });
  await step('Phone: pair the sample phone', async () => {
    await open(page, 'phone');
    await page.getByRole('button', { name: 'Confirm' }).first().waitFor({ timeout: 20000 });
    await page.getByRole('button', { name: 'Confirm' }).first().dispatchEvent('click');
    await page.getByText('Sample phone').first().waitFor({ timeout: 10000 });
  });
  await step('Guide Mode: pair my phone', async () => {
    await open(page, 'guide');
    await page.getByLabel('What to do').fill('pair my phone');
    await page.getByRole('button', { name: 'Guide me' }).dispatchEvent('click');
    await page.getByText('Step 1 of 4').waitFor({ timeout: 10000 });
  });
  const site = await browser.newPage();
  site.on('pageerror', (e) => errors.push(`site: ${e.message}`));
  await site.goto(`${base}/index.html`, { waitUntil: 'load' });
  await step('Site: renders site.json', async () => { await site.locator('#features article').first().waitFor({ timeout: 10000 }); });
  await site.close();
  console.log(report.slice(-4).join('\n'));
  const misses = await page.evaluate(() => window.__demoMisses || []);
  console.log(report.slice(0, -4).join('\n'));
  if (misses.length) console.log(`Routes the preview didn't answer:\n  ${[...new Set(misses)].join('\n  ')}`);
  const bad = errors.length > 0 || misses.length > 0;
  console.log(bad ? 'Smoke test FAILED' : `Smoke test passed: ${ids.length} menu items opened with no errors.`);
  return bad;
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
