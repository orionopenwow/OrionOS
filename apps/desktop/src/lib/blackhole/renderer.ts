/**
 * WebGL2 renderer for Orion's living black hole (os/theme/blackhole/blackhole.frag).
 *
 * One full-screen triangle, one fragment pass. Two shaders share one contract:
 *   - quality 4 "Ultra": blackhole_rt.frag, a real ray-march through curved space-time (lensed sky, Doppler-beamed
 *     disk, photon ring), rendered at a reduced backing scale and compiled only when first needed;
 *   - quality 3/2/1 (High / Balanced / Battery) and 0 (Still): blackhole.frag, the single-pass painted shader.
 * If the Ultra shader can't compile on this GPU, or frames get slow, the renderer steps down to High by itself. The HUD core, the greeter and any other surface can host one:
 *
 *   const r = BlackHoleRenderer.create(canvas, { diskUrl: '/black-hole-core.png', transparent: true });
 *   if (!r) showFallbackImage();
 *   r.setTarget({ spin: 1.5, ring: 1.4 });   // eases toward these values
 *   r.set({ voice: level });                 // jumps immediately (signals that are already smoothed)
 *   r.start();
 *
 * Uniform values glide toward their targets (mode changes never snap), spin is integrated into `u_flow` so
 * changing speed never makes the disk jump, the loop pauses while the page is hidden, and `quality: 0`
 * (Still) draws a single frame and only redraws when something changes.
 */
import fragSource from '../../../../../os/theme/blackhole/blackhole.frag?raw';
import rtSource from '../../../../../os/theme/blackhole/blackhole_rt.frag?raw';
import type { BlackHoleQuality } from './quality';

export { fragSource as blackHoleFragmentSource, rtSource as blackHoleRayMarchSource };

/** Shader inputs, named after the contract's uniforms without the `u_` prefix. */
export interface BlackHoleUniforms {
  spin: number;
  temp: number;
  ring: number;
  glow: number;
  turbulence: number;
  size: number;
  tilt: number;
  voice: number;
  ripple: number;
  hue: number;
  parallaxX: number;
  parallaxY: number;
  /** Smart reactions: the disk flares (feeding the hole, an approval falling in). */
  flare: number;
  /** Smart reactions: soft second ring (an update is ready). */
  ring2: number;
}

/** Slots in `u_bodies` (comets and approval stars); 4 floats each: x, y, brightness, colour + tail. */
export const BODY_SLOTS = 10;

export const DEFAULT_UNIFORMS: BlackHoleUniforms = {
  spin: 1,
  temp: 0,
  ring: 1,
  glow: 1,
  turbulence: 0.25,
  size: 1,
  tilt: 0,
  voice: 0,
  ripple: 0,
  hue: 0,
  parallaxX: 0,
  parallaxY: 0,
  flare: 0,
  ring2: 0,
};

export interface BlackHoleOptions {
  /** URL of the painted disk (black-hole-core.png). */
  diskUrl: string;
  /** 0 Still, 1 Battery, 2 Balanced, 3 High, 4 Ultra (ray-marched). Default 2. */
  quality?: BlackHoleQuality;
  /** Fade the frame edge to transparent so the page's own sky shows (HUD overlay). Default false. */
  transparent?: boolean;
  /** Render at half resolution and let the browser upscale (cheap mode for big canvases). */
  halfResolution?: boolean;
  /** Max device-pixel-ratio used for the backing store. Default 2. */
  maxDpr?: number;
  /** Backing-store scale for the ray-marched Ultra tier (it is upscaled by the browser). Default 0.7. */
  ultraScale?: number;
  /** Seconds for uniform targets to settle (time constant). Default 0.9. */
  easing?: number;
  /** Boot timeline: start the u_boot clock at this many seconds (see README). Omit for "fully formed". */
  bootFrom?: number;
  /** Called once if the GL context is lost or the shader fails, so the host can show a fallback. */
  onFailure?: (reason: string) => void;
  /** Step down a tier when frames take too long (auto quality only). Default false. */
  autoDowngrade?: boolean;
  /** Told whenever the renderer changes its own tier (auto downgrade). */
  onQualityChange?: (q: BlackHoleQuality) => void;
}

