#!/usr/bin/env node
// OrionOS: render the wallpaper and lock-screen stills from the living black hole shader (Art Update §4, §8).
//
//   node os/theme/wallpapers/render-wallpapers.mjs [--shader FILE] [--disk FILE] [--out DIR]
//        [--size 3840x2160] [--only NAME[,NAME]] [--format png|jpeg] [--time SECONDS]
//
// Uses headless Chromium through Playwright (WebGL2 in software, so it runs on a build machine without a GPU).
// Playwright is looked up as `playwright` from this repo, then $PLAYWRIGHT_MODULE, then /opt/npm-tools.
// The shader follows the contract in os/theme/blackhole/README.md (art-core): GLSL ES 3.00 with u_resolution,
// u_time, u_disk (the painted disk, black-hole-core.png), u_spin, u_temp, u_ring, u_glow, u_turbulence, u_size,
// u_tilt, u_boot (<0 = fully formed), u_voice, u_ripple, u_quality (3 = ultra), u_parallax.
//
// Output (default os/theme/wallpapers/): orion-blackhole-4k  (desktop, sway background)
//                                       orion-interstellar-4k (edge-on "Interstellar" view, swaylock)
//                                       orion-night-4k, orion-warm-4k (cool violet / warm variants)
// install-desktop-theme.sh copies them to /usr/share/backgrounds/orion and links desktop.* and lock.* to them.
import { createRequire } from 'node:module';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO = resolve(HERE, '../../..');

// name -> uniforms (anything not listed uses BASE)
const BASE = {
  u_spin: 1, u_temp: 0, u_ring: 1, u_glow: 1, u_turbulence: 0.25, u_size: 1, u_tilt: 0.18,
  u_boot: -1, u_voice: 0, u_ripple: 0, u_quality: 3, u_parallax: [0, 0],
};
const STILLS = {
  'orion-blackhole-4k': {},
  'orion-interstellar-4k': { u_tilt: 1, u_glow: 1.1, u_size: 0.95 },
  'orion-night-4k': { u_temp: -0.8, u_glow: 0.85, u_turbulence: 0.15 },
  'orion-warm-4k': { u_temp: 0.7, u_ring: 1.15, u_turbulence: 0.35 },
};

function args() {
  const a = process.argv.slice(2);
  const o = {};
  for (let i = 0; i < a.length; i++) {
    if (!a[i].startsWith('--')) continue;
    o[a[i].slice(2)] = a[i + 1] && !a[i + 1].startsWith('--') ? a[++i] : true;
  }
  return o;
}

function loadPlaywright() {
  const tries = [join(REPO, 'package.json'), process.env.PLAYWRIGHT_MODULE && join(process.env.PLAYWRIGHT_MODULE, 'x'),
    '/opt/npm-tools/node_modules/playwright/x'].filter(Boolean);
  for (const base of tries) {
    try {
      const req = createRequire(base);
      return req(base.endsWith('/x') ? dirname(base) : 'playwright');
    } catch { /* next */ }
  }
  throw new Error('Playwright not found: npm i -D playwright (its Chromium must already be installed)');
}

const firstExisting = (...p) => p.find((f) => f && existsSync(f));

