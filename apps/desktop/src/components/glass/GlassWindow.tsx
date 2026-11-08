/**
 * Orion Glass window frame (Art Update §4): the one frame every Orion panel uses.
 *  - Frosted glass (~34% tint, blur) so the black hole glows through, 16px corners, deep shadow, thin edge.
 *  - The edge facing the core picks up its light (useCoreLight). Focused window: violet edge glow; others dimmer.
 *  - Title bar: Orion's belt dots on the left (close rose, minimise ember, full size aurora), a title or tabs
 *    with icons and "+", and minimise / maximise / close again on the right.
 *  - New windows grow out of the horizon; minimised windows spiral into the disk; closing just fades.
 *    Calm motion (system reduced motion or the Settings toggle) turns all of that into simple fades.
 *  - On phones it becomes a sheet that slides up from the bottom.
 *
 * Put it directly inside <AnimatePresence> so the leave animation can play. The owner keeps the open state;
 * minimised/maximised/focus live in lib/windowManager (use `useWindowShown(id, open)` to decide rendering).
 */
import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent, type ReactNode } from 'react';
import { usePresence } from 'framer-motion';
import { Maximize2, Minimize2, Minus, Plus, X } from 'lucide-react';
import { clampRect, dragRect, loadRect, saveRect, type Edge, type WinRect } from '../../lib/windowRect';
import { windowManager, useWindowManager } from '../../lib/windowManager';
import { calmMotionNow } from '../../lib/hudPrefs';
import { coreGeometry } from '../../lib/coreGeometry';
import { arrivalPhase } from '../../lib/arrival';
import { applyCoreLight, useCoreLight } from './useCoreLight';
import '../../styles/glass.css';

export interface GlassTab {
  id: string;
  label: string;
  icon?: ReactNode;
  badge?: number;
}

interface Props {
  /** Stable id: focus, minimise and the taskbar tile all use it. */
  id: string;
  title?: ReactNode;
  icon?: ReactNode;
  subtitle?: ReactNode;
  tabs?: GlassTab[];
  activeTab?: string;
  onTab?: (id: string) => void;
  onNewTab?: () => void;
  newTabLabel?: string;
  /** Only the selected tab shows its label; the others show their icon (and badge). */
  compactTabs?: boolean;
  onClose: () => void;
  /** Defaults to spiralling into the disk and keeping a taskbar tile. */
  onMinimise?: () => void;
  maximisable?: boolean;
  /** Phone layout: full-width sheet from the bottom. */
  mobile?: boolean;
  className?: string;
  style?: CSSProperties;
  bodyClassName?: string;
  /** Extra content at the right end of the title bar, before the window buttons. */
  titleExtra?: ReactNode;
  children: ReactNode;
  ariaLabel?: string;
  /** Lowest z-index of the stack (above the widget column at 40, below overlays at 60+); the focused window sits on top. */
  zBase?: number;
}

/** Should a window be on screen? (open and not minimised) */
export function useWindowShown(id: string, open: boolean): boolean {
  const s = useWindowManager();
  return open && !s.minimised[id];
}

const EASE_OUT = 'cubic-bezier(0.16, 0.9, 0.3, 1)';

function relToCore(el: HTMLElement) {
  const b = el.getBoundingClientRect();
  const c = coreGeometry();
  return { sx: b.left + b.width / 2, sy: b.top + b.height / 2, c };
}

function growFromHorizon(el: HTMLElement): Animation {
  const { sx, sy, c } = relToCore(el);
  const dx = c.x - sx;
  const dy = c.y - sy;
  return el.animate(
    [
      { transform: `translate(${dx}px, ${dy}px) scale(0.04)`, opacity: 0 },
      { transform: `translate(${dx * 0.35}px, ${dy * 0.35}px) scale(0.55)`, opacity: 1, offset: 0.45 },
      { transform: 'none', opacity: 1 },
    ],
    { duration: 640, easing: EASE_OUT },
  );
}

/**
 * Spiral into the disk. Driven per frame (not fixed keyframes) because the core itself glides back to the
 * centre when a window leaves, and the window should land where the hole actually is.
 */
