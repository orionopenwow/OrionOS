/** Guide Mode ring geometry: pure, so it is tested with plain Node (tests/guide.test.ts). */
export interface GuideTarget {
  x: number;
  y: number;
  w: number;
  h: number;
}

/**
 * Screen pixels (from the screenshot or the accessibility tree) to CSS pixels inside a full-screen window.
 * `scale` is the screen's devicePixelRatio; the ring is a circle around the target with some room to spare.
 */
export function ringBox(t: GuideTarget, scale: number, pad = 10): { left: number; top: number; size: number } {
  const s = scale > 0 ? scale : 1;
  const cx = (t.x + t.w / 2) / s;
  const cy = (t.y + t.h / 2) / s;
  const size = Math.max(44, Math.max(t.w, t.h) / s + pad * 2);
  return { left: Math.round(cx - size / 2), top: Math.round(cy - size / 2), size: Math.round(size) };
}

/** "Step 2 of 4" */
export function stepWords(index: number | undefined, total: number | undefined): string {
  if (index === undefined || !total) return '';
  return `Step ${Math.min(index + 1, total)} of ${total}`;
}
