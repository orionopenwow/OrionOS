/**
 * Focus: the middle of the command console before a conversation starts (owner feedback: "center dash becomes most
 * important dashboard, no more than 5 things at a time"). It shows at most five cards from GET /focus (focus.py),
 * most important first: Now, messages that matter, waiting for your OK, needs you, next up.
 *
 * Hierarchy pass (docs/design/CONSOLE_DESIGN.md): the "Now" card is the hero (big, its own layout, big progress,
 * one button). The other cards are smaller, two to a row, and told apart by kind: each kind has one colour role
 * used only on the left bar, the icon chip and the small label. Every card has one main button; "Pin", "Not
 * important" and "Draft a reply" live in its More menu (keyboard reachable, named for screen readers).
 *
 * "Pin" and "Not important" teach Orion what matters to you. "Draft a reply" only fills the reply box in Messages;
 * sending stays your tap there. Opening the approvals card goes to the queue; this card never approves anything.
 * When the daemon can't be reached, `fallback` shows instead.
 */
import { emojiUrl, pickEmoji } from '../../lib/emoji';
import { useCallback, useEffect, useRef, useState, type Dispatch, type ReactNode, type SetStateAction } from 'react';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import {
  EyeOff, Loader2, MoreHorizontal, PenLine, Pin, PinOff, type LucideIcon,
} from 'lucide-react';
import { focusApi, greeting, KIND_LABEL, PRIMARY_LABEL, summaryLine, topCards, type FocusCard, type FocusView } from '../../lib/focus';
import { useCalmMotion } from '../../lib/hudPrefs';
import OrionMark from '../glass/OrionMark';
import './focus.css';

/** The small brand dot on message cards (the app's own colour). */
const APP_DOT: Record<string, string> = {
  telegram: '#2AABEE',
  discord: '#5865F2',
  whatsapp: '#25D366',
  signal: '#3A76F0',
  sms: '#34C759',
  messages: '#34C759',
  instagram: '#E1306C',
  slack: '#E8A93B',
  email: '#9AA6B8',
};

const REFRESH_MS = 20_000;

interface Props {
  /** Open a window by its menu id (messages, queue, missions, agents, tests, life...). */
  onOpenWindow: (id: string) => void;
  /** Open a conversation in Messages, optionally with a draft in the reply box (never sent). */
  onOpenThread: (thread: string, draft?: string) => void;
  /** Shown when Orion's brain can't be reached. */
  fallback: ReactNode;
}

interface MenuEntry {
  label: string;
  icon: LucideIcon;
  run: () => void;
  disabled?: boolean;
}

