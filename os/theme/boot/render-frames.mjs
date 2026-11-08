#!/usr/bin/env node
// Orion boot frames: renders the black hole shader's boot timeline into the PNG frame sequence that Plymouth
// plays (Plymouth can't run shaders). Same shader as the HUD and the greeter, so boot -> login -> desktop is one
// continuous shot.
//
//   node os/theme/boot/render-frames.mjs                    render with the real shader into the Plymouth theme
//   node os/theme/boot/render-frames.mjs --sheet sheet.png  ... and write a contact sheet to check the sequence
//   options: --frag F --disk PNG --font FILE --out DIR --fps 15 --crop 800x680 --wordmark-y 0.37 --quality 45-80
//            --lock-still F.png (a 4K still of the formed hole, e.g. for swaylock)
//
// Output (default os/image/files/usr/share/plymouth/themes/orion/frames/):
//   b-000.png .. b-NNN.png   the boot timeline, u_boot 0 -> 6 s (spark, star fall, collapse, accretion, wordmark)
//   i-000.png .. i-NNN.png   the formed hole turning (u_boot < 0), continuing from the last boot frame: Plymouth
//                            loops it while it waits (vault prompt) and slows it to a stop for sleep
//   count.png                a blank image whose width/height are the boot/idle frame counts (Plymouth can't list
//                            a folder, and decoding one 1-pixel-high image is cheaper than probing)
//   layout.json              geometry and timing for the greeter / HUD hand-off
// Shutdown plays the boot frames backwards, so it needs no frames of its own.
//
// Needs Playwright with Chromium (headless WebGL2 through SwiftShader is fine). It looks for playwright in the
// repo's node_modules, then /opt/node-tools and /opt/npm-tools; browsers in $PLAYWRIGHT_BROWSERS_PATH or
// /opt/pw-browsers. PNGs are shrunk with pngquant (+ oxipng when present), else Pillow, else left as they are.
import { createRequire } from 'node:module';
import { execFileSync, spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, '../../..');

// ---- timeline (must match orion.script and the shader contract) ------------------------------------------
export const TIMELINE = { spark: 0, starfall: 0.8, collapse: 2.6, accretion: 3.3, wordmark: 4.3, settled: 6.0 };

function args() {
  const a = process.argv.slice(2), o = {};
  for (let i = 0; i < a.length; i++) {
    if (!a[i].startsWith('--')) continue;
    const k = a[i].slice(2), v = a[i + 1] && !a[i + 1].startsWith('--') ? a[++i] : true;
    o[k] = v;
  }
  return o;
}
const A = args();
if (A.help) {
  console.log(fs.readFileSync(fileURLToPath(import.meta.url), 'utf8').split('\n').slice(1, 25).join('\n'));
  process.exit(0);
}
const FRAG = path.resolve(A.frag || path.join(REPO, 'os/theme/blackhole/blackhole.frag'));
const DISK = path.resolve(A.disk || path.join(REPO, 'apps/desktop/public/black-hole-core.png'));
const OUT = path.resolve(A.out || path.join(REPO, 'os/image/files/usr/share/plymouth/themes/orion/frames'));
const FPS = Number(A.fps || 15);
const IDLE_S = Number(A.idle || 1.6);
const [W, H] = String(A.size || '1280x720').split('x').map(Number);   // the screen the shader renders for
const [CW, CH] = String(A.crop || '800x680').split('x').map(Number);  // the centre region kept
const QUALITY = String(A.quality || '45-80');
const WORDMARK_Y = Number(A['wordmark-y'] || 0.37);   // the wordmark's centre below the hole's, in short-side units

if (!fs.existsSync(FRAG)) {
  console.error(`render-frames: no shader at ${FRAG} (art-core's os/theme/blackhole/blackhole.frag, or --frag)`);
  process.exit(1);
}

// ---- playwright -----------------------------------------------------------------------------------------
if (!process.env.PLAYWRIGHT_BROWSERS_PATH && fs.existsSync('/opt/pw-browsers')) process.env.PLAYWRIGHT_BROWSERS_PATH = '/opt/pw-browsers';
function loadPlaywright() {
  const bases = [REPO, process.env.ORION_PLAYWRIGHT_DIR, '/opt/node-tools', '/opt/npm-tools'].filter(Boolean);
  for (const b of bases) {
    try { return createRequire(path.join(b, 'package.json'))('playwright'); } catch { /* next */ }
  }
  console.error('render-frames: playwright not found (npm i -D playwright, or set ORION_PLAYWRIGHT_DIR)');
  process.exit(1);
}

