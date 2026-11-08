/**
 * Focus: the middle of the command console before a conversation starts (owner feedback: "center dash becomes most
 * important dashboard, no more than 5 things at a time"). It shows at most five cards from GET /focus (focus.py),
 * most important first: Now, messages that matter, waiting for your OK, needs you, next up.
 *
 * Every card says why it is there and has "Pin" and "Not important", which teach Orion what matters to you.
 * "Draft reply" only fills the reply box in Messages; sending stays your tap there. Opening the approvals card goes
 * to the queue; this card never approves anything. When the daemon can't be reached, `fallback` shows instead.
 */
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import {
  AlertTriangle, CheckCircle2, Clock, EyeOff, Loader2, MessageCircle, PenLine, Pin, PinOff, Rocket, ShieldCheck, type LucideIcon,
} from 'lucide-react';
import { focusApi, greeting, KIND_LABEL, topCards, type FocusCard, type FocusKind, type FocusView } from '../../lib/focus';
import { useCalmMotion } from '../../lib/hudPrefs';
import './focus.css';

const KIND_ICON: Record<FocusKind, LucideIcon> = {
  now: Rocket,
  message: MessageCircle,
  approval: ShieldCheck,
  needs_you: AlertTriangle,
  next_up: Clock,
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
  const Icon = KIND_ICON[card.kind] ?? Rocket;
  const label = card.kind === 'message' && card.meta.app_label ? `Message · ${card.meta.app_label}` : KIND_LABEL[card.kind];
  const progress = card.kind === 'now' && typeof card.meta.progress === 'number' ? card.meta.progress : null;
  return (
    <article className="focus-card" data-kind={card.kind} data-pinned={card.pinned || undefined} data-focus-card={card.id} aria-label={`${label}: ${card.title}`}>
      <span className="focus-card__icon" aria-hidden>
        <Icon size={22} strokeWidth={2.4} />
      </span>
      <div className="focus-card__body">
        <div className="focus-card__label">
          {label}
          {card.pinned && <span className="focus-card__pin"><Pin size={12} strokeWidth={2.6} /> Pinned</span>}
        </div>
        <h3 className="focus-card__title">{card.title}</h3>
        {card.detail && <p className="focus-card__detail">{card.detail}</p>}
        {progress !== null && (
          <div className="focus-card__progress" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(progress * 100)} aria-label="Progress">
            <span style={{ width: `${Math.max(4, Math.min(100, progress * 100))}%` }} />
          </div>
        )}
        {card.meta.waiting_on && <p className="focus-card__waiting">Waiting on: {card.meta.waiting_on}</p>}
        <div className="focus-card__foot">
          <p className="focus-card__why">{card.why}</p>
          <div className="focus-card__actions">
          <button type="button" className="focus-btn focus-btn--primary" onClick={onOpen}>
            Open
          </button>
          {card.actions.includes('draft') && (
            <button type="button" className="focus-btn" onClick={onDraft} disabled={busy} title="Write a reply for you to check. It is never sent for you.">
              {busy ? <Loader2 size={15} className="animate-spin" /> : <PenLine size={15} />} {busy ? 'Drafting' : 'Draft reply'}
            </button>
          )}
          <button type="button" className="focus-btn focus-btn--quiet focus-btn--push" onClick={onPin} aria-pressed={card.pinned} aria-label={card.pinned ? 'Unpin' : 'Pin'} title={card.pinned ? 'Unpin' : 'Pin: keep this at the top'}>
            {card.pinned ? <PinOff size={15} /> : <Pin size={15} />} <span className="focus-btn__text">{card.pinned ? 'Unpin' : 'Pin'}</span>
          </button>
          <button type="button" className="focus-btn focus-btn--quiet" onClick={onDismiss} aria-label="Not important" title="Not important: hide this and show fewer like it">
            <EyeOff size={15} /> <span className="focus-btn__text">Not important</span>
          </button>
          </div>
        </div>
      </div>
    </article>
  );
}

export default function FocusDashboard({ onOpenWindow, onOpenThread, fallback }: Props) {
  const [view, setView] = useState<FocusView | null>(null);
  const [failed, setFailed] = useState(false);
  const [drafting, setDrafting] = useState<string | null>(null);
  const [note, setNote] = useState('');
  const alive = useRef(true);
  const reduce = !!useReducedMotion() || useCalmMotion();

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
    alive.current = true;
    void load();
    const t = window.setInterval(() => void load(), REFRESH_MS);
    return () => {
      alive.current = false;
      window.clearInterval(t);
    };
  }, [load]);

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
      if (alive.current) setDrafting(null);
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

  if (failed && !view) return <>{fallback}</>;
  const cards = topCards(view?.cards ?? []);

  return (
    <section className="focus" aria-label="Focus: what matters right now" data-focus-dashboard>
      <header className="focus__head">
        <h2 className="focus__title">{greeting()}.</h2>
        <p className="focus__sub">{cards.length ? 'Here is what matters right now.' : 'Type below to talk to Orion.'}</p>
      </header>
      {!view ? (
        <div className="focus__loading"><Loader2 size={18} className="animate-spin" /> Looking at what needs you</div>
      ) : cards.length === 0 ? (
        <div className="focus__clear">
          <CheckCircle2 size={26} strokeWidth={2.4} aria-hidden />
          <span>{view.line || 'All clear. Nothing needs you right now.'}</span>
        </div>
      ) : (
        <div className="focus__list">
          <AnimatePresence initial={false}>
            {cards.map((c, i) => (
              <motion.div
                key={c.id}
                layout={!reduce}
                initial={reduce ? { opacity: 0 } : { opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0, transition: { delay: reduce ? 0 : i * 0.04 } }}
                exit={reduce ? { opacity: 0 } : { opacity: 0, x: 24, transition: { duration: 0.18 } }}
              >
                <Card card={c} busy={drafting === c.id} onOpen={() => open(c)} onDraft={() => void draft(c)} onPin={() => void pin(c)} onDismiss={() => void dismiss(c)} />
              </motion.div>
            ))}
          </AnimatePresence>
        </div>
      )}
      {note && <p className="focus__note" role="status">{note}</p>}
    </section>
  );
}
