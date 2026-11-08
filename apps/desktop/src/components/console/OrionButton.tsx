/**
 * The one Orion button (look pass): opens and closes the full-screen command console. Drag it anywhere; a
 * click without moving opens the console. Its spot is remembered (lib/orionButton.ts) and pulled back on
 * screen when the window gets smaller. Super+Space, Alt+Space and Ctrl+Space work from anywhere.
 */
import { useEffect, useRef, useState } from 'react';
import OrionMark from '../glass/OrionMark';
import { openOrion } from '../../lib/menu';
import { clampPos, getButtonPos, setButtonPos, subscribeButtonPos, type Pos } from '../../lib/orionButton';
import { MENU_EVENT_ID, MENU_STATE_EVENT } from './CommandConsole';

export function useOrionButtonPos(): Pos {
  const [pos, setPos] = useState<Pos>(() => getButtonPos());
  useEffect(() => subscribeButtonPos(setPos), []);
  return pos;
}

export default function OrionButton({ badge = 0 }: { badge?: number }) {
  const pos = useOrionButtonPos();
  const [open, setOpen] = useState(false);
  const [dragging, setDragging] = useState(false);
  const drag = useRef<{ id: number; sx: number; sy: number; ox: number; oy: number; moved: boolean } | null>(null);
  const justDragged = useRef(false);

  useEffect(() => {
    const on = (e: Event) => setOpen(Boolean((e as CustomEvent<boolean>).detail));
    window.addEventListener(MENU_STATE_EVENT, on);
    return () => window.removeEventListener(MENU_STATE_EVENT, on);
  }, []);

  // keep it on screen when the window shrinks (the saved spot is kept until it is dragged again)
  useEffect(() => {
    const onResize = () => {
      const p = getButtonPos();
      const c = clampPos(p, window.innerWidth, window.innerHeight);
      if (c.x !== p.x || c.y !== p.y) setButtonPos(c);
    };
    onResize();
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  const onPointerDown = (e: React.PointerEvent<HTMLButtonElement>) => {
    if (e.button !== 0) return;
    drag.current = { id: e.pointerId, sx: e.clientX, sy: e.clientY, ox: pos.x, oy: pos.y, moved: false };
    try {
      e.currentTarget.setPointerCapture(e.pointerId); // keep getting moves even if the pointer outruns the button
    } catch {
      /* older browsers */
    }
  };
  const onPointerMove = (e: React.PointerEvent<HTMLButtonElement>) => {
    const d = drag.current;
    if (!d || d.id !== e.pointerId) return;
    const dx = e.clientX - d.sx;
    const dy = e.clientY - d.sy;
    if (!d.moved && Math.hypot(dx, dy) < 6) return;
    if (!d.moved) {
      d.moved = true;
      setDragging(true);
    }
    setButtonPos(clampPos({ x: d.ox + dx, y: d.oy + dy }, window.innerWidth, window.innerHeight));
  };
  const endDrag = (e: React.PointerEvent<HTMLButtonElement>) => {
    const d = drag.current;
    if (!d || d.id !== e.pointerId) return;
    drag.current = null;
    if (d.moved) {
      justDragged.current = true;
      setDragging(false);
      setButtonPos(getButtonPos(), true);
    }
  };

  return (
    <button
      type="button"
      data-orion-button
      data-menu-trigger
      data-open={open || undefined}
      data-dragging={dragging || undefined}
      className="orion-orb"
      style={{ left: pos.x, top: pos.y }}
      aria-haspopup="dialog"
      aria-expanded={open}
      aria-label={open ? 'Close the Orion console' : 'Open the Orion console (drag to move)'}
      title={open ? 'Close (Esc)' : 'Orion: click to open, drag to move'}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
      onClick={(e) => {
        e.stopPropagation();
        if (justDragged.current) {
          justDragged.current = false;
          return;
        }
        openOrion(MENU_EVENT_ID);
      }}
    >
      <span className="orion-orb__ring" aria-hidden="true" />
      <OrionMark size={32} />
      {badge > 0 && !open && <span className="orion-orb__badge">{badge > 99 ? '99+' : badge}</span>}
    </button>
  );
}