// ---- font: Unbounded from os/theme/fonts, else a system font --------------------------------------------
function findFont() {
  if (A.font) return path.resolve(A.font);
  const dir = path.join(REPO, 'os/theme/fonts');
  let files = [];
  try { files = fs.readdirSync(dir, { recursive: true }).map(String); } catch { return null; }
  const cands = files.filter((f) => /unbounded/i.test(f) && /\.(ttf|otf|woff2?)$/i.test(f));
  const rank = (f) => (/variable|\[wght\]/i.test(f) ? 0 : /regular|-400/i.test(f) ? 1 : /medium|-500/i.test(f) ? 2 : 5);
  cands.sort((x, y) => rank(x) - rank(y));
  return cands.length ? path.join(dir, cands[0]) : null;
}
const FONT = findFont();
const mime = (f) => ({ '.ttf': 'font/ttf', '.otf': 'font/otf', '.woff': 'font/woff', '.woff2': 'font/woff2', '.png': 'image/png' })[path.extname(f).toLowerCase()];
const dataUrl = (f) => `data:${mime(f)};base64,${fs.readFileSync(f).toString('base64')}`;

// ---- the page that renders ------------------------------------------------------------------------------
const PAGE = `<!doctype html><html><body style="margin:0;background:#000">
<canvas id="gl"></canvas><canvas id="out"></canvas>
<script>
const VERT = \`#version 300 es
in vec2 a_pos; out vec2 v_uv; out vec2 vUv; out vec2 v_texcoord;
void main(){ v_uv = a_pos * 0.5 + 0.5; vUv = v_uv; v_texcoord = v_uv; gl_Position = vec4(a_pos, 0.0, 1.0); }\`;
let gl, prog, U = {}, cfg, out, octx, dust = [], fontOk = false;
const DEFAULTS = { u_spin: 1, u_temp: 0, u_ring: 1, u_glow: 1, u_turbulence: 0.2, u_size: 1, u_tilt: 0,
  u_voice: 0, u_ripple: 0, u_quality: 3 };
function rng(seed) { return () => ((seed = (seed * 16807) % 2147483647) - 1) / 2147483646; }
const smooth = (a, b, x) => { const t = Math.max(0, Math.min(1, (x - a) / (b - a))); return t * t * (3 - 2 * t); };

window.init = async (c) => {
  cfg = c;
  const cv = document.getElementById('gl');
  cv.width = c.W; cv.height = c.H;
  gl = cv.getContext('webgl2', { preserveDrawingBuffer: true, antialias: false, premultipliedAlpha: false });
  if (!gl) return 'no WebGL2';
  let src = c.frag;
  if (!/^\\s*#version/.test(src)) src = '#version 300 es\\n' + src;
  const sh = (type, s) => { const o = gl.createShader(type); gl.shaderSource(o, s); gl.compileShader(o);
    if (!gl.getShaderParameter(o, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(o)); return o; };
  try {
    prog = gl.createProgram();
    gl.attachShader(prog, sh(gl.VERTEX_SHADER, VERT));
    gl.attachShader(prog, sh(gl.FRAGMENT_SHADER, src));
    gl.bindAttribLocation(prog, 0, 'a_pos');
    gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(prog));
  } catch (e) { return 'shader: ' + e.message; }
  gl.useProgram(prog);
  const buf = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, buf);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
  gl.enableVertexAttribArray(0);
  gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
  const n = gl.getProgramParameter(prog, gl.ACTIVE_UNIFORMS);
  for (let i = 0; i < n; i++) { const u = gl.getActiveUniform(prog, i); U[u.name] = gl.getUniformLocation(prog, u.name); }
  // the painted disk
  const img = new Image(); img.src = c.disk; await img.decode();
  const tex = gl.createTexture();
  gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, tex);
  gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, img);
  gl.generateMipmap(gl.TEXTURE_2D);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  if (U.u_disk) gl.uniform1i(U.u_disk, 0);
  // the wordmark font
  if (c.font) { try { const f = new FontFace('OrionMark', 'url(' + c.font + ')'); await f.load(); document.fonts.add(f); fontOk = true; } catch (e) {} }
  out = document.getElementById('out'); out.width = c.CW; out.height = c.CH; octx = out.getContext('2d');
  makeDust();
  return fontOk ? 'ok' : 'ok (wordmark in the system font)';
};

function markFont(px) { return (fontOk ? '400 ' : '600 ') + px + 'px ' + (fontOk ? 'OrionMark' : 'sans-serif'); }
function makeDust() {
  // Sample the wordmark into points: star dust that condenses into the letters.
  const m = Math.min(cfg.W, cfg.H), px = Math.round(m * 0.062);
  const c = document.createElement('canvas'); c.width = cfg.CW; c.height = px * 2;
  const g = c.getContext('2d');
  g.font = markFont(px); g.textAlign = 'center'; g.textBaseline = 'middle'; g.fillStyle = '#fff';
  if ('letterSpacing' in g) g.letterSpacing = Math.round(px * 0.14) + 'px';
  g.fillText('orion', c.width / 2, c.height / 2);
  const d = g.getImageData(0, 0, c.width, c.height).data, r = rng(4242), step = 2;
  for (let y = 0; y < c.height; y += step) for (let x = 0; x < c.width; x += step) {
    if (d[(y * c.width + x) * 4 + 3] > 140 && r() < 0.8) {
      const ang = r() * Math.PI * 2, dist = m * (0.05 + r() * 0.22);
      dust.push({ x, y: y - c.height / 2, ang, dist, spin: (r() < 0.5 ? -1 : 1) * (0.9 + r() * 1.4),
                  delay: r() * 0.35, tw: r() * 6.28, tint: r() });
    }
  }
  cfg.markPx = px; cfg.markCanvas = c;
}

window.frame = (boot, time) => {
  gl.viewport(0, 0, cfg.W, cfg.H);
  const set = (k, v) => { if (U[k]) gl.uniform1f(U[k], v); };
  for (const [k, v] of Object.entries(DEFAULTS)) set(k, v);
  if (U.u_resolution) gl.uniform2f(U.u_resolution, cfg.W, cfg.H);
  if (U.u_parallax) gl.uniform2f(U.u_parallax, 0, 0);
  set('u_time', time); set('u_boot', boot);
  gl.drawArrays(gl.TRIANGLES, 0, 3);
  // crop the centre, add the wordmark, fade the edges to black
  const W = cfg.W, H = cfg.H, CW = cfg.CW, CH = cfg.CH, m = Math.min(W, H);
  octx.globalCompositeOperation = 'source-over'; octx.globalAlpha = 1;
  octx.fillStyle = '#000'; octx.fillRect(0, 0, CW, CH);
  octx.drawImage(gl.canvas, (W - CW) / 2, (H - CH) / 2, CW, CH, 0, 0, CW, CH);
  // vignette: an ellipse that fades to black well inside the crop, so the frame sits on a black screen
  octx.globalAlpha = 1;
  octx.globalCompositeOperation = 'destination-in';
  octx.save(); octx.translate(CW / 2, CH / 2); octx.scale(CW / 2, CH / 2);
  const g = octx.createRadialGradient(0, 0, 0, 0, 0, 1);
  g.addColorStop(0, 'rgba(0,0,0,1)'); g.addColorStop(0.62, 'rgba(0,0,0,1)'); g.addColorStop(0.98, 'rgba(0,0,0,0)');
  octx.fillStyle = g; octx.fillRect(-1, -1, 2, 2); octx.restore();
  octx.globalCompositeOperation = 'destination-over'; octx.fillStyle = '#000'; octx.fillRect(0, 0, CW, CH);
  // the wordmark goes on top, so the vignette never dims it
  const b = boot < 0 ? 99 : boot, t0 = cfg.T.wordmark;
  const my = CH / 2 + m * cfg.WORDMARK_Y;
  if (b > t0) {
    octx.globalCompositeOperation = 'lighter';
    for (const p of dust) {
      const q = Math.max(0, Math.min(1, (b - t0 - p.delay) / 1.15));
      if (q <= 0) continue;
      const e = 1 - Math.pow(1 - q, 3);                         // ease out: dust settles into the letters
      const rr = p.dist * (1 - e), a = p.ang + p.spin * (1 - e) * 2.2;
      const x = CW / 2 - cfg.markCanvas.width / 2 + p.x + Math.cos(a) * rr;
      const y = my + p.y + Math.sin(a) * rr * 0.45;
      const fadeIn = Math.min(1, q * 4), fadeOut = 1 - smooth(5.3, 5.95, b);
      const tw = 0.65 + 0.35 * Math.sin(p.tw + b * 9);
      octx.globalAlpha = 0.85 * fadeIn * tw * (0.35 + 0.65 * fadeOut);
      octx.fillStyle = p.tint < 0.25 ? '#9D85FF' : p.tint < 0.4 ? '#7FF2FF' : '#D8DEFF';
      const s = 1.1 + (1 - e) * 0.9;
      octx.fillRect(x - s / 2, y - s / 2, s, s);
    }
    // the crisp letters arrive as the dust settles
    const k = smooth(4.9, 5.9, b);
    if (k > 0) {
      octx.globalCompositeOperation = 'source-over';
      octx.globalAlpha = k;
      octx.font = markFont(cfg.markPx); octx.textAlign = 'center'; octx.textBaseline = 'middle';
      if ('letterSpacing' in octx) octx.letterSpacing = Math.round(cfg.markPx * 0.14) + 'px';
      octx.shadowColor = 'rgba(124,92,255,0.55)'; octx.shadowBlur = 14 * k;
      octx.fillStyle = '#D8DEFF';
      octx.fillText('orion', CW / 2, my);
      octx.shadowBlur = 0;
    }
  }
  return out.toDataURL('image/png');
};

window.still = (w, h, time) => {     // a full-screen still of the formed hole (lock screen)
  gl.canvas.width = w; gl.canvas.height = h; gl.viewport(0, 0, w, h);
  const set = (k, v) => { if (U[k]) gl.uniform1f(U[k], v); };
  for (const [k, v] of Object.entries(DEFAULTS)) set(k, v);
  if (U.u_resolution) gl.uniform2f(U.u_resolution, w, h);
  if (U.u_parallax) gl.uniform2f(U.u_parallax, 0, 0);
  set('u_time', time); set('u_boot', -1);
  gl.drawArrays(gl.TRIANGLES, 0, 3);
  const url = gl.canvas.toDataURL('image/png');
  gl.canvas.width = cfg.W; gl.canvas.height = cfg.H;
  return url;
};

window.sheet = async (urls, labels, cols, tw) => {
  const th = Math.round(tw * cfg.CH / cfg.CW), rows = Math.ceil(urls.length / cols);
  const c = document.createElement('canvas'); c.width = cols * tw; c.height = rows * (th + 16);
  const g = c.getContext('2d'); g.fillStyle = '#111'; g.fillRect(0, 0, c.width, c.height);
  for (let i = 0; i < urls.length; i++) {
    const im = new Image(); im.src = urls[i]; await im.decode();
    const x = (i % cols) * tw, y = Math.floor(i / cols) * (th + 16);
    g.drawImage(im, x, y, tw, th);
    g.fillStyle = '#9DB0FF'; g.font = '11px monospace'; g.fillText(labels[i], x + 4, y + th + 12);
  }
  return c.toDataURL('image/png');
};
</script></body></html>`;

