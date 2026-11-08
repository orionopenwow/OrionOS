/**
 * "Glass lit by the core": works out which side of a glass surface faces the black hole and how close it is,
 * and writes that into CSS variables read by `.orion-lit` (styles/glass.css):
 *   --lit-angle  gradient direction so the colour sits on the edge facing the core
 *   --lit-a      strength (closer to the core = brighter)
 *   --lit-x/y    where the soft inner glow sits
 * It also sets `data-over-core` when the surface overlaps the bright disk, so the glass gets darker and
 * small text keeps its contrast (§8).
 */
import { useEffect, type RefObject } from 'react';
import { coreGeometry } from '../../lib/coreGeometry';
import '../../styles/glass.css';

export function applyCoreLight(el: HTMLElement) {
  const b = el.getBoundingClientRect();
  if (!b.width || !b.height) return;
  const c = coreGeometry();
  const cx = b.left + b.width / 2;
  const cy = b.top + b.height / 2;
  const vx = c.x - cx;
  const vy = c.y - cy;
  // CSS gradient angles: 0deg points up, 90deg right. The colour stop at 0% sits opposite the direction,
  // so point the gradient away from the core.
  const angle = (Math.atan2(-vx, vy) * 180) / Math.PI;
  // Distance from the core to the nearest point of the rectangle.
  const nx = Math.max(b.left, Math.min(c.x, b.right));
  const ny = Math.max(b.top, Math.min(c.y, b.bottom));
  const edgeDist = Math.hypot(c.x - nx, c.y - ny);
  const diag = Math.hypot(window.innerWidth, window.innerHeight) || 1;
  const a = Math.max(0.1, Math.min(0.42, 0.42 - (edgeDist / diag) * 0.7));
  // Inner glow position: where the line to the core leaves the rectangle.
  const t = Math.min(b.width / 2 / Math.abs(vx || 1e-6), b.height / 2 / Math.abs(vy || 1e-6));
  const gx = 50 + ((vx * Math.min(t, 1e6)) / b.width) * 100;
  const gy = 50 + ((vy * Math.min(t, 1e6)) / b.height) * 100;
  const s = el.style;
  s.setProperty('--lit-angle', `${angle.toFixed(1)}deg`);
  s.setProperty('--lit-a', a.toFixed(3));
  s.setProperty('--lit-x', `${Math.max(0, Math.min(100, gx)).toFixed(1)}%`);
  s.setProperty('--lit-y', `${Math.max(0, Math.min(100, gy)).toFixed(1)}%`);
  // Overlapping the bright part of the disk?
  const over = edgeDist < c.r * 0.92;
  if (over) el.dataset.overCore = '';
  else delete el.dataset.overCore;
}

export function useCoreLight(ref: RefObject<HTMLElement>, deps: unknown[] = []) {
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const run = () => ref.current && applyCoreLight(ref.current);
    run();
    // The core glides when panels open, so re-check now and then (cheap: two getBoundingClientRect calls).
    const t = window.setInterval(run, 600);
    window.addEventListener('resize', run);
    const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(run) : null;
    ro?.observe(el);
    return () => {
      window.clearInterval(t);
      window.removeEventListener('resize', run);
      ro?.disconnect();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
}

/** Same, for every `.orion-lit` element inside a container (cards that come and go). */
export function useCoreLightAll(ref: RefObject<HTMLElement>) {
  useEffect(() => {
    const run = () => ref.current?.querySelectorAll<HTMLElement>('.orion-lit').forEach(applyCoreLight);
    run();
    const t = window.setInterval(run, 700);
    window.addEventListener('resize', run);
    return () => {
      window.clearInterval(t);
      window.removeEventListener('resize', run);
    };
  }, [ref]);
}