const VERT = `#version 300 es
in vec2 a_pos;
void main() { gl_Position = vec4(a_pos, 0.0, 1.0); }`;

const UNIFORM_NAMES = [
  'u_resolution', 'u_time', 'u_disk', 'u_spin', 'u_temp', 'u_ring', 'u_glow', 'u_turbulence', 'u_size',
  'u_tilt', 'u_boot', 'u_voice', 'u_ripple', 'u_quality', 'u_parallax', 'u_flow', 'u_hue', 'u_transparent',
  'u_flare', 'u_ring2', 'u_bodies',
] as const;
type UniformName = (typeof UNIFORM_NAMES)[number];
/** Exported for the contract test: every name here must be declared by both shaders. */
export const BLACKHOLE_UNIFORMS: readonly string[] = UNIFORM_NAMES;

interface Prog {
  program: WebGLProgram;
  loc: Record<UniformName, WebGLUniformLocation | null>;
}

/** Is WebGL2 usable here at all? (Cheap probe on a throwaway canvas.) */
export function supportsWebGL2(): boolean {
  try {
    const c = document.createElement('canvas');
    const gl = c.getContext('webgl2');
    if (!gl) return false;
    gl.getExtension('WEBGL_lose_context')?.loseContext();
    return true;
  } catch {
    return false;
  }
}

export class BlackHoleRenderer {
  readonly canvas: HTMLCanvasElement;
  private gl: WebGL2RenderingContext;
  private flat: Prog | null = null;
  private rt: Prog | null = null;
  private rtFailed = false;
  private vbo: WebGLBuffer | null = null;
  private texture: WebGLTexture | null = null;
  private textureReady = false;
  private opts: Required<Omit<BlackHoleOptions, 'bootFrom' | 'onFailure' | 'onQualityChange'>> &
    Pick<BlackHoleOptions, 'onFailure' | 'onQualityChange'>;
  private current: BlackHoleUniforms = { ...DEFAULT_UNIFORMS };
  private target: BlackHoleUniforms = { ...DEFAULT_UNIFORMS };
  private raf = 0;
  private running = false;
  private lastNow = 0;
  private time = 7; // start mid-flow so the first frame is already "alive"
  private flow = 7;
  private boot = -1;
  private dirty = true;
  private failed = false;
  private resizeObs: ResizeObserver | null = null;
  private frameTimes: number[] = [];
  private bodies = new Float32Array(BODY_SLOTS * 4);
  private onVisibility = () => {
    if (document.hidden) this.pauseLoop();
    else if (this.running) this.resumeLoop();
  };

  /** Returns null when WebGL2 or the shader is not available; the caller then shows the static painting. */
  static create(canvas: HTMLCanvasElement, opts: BlackHoleOptions): BlackHoleRenderer | null {
    try {
      const gl = canvas.getContext('webgl2', {
        alpha: true,
        premultipliedAlpha: true,
        antialias: false,
        depth: false,
        stencil: false,
        powerPreference: 'low-power',
        preserveDrawingBuffer: false,
      });
      if (!gl) return null;
      const r = new BlackHoleRenderer(canvas, gl, opts);
      return r.failed ? null : r;
    } catch {
      return null;
    }
  }