// ---- run ------------------------------------------------------------------------------------------------
const { chromium } = loadPlaywright();
const browser = await chromium.launch({
  args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'],
});
const page = await browser.newPage({ viewport: { width: 400, height: 300 } });
page.on('console', (m) => { if (m.type() === 'error') console.error('[page]', m.text()); });
await page.setContent(PAGE);
const status = await page.evaluate((c) => window.init(c), {
  frag: fs.readFileSync(FRAG, 'utf8'), disk: dataUrl(DISK), font: FONT ? dataUrl(FONT) : null,
  W, H, CW, CH, T: TIMELINE, WORDMARK_Y,
});
if (!status.startsWith('ok')) { console.error('render-frames:', status); await browser.close(); process.exit(1); }
console.log(`render-frames: ${path.relative(REPO, FRAG)} at ${W}x${H}, keeping the centre ${CW}x${CH}; ${status}${FONT ? ' (' + path.basename(FONT) + ')' : ''}`);

fs.mkdirSync(OUT, { recursive: true });
for (const f of fs.readdirSync(OUT)) if (/^(b|i)-\d+\.png$|^count\.png$/.test(f)) fs.rmSync(path.join(OUT, f));
const pad = (n) => String(n).padStart(3, '0');
const write = (file, url) => fs.writeFileSync(file, Buffer.from(url.split(',')[1], 'base64'));
const nBoot = Math.round(TIMELINE.settled * FPS) + 1;     // includes t = 0 and t = 6.0
const nIdle = Math.round(IDLE_S * FPS);
const written = [];
for (let i = 0; i < nBoot; i++) {
  const t = i / FPS;
  const f = path.join(OUT, `b-${pad(i)}.png`);
  write(f, await page.evaluate(([b, tt]) => window.frame(b, tt), [t, t]));
  written.push(f);
}
for (let i = 0; i < nIdle; i++) {
  const t = TIMELINE.settled + (i + 1) / FPS;                // continues where the boot ends
  const f = path.join(OUT, `i-${pad(i)}.png`);
  write(f, await page.evaluate(([b, tt]) => window.frame(b, tt), [-1, t]));
  written.push(f);
}
if (A['lock-still']) {
  write(path.resolve(A['lock-still']), await page.evaluate(() => window.still(3840, 2160, 6.0)));
  console.log('render-frames: lock-screen still ->', A['lock-still']);
}