const page = (frag, diskDataUrl, w, h, uniforms) => `<!doctype html><html><body style="margin:0;background:#05070E">
<canvas id="c" width="${w}" height="${h}"></canvas>
<script>
(async () => {
  const c = document.getElementById('c');
  const gl = c.getContext('webgl2', { preserveDrawingBuffer: true, antialias: false, premultipliedAlpha: false });
  if (!gl) { window.__err = 'no WebGL2'; return; }
  let fs = ${JSON.stringify(frag)};
  if (!/^\\s*#version/.test(fs)) fs = '#version 300 es\\nprecision highp float;\\n' + fs;
  const vs = '#version 300 es\\nin vec2 p; out vec2 v_uv; void main(){ v_uv = p*0.5+0.5; gl_Position = vec4(p,0.,1.); }';
  const sh = (t, s) => { const o = gl.createShader(t); gl.shaderSource(o, s); gl.compileShader(o);
    if (!gl.getShaderParameter(o, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(o)); return o; };
  try {
    const pr = gl.createProgram();
    gl.attachShader(pr, sh(gl.VERTEX_SHADER, vs)); gl.attachShader(pr, sh(gl.FRAGMENT_SHADER, fs));
    gl.bindAttribLocation(pr, 0, 'p'); gl.linkProgram(pr);
    if (!gl.getProgramParameter(pr, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(pr));
    gl.useProgram(pr);
    const b = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, b);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1,-1, 3,-1, -1,3]), gl.STATIC_DRAW);
    gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
    const img = new Image(); img.src = ${JSON.stringify(diskDataUrl)}; await img.decode();
    const tex = gl.createTexture(); gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, img);
    gl.generateMipmap(gl.TEXTURE_2D);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    const U = ${JSON.stringify(uniforms)};
    const set = (n, v) => { const l = gl.getUniformLocation(pr, n); if (l === null) return;
      if (Array.isArray(v)) gl.uniform2f(l, v[0], v[1]); else gl.uniform1f(l, v); };
    for (const [n, v] of Object.entries(U)) set(n, v);
    set('u_resolution', [c.width, c.height]);
    const d = gl.getUniformLocation(pr, 'u_disk'); if (d !== null) gl.uniform1i(d, 0);
    gl.viewport(0, 0, c.width, c.height); gl.drawArrays(gl.TRIANGLES, 0, 3); gl.finish();
    window.__png = c.toDataURL('image/png');
  } catch (e) { window.__err = String(e && e.message || e); }
})();
</script></body></html>`;

async function main() {
  const o = args();
  const shader = firstExisting(o.shader, join(REPO, 'os/theme/blackhole/blackhole.frag'));
  if (!shader) throw new Error('shader not found: os/theme/blackhole/blackhole.frag (or pass --shader)');
  const disk = firstExisting(o.disk, join(REPO, 'os/theme/blackhole/black-hole-core.png'),
    join(REPO, 'apps/desktop/public/black-hole-core.png'));
  if (!disk) throw new Error('disk painting not found (black-hole-core.png); pass --disk');
  const out = resolve(o.out || HERE);
  const [w, h] = String(o.size || '3840x2160').split('x').map(Number);
  const fmt = o.format === 'jpeg' || o.format === 'jpg' ? 'jpeg' : 'png';
  const only = o.only ? String(o.only).split(',') : Object.keys(STILLS);
  const time = Number(o.time ?? 42);
  mkdirSync(out, { recursive: true });

  const { chromium } = loadPlaywright();
  const browser = await chromium.launch({ args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] });
  const frag = readFileSync(shader, 'utf8');
  const diskUrl = 'data:image/png;base64,' + readFileSync(disk).toString('base64');
  try {
    for (const name of only) {
      if (!STILLS[name]) throw new Error(`unknown still ${name} (known: ${Object.keys(STILLS).join(', ')})`);
      const uniforms = { ...BASE, u_time: time, ...STILLS[name] };
      const p = await browser.newPage({ viewport: { width: 640, height: 360 } });
      await p.setContent(page(frag, diskUrl, w, h, uniforms));
      await p.waitForFunction(() => window.__png || window.__err, null, { timeout: 600_000 });
      const err = await p.evaluate(() => window.__err);
      if (err) throw new Error(`${name}: ${err}`);
      let buf = Buffer.from((await p.evaluate(() => window.__png)).split(',')[1], 'base64');
      if (fmt === 'jpeg') {
        await p.setContent(`<img id="i" src="data:image/png;base64,${buf.toString('base64')}">`);
        const j = await p.evaluate(async () => { const i = document.getElementById('i'); await i.decode();
          const c = new OffscreenCanvas(i.naturalWidth, i.naturalHeight); c.getContext('2d').drawImage(i, 0, 0);
          const b = await c.convertToBlob({ type: 'image/jpeg', quality: 0.95 });
          return await new Promise((r) => { const f = new FileReader(); f.onload = () => r(String(f.result).split(',')[1]);
            f.readAsDataURL(b); }); });
        buf = Buffer.from(j, 'base64');
      }
      const file = join(out, `${name}.${fmt === 'jpeg' ? 'jpg' : 'png'}`);
      writeFileSync(file, buf);
      console.log(`${file}  ${w}x${h}  ${(buf.length / 1048576).toFixed(1)} MiB`);
      await p.close();
    }
  } finally {
    await browser.close();
  }
}

main().catch((e) => { console.error(String(e.message || e)); process.exit(1); });
