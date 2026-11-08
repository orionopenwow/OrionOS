#!/usr/bin/env node
// Rasterise the brand SVGs (os/theme/brand/svg, made by build.py) to PNGs in os/theme/brand/png using
// Playwright's Chromium (PLAYWRIGHT_BROWSERS_PATH, default /opt/pw-browsers; never downloads a browser).
//
//   python3 os/theme/brand/build.py && node os/theme/brand/export.mjs
import { readFileSync, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';
import { execSync } from 'node:child_process';
import { existsSync } from 'node:fs';

const here = dirname(fileURLToPath(import.meta.url));
process.env.PLAYWRIGHT_BROWSERS_PATH ||= '/opt/pw-browsers';

async function loadPlaywright() {
  try {
    return await import('playwright');
  } catch {
    const roots = [];
    try { roots.push(execSync('npm root -g').toString().trim()); } catch { /* ignore */ }
    roots.push('/opt/npm-tools/node_modules');
    for (const root of roots) if (existsSync(join(root, 'playwright'))) return createRequire(join(root, 'x.js'))('playwright');
    throw new Error('playwright not found');
  }
}

const svgDir = join(here, 'svg');
const out = join(here, 'png');
mkdirSync(out, { recursive: true });

// [svg, png, width, height?, background?]
const jobs = [];
for (const s of [16, 24, 32, 48]) jobs.push(['orion-icon-small.svg', `orion-icon-${s}.png`, s]);
for (const s of [64, 128, 180, 192, 256, 512, 1024]) jobs.push(['orion-icon.svg', `orion-icon-${s}.png`, s]);
for (const s of [64, 128, 256, 512]) jobs.push(['orion-mark.svg', `orion-mark-${s}.png`, s]);
jobs.push(['orion-mark-small.svg', 'orion-mark-small-48.png', 48]);
for (const s of [256, 1024]) jobs.push(['orion-mark-light.svg', `orion-mark-light-${s}.png`, s]);
jobs.push(['orion-mark-small.svg', 'orion-notify.png', 96]); // mako notification icon
for (const n of ['orion-lockup', 'orion-os-lockup', 'orion-lockup-light', 'orion-os-lockup-light']) jobs.push([`${n}.svg`, `${n}.png`, 0, 256]);
jobs.push(['orion-stacked.svg', 'orion-stacked-512.png', 512]);
jobs.push(['orion-stacked.svg', 'orion-splash-1080x1920.png', 1080, 1920, '#05070E', 0.42]);

const { chromium } = await loadPlaywright();
const browser = await chromium.launch();
const page = await browser.newPage();
for (const [svgName, png, w0, h0, bg, fit] of jobs) {
  let svg = readFileSync(join(svgDir, svgName), 'utf8');
  const [, vw, vh] = svg.match(/viewBox="0 0 ([\d.]+) ([\d.]+)"/).map(Number);
  let W = w0 || Math.round((vw / vh) * h0);
  let H = h0 || Math.round((vh / vw) * w0);
  // `fit`: draw the art at this fraction of the canvas width, centred (splash)
  const artW = fit ? Math.round(W * fit) : W;
  const artH = fit ? Math.round((vh / vw) * artW) : H;
  svg = svg.replace(/width="[\d.]+" height="[\d.]+"/, `width="${artW}" height="${artH}"`);
  await page.setViewportSize({ width: W, height: H });
  await page.setContent(
    `<html><body style="margin:0;width:${W}px;height:${H}px;display:grid;place-items:center;background:${bg || 'transparent'}">${svg}</body></html>`,
  );
  await page.screenshot({ path: join(out, png), omitBackground: !bg, clip: { x: 0, y: 0, width: W, height: H } });
  console.log(`${png} ${W}x${H}`);
}
await browser.close();