// count.png: width = boot frames, height = idle frames (a 1-bit grey PNG)
writeCountPng(path.join(OUT, 'count.png'), nBoot, nIdle);
fs.writeFileSync(path.join(OUT, 'layout.json'), JSON.stringify({
  fps: FPS, renderSize: [W, H], crop: [CW, CH], bootFrames: nBoot, idleFrames: nIdle, timeline: TIMELINE,
  wordmarkY: WORDMARK_Y, wordmarkPx: Math.round(Math.min(W, H) * 0.062),
  handoff: { u_time: TIMELINE.settled, u_boot: -1, note: 'the last boot frame is u_boot = 6.0 at u_time = 6.0; scale = min(screen)/720' },
  shader: path.relative(REPO, FRAG), font: FONT ? path.basename(FONT) : 'system sans-serif',
}, null, 2) + '\n');

// ---- shrink -------------------------------------------------------------------------------------------
const has = (cmd) => spawnSync('sh', ['-c', `command -v ${cmd}`]).status === 0;
const rawBytes = written.reduce((s, f) => s + fs.statSync(f).size, 0);
let how = 'none';
if (has('pngquant')) {
  for (let i = 0; i < written.length; i += 40) {
    spawnSync('pngquant', ['--force', '--ext', '.png', '--skip-if-larger', '--quality', QUALITY, '--speed', '1', '--strip', ...written.slice(i, i + 40)]);
  }
  how = 'pngquant';
  if (has('oxipng')) { spawnSync('oxipng', ['-q', '-o', '3', '--strip', 'safe', ...written]); how += ' + oxipng'; }
} else if (spawnSync('python3', ['-c', 'import PIL']).status === 0) {
  execFileSync('python3', ['-c', `
import sys
from PIL import Image
for f in sys.argv[1:]:
    Image.open(f).convert('RGB').quantize(colors=192, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE).save(f, optimize=True)
`, ...written]);
  how = 'Pillow quantize';
}
const bytes = written.reduce((s, f) => s + fs.statSync(f).size, 0);
const mb = (n) => (n / 1048576).toFixed(1) + ' MB';
console.log(`render-frames: ${nBoot} boot + ${nIdle} idle frames -> ${path.relative(REPO, OUT) || OUT}`);
console.log(`render-frames: ${mb(rawBytes)} raw, ${mb(bytes)} after ${how} (initramfs adds them as they are)`);

