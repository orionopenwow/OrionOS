/**
 * The sky behind the core (Art Update §4):
 *  - Parallax space: three star depths that move slightly with the mouse (or tilt, on a tablet).
 *  - Rare events: now and then a shooting star, or a comet that curves around the black hole. A few per hour
 *    at most, never while you type, off with Settings → "Rare sky events".
 *  - Calm motion / reduced motion: a still sky, no parallax, no events.
 * The black hole itself is drawn by the core (Singularity) on top of this canvas.
 */
import { useEffect, useRef } from 'react';
import { stepParallax } from '../lib/parallax';
import { calmMotionNow, hudPrefs } from '../lib/hudPrefs';
import { coreGeometry } from '../lib/coreGeometry';

interface Star {
  x: number;
  y: number;
  size: number;
  depth: 0 | 1 | 2;
  baseAlpha: number;
  phase: number;
  speed: number;
  color: string;
}

type SkyEvent =
  | { kind: 'shooting'; t0: number; dur: number; x: number; y: number; angle: number; len: number; dist: number }
  | {
      kind: 'comet';
      t0: number;
      dur: number;
      p0: [number, number];
      p1: [number, number];
      p2: [number, number];
    };

/** Fire a sky event now (for testing / the demo): `window.dispatchEvent(new CustomEvent('orion:sky-event', { detail: 'comet' }))`. */
export const SKY_EVENT = 'orion:sky-event';

const DEPTH_SHIFT = [3, 9, 18]; // px of travel at full parallax, far → near
const STAR_COLORS = ['#FFFFFF', '#D8DEFF', '#C2B3FF', '#9D85FF', '#7FF2FF'];
const MIN = 60_000;
const MAX_PER_HOUR = 4;
const rand = (a: number, b: number) => a + Math.random() * (b - a);

