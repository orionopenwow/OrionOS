/**
 * Graphics quality tiers for the black hole (Art plan §8, after-1.0 plan §3):
 *   4 Ultra     the ray-marched black hole (blackhole_rt.frag): real lensing, Doppler-beamed disk, photon ring
 *   3 High      the painted single-pass shader at full detail (blackhole.frag)
 *   2 Balanced  the same, fewer noise octaves
 *   1 Battery   the same, cheapest, smaller backing store
 *   0 Still     one frame, redrawn only when something changes (reduced motion, software GL)
 * Chosen automatically from the GPU, battery and reduced-motion preference, and stepped down when frames get slow.
 * Settings can override it by writing `orion.graphicsQuality`
 * ('auto' | 'ultra' | 'high' | 'balanced' | 'battery' | 'still') to localStorage.
 */
export type BlackHoleQuality = 0 | 1 | 2 | 3 | 4;
export type QualitySetting = 'auto' | 'ultra' | 'high' | 'balanced' | 'battery' | 'still';

export const QUALITY_STORAGE_KEY = 'orion.graphicsQuality';
/** Same-window change notice (the 'storage' event only reaches other windows). */
export const QUALITY_EVENT = 'orion:graphics-quality';
const BY_NAME: Record<Exclude<QualitySetting, 'auto'>, BlackHoleQuality> = {
  still: 0,
  battery: 1,
  balanced: 2,
  high: 3,
  ultra: 4,
};

export function qualityName(q: BlackHoleQuality): Exclude<QualitySetting, 'auto'> {
  return (['still', 'battery', 'balanced', 'high', 'ultra'] as const)[q];
}

/** Save the Settings choice and tell every black hole in this window. */
export function writeQualitySetting(v: QualitySetting) {
  try {
    if (v === 'auto') localStorage.removeItem(QUALITY_STORAGE_KEY);
    else localStorage.setItem(QUALITY_STORAGE_KEY, v);
  } catch {
    /* storage blocked */
  }
  try {
    window.dispatchEvent(new CustomEvent(QUALITY_EVENT, { detail: v }));
  } catch {
    /* no window */
  }
}

export function prefersReducedMotion(): boolean {
  try {
    return !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  } catch {
    return false;
  }
}

export function readQualitySetting(): QualitySetting {
  try {
    const v = localStorage.getItem(QUALITY_STORAGE_KEY);
    if (v === 'ultra' || v === 'high' || v === 'balanced' || v === 'battery' || v === 'still' || v === 'auto') return v;
  } catch {
    /* storage blocked */
  }
  return 'auto';
}

export interface GpuInfo {
  renderer: string;
  software: boolean;
  virtual: boolean;
  discrete: boolean;
  /** A strong integrated GPU (Apple M, Iris Xe, Radeon 680M/780M/890M, Arc): High by default. */
  strongIntegrated: boolean;
}

/** What GPU are we on? Software rasterisers and VMs are detected so they get Still/Balanced. */
export function probeGpu(): GpuInfo | null {
  try {
    const c = document.createElement('canvas');
    const gl = c.getContext('webgl2');
    if (!gl) return null;
    const ext = gl.getExtension('WEBGL_debug_renderer_info');
    const renderer = String(ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER));
    gl.getExtension('WEBGL_lose_context')?.loseContext();
    const r = renderer.toLowerCase();
    const software = /swiftshader|llvmpipe|softpipe|software|basic render|lavapipe|mesa offscreen/.test(r);
    const virtual = /virgl|virtio|vmware|svga3d|virtualbox|parallels|qxl|red hat|bochs|qemu/.test(r);
    const discrete = /nvidia|geforce|rtx|quadro|radeon rx|radeon pro|arc a\d|rx \d{3,4}/.test(r);
    const strongIntegrated = /apple m\d|apple gpu|iris\(r\) xe|iris xe|radeon \d{3}m|radeon graphics|arc\(tm\) graphics|intel\(r\) arc|adreno.*(7|8)\d\d/.test(r);
    return { renderer, software, virtual, discrete, strongIntegrated };
  } catch {
    return null;
  }
}

/** [pages] True in the GitHub Pages preview build (vite --mode demo). Safe when run by node --test (no env). */
const DEMO_PAGE = (import.meta as { env?: { MODE?: string } }).env?.MODE === 'demo';

/** [pages] Browser preview tiers: phones and small screens Battery, strong GPUs High (no ray-marched Ultra), else Balanced. */
export function demoQuality(gpu: GpuInfo, small = typeof window !== 'undefined' && (window.matchMedia?.('(pointer: coarse)').matches || window.innerWidth < 700)): BlackHoleQuality {
  if (small) return 1;
  if (gpu.virtual) return 1;
  if (gpu.discrete || gpu.strongIntegrated) return 3;
  return 2;
}

/** Pick a tier with no user override. Battery state is async-only, so it is applied later by `watchBattery`. */
export function autoQuality(gpu = probeGpu()): BlackHoleQuality {
  if (prefersReducedMotion()) return 0; // still render, no motion
  if (!gpu) return 0;
  if (gpu.software) return 0;
  if (DEMO_PAGE) return demoQuality(gpu); // [pages] the web preview stays light: it shares the tab with a website
  if (gpu.virtual) return 2;
  if (gpu.discrete) return 4; // ray-marched; the renderer drops to High by itself if frames get slow
  if (gpu.strongIntegrated) return 3;
  return 2;
}

export function resolveQuality(setting: QualitySetting = readQualitySetting()): BlackHoleQuality {
  if (setting !== 'auto') {
    // reduced motion always wins over a manual "Ultra": a still render
    return prefersReducedMotion() ? 0 : BY_NAME[setting];
  }
  return autoQuality();
}

interface BatteryLike extends EventTarget {
  charging: boolean;
  level: number;
}

/** Calls back with `true` while on battery below 50 % (Battery saver), `false` otherwise. Returns a stop fn. */
export function watchBattery(cb: (save: boolean) => void): () => void {
  let stop = () => {};
  const nav = navigator as Navigator & { getBattery?: () => Promise<BatteryLike> };
  if (!nav.getBattery) return stop;
  let alive = true;
  nav
    .getBattery()
    .then((b) => {
      if (!alive) return;
      const update = () => cb(!b.charging && b.level < 0.5);
      update();
      b.addEventListener('chargingchange', update);
      b.addEventListener('levelchange', update);
      stop = () => {
        b.removeEventListener('chargingchange', update);
        b.removeEventListener('levelchange', update);
      };
    })
    .catch(() => {});
  return () => {
    alive = false;
    stop();
  };
}