// ---- contact sheet ------------------------------------------------------------------------------------
if (A.sheet) {
  const pick = [];
  for (let i = 0; i < nBoot; i += 5) pick.push(i);
  if (pick[pick.length - 1] !== nBoot - 1) pick.push(nBoot - 1);
  const urls = pick.map((i) => dataUrl(path.join(OUT, `b-${pad(i)}.png`)));
  const labels = pick.map((i) => `b-${pad(i)}  ${(i / FPS).toFixed(2)}s`);
  urls.push(dataUrl(path.join(OUT, `i-${pad(Math.floor(nIdle / 2))}.png`))); labels.push('idle');
  write(path.resolve(A.sheet), await page.evaluate(([u, l]) => window.sheet(u, l, 5, 256), [urls, labels]));
  console.log('render-frames: contact sheet ->', A.sheet);
}
await browser.close();

function writeCountPng(file, w, h) {
  // minimal valid PNG: greyscale, 1 bit, all zero, zlib "stored"
  const zlib = createRequire(import.meta.url)('node:zlib');
  const row = Buffer.alloc(1 + Math.ceil(w / 8));
  const raw = Buffer.concat(Array.from({ length: h }, () => row));
  const crcT = Array.from({ length: 256 }, (_, n) => { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; return c >>> 0; });
  const crc = (b) => { let c = 0xffffffff; for (const x of b) c = crcT[(c ^ x) & 255] ^ (c >>> 8); return (c ^ 0xffffffff) >>> 0; };
  const chunk = (type, data) => { const len = Buffer.alloc(4); len.writeUInt32BE(data.length); const td = Buffer.concat([Buffer.from(type), data]); const c = Buffer.alloc(4); c.writeUInt32BE(crc(td)); return Buffer.concat([len, td, c]); };
  const ihdr = Buffer.alloc(13); ihdr.writeUInt32BE(w, 0); ihdr.writeUInt32BE(h, 4); ihdr[8] = 1; ihdr[9] = 0;
  fs.writeFileSync(file, Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk('IHDR', ihdr),
    chunk('IDAT', zlib.deflateSync(raw, { level: 9 })), chunk('IEND', Buffer.alloc(0))]));
}