  private constructor(canvas: HTMLCanvasElement, gl: WebGL2RenderingContext, opts: BlackHoleOptions) {
    this.canvas = canvas;
    this.gl = gl;
    this.opts = {
      diskUrl: opts.diskUrl,
      quality: opts.quality ?? 2,
      transparent: opts.transparent ?? false,
      halfResolution: opts.halfResolution ?? false,
      maxDpr: opts.maxDpr ?? 2,
      ultraScale: opts.ultraScale ?? 0.7,
      easing: opts.easing ?? 0.9,
      onFailure: opts.onFailure,
      autoDowngrade: opts.autoDowngrade ?? false,
      onQualityChange: opts.onQualityChange,
    };
    if (opts.bootFrom !== undefined) this.boot = Math.max(0, opts.bootFrom);
    if (!this.compile()) {
      this.failed = true;
      return;
    }
    this.loadDisk(opts.diskUrl);
    canvas.addEventListener('webglcontextlost', this.onContextLost, false);
    document.addEventListener('visibilitychange', this.onVisibility);
    if (typeof ResizeObserver !== 'undefined') {
      this.resizeObs = new ResizeObserver(() => {
        this.dirty = true;
        if (this.opts.quality === 0) this.requestStill();
      });
      this.resizeObs.observe(canvas);
    }
  }

  private onContextLost = (e: Event) => {
    e.preventDefault();
    this.fail('webgl context lost');
  };

  private fail(reason: string) {
    if (this.failed) return;
    this.failed = true;
    this.stop();
    this.opts.onFailure?.(reason);
  }

