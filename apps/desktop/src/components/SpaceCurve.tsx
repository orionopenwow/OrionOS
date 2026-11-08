/**
 * Depth layer (after-1.0 plan §3, Phase 4): a faint violet grid that is straight at the screen edges and bends
 * toward the black hole, with a soft vignette down the slope, so the screen reads as the rim of a gravity well.
 * Now and then a faint ring of light slides down the well into the core ("space falls inward").
 *
 * Calm by design: the grid is drawn once (again only on resize or when the core moves); the inward rings run at a
 * low frame rate and stop with calm motion / reduced motion, while the page is hidden, or while a full-screen game
 * has paused the core. Settings > Look & motion > "Depth" turns the whole layer off. No pointer events.
 */
import { useEffect, useRef } from 'react';
import { calmMotionNow, hudPrefs, useHudPrefs } from '../lib/hudPrefs';
import { coreGeometry } from '../lib/coreGeometry';
import { getBlackHoleSignals } from '../lib/blackhole';
import { curve, lineAlpha, pull, wellFor, type Well } from '../lib/spaceCurve';

const SPACING = 72; // px between grid lines in flat space
const SEG = 18; // px per polyline segment
const RING_PERIOD = 26_000; // ms for one ring to slide from the rim into the core
const RINGS = 2;
const FPS = 20;

function drawGrid(ctx: CanvasRenderingContext2D, w: number, h: number, well: Well, dpr: number) {
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  // vignette down the slope: a gentle darkening that deepens toward the disk, flat (none) at the rim
  const g = ctx.createRadialGradient(well.x, well.y, well.r * 0.9, well.x, well.y, well.rim);
  g.addColorStop(0, 'rgba(5,7,14,0.28)');
  g.addColorStop(0.35, 'rgba(5,7,14,0.12)');
  g.addColorStop(0.7, 'rgba(5,7,14,0.0)');
  g.addColorStop(1, 'rgba(5,7,14,0.0)');
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, w, h);

  ctx.lineWidth = 1;
  ctx.lineCap = 'round';
  const stroke = (pts: [number, number][]) => {
    // per-segment alpha (the lines fade under the disk and grow a touch stronger down the slope)
    for (let i = 1; i < pts.length; i++) {
      const [x0, y0] = pts[i - 1];
      const [x1, y1] = pts[i];
      const a = lineAlpha(Math.hypot((x0 + x1) / 2 - well.x, (y0 + y1) / 2 - well.y), well);
      if (a < 0.004) continue;
      ctx.strokeStyle = `rgba(124,92,255,${a.toFixed(3)})`;
      ctx.beginPath();
      ctx.moveTo(x0, y0);
      ctx.lineTo(x1, y1);
      ctx.stroke();
    }
  };
  const ox = well.x % SPACING;
  const oy = well.y % SPACING;
  for (let x = ox - SPACING; x <= w + SPACING; x += SPACING) {
    const pts: [number, number][] = [];
    for (let y = -SEG; y <= h + SEG; y += SEG) pts.push(curve(x, y, well));
    stroke(pts);
  }
  for (let y = oy - SPACING; y <= h + SPACING; y += SPACING) {
    const pts: [number, number][] = [];
    for (let x = -SEG; x <= w + SEG; x += SEG) pts.push(curve(x, y, well));
    stroke(pts);
  }
}

export default function SpaceCurve() {
  const prefs = useHudPrefs();
  const ref = useRef<HTMLCanvasElement>(null);
  const on = prefs.depth !== false;

  useEffect(() => {
    const canvas = ref.current;
    if (!on || !canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    const grid = document.createElement('canvas');
    const gctx = grid.getContext('2d');
    if (!gctx) return;
    let w = 0;
    let h = 0;
    let dpr = 1;
    let well: Well = wellFor(1, 1, { x: 0, y: 0, r: 1 });
    let key = '';

    const layout = () => {
      dpr = Math.min(2, window.devicePixelRatio || 1);
      w = window.innerWidth;
      h = window.innerHeight;
      const c = coreGeometry();
      const k = `${w}x${h}@${dpr}:${Math.round(c.x)},${Math.round(c.y)},${Math.round(c.r)}`;
      if (k === key) return false;
      key = k;
      well = wellFor(w, h, c);
      for (const cv of [canvas, grid]) {
        cv.width = Math.round(w * dpr);
        cv.height = Math.round(h * dpr);
      }
      canvas.style.width = `${w}px`;
      canvas.style.height = `${h}px`;
      drawGrid(gctx, w, h, well, dpr);
      return true;
    };

    const frame = (now: number) => {
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.drawImage(grid, 0, 0);
      if (calmMotionNow() || getBlackHoleSignals().paused >= 0.5) return;
      // inward rings: a radial pull maps circles to circles, so each ring is a circle at the curved radius
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      for (let i = 0; i < RINGS; i++) {
        const ph = ((now / RING_PERIOD + i / RINGS) % 1 + 1) % 1; // 0 at the rim -> 1 at the disk
        const flat = well.rim * (1 - ph) + well.r * 1.1 * ph;
        const d = flat - pull(flat, well);
        const a = Math.sin(Math.PI * ph) * 0.07 * Math.min(1, (d - well.r) / well.r);
        if (a <= 0.003 || d <= well.r) continue;
        ctx.strokeStyle = `rgba(157,133,255,${a.toFixed(3)})`;
        ctx.lineWidth = 1.5 + 2.5 * ph;
        ctx.beginPath();
        ctx.arc(well.x, well.y, d, 0, Math.PI * 2);
        ctx.stroke();
      }
    };

    layout();
    frame(performance.now());
    let raf = 0;
    let last = 0;
    let lastLayout = 0;
    const loop = (now: number) => {
      raf = requestAnimationFrame(loop);
      if (document.hidden || now - last < 1000 / FPS) return;
      last = now;
      if (now - lastLayout > 1000) {
        lastLayout = now;
        layout(); // follows the core if it moves (window resized, layout changed)
      }
      frame(now);
    };
    const startLoop = () => {
      if (!raf && !calmMotionNow()) raf = requestAnimationFrame(loop);
    };
    const stopLoop = () => {
      if (raf) cancelAnimationFrame(raf);
      raf = 0;
    };
    const restyle = () => {
      layout();
      frame(performance.now());
      if (calmMotionNow()) stopLoop();
      else startLoop();
    };
    startLoop();
    window.addEventListener('resize', restyle);
    const mq = window.matchMedia?.('(prefers-reduced-motion: reduce)');
    mq?.addEventListener?.('change', restyle);
    const unsub = hudPrefs.subscribe(restyle);
    return () => {
      stopLoop();
      window.removeEventListener('resize', restyle);
      mq?.removeEventListener?.('change', restyle);
      unsub();
    };
  }, [on]);

  if (!on) return null;
  return <canvas ref={ref} className="fixed inset-0 pointer-events-none z-0" aria-hidden="true" data-orion-depth="" />;
}
