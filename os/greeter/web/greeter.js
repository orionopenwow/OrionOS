/* Orion login screen.
 *
 * Talks to the launcher (orion-greeter) through window.webkit.messageHandlers.orion:
 *   page -> launcher  {"type":"login","password":"...","target":"orion"|"rescue"}   check the password with greetd
 *                     {"type":"login","nopass":true,"target":...}   start without a password (auto mode only)
 *                     {"type":"start"}                     the black hole is born: start the session now
 *   launcher -> page  orionGreeter.result({ok, message, reason})   answer to "login" (reason "needs_password":
 *                                                         auto mode, but PAM asked for one: show the field)
 *                     orionGreeter.startFailed(message)    the session could not start
 * Opened in a normal browser (no launcher) it runs as a demo: the password "orion" works.
 * With web/shader.js (the shared black hole shader) the background is the live black hole; ?nohole forces the
 * 2D sky.
 */
(() => {
  'use strict';

  const CFG = Object.assign({ user: 'orion', name: 'Orion', avatar: '', reducedMotion: false, mode: 'password',
                              rescue: true },
                            window.ORION_GREETER || {});
  const native = window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.orion;
  const params = new URLSearchParams(location.search);
  const DEMO = !native;
  // Two ways in (greeter_logic.py): the Orion session, or the plain Rescue desktop. In "auto" mode there is no
  // password to type: the buttons start straight away (?auto in a browser shows that mode).
  let target = 'orion';
  let mode = (params.has('auto') ? 'auto' : CFG.mode) === 'auto' ? 'auto' : 'password';

  const $ = (id) => document.getElementById(id);
  const sky = $('sky'), ctx = sky.getContext('2d');
  const pw = $('pw'), form = $('login'), statusEl = $('status'), capsEl = $('caps');
  const reduced = CFG.reducedMotion || params.has('reduced') ||
                  (window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches);
  if (reduced) document.body.classList.add('reduced');

  // ------------------------------------------------------------------ who, when
  $('name').textContent = CFG.name || CFG.user;
  pw.setAttribute('aria-label', `Password for ${CFG.name || CFG.user}`);
  $('pw-label').textContent = `Password for ${CFG.name || CFG.user}`;
  if (CFG.avatar) {
    const img = new Image();
    img.alt = '';
    img.src = CFG.avatar;
    img.onload = () => $('avatar').replaceChildren(img);
  } else {
    $('initial').textContent = (CFG.name || CFG.user || 'O').trim().charAt(0).toUpperCase();
  }

  const clockEl = $('clock'), dateEl = $('date');
  function tick() {
    const now = params.has('time') ? new Date(params.get('time')) : new Date();
    clockEl.textContent = now.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
    clockEl.dateTime = now.toISOString();
    dateEl.textContent = now.toLocaleDateString([], { weekday: 'long', month: 'long', day: 'numeric' });
  }
  tick();
  setInterval(tick, 1000);

  // ------------------------------------------------------------------ the sky
  let W = 0, H = 0, DPR = 1, stars = [];
  const TAU = Math.PI * 2;
  const tints = ['255,255,255', '216,222,255', '200,190,255', '170,240,255', '255,236,220'];

  function makeSprite(size) {
    const c = document.createElement('canvas');
    c.width = c.height = size;
    const g = c.getContext('2d');
    const r = g.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
    r.addColorStop(0, 'rgba(255,255,255,1)');
    r.addColorStop(0.18, 'rgba(255,255,255,.85)');
    r.addColorStop(0.45, 'rgba(255,255,255,.12)');
    r.addColorStop(1, 'rgba(255,255,255,0)');
    g.fillStyle = r;
    g.fillRect(0, 0, size, size);
    return c;
  }
  const sprite = makeSprite(32);

  // Seeded so screenshots and tests are stable.
  let seed = 7;
  const rnd = () => ((seed = (seed * 16807) % 2147483647) - 1) / 2147483646;

  function resize() {
    DPR = Math.min(window.devicePixelRatio || 1, 1.5);   // software rendering in VMs: stay light
    W = window.innerWidth; H = window.innerHeight;
    sky.width = Math.round(W * DPR); sky.height = Math.round(H * DPR);
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    const n = Math.round(Math.min(1400, (W * H) / 1500));
    seed = 7;
    stars = [];
    for (let i = 0; i < n; i++) {
      const z = Math.pow(rnd(), 2.2);                    // most stars far away, a few close
      stars.push({
        x: rnd() * (W + 80) - 40, y: rnd() * (H + 80) - 40, z,
        s: 0.6 + z * 2.6,                                 // size
        a: 0.25 + z * 0.65 + rnd() * 0.1,                 // brightness
        tw: 0.4 + rnd() * 1.6, ph: rnd() * TAU,           // twinkle speed, phase
        c: tints[(rnd() * tints.length) | 0],
        px: 0, py: 0,                                     // where it was last drawn
      });
    }
  }
  resize();
  window.addEventListener('resize', resize);

  // Mouse parallax (and a slow wander when the mouse is still).
  const mouse = { x: 0, y: 0, sx: 0, sy: 0 };
  window.addEventListener('pointermove', (e) => {
    mouse.x = (e.clientX / W) * 2 - 1;
    mouse.y = (e.clientY / H) * 2 - 1;
  });

  const tintCache = new Map();
  function tinted(c) {
    if (tintCache.has(c)) return tintCache.get(c);
    const t = makeSprite(32), g = t.getContext('2d');
    g.globalCompositeOperation = 'source-atop';
    g.fillStyle = `rgb(${c})`;
    g.fillRect(0, 0, 32, 32);
    tintCache.set(c, t);
    return t;
  }

  function drawCalm(t, dt) {
    ctx.globalCompositeOperation = 'source-over';
    ctx.fillStyle = '#000';
    ctx.fillRect(0, 0, W, H);
    mouse.sx += (mouse.x - mouse.sx) * Math.min(1, dt * 2.2);
    mouse.sy += (mouse.y - mouse.sy) * Math.min(1, dt * 2.2);
    const wx = Math.sin(t * 0.05) * 0.15, wy = Math.cos(t * 0.04) * 0.1;
    const ox = -(mouse.sx + wx) * 34, oy = -(mouse.sy + wy) * 22;
    ctx.globalCompositeOperation = 'lighter';
    for (const s of stars) {
      if (!reduced) {
        s.x -= (2 + s.z * 9) * dt;                          // the slow drift
        s.y -= (0.6 + s.z * 2) * dt;
        if (s.x < -40) s.x += W + 80;
        if (s.y < -40) s.y += H + 80;
      }
      const x = s.x + ox * s.z, y = s.y + oy * s.z;
      const tw = reduced ? 1 : 0.72 + 0.28 * Math.sin(t * s.tw + s.ph);
      const sz = s.s * 3.2;
      ctx.globalAlpha = s.a * tw;
      ctx.drawImage(tinted(s.c), x - sz / 2, y - sz / 2, sz, sz);
      s.px = x; s.py = y;
    }
    ctx.globalAlpha = 1;
  }

  // ------------------------------------------------------------------ the black hole (the shared shader)
  // web/shader.js is made at install time from os/theme/blackhole/blackhole.frag, the shader the boot splash was
  // rendered from and the HUD runs, so the login screen starts on exactly the splash's last frame (hole centred,
  // "orion" beneath it), and hands the same hole on to the HUD (ORION_LOGIN_HANDOFF=blackhole). Without shader.js
  // or WebGL2 the 2D sky further down is used, as before.
  const hole = (() => {
    const S = window.ORION_SHADER;
    if (!S || !S.frag || params.has('nohole')) return null;
    const cv = $('hole'), veil = $('veil');
    let gl = null;
    try { gl = cv.getContext('webgl2', { antialias: false, alpha: false, premultipliedAlpha: false, powerPreference: 'low-power' }); } catch (e) { gl = null; }
    if (!gl) return null;
    const VERT = '#version 300 es\nin vec2 a_pos; out vec2 v_uv; out vec2 vUv; out vec2 v_texcoord;\n' +
      'void main(){ v_uv = a_pos * 0.5 + 0.5; vUv = v_uv; v_texcoord = v_uv; gl_Position = vec4(a_pos, 0.0, 1.0); }';
    let src = S.frag;
    if (!/^\s*#version/.test(src)) src = '#version 300 es\n' + src;
    const compile = (type, text) => {
      const o = gl.createShader(type); gl.shaderSource(o, text); gl.compileShader(o);
      if (!gl.getShaderParameter(o, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(o) || 'compile');
      return o;
    };
    const prog = gl.createProgram();
    try {
      gl.attachShader(prog, compile(gl.VERTEX_SHADER, VERT));
      gl.attachShader(prog, compile(gl.FRAGMENT_SHADER, src));
      gl.bindAttribLocation(prog, 0, 'a_pos');
      gl.linkProgram(prog);
      if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(prog) || 'link');
    } catch (e) {
      console.warn('orion greeter: shader failed, using the 2D sky:', e.message);
      return null;
    }
    gl.useProgram(prog);
    gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
    gl.enableVertexAttribArray(0);
    gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
    const U = {};
    for (let i = 0, n = gl.getProgramParameter(prog, gl.ACTIVE_UNIFORMS); i < n; i++) {
      const u = gl.getActiveUniform(prog, i);
      U[u.name] = gl.getUniformLocation(prog, u.name);
    }
    const tex = gl.createTexture();
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 1, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, new Uint8Array([0, 0, 0, 255]));
    const disk = new Image();
    disk.onload = () => {
      gl.bindTexture(gl.TEXTURE_2D, tex);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, disk);
      gl.generateMipmap(gl.TEXTURE_2D);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    };
    disk.src = S.disk;
    if (U.u_disk) gl.uniform1i(U.u_disk, 0);

    const L = Object.assign({ wordmarkY: 0.37, wordmarkPx: 0.062, handoffTime: 6.0, crop: [800, 680], renderSize: [1280, 720] }, S.layout || {});
    const vctx = veil.getContext('2d');
    const st = { t0: performance.now(), size: 1, spin: 1, ring: 1, glow: 1, px: 0, py: 0, arrived: false, leaveT: 0 };
    // The hole is a little smaller while the login screen is up, so the clock and the field stay off the disk
    // (layout.diskR: the disk's outer radius at u_size 1, in short-side units).
    const restSize = () => Math.max(0.5, Math.min(0.78, (H * 0.5 - 230) / ((L.diskR || 0.33) * Math.min(W, H))));
    let REST = restSize();
    let drawn = false;            // reduced motion: one still frame (redrawn only on resize)
    let cw = 0, ch = 0;
    function resize() {
      const q = Math.min(window.devicePixelRatio || 1, 1);
      cw = Math.round(W * q); ch = Math.round(H * q);
      cv.width = cw; cv.height = ch;
      veil.width = W; veil.height = H;
      REST = restSize();
      drawn = false;
    }
    resize();
    const smooth = (a, b, x) => { const k = clamp((x - a) / (b - a)); return k * k * (3 - 2 * k); };

    // The first second: the boot splash's vignette and wordmark fade away while the hole settles to its login size.
    function drawVeil(e) {
      vctx.clearRect(0, 0, W, H);
      const k = 1 - smooth(0.1, 1.1, e);
      if (k <= 0) { st.arrived = true; return; }
      const m = Math.min(W, H), sc = m / Math.min(L.renderSize[0], L.renderSize[1]);
      const rx = L.crop[0] * sc / 2, ry = L.crop[1] * sc / 2, cx = W / 2, cy = H / 2;
      vctx.save();
      vctx.globalAlpha = k;
      vctx.fillStyle = '#000';
      vctx.beginPath(); vctx.rect(0, 0, W, H); vctx.ellipse(cx, cy, rx, ry, 0, 0, TAU); vctx.fill('evenodd');
      vctx.translate(cx, cy); vctx.scale(rx, ry);
      const g = vctx.createRadialGradient(0, 0, 0, 0, 0, 1);
      g.addColorStop(0, 'rgba(0,0,0,0)'); g.addColorStop(0.62, 'rgba(0,0,0,0)'); g.addColorStop(0.98, 'rgba(0,0,0,1)'); g.addColorStop(1, 'rgba(0,0,0,1)');
      vctx.fillStyle = g; vctx.fillRect(-1, -1, 2, 2);
      vctx.restore();
      // "orion", exactly where the splash drew it
      const px = Math.round(m * L.wordmarkPx);
      vctx.save();
      vctx.globalAlpha = 1 - smooth(0.0, 0.8, e);
      vctx.font = `400 ${px}px Unbounded, "Nunito", sans-serif`;
      vctx.textAlign = 'center'; vctx.textBaseline = 'middle';
      if ('letterSpacing' in vctx) vctx.letterSpacing = Math.round(px * 0.14) + 'px';
      vctx.shadowColor = 'rgba(124,92,255,0.55)'; vctx.shadowBlur = 14;
      vctx.fillStyle = '#D8DEFF';
      vctx.fillText('orion', cx, cy + m * L.wordmarkY);
      vctx.restore();
    }

    function draw(now, dt) {
      const e = (now - st.t0) / 1000;
      if (reduced && drawn && st.arrived) return;
      drawn = true;
      if (!leaving) {
        st.size += ((reduced ? REST : (e < 0.2 ? 1 : REST)) - st.size) * Math.min(1, dt * 2.2);
        if (reduced) st.size = REST;
      }
      if (!st.arrived) drawVeil(reduced ? 2 : e);
      st.px += (mouse.x - st.px) * Math.min(1, dt * 2.2);
      st.py += (mouse.y - st.py) * Math.min(1, dt * 2.2);
      gl.viewport(0, 0, cw, ch);
      const f = (k, v) => { if (U[k]) gl.uniform1f(U[k], v); };
      if (U.u_resolution) gl.uniform2f(U.u_resolution, cw, ch);
      if (U.u_parallax) gl.uniform2f(U.u_parallax, reduced ? 0 : st.px, reduced ? 0 : -st.py);
      f('u_time', L.handoffTime + (reduced ? 0 : e)); f('u_boot', -1);
      f('u_spin', st.spin); f('u_temp', 0); f('u_ring', st.ring); f('u_glow', st.glow); f('u_turbulence', 0.2);
      f('u_size', st.size); f('u_tilt', 0); f('u_voice', 0); f('u_ripple', 0); f('u_quality', reduced ? 0 : 2);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
    }

    // After the right password: the clock, name and field spiral in (DOM, pullUi), the disk spins up, the ring
    // flares, and it settles back to the HUD's starting frame (normal size, normal spin) before the session starts.
    function leave(ms) {
      const p = clamp(ms / PULL);
      st.spin = 1 + 1.8 * Math.sin(Math.PI * clamp(ms / (PULL + BLOOM)));
      st.size = REST + (1 - REST) * easeOut(clamp(ms / (PULL + FLASH + BLOOM)));
      const fl = (ms - PULL) / FLASH;
      st.ring = 1 + 1.6 * Math.exp(-fl * fl * 0.9) + 0.25 * easeIn(p);
      st.glow = 1 + 0.6 * Math.exp(-fl * fl * 0.6);
    }

    document.body.classList.add('has-hole');
    window.addEventListener('resize', resize);
    return { draw, leave };
  })();

  // ------------------------------------------------------------------ the swirl and the birth
  const PULL = 1050, FLASH = 180, BLOOM = 820, HOLD = 260;   // ms; total about 2.3 s with the hold
  const bh = new Image();
  bh.src = 'black-hole.png';
  let leaving = null;   // {t0, cx, cy, maxR, disk: [...]}

  const easeIn = (p) => p * p * p;
  const easeOut = (p) => 1 - Math.pow(1 - p, 3);
  const clamp = (v) => Math.max(0, Math.min(1, v));

  function beginSwirl(now) {
    if (hole) { leaving = { t0: now }; pullUi(); return; }
    const cx = W / 2, cy = H / 2;
    let maxR = 1;
    for (const s of stars) {
      s.r0 = Math.hypot(s.px - cx, s.py - cy);
      s.a0 = Math.atan2(s.py - cy, s.px - cx);
      maxR = Math.max(maxR, s.r0);
    }
    const disk = [];
    seed = 99;
    for (let i = 0; i < 420; i++) {
      const r = 1.25 + Math.pow(rnd(), 1.6) * 3.2;        // in horizon radii
      disk.push({ r, a: rnd() * TAU, w: 0.6 + rnd() * 1.4, k: rnd() });
    }
    leaving = { t0: now, cx, cy, maxR, disk };
    pullUi();
  }

  function starPos(s, p, L) {
    const pull = easeIn(p);
    const r = s.r0 * Math.pow(1 - pull, 1.15);
    const spin = 3.4 * Math.pow(pull, 1.3) * (L.maxR / (s.r0 + L.maxR * 0.12));
    const a = s.a0 + spin;
    return [L.cx + Math.cos(a) * r, L.cy + Math.sin(a) * r * 0.92];
  }

  function drawLeaving(ms) {
    const L = leaving;
    ctx.globalCompositeOperation = 'source-over';
    ctx.fillStyle = '#000';
    ctx.fillRect(0, 0, W, H);
    ctx.globalCompositeOperation = 'lighter';

    // 1. Everything is pulled into a spiral, stretching into streaks.
    if (ms < PULL + 60) {
      const p = clamp(ms / PULL);
      const pPrev = clamp((ms - 70) / PULL);
      ctx.lineCap = 'round';
      for (const s of stars) {
        const [x, y] = starPos(s, p, L);
        const [x0, y0] = starPos(s, pPrev, L);
        const fade = p > 0.92 ? (1 - p) / 0.08 : 1;
        ctx.globalAlpha = Math.min(1, s.a * (1 + p * 1.4)) * fade;
        ctx.strokeStyle = `rgb(${s.c})`;
        ctx.lineWidth = Math.max(0.6, s.s * (1 - p * 0.5));
        ctx.beginPath();
        ctx.moveTo(x0, y0);
        ctx.lineTo(x + 0.01, y);
        ctx.stroke();
      }
      // a faint gathering glow at the centre
      glow(L.cx, L.cy, 30 + 120 * easeIn(p), 0.35 * easeIn(p), '124,92,255');
    }

    // 2. It falls into a point: a short, bright flash.
    const f = (ms - PULL + 60) / (FLASH + 60);
    if (f > 0 && f < 1.6) {
      const k = f < 0.35 ? f / 0.35 : Math.max(0, 1 - (f - 0.35) / 1.25);
      glow(L.cx, L.cy, 12 + 70 * k, k, '255,255,255');
      glow(L.cx, L.cy, 40 + 160 * k, 0.6 * k, '0,229,255');
    }

    // 3. The accretion disk blooms out of it.
    const q = clamp((ms - PULL - FLASH * 0.5) / BLOOM);
    if (q > 0) drawBlackHole(L, easeOut(q), ms / 1000);
    ctx.globalAlpha = 1;
    ctx.globalCompositeOperation = 'source-over';
  }

  function glow(x, y, r, a, c) {
    if (a <= 0 || r <= 0) return;
    const g = ctx.createRadialGradient(x, y, 0, x, y, r);
    g.addColorStop(0, `rgba(${c},${a})`);
    g.addColorStop(0.4, `rgba(${c},${a * 0.35})`);
    g.addColorStop(1, `rgba(${c},0)`);
    ctx.globalAlpha = 1;
    ctx.fillStyle = g;
    ctx.fillRect(x - r, y - r, r * 2, r * 2);
  }

  function drawBlackHole(L, q, t) {
    const R = Math.min(W, H) * 0.085 * q;               // event horizon
    const { cx, cy } = L;
    // The painted vortex, turning slowly, behind everything.
    if (bh.complete && bh.naturalWidth) {
      const size = R * 7.2;
      ctx.save();
      ctx.globalCompositeOperation = 'source-over';
      ctx.globalAlpha = 0.9 * q;
      ctx.translate(cx, cy);
      ctx.rotate(-t * 0.35);
      ctx.drawImage(bh, -size / 2, -size / 2, size, size);
      ctx.restore();
    }
    ctx.globalCompositeOperation = 'lighter';
    glow(cx, cy, R * 5, 0.22 * q, '124,92,255');
    // Disk particles: back half, horizon, front half (so the disk wraps the hole).
    const tilt = 0.3;
    const drawDisk = (front) => {
      // Smooth bands of glowing gas (half ellipses: the far half before the horizon, the near half after).
      const [a0, a1] = front ? [0, Math.PI] : [Math.PI, TAU];
      ctx.lineCap = 'butt';
      for (let i = 0; i < 26; i++) {
        const k = i / 25;                                  // 0 = inner edge, 1 = outer edge
        const rr = R * (1.3 + k * 2.6);
        const col = k < 0.25 ? '190,250,255' : k < 0.55 ? '0,229,255' : '124,92,255';
        ctx.globalAlpha = q * (0.3 - k * 0.24) * (front ? 1 : 0.75);
        ctx.strokeStyle = `rgb(${col})`;
        ctx.lineWidth = R * (0.2 - k * 0.08);
        ctx.beginPath();
        ctx.ellipse(cx, cy, rr, rr * tilt, 0, a0, a1);
        ctx.stroke();
      }
      // Sparks riding the orbits; inner ones are faster.
      for (const d of L.disk) {
        const rr = d.r * R;
        const a = d.a - t * (2.4 / Math.pow(d.r, 1.5));
        const sy = Math.sin(a);
        if ((sy > 0) !== front) continue;
        const x = cx + Math.cos(a) * rr, y = cy + sy * rr * tilt;
        const inner = clamp(1 - (d.r - 1.25) / 3.2);
        ctx.globalAlpha = q * (0.15 + 0.5 * inner) * (front ? 1 : 0.7);
        const sz = d.w * (1 + inner * 1.6);
        ctx.drawImage(tinted(inner > 0.5 ? '200,250,255' : '150,130,255'), x - sz, y - sz, sz * 2, sz * 2);
      }
    };
    drawDisk(false);
    // Photon ring and horizon.
    ctx.globalAlpha = 1;
    ctx.globalCompositeOperation = 'source-over';
    const ring = ctx.createRadialGradient(cx, cy, R * 0.9, cx, cy, R * 1.45);
    ring.addColorStop(0, 'rgba(0,0,0,1)');
    ring.addColorStop(0.18, `rgba(170,240,255,${0.85 * q})`);
    ring.addColorStop(0.45, `rgba(124,92,255,${0.35 * q})`);
    ring.addColorStop(1, 'rgba(124,92,255,0)');
    ctx.fillStyle = ring;
    ctx.beginPath(); ctx.arc(cx, cy, R * 1.45, 0, TAU); ctx.fill();
    ctx.fillStyle = '#000';
    ctx.beginPath(); ctx.arc(cx, cy, R, 0, TAU); ctx.fill();
    ctx.globalCompositeOperation = 'lighter';
    drawDisk(true);
  }

  // The clock, name and field spiral into the centre too (real DOM, so it stays crisp until it's gone).
  let uiAnims = [];
  function pullUi() {
    const parts = [$('clock-block'), $('who'), form, $('choices'), statusEl, capsEl];
    const cx = W / 2, cy = H / 2;
    uiAnims = parts.map((el, i) => {
      const b = el.getBoundingClientRect();
      const dx = cx - (b.left + b.width / 2), dy = cy - (b.top + b.height / 2);
      const dir = i % 2 ? -1 : 1;
      return el.animate([
        { transform: 'none', opacity: 1, filter: 'blur(0px)' },
        { offset: 0.45, transform: `translate(${dx * 0.25}px, ${dy * 0.25}px) rotate(${dir * 40}deg) scale(1.08, 0.82)`,
          opacity: 1, filter: 'blur(0.5px)' },
        { offset: 0.8, transform: `translate(${dx * 0.8}px, ${dy * 0.8}px) rotate(${dir * 220}deg) scale(0.5, 0.12)`,
          opacity: 0.75, filter: 'blur(2px)' },
        { transform: `translate(${dx}px, ${dy}px) rotate(${dir * 400}deg) scale(0.02, 0.005)`,
          opacity: 0, filter: 'blur(4px)' },
      ], { duration: PULL - i * 50, delay: i * 25, easing: 'cubic-bezier(.55,0,.85,.35)', fill: 'forwards' });
    });
  }

  // ------------------------------------------------------------------ frame loop
  let last = performance.now(), frozenAt = null;
  function frame(now) {
    const dt = Math.min(0.05, (now - last) / 1000);
    last = now;
    if (hole) {
      if (leaving) {
        const ms = frozenAt != null ? frozenAt : now - leaving.t0;
        hole.leave(ms);
        if (frozenAt == null && ms > PULL + FLASH + BLOOM + HOLD && !leaving.sent) {
          leaving.sent = true;
          send({ type: 'start' });
        }
      }
      hole.draw(now, frozenAt != null && !leaving ? 0 : dt);
      requestAnimationFrame(frame);
      return;
    }
    if (leaving) {
      const ms = frozenAt != null ? frozenAt : now - leaving.t0;
      drawLeaving(ms);
      if (frozenAt == null && ms > PULL + FLASH + BLOOM + HOLD && !leaving.sent) {
        leaving.sent = true;
        send({ type: 'start' });
      }
    } else if (!fading) {
      drawCalm(now / 1000, frozenAt != null ? 0 : dt);
    }
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);

  // ------------------------------------------------------------------ the field
  let busy = false, fading = false;

  function say(text, bad) {
    statusEl.textContent = text || '';
    statusEl.classList.toggle('bad', !!bad);
  }

  function caps(e) {
    if (e && e.getModifierState) capsEl.hidden = !e.getModifierState('CapsLock');
  }
  window.addEventListener('keydown', caps, true);
  window.addEventListener('keyup', caps, true);

  window.addEventListener('keydown', (e) => {
    if (busy) { if (e.key !== 'Tab') e.preventDefault(); return; }
    if (e.key === 'Escape') {
      e.preventDefault();
      pw.value = '';
      target = 'orion';
      say('');
    }
    if (mode === 'password' && document.activeElement !== pw && e.key.length === 1 && !e.ctrlKey && !e.altKey &&
        !e.metaKey) pw.focus();
  });
  // Nothing else on the screen takes focus (apart from the two buttons): a click anywhere goes back to the field.
  document.addEventListener('pointerdown', (e) => {
    if (busy || (e.target && e.target.closest && e.target.closest('button.choice'))) return;
    e.preventDefault();
    focusMain();
  });
  window.addEventListener('focus', () => focusMain());

  function focusMain() {
    if (mode === 'password') pw.focus(); else $('go-orion').focus();
  }
  function applyMode() {
    document.body.classList.toggle('auto', mode === 'auto');
    form.hidden = mode === 'auto';
    $('go-rescue').hidden = !CFG.rescue;
  }
  applyMode();

  function go(t) {
    if (busy) return;
    target = t === 'rescue' ? 'rescue' : 'orion';
    if (mode === 'auto') {
      busy = true;
      document.body.classList.add('checking');
      say(target === 'rescue' ? 'Opening the Rescue desktop…' : 'Starting Orion…');
      send({ type: 'login', nopass: true, target });
      return;
    }
    if (!pw.value) {
      say(target === 'rescue' ? 'Type your password, then press Enter to open the Rescue desktop.'
                              : 'Type your password, then press Enter.');
      pw.focus();
      return;
    }
    form.requestSubmit();
  }
  document.querySelectorAll('button.choice').forEach((b) => b.addEventListener('click', () => go(b.dataset.target)));

  form.addEventListener('submit', (e) => {
    e.preventDefault();
    if (busy) return;
    if (!pw.value) { say('Type your password, then press Enter.'); return; }
    busy = true;
    document.body.classList.add('checking');
    pw.disabled = true;
    say('');
    let secret = pw.value;
    pw.value = '';
    send({ type: 'login', password: secret, target });
    secret = null;
  });

  function send(msg) {
    if (native) {
      let s = JSON.stringify(msg);
      native.postMessage(s);
      s = null;
      return;
    }
    // Demo (a normal browser, no launcher): "orion" is the password.
    if (msg.type === 'login') {
      const ok = msg.nopass === true || msg.password === (params.get('pw') || 'orion');
      setTimeout(() => api.result(ok ? { ok: true } : { ok: false, reason: 'wrong_password', message: "That's not it" }), 450);
    } else if (msg.type === 'start') {
      console.log('[demo] the session would start now');
    }
  }

  function fail(message) {
    busy = false;
    document.body.classList.remove('checking');
    pw.disabled = false;
    if (mode === 'auto') { say(message || "Orion couldn't start. Try again.", true); focusMain(); return; }
    say(message || "That's not it", true);
    if (!reduced) {
      form.classList.remove('shake');
      void form.offsetWidth;
      form.classList.add('shake');
    }
    pw.focus();
  }

  function succeed() {
    document.body.classList.remove('checking');
    document.body.classList.add('leaving');
    say('');
    if (reduced || target === 'rescue') {
      // Reduced motion: everything simply fades to black, then the session starts.
      fading = true;
      const what = hole ? $('ui') : document.body;
      what.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 600, fill: 'forwards' })
        .finished.then(() => send({ type: 'start' }));
      return;
    }
    beginSwirl(performance.now());
  }

  const api = {
    result(r) {
      if (r && r.ok) { succeed(); return; }
      if (r && r.reason === 'needs_password' && mode === 'auto') {
        // This computer asks for a password after all: show the field and keep the chosen button.
        busy = false;
        document.body.classList.remove('checking');
        mode = 'password';
        applyMode();
        say('Type your password, then press Enter.');
        pw.focus();
        return;
      }
      fail(r && r.message);
    },
    startFailed(message) {
      // Bring the screen back so the person isn't left in the dark.
      leaving = null; fading = false;
      uiAnims.forEach((a) => a.cancel());
      document.body.getAnimations().forEach((a) => a.cancel());
      $('ui').getAnimations().forEach((a) => a.cancel());
      document.body.classList.remove('leaving');
      fail(message || "Orion couldn't start. Try again.");
    },
  };
  // Screenshot / test helpers, only without the launcher.
  if (DEMO) {
    api.demo = {
      seek(ms) {                       // freeze the farewell at a given moment
        if (!leaving) { busy = true; document.body.classList.add('leaving'); beginSwirl(performance.now()); }
        frozenAt = ms;
        uiAnims.forEach((a) => { a.pause(); a.currentTime = ms; });
      },
      freezeSky() { frozenAt = 0; },
      fail: (m) => fail(m),
    };
  }
  window.orionGreeter = api;
  focusMain();
  if (native) send({ type: 'ready' });   // the launcher's watchdog: the page is up
})();
