/**
 * Depth (after-1.0 plan §3): the edges of the screen stay flat, and toward the core space curves and falls inward,
 * so the screen feels like the rim of a gravity well. Pure geometry here (tested in tests/look.test.ts); the
 * SpaceCurve component draws it.
 *
 * A point at distance r from the core is pulled inward by `pull(r)`: nothing at the screen edge, more and more
 * toward the core (like a lens: about R²/r), and it never folds over itself, so a straight grid line bends smoothly
 * toward the centre instead of crossing.
 */
export interface Well {
  /** Core centre in px. */
  x: number;
  y: number;
  /** Radius of the painted disk in px. */
  r: number;
  /** Distance from the core to the farthest screen corner in px: the flat rim. */
  rim: number;
}

/** Pull strength: fraction of the disk radius a point just outside the disk moves inward. */
export const PULL = 0.55;

/** How far (px) a point at distance d from the core is pulled toward it. 0 at and beyond the rim. */
export function pull(d: number, w: Well): number {
  if (d <= 0) return 0;
  const R = Math.max(1, w.r);
  const rim = Math.max(R * 2, w.rim);
  // lens-like fall-off, softened inside the disk so it never exceeds d (no fold-over)
  const lens = (PULL * R * R) / Math.sqrt(d * d + R * R * 0.6);
  // fade to exactly flat at the rim (smoothstep from 35 % of the rim out to the rim)
  const t = Math.min(1, Math.max(0, (d - rim * 0.35) / (rim * 0.65)));
  const flat = 1 - t * t * (3 - 2 * t);
  return Math.min(d * 0.85, lens * flat);
}

/** Where a flat-space point lands on the curved screen. */
export function curve(px: number, py: number, w: Well): [number, number] {
  const dx = px - w.x;
  const dy = py - w.y;
  const d = Math.hypot(dx, dy);
  if (d < 1e-6) return [px, py];
  const k = (d - pull(d, w)) / d;
  return [w.x + dx * k, w.y + dy * k];
}

/** Grid line opacity at distance d: faint at the flat rim, a little stronger down the slope, gone under the disk. */
export function lineAlpha(d: number, w: Well): number {
  const R = Math.max(1, w.r);
  const under = Math.min(1, Math.max(0, (d - R * 0.9) / (R * 0.6)));
  const slope = 1 - Math.min(1, d / Math.max(R * 2, w.rim));
  return under * (0.03 + 0.07 * slope);
}

export function wellFor(width: number, height: number, core: { x: number; y: number; r: number }): Well {
  const rim = Math.max(
    Math.hypot(core.x, core.y),
    Math.hypot(width - core.x, core.y),
    Math.hypot(core.x, height - core.y),
    Math.hypot(width - core.x, height - core.y),
  );
  return { x: core.x, y: core.y, r: core.r, rim };
}
