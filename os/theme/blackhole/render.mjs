#!/usr/bin/env node
// Headless screenshot test for the black hole shader.
//
//   node os/theme/blackhole/render.mjs [--out DIR] [--size 720] [--width W --height H]
//        [--frame name:key=val,key=val ...] [--bench N] [--ultra] [--check]
//
// Each --frame renders one PNG named <name>.png with the given uniforms (u_ prefix optional), e.g.
//   --frame idle:time=12 --frame edge:time=12,tilt=1 --frame boot-collapse:boot=2.75
// With no --frame a standard set is rendered (settled, tilt, boot timeline, moods, quality tiers).
// --bench N renders N frames back to back and logs the mean frame time (software GL in CI, so treat the
// number as a relative cost, not real GPU time).
//
// --ultra renders the ray-marched Ultra tier (blackhole_rt.frag, u_quality 4) instead of blackhole.frag.
// --check only compiles and links both shaders and lists their active uniforms as JSON (used by the tests).
//
// Uses Playwright's Chromium (PLAYWRIGHT_BROWSERS_PATH, default /opt/pw-browsers). Never downloads browsers.
import { readFileSync, mkdirSync, writeFileSync, existsSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';
import { execSync } from 'node:child_process';

const here = dirname(fileURLToPath(import.meta.url));
const repo = resolve(here, '../../..');
process.env.PLAYWRIGHT_BROWSERS_PATH ||= '/opt/pw-browsers';

async function loadPlaywright() {
  try {
    return await import('playwright');
  } catch {
    const roots = [];
    try { roots.push(execSync('npm root -g').toString().trim()); } catch { /* ignore */ }
    roots.push('/opt/npm-tools/node_modules');
    for (const root of roots) {
      const p = join(root, 'playwright');
      if (existsSync(p)) return createRequire(join(root, 'x.js'))('playwright');
    }
    throw new Error('playwright not found (npm i -D playwright, or install it globally)');
  }
}

const args = process.argv.slice(2);
const opt = { out: join(repo, 'os/theme/blackhole/shots'), size: 720, width: 0, height: 0, bench: 0, frames: [], ultra: false, check: false };
for (let i = 0; i < args.length; i++) {
  const a = args[i];
  if (a === '--out') opt.out = resolve(args[++i]);
  else if (a === '--size') opt.size = +args[++i];
  else if (a === '--width') opt.width = +args[++i];
  else if (a === '--height') opt.height = +args[++i];
  else if (a === '--bench') opt.bench = +args[++i];
  else if (a === '--ultra') opt.ultra = true;
  else if (a === '--check') opt.check = true;
  else if (a === '--frame') {
    const [name, spec = ''] = args[++i].split(':');
    const u = {};
    for (const kv of spec.split(',').filter(Boolean)) {
      const [k, v] = kv.split('=');
      u[k.startsWith('u_') ? k : `u_${k}`] = v.includes('/') ? v.split('/').map(Number) : Number(v);
    }
    opt.frames.push({ name, u });
  }
}
if (!opt.frames.length) {
  const std = [
    ['settled', {}],
    ['settled-t40', { u_time: 40 }],
    ['tilt-half', { u_tilt: 0.5 }],
    ['tilt-edge', { u_tilt: 1 }],
    ['boot-0.4-spark', { u_boot: 0.4 }],
    ['boot-1.6-starfall', { u_boot: 1.6 }],
    ['boot-2.4-starfall', { u_boot: 2.4 }],
    ['boot-2.75-collapse', { u_boot: 2.75 }],
    ['boot-3.1-ring', { u_boot: 3.1 }],
    ['boot-3.9-accretion', { u_boot: 3.9 }],
    ['boot-4.6-accretion', { u_boot: 4.6 }],
    ['boot-6.0-settled', { u_boot: 6 }],
    ['mood-focus', { u_spin: 1.5, u_ring: 1.35, u_glow: 1.15, u_temp: -0.3, u_hue: -0.9, u_turbulence: 0.35 }],
    ['mood-voice', { u_voice: 0.8, u_ripple: 0.7 }],
    ['mood-podcast', { u_temp: 0.45, u_ring: 1.2, u_glow: 1.1, u_turbulence: 0.45 }],
    ['mood-sanctuary', { u_temp: 0.95, u_glow: 0.8, u_ring: 0.85, u_spin: 0.5, u_turbulence: 0.15 }],
    ['mood-winddown', { u_temp: 0.75, u_glow: 0.3, u_ring: 0.5, u_spin: 0.25, u_turbulence: 0.1 }],
    ['mood-turbulent', { u_turbulence: 1 }],
    ['quality-1-battery', { u_quality: 1 }],
    ['quality-3-high', { u_quality: 3 }],
    ['overlay-transparent', { u_transparent: 1 }],
    // smart reactions (stage 6): flare, update ring, an ember comet + a message comet + three approval stars
    ['react-flare', { u_flare: 1 }],
    ['react-ring2', { u_ring2: 1 }],
    ['react-bodies', { u_bodies: [0.21, 0.33, 1, 1.55, -0.3, -0.12, 0.8, 0.4, 0.3, 0.304, 0.85, 0, -0.152, 0.4, 0.85, 0,
      -0.36, -0.24, 0.85, 2, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0] }],
  ];
  opt.frames = std.map(([name, u]) => ({ name, u }));
}

if (opt.ultra) for (const f of opt.frames) if (f.u.u_quality === undefined || f.u.u_quality < 4) f.u.u_quality = 4;
const fragFlat = readFileSync(join(here, 'blackhole.frag'), 'utf8');
const fragRt = readFileSync(join(here, 'blackhole_rt.frag'), 'utf8');
const frag = opt.ultra ? fragRt : fragFlat;
const diskPath = join(repo, 'apps/desktop/public/black-hole-core.png');
const disk = `data:image/png;base64,${readFileSync(diskPath).toString('base64')}`;
const W = opt.width || opt.size;
const H = opt.height || opt.size;

const html = `<!doctype html><html><body style="margin:0;background:#05070e">
<canvas id="c" width="${W}" height="${H}"></canvas>
<script>
const FRAG = ${JSON.stringify(frag)};
const VERT = '#version 300 es\\nin vec2 a;void main(){gl_Position=vec4(a,0.,1.);}';
const cv = document.getElementById('c');
const gl = cv.getContext('webgl2', { preserveDrawingBuffer: true, premultipliedAlpha: true, alpha: true });
window.__err = gl ? '' : 'no webgl2';
function sh(type, src) { const s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s);
  if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s)); return s; }
let prog;
try {
  prog = gl.createProgram(); gl.attachShader(prog, sh(gl.VERTEX_SHADER, VERT)); gl.attachShader(prog, sh(gl.FRAGMENT_SHADER, FRAG));
  gl.bindAttribLocation(prog, 0, 'a'); gl.linkProgram(prog);
  if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(prog));
} catch (e) { window.__err = String(e.message || e); }
const buf = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, buf);
gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1,-1, 3,-1, -1,3]), gl.STATIC_DRAW);
gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
const DEF = { u_time: 12, u_spin: 1, u_temp: 0, u_ring: 1, u_glow: 1, u_turbulence: 0.25, u_size: 1, u_tilt: 0,
  u_boot: -1, u_voice: 0, u_ripple: 0, u_quality: 2, u_parallax: [0, 0], u_flow: 0, u_hue: 0, u_transparent: 0,
  u_flare: 0, u_ring2: 0, u_bodies: new Array(40).fill(0) };
window.__ready = new Promise((res) => { const img = new Image(); img.onload = () => {
  const tex = gl.createTexture(); gl.bindTexture(gl.TEXTURE_2D, tex);
  gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true); gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, true);
  gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, img); gl.generateMipmap(gl.TEXTURE_2D);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  res(true); }; img.src = ${JSON.stringify(disk)}; });
window.draw = (u) => {
  const all = Object.assign({}, DEF, u); gl.viewport(0, 0, cv.width, cv.height); gl.useProgram(prog);
  gl.uniform2f(gl.getUniformLocation(prog, 'u_resolution'), cv.width, cv.height);
  gl.uniform1i(gl.getUniformLocation(prog, 'u_disk'), 0);
  for (const [k, v] of Object.entries(all)) { const l = gl.getUniformLocation(prog, k); if (!l) continue;
    if (Array.isArray(v)) { if (v.length === 2) gl.uniform2f(l, v[0], v[1]); else gl.uniform4fv(l, new Float32Array(v)); }
    else gl.uniform1f(l, v); }
  gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT); gl.drawArrays(gl.TRIANGLES, 0, 3);
};
window.shot = (u) => { draw(u); return cv.toDataURL('image/png'); };
window.bench = (n) => { draw({}); gl.finish(); const t0 = performance.now();
  for (let i = 0; i < n; i++) { draw({ u_time: 12 + i / 60 }); gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, new Uint8Array(4)); }
  return (performance.now() - t0) / n; };
window.check = (srcs) => srcs.map((src) => { try {
  const p = gl.createProgram(); gl.attachShader(p, sh(gl.VERTEX_SHADER, VERT)); gl.attachShader(p, sh(gl.FRAGMENT_SHADER, src));
  gl.bindAttribLocation(p, 0, 'a'); gl.linkProgram(p);
  if (!gl.getProgramParameter(p, gl.LINK_STATUS)) return { ok: false, error: gl.getProgramInfoLog(p) };
  const n = gl.getProgramParameter(p, gl.ACTIVE_UNIFORMS); const u = [];
  for (let i = 0; i < n; i++) u.push(gl.getActiveUniform(p, i).name.split('[')[0]);
  return { ok: true, uniforms: u.sort() }; } catch (e) { return { ok: false, error: String(e.message || e) }; } });
window.renderer = () => { const e = gl.getExtension('WEBGL_debug_renderer_info');
  return e ? gl.getParameter(e.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER); };
</script></body></html>`;

const { chromium } = await loadPlaywright();
const browser = await chromium.launch({
  args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist', '--enable-webgl'],
});
try {
  const page = await browser.newPage({ viewport: { width: W, height: H } });
  await page.setContent(html);
  await page.evaluate(() => window.__ready);
  if (opt.check) {
    const res = await page.evaluate((s) => window.check(s), [fragFlat, fragRt]);
    console.log(JSON.stringify({ renderer: await page.evaluate(() => window.renderer()), flat: res[0], ultra: res[1] }));
    process.exit(res.every((r) => r.ok) ? 0 : 1);
  }
  const err = await page.evaluate(() => window.__err);
  if (err) throw new Error(`shader failed: ${err}`);
  mkdirSync(opt.out, { recursive: true });
  console.log(`renderer: ${await page.evaluate(() => window.renderer())}  frame ${W}x${H}`);
  for (const f of opt.frames) {
    const t0 = Date.now();
    const url = await page.evaluate((u) => window.shot(u), f.u);
    writeFileSync(join(opt.out, `${f.name}.png`), Buffer.from(url.split(',')[1], 'base64'));
    console.log(`${f.name}.png  ${JSON.stringify(f.u)}  ${Date.now() - t0} ms`);
  }
  if (opt.bench > 0) {
    const ms = await page.evaluate((n) => window.bench(n), opt.bench);
    console.log(`bench: ${ms.toFixed(2)} ms/frame at ${W}x${H} (~${(1000 / ms).toFixed(1)} fps on this renderer)`);
  }
} finally {
  await browser.close();
}
