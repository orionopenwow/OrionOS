/**
 * The HUD's one menu: six main items, then "More" with everything else by name and one line each.
 * Opens from the Orion mark on the taskbar (or its own button on phones), with Super+Space or Alt+Space,
 * or with `openOrion('menu')`. Esc closes it and gives focus back. Up/Down move between items; typing filters.
 * Picking an item fires `orion:open` with its id (lib/menu.ts); the window that owns that id opens itself.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import {
  Activity, Bot, Camera, ChevronDown, Chrome, Clapperboard, Compass, Eye, FlaskConical, FolderGit2, Gamepad2,
  HeartPulse, History, Inbox, Layers, LayoutGrid, MessageCircle, Mic, MousePointer, Rocket, Search, Settings2,
  ShieldCheck, Smartphone, Wand2, Wrench, X, type LucideIcon,
} from 'lucide-react';
import { filterMenu, isMenuShortcut, mainItems, MENU, moreItems, onOrionOpen, openOrion, ORION_BADGE, type MenuItem } from '../../lib/menu';
import OrionMark from '../glass/OrionMark';
import '../../styles/glass.css';

const ICONS: Record<string, LucideIcon> = {
  Activity, Bot, Camera, Chrome, Clapperboard, Compass, Eye, FlaskConical, FolderGit2, Gamepad2, HeartPulse, History,
  Inbox, Layers, LayoutGrid, MessageCircle, Mic, MousePointer, Rocket, Settings2, ShieldCheck, Smartphone, Wand2, Wrench,
};

/** The icon for a menu id (the taskbar uses it for windows opened from the menu). */
export function menuIcon(id: string): LucideIcon {
  const item = MENU.find((i) => i.id === id);
  return (item && ICONS[item.icon]) || LayoutGrid;
}

/** Small counts shown next to an item (for example approvals waiting). */
export type MenuBadges = Partial<Record<string, number>>;

export const MENU_EVENT_ID = 'menu';
/** Fired with `detail: boolean` whenever the menu opens or closes (the taskbar's menu tile listens). */
export const MENU_STATE_EVENT = 'orion:menu-state';