function spiralIntoDisk(el: HTMLElement, onDone: () => void): () => void {
  const b = el.getBoundingClientRect();
  const sx = b.left + b.width / 2;
  const sy = b.top + b.height / 2;
  const c0 = coreGeometry();
  const r0 = Math.hypot(sx - c0.x, sy - c0.y);
  const th0 = Math.atan2(sy - c0.y, sx - c0.x);
  // A bit under half a turn, curving toward the core's horizon line so it stays on screen.
  const dir = Math.cos(th0) * (c0.y - sy) > 0 ? 1 : -1;
  const sweep = Math.PI * 0.62 * dir;
  const DUR = 820;
  const t0 = performance.now();
  let raf = 0;
  const frame = () => {
    const p = Math.min(1, (performance.now() - t0) / DUR);
    const e = p * p * (1.6 - 0.6 * p); // eases in, still finishes at 1
    const c = coreGeometry();
    const r = r0 * (1 - e);
    const th = th0 + sweep * e;
    const x = c.x + Math.cos(th) * r - sx;
    const y = c.y + Math.sin(th) * r - sy;
    // Shrinks early so the swing reads as falling in, not flying around.
    const sc = 1 - 0.97 * Math.pow(Math.min(1, p * 1.25), 0.8);
    el.style.transform = `translate(${x.toFixed(1)}px, ${y.toFixed(1)}px) rotate(${(e * 150 * dir).toFixed(1)}deg) scale(${sc.toFixed(3)})`;
    el.style.opacity = String(p < 0.55 ? 1 : Math.max(0, 1 - (p - 0.55) / 0.45));
    if (p < 1) raf = requestAnimationFrame(frame);
    else onDone();
  };
  raf = requestAnimationFrame(frame);
  return () => cancelAnimationFrame(raf);
}