export default function CosmicBackgroundCanvas() {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    let raf = 0;
    let width = 0;
    let height = 0;
    let stars: Star[] = [];

    const initStars = () => {
      width = canvas.width = window.innerWidth;
      height = canvas.height = window.innerHeight;
      const count = Math.floor((width * height) / 3000);
      stars = [];
      for (let i = 0; i < count; i++) {
        const r = Math.random();
        const depth: Star['depth'] = r < 0.62 ? 0 : r < 0.9 ? 1 : 2;
        stars.push({
          x: rand(-24, width + 24),
          y: rand(-24, height + 24),
          size: depth === 0 ? rand(0.35, 0.9) : depth === 1 ? rand(0.6, 1.35) : rand(1.1, 2.2),
          depth,
          baseAlpha: depth === 0 ? rand(0.18, 0.5) : depth === 1 ? rand(0.3, 0.7) : rand(0.5, 0.9),
          phase: Math.random() * Math.PI * 2,
          speed: rand(0.3, 1.4),
          color: STAR_COLORS[Math.floor(Math.random() * STAR_COLORS.length)],
        });
      }
    };

    // ---- rare events ------------------------------------------------------------------------
    const events: SkyEvent[] = [];
    const fired: number[] = [];
    let nextAt = Date.now() + rand(4, 9) * MIN;
    let lastTyped = 0;
    const onKey = () => (lastTyped = Date.now());
    window.addEventListener('keydown', onKey, true);
    window.addEventListener('input', onKey, true);

    const typing = () => {
      const now = Date.now();
      if (now - lastTyped < 12_000) return true;
      const a = document.activeElement as HTMLElement | null;
      const inField = !!a && (a.matches?.('input, textarea, select') || a.isContentEditable);
      return inField && now - lastTyped < 60_000;
    };

    const spawn = (kind: 'shooting' | 'comet') => {
      const now = performance.now();
      if (kind === 'shooting') {
        const fromLeft = Math.random() < 0.5;
        events.push({
          kind,
          t0: now,
          dur: rand(700, 1100),
          x: fromLeft ? rand(0.05, 0.45) * width : rand(0.55, 0.95) * width,
          y: rand(0.04, 0.32) * height,
          angle: ((fromLeft ? rand(18, 34) : rand(146, 162)) * Math.PI) / 180,
          len: rand(110, 190),
          dist: rand(260, 420),
        });
        return;
      }
      // Comet: a quadratic curve whose midpoint passes just outside the disk, so it bends around the hole.
      const c = coreGeometry();
      const D = Math.hypot(width, height) * 0.62;
      const a0 = Math.random() * Math.PI * 2;
      const sweep = rand(0.62, 0.85) * Math.PI * (Math.random() < 0.5 ? 1 : -1);
      const a2 = a0 + sweep;
      const mid = a0 + sweep / 2;
      const peri = Math.max(c.r * 1.35, 150);
      const p0: [number, number] = [c.x + Math.cos(a0) * D, c.y + Math.sin(a0) * D];
      const p2: [number, number] = [c.x + Math.cos(a2) * D, c.y + Math.sin(a2) * D];
      const m: [number, number] = [c.x + Math.cos(mid) * peri, c.y + Math.sin(mid) * peri];
      const p1: [number, number] = [2 * m[0] - 0.5 * (p0[0] + p2[0]), 2 * m[1] - 0.5 * (p0[1] + p2[1])];
      events.push({ kind, t0: now, dur: rand(5200, 6800), p0, p1, p2 });
    };

    const fire = (kind?: 'shooting' | 'comet') => {
      spawn(kind ?? (Math.random() < 0.6 ? 'shooting' : 'comet'));
      fired.push(Date.now());
    };

    const maybeFire = () => {
      const now = Date.now();
      if (now < nextAt) return;
      while (fired.length && now - fired[0] > 60 * MIN) fired.shift();
      if (!hudPrefs.get().rareSkyEvents || calmMotionNow() || document.hidden || typing() || fired.length >= MAX_PER_HOUR) {
        nextAt = now + rand(20_000, 60_000); // try again a little later
        return;
      }
      fire();
      nextAt = now + rand(12, 25) * MIN;
    };

    const onManual = (e: Event) => {
      if (!hudPrefs.get().rareSkyEvents || calmMotionNow()) return;
      const d = (e as CustomEvent).detail;
      fire(d === 'comet' || d === 'shooting' ? d : undefined);
      if (!raf) raf = requestAnimationFrame(render);
    };
    window.addEventListener(SKY_EVENT, onManual);

    // ---- drawing ------------------------------------------------------------------------------
    const drawStars = (t: number, still: boolean) => {
      const p = still ? { x: 0, y: 0 } : stepParallax();
      for (const s of stars) {
        const tw = still ? 0.8 : 0.55 + 0.45 * Math.sin(s.phase + t * 0.001 * s.speed);
        const a = s.baseAlpha * tw;
        const shift = DEPTH_SHIFT[s.depth];
        const x = s.x - p.x * shift;
        const y = s.y - p.y * shift;
        ctx.globalAlpha = a;
        ctx.fillStyle = s.color;
        ctx.beginPath();
        ctx.arc(x, y, s.size, 0, Math.PI * 2);
        ctx.fill();
        if (s.depth === 2 && s.size > 1.8 && a > 0.6) {
          ctx.strokeStyle = s.color;
          ctx.lineWidth = 0.5;
          ctx.beginPath();
          ctx.moveTo(x - s.size * 2.2, y);
          ctx.lineTo(x + s.size * 2.2, y);
          ctx.moveTo(x, y - s.size * 2.2);
          ctx.lineTo(x, y + s.size * 2.2);
          ctx.stroke();
        }
      }
      ctx.globalAlpha = 1;
    };

    const drawNebula = () => {
      const g = ctx.createRadialGradient(width * 0.5, height * 0.45, 10, width * 0.5, height * 0.45, width * 0.5);
      g.addColorStop(0, 'rgba(124, 92, 255, 0.04)');
      g.addColorStop(0.6, 'rgba(0, 229, 255, 0.015)');
      g.addColorStop(1, 'transparent');
      ctx.fillStyle = g;
      ctx.fillRect(0, 0, width, height);
    };

    const drawEvents = (now: number) => {
      for (let i = events.length - 1; i >= 0; i--) {
        const e = events[i];
        const p = (now - e.t0) / e.dur;
        if (p >= 1) {
          events.splice(i, 1);
          continue;
        }
        if (e.kind === 'shooting') {
          const ease = 1 - Math.pow(1 - p, 2);
          const hx = e.x + Math.cos(e.angle) * e.dist * ease;
          const hy = e.y + Math.sin(e.angle) * e.dist * ease;
          const len = e.len * Math.min(1, p * 3);
          const tx = hx - Math.cos(e.angle) * len;
          const ty = hy - Math.sin(e.angle) * len;
          const fade = p < 0.7 ? 1 : 1 - (p - 0.7) / 0.3;
          const g = ctx.createLinearGradient(tx, ty, hx, hy);
          g.addColorStop(0, 'rgba(216, 222, 255, 0)');
          g.addColorStop(0.75, 'rgba(194, 179, 255, 0.35)');
          g.addColorStop(1, 'rgba(255, 255, 255, 0.95)');
          ctx.globalAlpha = fade;
          ctx.strokeStyle = g;
          ctx.lineWidth = 1.4;
          ctx.lineCap = 'round';
          ctx.beginPath();
          ctx.moveTo(tx, ty);
          ctx.lineTo(hx, hy);
          ctx.stroke();
          ctx.globalAlpha = 1;
          continue;
        }
        // comet: fast near the hole, slow far away. The dust tail samples the same curve a little
        // earlier in time, so it looks the same at any frame rate.
        const at = (q: number): [number, number] => {
          const tt = q - 0.1 * Math.sin(2 * Math.PI * q);
          const u = 1 - tt;
          return [
            u * u * e.p0[0] + 2 * u * tt * e.p1[0] + tt * tt * e.p2[0],
            u * u * e.p0[1] + 2 * u * tt * e.p1[1] + tt * tt * e.p2[1],
          ];
        };
        const [x, y] = at(p);
        const fade = Math.min(1, p * 6, (1 - p) * 6);
        const TAIL = 0.055;
        const STEPS = 18;
        let prev = at(Math.max(0, p - TAIL));
        for (let k = 1; k <= STEPS; k++) {
          const f = k / STEPS;
          const pt = at(Math.max(0, p - TAIL * (1 - f)));
          ctx.globalAlpha = fade * f * 0.6;
          ctx.strokeStyle = f > 0.75 ? '#D8DEFF' : '#9D85FF';
          ctx.lineWidth = 0.5 + f * 2.2;
          ctx.lineCap = 'round';
          ctx.beginPath();
          ctx.moveTo(prev[0], prev[1]);
          ctx.lineTo(pt[0], pt[1]);
          ctx.stroke();
          prev = pt;
        }
        // faint ion tail pointing away from the hole
        const c = coreGeometry();
        const ax = x - c.x;
        const ay = y - c.y;
        const al = Math.hypot(ax, ay) || 1;
        const ion = 30 + Math.min(60, 9000 / al); // longer close to the hole
        const ig = ctx.createLinearGradient(x, y, x + (ax / al) * ion, y + (ay / al) * ion);
        ig.addColorStop(0, 'rgba(127, 242, 255, 0.45)');
        ig.addColorStop(1, 'rgba(127, 242, 255, 0)');
        ctx.globalAlpha = fade;
        ctx.strokeStyle = ig;
        ctx.lineWidth = 1.2;
        ctx.beginPath();
        ctx.moveTo(x, y);
        ctx.lineTo(x + (ax / al) * ion, y + (ay / al) * ion);
        ctx.stroke();
        // head
        const hg = ctx.createRadialGradient(x, y, 0, x, y, 9);
        hg.addColorStop(0, 'rgba(255, 255, 255, 1)');
        hg.addColorStop(0.35, 'rgba(194, 179, 255, 0.55)');
        hg.addColorStop(1, 'rgba(124, 92, 255, 0)');
        ctx.fillStyle = hg;
        ctx.beginPath();
        ctx.arc(x, y, 9, 0, Math.PI * 2);
        ctx.fill();
        ctx.globalAlpha = 1;
      }
    };

    const render = (now: number) => {
      raf = 0;
      const still = calmMotionNow();
      ctx.clearRect(0, 0, width, height);
      drawNebula();
      drawStars(now, still);
      if (!still) {
        maybeFire();
        drawEvents(now);
      } else events.length = 0;
      // Still sky: draw once and stop until something changes.
      if (!still && !document.hidden) raf = requestAnimationFrame(render);
    };

    const restart = () => {
      if (raf) cancelAnimationFrame(raf);
      raf = requestAnimationFrame(render);
    };
    const onResize = () => {
      initStars();
      restart();
    };
    const onVisible = () => !document.hidden && restart();
    const mq = window.matchMedia?.('(prefers-reduced-motion: reduce)');

    initStars();
    restart();
    window.addEventListener('resize', onResize);
    document.addEventListener('visibilitychange', onVisible);
    mq?.addEventListener('change', restart);
    const offPrefs = hudPrefs.subscribe(restart);

    return () => {
      if (raf) cancelAnimationFrame(raf);
      window.removeEventListener('resize', onResize);
      window.removeEventListener('keydown', onKey, true);
      window.removeEventListener('input', onKey, true);
      window.removeEventListener(SKY_EVENT, onManual);
      document.removeEventListener('visibilitychange', onVisible);
      mq?.removeEventListener('change', restart);
      offPrefs();
    };
  }, []);

  return <canvas ref={canvasRef} className="orion-arrive-camera fixed inset-0 pointer-events-none z-0" style={{ opacity: 0.95 }} aria-hidden="true" />;
}