function Row({ item, badge, compact }: { item: MenuItem; badge?: number; compact?: boolean }) {
  const Icon = ICONS[item.icon] ?? LayoutGrid;
  return (
    <button
      type="button"
      data-menu-item={item.id}
      onClick={() => openOrion(item.id)}
      aria-label={`${item.label}${badge ? `, ${badge} waiting` : ''}. ${item.description}`}
      className="group flex w-full items-center gap-3 rounded-xl px-2.5 py-2 text-left outline-none hover:bg-white/[0.06] focus-visible:bg-white/[0.08] focus-visible:ring-1 focus-visible:ring-[#00E5FF]/70"
    >
      <span
        aria-hidden
        className={`grid shrink-0 place-items-center rounded-lg ${compact ? 'h-7 w-7' : 'h-8 w-8'}`}
        style={{ background: 'rgba(216,222,255,0.06)', border: '1px solid rgba(216,222,255,0.08)' }}
      >
        <Icon size={compact ? 14 : 16} className="text-[#D8DEFF]/85" />
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-2 text-[13px] font-semibold text-[#D8DEFF]">
          {item.label}
          {!!badge && (
            <span className="rounded-full px-1.5 text-[10px] font-bold" style={{ background: '#FFC66B33', color: '#FFC66B' }}>
              {badge > 99 ? '99+' : badge}
            </span>
          )}
        </span>
        <span className="block truncate text-[11.5px] leading-snug text-[#D8DEFF]/50">{item.description}</span>
      </span>
    </button>
  );
}

export default function MainMenu({ mobile, badges: given = {} }: { mobile?: boolean; badges?: MenuBadges }) {
  const [open, setOpen] = useState(false);
  const [reported, setReported] = useState<MenuBadges>({});
  useEffect(() => {
    const on = (e: Event) => {
      const d = (e as CustomEvent<{ id?: unknown; count?: unknown }>).detail;
      if (d && typeof d.id === 'string') setReported((r) => ({ ...r, [d.id as string]: Number(d.count) || 0 }));
    };
    window.addEventListener(ORION_BADGE, on);
    return () => window.removeEventListener(ORION_BADGE, on);
  }, []);
  const badges: MenuBadges = { ...reported, ...given };
  const [more, setMore] = useState(false);
  const [query, setQuery] = useState('');
  const panel = useRef<HTMLDivElement>(null);
  const search = useRef<HTMLInputElement>(null);
  const returnFocus = useRef<HTMLElement | null>(null);

  const close = () => {
    setOpen(false);
    setQuery('');
    const back = returnFocus.current;
    returnFocus.current = null;
    if (back && document.contains(back)) window.setTimeout(() => back.focus(), 0);
  };
  const show = () => {
    returnFocus.current = (document.activeElement as HTMLElement) ?? null;
    setOpen(true);
  };

  // Open/toggle: the taskbar mark and other callers fire orion:open "menu"; picking any item closes the menu.
  useEffect(
    () =>
      onOrionOpen(
        MENU.map((i) => i.id).concat(MENU_EVENT_ID),
        (id) => (id === MENU_EVENT_ID ? (open ? close() : show()) : close()),
      ),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [open],
  );

  // Super+Space / Alt+Space anywhere; Esc closes first (capture, so Esc doesn't also close the other windows).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isMenuShortcut(e)) {
        e.preventDefault();
        e.stopPropagation();
        if (open) close();
        else show();
      } else if (open && e.key === 'Escape') {
        e.preventDefault();
        e.stopPropagation();
        close();
      }
    };
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  useEffect(() => {
    if (open) window.setTimeout(() => search.current?.focus(), 30);
    window.dispatchEvent(new CustomEvent(MENU_STATE_EVENT, { detail: open }));
  }, [open]);

  // Clicking outside closes it.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      const t = e.target as Node;
      if (panel.current?.contains(t)) return;
      if ((t as HTMLElement).closest?.('[data-menu-trigger]')) return;
      close();
    };
    window.addEventListener('pointerdown', onDown, true);
    return () => window.removeEventListener('pointerdown', onDown, true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const results = useMemo(() => (query.trim() ? filterMenu(query) : null), [query]);

  const moveFocus = (e: React.KeyboardEvent) => {
    if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp' && e.key !== 'Enter') return;
    const items = Array.from(panel.current?.querySelectorAll<HTMLButtonElement>('[data-menu-item]') ?? []);
    if (!items.length) return;
    if (e.key === 'Enter') {
      if (document.activeElement === search.current) {
        e.preventDefault();
        items[0].click();
      }
      return;
    }
    e.preventDefault();
    const at = items.indexOf(document.activeElement as HTMLButtonElement);
    const next = e.key === 'ArrowDown' ? (at + 1) % items.length : at <= 0 ? items.length - 1 : at - 1;
    items[next].focus();
  };

  return (
    <>
      {mobile && (
        <button
          type="button"
          data-menu-trigger
          onClick={() => (open ? close() : show())}
          aria-haspopup="dialog"
          aria-expanded={open}
          aria-label="Menu"
          className="orion-glass fixed bottom-4 left-[84px] z-50 grid h-14 w-14 place-items-center rounded-2xl"
        >
          <OrionMark size={26} />
        </button>
      )}
      <AnimatePresence>
        {open && (
          <motion.div
            ref={panel}
            key="orion-menu"
            role="dialog"
            aria-modal="false"
            aria-label="Orion menu"
            initial={{ opacity: 0, y: 12, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 8, transition: { duration: 0.12 } }}
            transition={{ type: 'spring', stiffness: 380, damping: 32 }}
            onKeyDown={moveFocus}
            onClick={(e) => e.stopPropagation()}
            className={`orion-glass orion-glass--dense fixed z-[62] flex flex-col overflow-hidden rounded-2xl text-[#D8DEFF] ${
              mobile ? 'inset-x-3 bottom-[84px] max-h-[75vh]' : 'bottom-[84px] left-4 max-h-[min(640px,calc(100vh-120px))] w-[360px]'
            }`}
            style={{ fontFamily: 'Figtree, system-ui, sans-serif' }}
          >
            <div className="flex items-center gap-2.5 border-b border-white/5 px-3 py-2.5">
              <div className="orion-belt" role="group" aria-label="Menu buttons">
                <button type="button" className="orion-belt__dot" data-kind="close" onClick={close} aria-label="Close menu" title="Close (Esc)">
                  <X size={8} strokeWidth={3.2} />
                </button>
              </div>
              <label className="flex min-w-0 flex-1 items-center gap-2 rounded-lg bg-black/25 px-2 py-1.5">
                <Search size={13} aria-hidden className="shrink-0 text-[#D8DEFF]/45" />
                <input
                  ref={search}
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Find anything"
                  aria-label="Find a menu item"
                  className="min-w-0 flex-1 bg-transparent text-[12.5px] text-white placeholder:text-white/35 focus:outline-none"
                />
              </label>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto p-1.5">
              {results ? (
                <nav aria-label="Search results">
                  {results.length === 0 && <div className="px-3 py-4 text-[12px] text-white/45">Nothing called that. Try Chat and ask Orion.</div>}
                  {results.map((i) => (
                    <Row key={i.id} item={i} badge={badges[i.id]} compact={i.group === 'more'} />
                  ))}
                </nav>
              ) : (
                <>
                  <nav aria-label="Main">
                    {mainItems().map((i) => (
                      <Row key={i.id} item={i} badge={badges[i.id]} />
                    ))}
                  </nav>
                  <button
                    type="button"
                    onClick={() => setMore((m) => !m)}
                    aria-expanded={more}
                    aria-controls="orion-menu-more"
                    className="mt-1 flex w-full items-center gap-2 rounded-xl px-2.5 py-2 text-[12px] font-semibold uppercase tracking-wider text-[#D8DEFF]/55 outline-none hover:bg-white/[0.05] focus-visible:ring-1 focus-visible:ring-[#00E5FF]/70"
                  >
                    More
                    <span className="font-normal normal-case tracking-normal text-[#D8DEFF]/35">{moreItems().length} more things</span>
                    <ChevronDown size={14} aria-hidden className={`ml-auto transition-transform ${more ? 'rotate-180' : ''}`} />
                  </button>
                  {more && (
                    <nav id="orion-menu-more" aria-label="More">
                      {moreItems().map((i) => (
                        <Row key={i.id} item={i} badge={badges[i.id]} compact />
                      ))}
                    </nav>
                  )}
                </>
              )}
            </div>
            {!mobile && (
              <div className="border-t border-white/5 px-3 py-1.5 text-[10.5px] text-[#D8DEFF]/35">
                Super+Space or Alt+Space opens this menu · Esc closes it
              </div>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </>
  );
}