export default function GlassWindow({
  id,
  title,
  icon,
  subtitle,
  tabs,
  activeTab,
  onTab,
  onNewTab,
  newTabLabel = 'New tab',
  compactTabs = false,
  onClose,
  onMinimise,
  maximisable = true,
  mobile = false,
  className = '',
  style,
  bodyClassName = '',
  titleExtra,
  children,
  ariaLabel,
  zBase = 46,
}: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const [isPresent, safeToRemove] = usePresence();
  const wm = useWindowManager();
  const focused = windowManager.isFocused(id, wm);
  const maximised = !mobile && !!wm.maximised[id];

  useCoreLight(ref, [maximised, mobile]);

  // Enter: grow out of the horizon (or slide up on phones, or fade when motion is calm).
  useLayoutEffect(() => {
    windowManager.opened(id);
    const el = ref.current;
    if (!el) return;
    applyCoreLight(el);
    if (arrivalPhase() !== 'done') return; // the arrival CSS fades it in as glass
    if (calmMotionNow()) el.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 220, easing: 'ease-out' });
    else if (mobile) el.animate([{ transform: 'translateY(100%)' }, { transform: 'none' }], { duration: 340, easing: EASE_OUT });
    else growFromHorizon(el);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Leave: spiral into the disk when minimised, otherwise a quick fade.
  useEffect(() => {
    const el = ref.current;
    if (isPresent) {
      // Brought back while it was still leaving: undo the leave styles.
      if (el) {
        el.style.transform = '';
        el.style.opacity = '';
        el.style.pointerEvents = '';
      }
      return;
    }
    const done = () => {
      windowManager.gone(id);
      safeToRemove?.();
    };
    if (!el) return done();
    const kind = windowManager.exitKind(id);
    el.getAnimations().forEach((a) => a.cancel());
    el.style.pointerEvents = 'none';
    if (!calmMotionNow() && !mobile && kind === 'minimise') return spiralIntoDisk(el, done);
    let anim: Animation;
    if (calmMotionNow()) anim = el.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 180, fill: 'forwards' });
    else if (mobile)
      anim = el.animate([{ transform: 'none' }, { transform: 'translateY(100%)' }], { duration: 260, easing: 'ease-in', fill: 'forwards' });
    else
      anim = el.animate([{ opacity: 1, transform: 'none' }, { opacity: 0, transform: 'scale(0.965)' }], {
        duration: 190,
        easing: 'ease-in',
        fill: 'forwards',
      });
    anim.onfinish = done;
    anim.oncancel = done;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isPresent]);

  // Unmounted without AnimatePresence: still leave the stack.
  useEffect(() => () => windowManager.gone(id), [id]);

  const close = () => {
    windowManager.closed(id);
    onClose();
  };
  const minimise = () => {
    if (onMinimise) onMinimise();
    else windowManager.minimise(id);
  };
  const maximise = () => windowManager.toggleMaximise(id);

  // Size and position the owner picked (drag any edge or corner to resize, the title bar to move).
  const [rect, setRect] = useState<WinRect | null>(() => (typeof window === 'undefined' ? null : loadRect(id)));
  useEffect(() => {
    if (!rect) return;
    const fit = () => setRect((r) => (r ? clampRect(r, window.innerWidth, window.innerHeight) : r));
    window.addEventListener('resize', fit);
    return () => window.removeEventListener('resize', fit);
  }, [!!rect]); // eslint-disable-line react-hooks/exhaustive-deps
  const canResize = !mobile && !maximised;
  const startDrag = (edge: Edge) => (e: ReactPointerEvent<HTMLElement>) => {
    if (!canResize || e.button !== 0 || !ref.current) return;
    if (edge === 'move' && (e.target as HTMLElement).closest('button, input, textarea, select, a, [role="tab"]')) return;
    e.preventDefault();
    e.stopPropagation();
    const b = ref.current.getBoundingClientRect();
    const start: WinRect = { x: b.left, y: b.top, w: b.width, h: b.height };
    const sx = e.clientX;
    const sy = e.clientY;
    let last = start;
    const onMove = (ev: PointerEvent) => {
      last = clampRect(dragRect(start, edge, ev.clientX - sx, ev.clientY - sy), window.innerWidth, window.innerHeight);
      setRect(last);
    };
    const onUp = () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      document.body.style.cursor = '';
      saveRect(id, last);
    };
    document.body.style.cursor = edge === 'move' ? 'grabbing' : getComputedStyle(e.currentTarget).cursor;
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
  };
  const rectStyle: CSSProperties | undefined =
    rect && canResize ? { left: rect.x, top: rect.y, width: rect.w, height: rect.h, right: 'auto', bottom: 'auto' } : undefined;

  return (
    <div
      ref={ref}
      role="dialog"
      aria-label={ariaLabel ?? (typeof title === 'string' ? title : id)}
      data-window={id}
      data-focused={focused || undefined}
      data-maximised={maximised || undefined}
      data-sheet={mobile || undefined}
      className={`orion-window orion-lit glass-ui select-none ${className}`}
      style={{ zIndex: zBase + windowManager.rank(id, wm), ...style, ...rectStyle }}
      onPointerDownCapture={() => windowManager.focus(id)}
      onClick={(e) => e.stopPropagation()}
    >
      {canResize &&
        (['n', 's', 'e', 'w', 'ne', 'nw', 'se', 'sw'] as const).map((edge) => (
          <span key={edge} aria-hidden="true" className="orion-resize" data-edge={edge} onPointerDown={startDrag(edge)} />
        ))}
      <div className="orion-window__titlebar" onPointerDown={startDrag('move')} onDoubleClick={(e) => maximisable && !mobile && e.target === e.currentTarget && maximise()}>
        <div className="orion-belt" role="group" aria-label="Window buttons">
          <button type="button" className="orion-belt__dot" data-kind="close" onClick={close} aria-label="Close" title="Close">
            <X size={8} strokeWidth={3.2} />
          </button>
          <button type="button" className="orion-belt__dot" data-kind="minimise" onClick={minimise} aria-label="Minimise" title="Minimise">
            <Minus size={8} strokeWidth={3.2} />
          </button>
          {maximisable && !mobile && (
            <button
              type="button"
              className="orion-belt__dot"
              data-kind="maximise"
              onClick={maximise}
              aria-label={maximised ? 'Restore size' : 'Full size'}
              title={maximised ? 'Restore size' : 'Full size'}
            >
              {maximised ? <Minimize2 size={7} strokeWidth={3.2} /> : <Maximize2 size={7} strokeWidth={3.2} />}
            </button>
          )}
        </div>

        {tabs && tabs.length ? (
          <div className="orion-tabs" role="tablist" aria-label={typeof title === 'string' ? `${title} tabs` : 'Tabs'}>
            {tabs.map((t) => {
              const sel = t.id === activeTab;
              return (
                <button
                  key={t.id}
                  type="button"
                  role="tab"
                  aria-selected={sel}
                  className="orion-tab"
                  onClick={() => onTab?.(t.id)}
                  title={t.label}
                >
                  {t.icon}
                  {(!(mobile || compactTabs) || sel || !t.icon) && <span>{t.label}</span>}
                  {!!t.badge && <span className="orion-tab__badge">{t.badge > 99 ? '99+' : t.badge}</span>}
                </button>
              );
            })}
            {onNewTab && (
              <button type="button" className="orion-tab-add" onClick={onNewTab} aria-label={newTabLabel} title={newTabLabel}>
                <Plus size={14} />
              </button>
            )}
          </div>
        ) : (
          <div className="orion-window__title" style={{ flex: '1 1 auto' }}>
            {icon}
            <span className="truncate">{title}</span>
            {subtitle && <span className="orion-window__subtitle truncate">{subtitle}</span>}
          </div>
        )}

        {titleExtra}
      </div>
      <div className={`orion-window__body ${bodyClassName}`}>{children}</div>
    </div>
  );
}