/** The card's More menu: a small list of secondary actions. Arrow keys move, Esc closes and returns focus. */
function MoreMenu({ title, entries }: { title: string; entries: MenuEntry[] }) {
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const first = wrap.current?.querySelector<HTMLButtonElement>('[role="menuitem"]:not(:disabled)');
    first?.focus();
    const away = (e: PointerEvent) => {
      if (!wrap.current?.contains(e.target as Node)) setOpen(false);
    };
    window.addEventListener('pointerdown', away, true);
    return () => window.removeEventListener('pointerdown', away, true);
  }, [open]);

  const onKey = (e: React.KeyboardEvent) => {
    const items = Array.from(wrap.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]:not(:disabled)') ?? []);
    const at = items.indexOf(document.activeElement as HTMLButtonElement);
    if (e.key === 'Escape') {
      e.preventDefault();
      e.stopPropagation();
      setOpen(false);
      button.current?.focus();
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      items[(at + 1) % items.length]?.focus();
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      items[at <= 0 ? items.length - 1 : at - 1]?.focus();
    } else if (e.key === 'Tab') {
      setOpen(false);
    }
  };

  return (
    <div className="focus-more" ref={wrap} onKeyDown={onKey} data-popover-open={open || undefined}>
      <button
        ref={button}
        type="button"
        className="focus-more__btn"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`More for ${title}`}
        title="More"
        onClick={() => setOpen((v) => !v)}
      >
        <MoreHorizontal size={18} />
      </button>
      {open && (
        <div className="focus-more__menu" role="menu" aria-label={`More for ${title}`}>
          {entries.map((m) => (
            <button
              key={m.label}
              type="button"
              role="menuitem"
              disabled={m.disabled}
              className="focus-more__item"
              onClick={() => {
                setOpen(false);
                m.run();
              }}
            >
              <m.icon size={16} /> {m.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function Eyebrow({ card }: { card: FocusCard }) {
  const app = card.kind === 'message' ? card.meta.app_label : undefined;
  const dot = app ? APP_DOT[app.toLowerCase()] : undefined;
  return (
    <div className="focus-eyebrow">
      {app ? (
        <>
          <span className="focus-eyebrow__dot" style={dot ? { background: dot } : undefined} aria-hidden />
          {app}
        </>
      ) : (
        KIND_LABEL[card.kind]
      )}
      {card.kind === 'now' && card.meta.step && <span className="focus-eyebrow__step">{card.meta.step}</span>}
      {card.pinned && (
        <span className="focus-eyebrow__pin">
          <Pin size={12} /> Pinned
        </span>
      )}
    </div>
  );
}

function Card({
  card, busy, onOpen, onDraft, onPin, onDismiss,
}: {
  card: FocusCard;
  busy: boolean;
  onOpen: () => void;
  onDraft: () => void;
  onPin: () => void;
  onDismiss: () => void;
}) {
  const hero = card.kind === 'now';
  const label = card.kind === 'message' && card.meta.app_label ? `Message on ${card.meta.app_label}` : KIND_LABEL[card.kind];
  const progress = hero && typeof card.meta.progress === 'number' ? Math.max(0, Math.min(1, card.meta.progress)) : null;
  const pct = progress === null ? null : Math.round(progress * 100);
  const app = card.kind === 'message' ? card.meta.app_label : undefined;
  const dot = app ? APP_DOT[app.toLowerCase()] : undefined;

  const more: MenuEntry[] = [];
  if (card.actions.includes('draft')) more.push({ label: busy ? 'Drafting...' : 'Draft a reply', icon: PenLine, run: onDraft, disabled: busy });
  more.push({ label: card.pinned ? 'Unpin' : 'Pin to the top', icon: card.pinned ? PinOff : Pin, run: onPin });
  more.push({ label: 'Not important', icon: EyeOff, run: onDismiss });

  return (
    <article
      className={hero ? 'focus-card focus-card--hero' : 'focus-card'}
      data-kind={card.kind}
      data-pinned={card.pinned || undefined}
      data-focus-card={card.id}
      aria-label={`${label}: ${card.title}`}
    >
      <div className="focus-card__top">
        <span className="focus-chip" aria-hidden>
          <img className="focus-chip__emoji" src={emojiUrl(pickEmoji(card.kind, card.title, card.detail ?? ''))} alt="" draggable={false} />
          {dot && <span className="focus-chip__dot" style={{ background: dot }} />}
        </span>
        <Eyebrow card={card} />
        <MoreMenu title={card.title} entries={more} />
      </div>
      <h3 className="focus-card__title">{card.title}</h3>
      {card.detail && <p className="focus-card__detail">{card.detail}</p>}
      {pct !== null && (
        <div className="focus-progress">
          <div className="focus-progress__bar" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct} aria-label="Progress">
            <span style={{ width: `${Math.max(3, pct)}%` }} />
          </div>
          <span className="focus-progress__pct" aria-hidden>
            {pct}%
          </span>
        </div>
      )}
      <div className="focus-card__foot">
        <div className="focus-card__why">
          {card.meta.waiting_on && <span className="focus-card__waiting">Waiting on {lowerFirst(card.meta.waiting_on)}</span>}
          <span>{card.why}</span>
        </div>
        <button type="button" className="focus-btn" onClick={onOpen}>
          {PRIMARY_LABEL[card.kind]}
        </button>
      </div>
    </article>
  );
}

const lowerFirst = (s: string) => (s && /^[A-Z][a-z]/.test(s) ? s[0].toLowerCase() + s.slice(1) : s);

/** The calm "nothing needs you" view: the Orion mark with a few stars orbiting it. Also used when offline. */
export function AllClear({ title, text, children }: { title: string; text: string; children?: ReactNode }) {
  return (
    <div className="focus-clear" role="status">
      <div className="focus-clear__art" aria-hidden>
        <span className="focus-clear__ring" />
        <span className="focus-clear__orbit">
          <i />
          <i />
          <i />
        </span>
        <OrionMark size={52} />
      </div>
      <h3 className="focus-clear__title">{title}</h3>
      <p className="focus-clear__text">{text}</p>
      {children}
    </div>
  );
}

/** [context] The focus list from GET /focus, refreshed every 20 seconds while `enabled` (the console is open). */
export interface FocusState {
  view: FocusView | null;
  failed: boolean;
  load: () => Promise<void>;
  setView: Dispatch<SetStateAction<FocusView | null>>;
}
export function useFocus(enabled = true): FocusState {
  const [view, setView] = useState<FocusView | null>(null);
  const [failed, setFailed] = useState(false);
  const alive = useRef(true);
  const load = useCallback(async () => {
    try {
      const v = await focusApi.view();
      if (!alive.current) return;
      setView(v);
      setFailed(false);
    } catch {
      if (alive.current) setFailed(true);
    }
  }, []);
  useEffect(() => {
    if (!enabled) return;
    alive.current = true;
    void load();
    const t = window.setInterval(() => void load(), REFRESH_MS);
    return () => {
      alive.current = false;
      window.clearInterval(t);
    };
  }, [load, enabled]);
  return { view, failed, load, setView };
}

/**
 * [context] The five focus cards (Now first, the rest two to a row) with their buttons and More menus. The console
 * shows them under "Show all" on the Next best action card.
 */
export function FocusCardList({ focus, onOpenWindow, onOpenThread }: { focus: FocusState; onOpenWindow: (id: string) => void; onOpenThread: (thread: string, draft?: string) => void }) {
  const { view, load, setView } = focus;
  const [drafting, setDrafting] = useState<string | null>(null);
  const [note, setNote] = useState('');
  const reduce = !!useReducedMotion() || useCalmMotion();

  const teach = (card: FocusCard, action: 'open' | 'dismiss' | 'pin' | 'unpin') => focusApi.feedback(card.id, action).catch(() => undefined);

  const open = (card: FocusCard) => {
    void teach(card, 'open');
    if (card.kind === 'message' && card.open.thread) onOpenThread(card.open.thread);
    else onOpenWindow(card.open.window || 'chat');
  };

  const draft = async (card: FocusCard) => {
    setDrafting(card.id);
    setNote('');
    try {
      const d = await focusApi.draft(card.id);
      onOpenThread(d.thread || card.open.thread || '', d.draft);
    } catch (e) {
      setNote(e instanceof Error ? e.message : "Couldn't write a draft just now.");
    } finally {
      setDrafting(null);
    }
  };

  const pin = async (card: FocusCard) => {
    await teach(card, card.pinned ? 'unpin' : 'pin');
    void load();
  };

  const dismiss = async (card: FocusCard) => {
    setView((v) => (v ? { ...v, cards: v.cards.filter((c) => c.id !== card.id) } : v));
    await teach(card, 'dismiss');
    void load();
  };

  const cards = topCards(view?.cards ?? []);
  const heroAt = cards.findIndex((c) => c.kind === 'now');
  const hero = heroAt >= 0 ? cards[heroAt] : null;
  const rest = cards.filter((_, i) => i !== heroAt);

  const render = (c: FocusCard, i: number) => (
    <motion.div
      key={c.id}
      layout={!reduce}
      className={c.kind === 'now' ? 'focus__hero' : 'focus__item'}
      initial={reduce ? { opacity: 0 } : { opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0, transition: { delay: reduce ? 0 : i * 0.05 } }}
      exit={reduce ? { opacity: 0 } : { opacity: 0, scale: 0.96, transition: { duration: 0.18 } }}
    >
      <Card card={c} busy={drafting === c.id} onOpen={() => open(c)} onDraft={() => void draft(c)} onPin={() => void pin(c)} onDismiss={() => void dismiss(c)} />
    </motion.div>
  );

  return (
    <>
      <div className="focus__list">
        <AnimatePresence initial={false}>
          {hero && render(hero, 0)}
          {rest.map((c, i) => render(c, i + 1))}
        </AnimatePresence>
      </div>
      {note && (
        <p className="focus__note" role="status">
          {note}
        </p>
      )}
    </>
  );
}

/** The greeting with the focus cards (the earlier console middle; kept for any other screen that wants it). */
export default function FocusDashboard({ onOpenWindow, onOpenThread, fallback }: Props) {
  const focus = useFocus();
  const { view, failed } = focus;
  if (failed && !view) return <>{fallback}</>;
  const cards = topCards(view?.cards ?? []);
  return (
    <section className="focus" aria-label="Focus: what matters right now" data-focus-dashboard>
      <header className="focus__head">
        <h2 className="focus__title">{greeting()}.</h2>
        {view && cards.length > 0 && <p className="focus__sub">{summaryLine(cards)}</p>}
      </header>
      {!view ? (
        <div className="focus__loading">
          <Loader2 size={18} className="animate-spin" /> Looking at what needs you
        </div>
      ) : cards.length === 0 ? (
        <AllClear title="All clear" text="Nothing needs you right now. Ask Orion anything below, or type a feature's name to open it." />
      ) : (
        <FocusCardList focus={focus} onOpenWindow={onOpenWindow} onOpenThread={onOpenThread} />
      )}
    </section>
  );
}
