/**
 * Glass taskbar at the bottom left (Art Update §4): Orion's open windows (look pass: the Orion button now sits
 * on its own and opens the command console). Open windows get a cobalt tile with a glow, minimised ones a dimmer tile with a bar; hover lifts a tile.
 * Not shown on phones.
 */
import { useRef, type CSSProperties, type ReactNode } from 'react';
import { useCoreLight } from './useCoreLight';
import '../../styles/glass.css';

export interface TaskbarTile {
  id: string;
  label: string;
  icon: ReactNode;
  /** Icon colour when the tile is idle. */
  color?: string;
  open?: boolean;
  minimised?: boolean;
  badge?: number;
  onClick: () => void;
  /** Draw a thin separator before this tile. */
  separated?: boolean;
  /** [simple-menu] The tile opens the menu (a popup), not a window. */
  popup?: boolean;
}

export default function Taskbar({ tiles, style }: { tiles: TaskbarTile[]; style?: CSSProperties }) {
  const ref = useRef<HTMLDivElement>(null);
  useCoreLight(ref);
  return (
    <nav ref={ref} className="orion-taskbar orion-glass orion-lit" aria-label="Taskbar" style={style} onClick={(e) => e.stopPropagation()}>
      {tiles.map((t, i) => (
        <span key={t.id} className="contents">
          {t.separated && <span className="orion-taskbar__sep" aria-hidden="true" />}
          <button
            type="button"
            className="orion-tile"
            data-tile={t.id}
            data-label={t.label}
            data-open={(t.open && !t.minimised) || undefined}
            data-minimised={(t.open && t.minimised) || undefined}
            aria-label={`${t.label}${!t.popup && t.open ? (t.minimised ? ' (minimised)' : ' (open)') : ''}${t.badge ? `, ${t.badge} waiting` : ''}`}
            aria-pressed={t.popup ? undefined : !!t.open && !t.minimised}
            aria-haspopup={t.popup ? 'dialog' : undefined}
            aria-expanded={t.popup ? !!t.open : undefined}
            data-menu-trigger={t.popup || undefined}
            style={{ ['--i' as string]: i, ['--tile-c' as string]: t.color ?? '#DDE5F0' }}
            onClick={t.onClick}
          >
            {t.icon}
            {!!t.badge && <span className="orion-tile__badge">{t.badge > 99 ? '99+' : t.badge}</span>}
          </button>
        </span>
      ))}
    </nav>
  );
}