  private link(src: string, label: string): Prog | null {
    const gl = this.gl;
    const mk = (type: number, code: string) => {
      const sh = gl.createShader(type)!;
      gl.shaderSource(sh, code);
      gl.compileShader(sh);
      if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) {
        console.warn(`[blackhole] ${label} shader compile failed:`, gl.getShaderInfoLog(sh));
        gl.deleteShader(sh);
        return null;
      }
      return sh;
    };
    const vs = mk(gl.VERTEX_SHADER, VERT);
    const fs = mk(gl.FRAGMENT_SHADER, src);
    if (!vs || !fs) {
      if (vs) gl.deleteShader(vs);
      if (fs) gl.deleteShader(fs);
      return null;
    }
    const p = gl.createProgram()!;
    gl.attachShader(p, vs);
    gl.attachShader(p, fs);
    gl.bindAttribLocation(p, 0, 'a_pos');
    gl.linkProgram(p);
    gl.deleteShader(vs);
    gl.deleteShader(fs);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) {
      console.warn(`[blackhole] ${label} link failed:`, gl.getProgramInfoLog(p));
      gl.deleteProgram(p);
      return null;
    }
    const loc = {} as Record<UniformName, WebGLUniformLocation | null>;
    for (const n of UNIFORM_NAMES) loc[n] = gl.getUniformLocation(p, n);
    return { program: p, loc };
  }

  private compile(): boolean {
    const gl = this.gl;
    this.flat = this.link(fragSource, 'flat');
    if (!this.flat) return false;
    this.vbo = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, this.vbo);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
    gl.enableVertexAttribArray(0);
    gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
    if (this.opts.quality === 4) this.ensureUltra();
    return true;
  }

  /** Compile the ray-marched shader the first time Ultra is used; on failure settle on High. */
  private ensureUltra(): boolean {
    if (this.rt) return true;
    if (!this.rtFailed) {
      this.rt = this.link(rtSource, 'ultra');
      if (this.rt) return true;
      this.rtFailed = true;
    }
    if (this.opts.quality === 4) {
      this.opts.quality = 3;
      this.dirty = true;
      this.opts.onQualityChange?.(3);
    }
    return false;
  }

  /** Can this GPU run the ray-marched Ultra tier? (false once its shader failed to compile here) */
  get ultraAvailable(): boolean {
    return !this.rtFailed;
  }

  private loadDisk(url: string) {
    const img = new Image();
    img.decoding = 'async';
    img.onload = () => {
      if (this.failed) return;
      const gl = this.gl;
      this.texture = gl.createTexture();
      gl.bindTexture(gl.TEXTURE_2D, this.texture);
      gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true);
      gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, true); // the painting's transparent rim must not fringe
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, img);
      gl.generateMipmap(gl.TEXTURE_2D);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      this.textureReady = true;
      this.dirty = true;
      if (this.opts.quality === 0 || !this.running) this.requestStill();
    };
    img.onerror = () => this.fail(`could not load ${url}`);
    img.src = url;
  }

  // ------------------------------------------------------------------ public API
  /** Ease toward these values. */
  setTarget(u: Partial<BlackHoleUniforms>) {
    Object.assign(this.target, u);
    this.dirty = true;
    if (this.opts.quality === 0) {
      Object.assign(this.current, u);
      this.requestStill();
    }
  }

  /** Set immediately (no easing), e.g. a mic level that is already smoothed. */
  set(u: Partial<BlackHoleUniforms>) {
    Object.assign(this.target, u);
    Object.assign(this.current, u);
    this.dirty = true;
    if (this.opts.quality === 0) this.requestStill();
  }

  /** Small bodies (comets, approval stars), already animated by the caller: drawn as given, no easing. */
  setBodies(b: ArrayLike<number>) {
    const n = Math.min(this.bodies.length, b.length);
    for (let i = 0; i < n; i++) this.bodies[i] = b[i];
    this.bodies.fill(0, n);
    if (this.opts.quality === 0) this.requestStill();
  }

  get uniforms(): Readonly<BlackHoleUniforms> {
    return this.current;
  }

  setQuality(q: BlackHoleQuality) {
    if (q === this.opts.quality) return;
    this.opts.quality = q;
    this.dirty = true;
    if (q === 4 && !this.ensureUltra()) return;
    if (q === 0) {
      this.pauseLoop();
      Object.assign(this.current, this.target);
      this.requestStill();
    } else if (this.running) this.resumeLoop();
  }

  get quality(): BlackHoleQuality {
    return this.opts.quality;
  }

  setHalfResolution(on: boolean) {
    this.opts.halfResolution = on;
    this.dirty = true;
  }

  /** Restart the boot timeline (spark -> starfall -> collapse -> accretion -> settled). */
  playBoot(from = 0) {
    this.boot = Math.max(0, from);
    this.dirty = true;
  }

  /** Mean frame interval (ms) over the last ~2 s of animation, or 0 if not animating. */
  get frameIntervalMs(): number {
    const f = this.frameTimes;
    if (f.length < 2) return 0;
    return (f[f.length - 1] - f[0]) / (f.length - 1);
  }

  start() {
    if (this.failed) return;
    this.running = true;
    if (this.opts.quality === 0) this.requestStill();
    else this.resumeLoop();
  }

  stop() {
    this.running = false;
    this.pauseLoop();
  }

  dispose() {
    this.stop();
    document.removeEventListener('visibilitychange', this.onVisibility);
    this.canvas.removeEventListener('webglcontextlost', this.onContextLost);
    this.resizeObs?.disconnect();
    const gl = this.gl;
    if (this.texture) gl.deleteTexture(this.texture);
    if (this.flat) gl.deleteProgram(this.flat.program);
    if (this.rt) gl.deleteProgram(this.rt.program);
    if (this.vbo) gl.deleteBuffer(this.vbo);
    gl.getExtension('WEBGL_lose_context')?.loseContext();
  }

  /** Draw one frame now (used by Still quality and for screenshots). */
  renderOnce() {
    this.draw();
  }

  // ------------------------------------------------------------------ loop
  private stillQueued = false;
  private requestStill() {
    if (this.stillQueued || this.failed) return;
    this.stillQueued = true;
    requestAnimationFrame(() => {
      this.stillQueued = false;
      this.draw();
    });
  }

  private resumeLoop() {
    if (this.raf || this.failed || document.hidden || this.opts.quality === 0) return;
    this.lastNow = performance.now();
    const tick = (now: number) => {
      this.raf = requestAnimationFrame(tick);
      const raw = Math.max(0, (now - this.lastNow) / 1000);
      this.lastNow = now;
      // motion advances at most 0.1 s per frame (no jumps after a stall); easing uses real time so slow
      // machines still settle on a new mood promptly
      this.step(Math.min(0.1, raw), Math.min(0.5, raw));
      this.draw();
      this.frameTimes.push(now);
      if (this.frameTimes.length > 120) {
        this.frameTimes.shift();
        this.maybeDowngrade();
      }
    };
    this.raf = requestAnimationFrame(tick);
  }

  /** Over ~2 s: slower than ~36 fps on Ultra (ray-march), ~24 fps on High/Balanced, or ~14 fps on Battery,
   * drops one tier. */
  private maybeDowngrade() {
    if (!this.opts.autoDowngrade) return;
    const ms = this.frameIntervalMs;
    const q = this.opts.quality;
    const next =
      q === 4 && ms > 28 ? 3 : q >= 2 && q <= 3 && ms > 42 ? ((q - 1) as BlackHoleQuality) : q === 1 && ms > 70 ? 0 : null;
    if (next === null) return;
    console.info(`[blackhole] ${ms.toFixed(1)} ms/frame, dropping to quality ${next}`);
    this.frameTimes = [];
    this.setQuality(next);
    this.opts.onQualityChange?.(next);
  }

  private pauseLoop() {
    if (this.raf) cancelAnimationFrame(this.raf);
    this.raf = 0;
    this.frameTimes = [];
  }

  private step(dt: number, easeDt = dt) {
    const k = 1 - Math.exp(-easeDt / Math.max(0.01, this.opts.easing));
    const c = this.current as unknown as Record<string, number>;
    const t = this.target as unknown as Record<string, number>;
    for (const key of Object.keys(t)) c[key] += (t[key] - c[key]) * k;
    this.time += dt;
    this.flow += dt * this.current.spin;
    if (this.boot >= 0) {
      this.boot += dt;
      if (this.boot > 6.5) this.boot = -1;
    }
  }

  private resize() {
    const c = this.canvas;
    const dpr = Math.min(this.opts.maxDpr, window.devicePixelRatio || 1);
    const tier = this.opts.quality === 1 ? 0.75 : this.opts.quality === 4 ? this.opts.ultraScale : 1;
    const scale = dpr * (this.opts.halfResolution ? 0.5 : 1) * tier;
    const w = Math.max(1, Math.round(c.clientWidth * scale));
    const h = Math.max(1, Math.round(c.clientHeight * scale));
    if (c.width !== w || c.height !== h) {
      c.width = w;
      c.height = h;
    }
  }

  private draw() {
    const prog = this.opts.quality === 4 && this.rt ? this.rt : this.flat;
    if (this.failed || !prog || !this.textureReady) return;
    const gl = this.gl;
    if (gl.isContextLost()) return;
    if (this.dirty) {
      this.resize();
      this.dirty = false;
    }
    const u = this.current;
    const L = prog.loc;
    gl.viewport(0, 0, this.canvas.width, this.canvas.height);
    gl.useProgram(prog.program);
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, this.texture);
    gl.uniform1i(L.u_disk, 0);
    gl.uniform2f(L.u_resolution, this.canvas.width, this.canvas.height);
    gl.uniform1f(L.u_time, this.time);
    gl.uniform1f(L.u_flow, this.flow || 1e-3);
    gl.uniform1f(L.u_spin, u.spin);
    gl.uniform1f(L.u_temp, u.temp);
    gl.uniform1f(L.u_ring, u.ring);
    gl.uniform1f(L.u_glow, u.glow);
    gl.uniform1f(L.u_turbulence, u.turbulence);
    gl.uniform1f(L.u_size, u.size);
    gl.uniform1f(L.u_tilt, u.tilt);
    gl.uniform1f(L.u_boot, this.boot);
    gl.uniform1f(L.u_voice, u.voice);
    gl.uniform1f(L.u_ripple, u.ripple);
    gl.uniform1f(L.u_quality, prog === this.rt ? 4 : Math.min(3, this.opts.quality));
    gl.uniform2f(L.u_parallax, u.parallaxX, u.parallaxY);
    gl.uniform1f(L.u_hue, u.hue);
    gl.uniform1f(L.u_transparent, this.opts.transparent ? 1 : 0);
    gl.uniform1f(L.u_flare, u.flare);
    gl.uniform1f(L.u_ring2, u.ring2);
    gl.uniform4fv(L.u_bodies, this.bodies);
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
  }
}
