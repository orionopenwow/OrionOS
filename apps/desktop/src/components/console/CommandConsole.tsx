/**
 * The command console (look pass, owner feedback: "one Orion button that takes up the whole screen and a command
 * console with features laid out strategically"). It replaces the old menu popover and the separate chat window:
 *
 *   - The chat is the middle: sessions, Concise / Deep, the conversation, quick actions and one big input.
 *   - Every item in lib/menu.ts is laid out around it in four groups (Work, Connect, Life and play, System).
 *   - Typing in the same input also searches the features: matches light up and are listed above the input;
 *     Up/Down picks one and Enter opens it. With nothing picked, Enter sends the text to Orion.
 *   - Opens from the Orion button, Super+Space, Alt+Space or Ctrl+Space, or `openOrion('menu')` (toggle) and
 *     `openOrion('chat')` (open). Esc or the Orion button closes it; it grows from and shrinks into the button.
 *   - Files can still be dropped anywhere on the screen (GlobalDropAndPasteZone).
 *   - [focus] Before a conversation starts, the middle shows the Focus dashboard (FocusDashboard.tsx, at most five
 *     cards). It moves away as soon as the session has messages or a chat message is being typed (a feature search
 *     keeps it); "New" brings it back.
 * Picking an item fires `orion:open` with its id; the window that owns that id opens itself.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import {
  Activity, Bot, Camera, Check, ChevronDown, Chrome, Clapperboard, Compass, Copy, Database, Eye, FlaskConical, FolderGit2,
  Gamepad2, HeartPulse, History, Inbox, Layers, LayoutGrid, MessageCircle, Mic, Monitor, MousePointer, Plus, Rocket, ScrollText,
  Search, Send, SendHorizontal, Settings2, ShieldCheck, Smartphone, Upload, Wand2, Wrench, X, Zap, type LucideIcon,
} from 'lucide-react';
import type { OrchestratorResponseEvent, PersonalityMode } from '@orion/shared-types';
import {
  filterMenu, isMenuShortcut, MENU, onOrionOpen, openOrion, ORION_BADGE, SECTIONS, sectionItems, type MenuItem, type MenuSection,
} from '../../lib/menu';
import { getButtonPos, BUTTON_SIZE } from '../../lib/orionButton';
import { cosmicAudio } from '../../lib/cosmicAudio';
import { useCalmMotion } from '../../lib/hudPrefs';
import OrionMark from '../glass/OrionMark';
import FocusDashboard, { AllClear } from './FocusDashboard'; // [focus]
import { showDashboard } from '../../lib/focus'; // [focus]
import '../../styles/glass.css';

const ICONS: Record<string, LucideIcon> = {
  Activity, Bot, Camera, Chrome, Clapperboard, Compass, Eye, FlaskConical, FolderGit2, Gamepad2, HeartPulse, History,
  Inbox, Layers, LayoutGrid, MessageCircle, Mic, MousePointer, Rocket, Settings2, ShieldCheck, Smartphone, Wand2, Wrench,
};

/** The icon for a menu id (the taskbar uses it for windows opened from the console). */
export function menuIcon(id: string): LucideIcon {
  const item = MENU.find((i) => i.id === id);
  return (item && ICONS[item.icon]) || LayoutGrid;
}

/** Small counts shown next to an item (for example approvals waiting). */
export type MenuBadges = Partial<Record<string, number>>;

/** `openOrion('menu')` toggles the console. */
export const MENU_EVENT_ID = 'menu';
/** Fired with `detail: boolean` whenever the console opens or closes (the Orion button listens). */
export const MENU_STATE_EVENT = 'orion:menu-state';

interface ConvoSession {
  id: string;
  title: string;
  preview: string;
  timestamp: string;
}

interface Props {
  mobile?: boolean;
  badges?: MenuBadges;
  responses: OrchestratorResponseEvent[];
  onSendMessage: (text: string) => void;
  isProcessing: boolean;
  currentTask?: string;
  micActive?: boolean;
  onToggleMic?: () => void;
  activePersonality?: PersonalityMode;
  onSetPersonality?: (mode: PersonalityMode) => void;
  onConsolidateMemories?: () => void;
  onSendTelegram?: (text: string) => void;
  /** [focus] Open a conversation in Messages, optionally with a draft in its reply box (never sent). */
  onOpenThread?: (thread: string, draft?: string) => void;
}

// --------------------------------------------------------------------------------------- feature tiles
// Hierarchy pass (docs/design/CONSOLE_DESIGN.md): features are compact icon tiles (icon + short name) tinted by
// their group's colour. The description shows in the group's info line when a tile is pointed at or focused, and
// stays in what screen readers hear and in the search results.
function Tile({
  item, badge, dim, lit, onPick, onShow,
}: { item: MenuItem; badge?: number; dim: boolean; lit: boolean; onPick: (id: string) => void; onShow: (item: MenuItem | null) => void }) {
  const Icon = ICONS[item.icon] ?? LayoutGrid;
  const main = item.group === 'main';
  return (
    <button
      type="button"
      data-menu-item={item.id}
      data-dim={dim || undefined}
      data-lit={lit || undefined}
      data-main={main || undefined}
      onClick={() => onPick(item.id)}
      onMouseEnter={() => onShow(item)}
      onMouseLeave={() => onShow(null)}
      onFocus={() => onShow(item)}
      onBlur={() => onShow(null)}
      aria-label={`${item.label}${badge ? `, ${badge} waiting` : ''}. ${item.description}`}
      title={item.description}
      className="orion-feature"
    >
      <span aria-hidden className="orion-feature__icon">
        <Icon size={main ? 21 : 18} />
        {!!badge && <span className="orion-feature__badge">{badge > 99 ? '99+' : badge}</span>}
      </span>
      <span className="orion-feature__label">{item.label}</span>
    </button>
  );
}

function SectionBlock({
  id, badges, hits, onPick,
}: { id: MenuSection; badges: MenuBadges; hits: Set<string> | null; onPick: (id: string) => void }) {
  const meta = SECTIONS.find((s) => s.id === id)!;
  const items = sectionItems(id);
  const [shown, setShown] = useState<MenuItem | null>(null);
  return (
    <section aria-label={meta.label} className="orion-section" data-section={id}>
      <header className="orion-section__head">
        <h2 className="orion-section__title">{meta.label}</h2>
        <span className="orion-section__count">{items.length}</span>
      </header>
      <div className="orion-section__grid">
        {items.map((i) => (
          <Tile key={i.id} item={i} badge={badges[i.id]} dim={!!hits && !hits.has(i.id)} lit={!!hits && hits.has(i.id)} onPick={onPick} onShow={setShown} />
        ))}
      </div>
      <p className="orion-section__info" aria-hidden data-showing={shown ? true : undefined}>
        {shown ? (
          <>
            <b>{shown.label}</b> {shown.description}
          </>
        ) : (
          meta.hint
        )}
      </p>
    </section>
  );
}

// --------------------------------------------------------------------------------------- the console
export default function CommandConsole({
  mobile = false,
  badges: given = {},
  responses,
  onSendMessage,
  isProcessing,
  currentTask,
  micActive = false,
  onToggleMic,
  activePersonality = 'concise_companion',
  onSetPersonality,
  onConsolidateMemories,
  onSendTelegram,
  onOpenThread,
}: Props) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [pick, setPick] = useState(-1);
  const [reported, setReported] = useState<MenuBadges>({});
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [sessionsOpen, setSessionsOpen] = useState(false);
  const [historySearch, setHistorySearch] = useState('');
  const [activeSessionId, setActiveSessionId] = useState('session-current');
  const [sessions, setSessions] = useState<ConvoSession[]>(() => [
    { id: 'session-current', title: 'This conversation', preview: 'Talking with Orion now.', timestamp: 'Just now' },
    { id: 'session-screen-tasks', title: 'Desktop clicks and shortcuts', preview: 'Running a click-and-type task on the desktop.', timestamp: '25m ago' },
    { id: 'session-game-bugs', title: 'Game bug reports from Discord', preview: 'Looking at the screenshot in player report #142.', timestamp: '2h ago' },
    { id: 'session-chrome-scrape', title: 'Reading a page in Chrome', preview: 'Pulling the useful text out of the open tab.', timestamp: 'Yesterday' },
  ]);
  // [focus] this session's conversation starts at `baseline` ("New" moves it); `chatting` = a chat message was sent
  const [baseline, setBaseline] = useState(0);
  const [chatting, setChatting] = useState(false);
  const convo = responses.slice(Math.min(baseline, responses.length));
  const input = useRef<HTMLInputElement>(null);
  const feed = useRef<HTMLDivElement>(null);
  const returnFocus = useRef<HTMLElement | null>(null);
  const reduce = !!useReducedMotion() || useCalmMotion();

  useEffect(() => {
    const on = (e: Event) => {
      const d = (e as CustomEvent<{ id?: unknown; count?: unknown }>).detail;
      if (d && typeof d.id === 'string') setReported((r) => ({ ...r, [d.id as string]: Number(d.count) || 0 }));
    };
    window.addEventListener(ORION_BADGE, on);
    return () => window.removeEventListener(ORION_BADGE, on);
  }, []);
  const badges: MenuBadges = { ...reported, ...given };

  const show = () => {
    if (open) {
      window.setTimeout(() => input.current?.focus(), 30);
      return;
    }
    returnFocus.current = (document.activeElement as HTMLElement) ?? null;
    cosmicAudio.playConsoleOpen();
    setOpen(true);
  };
  const close = (quiet = false) => {
    if (!open) return;
    if (!quiet) cosmicAudio.playConsoleClose();
    setOpen(false);
    setQuery('');
    setPick(-1);
    const back = returnFocus.current;
    returnFocus.current = null;
    if (back && document.contains(back) && !back.closest('[data-console]')) window.setTimeout(() => back.focus(), 0);
  };

  // 'menu' toggles, 'chat' opens, any other item closes (its own window opens)
  useEffect(
    () =>
      onOrionOpen(MENU.map((i) => i.id).concat(MENU_EVENT_ID), (id) => {
        if (id === MENU_EVENT_ID) open ? close() : show();
        else if (id === 'chat') show();
        else close(true);
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [open],
  );

  // Super+Space / Alt+Space / Ctrl+Space anywhere; Esc closes first (capture, so Esc doesn't also close other windows)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const ctrlSpace = e.ctrlKey && !e.altKey && !e.metaKey && e.code === 'Space';
      if (isMenuShortcut(e) || ctrlSpace) {
        e.preventDefault();
        e.stopPropagation();
        if (open) close();
        else show();
      } else if (open && e.key === 'Escape') {
        if ((e.target as HTMLElement | null)?.closest?.('[data-popover-open]')) return; // a card's More menu closes first
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
    if (open) window.setTimeout(() => input.current?.focus({ preventScroll: true }), 60);
    window.dispatchEvent(new CustomEvent(MENU_STATE_EVENT, { detail: open }));
  }, [open]);

  useEffect(() => {
    // scroll only the conversation (scrollIntoView would also scroll the console itself on phones)
    const el = feed.current;
    if (open && el) el.scrollTo({ top: el.scrollHeight, behavior: reduce ? 'auto' : 'smooth' });
  }, [convo.length, open, reduce]);

  const results = useMemo(() => (query.trim() ? filterMenu(query).filter((i) => i.id !== 'chat') : null), [query]);
  const hits = useMemo(() => (results ? new Set(results.map((r) => r.id)) : null), [results]);
  const shown = results?.slice(0, 6) ?? [];
  useEffect(() => setPick(-1), [query]);

  const pickItem = (id: string) => {
    cosmicAudio.playTap();
    openOrion(id); // the console hears it and closes; the owner window opens
  };

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (pick >= 0 && shown[pick]) {
      pickItem(shown[pick].id);
      return;
    }
    const text = query.trim();
    if (!text) return;
    cosmicAudio.playCommandDetected();
    setChatting(true); // [focus]
    onSendMessage(text);
    setQuery('');
  };

  const onInputKey = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (!shown.length) return;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setPick((p) => (p + 1) % shown.length);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setPick((p) => (p <= 0 ? shown.length - 1 : p - 1));
    }
  };

  const newConversation = () => {
    const id = `session-${Date.now()}`;
    setSessions((s) => [{ id, title: `Conversation ${s.length + 1}`, preview: 'A fresh start.', timestamp: 'Just now' }, ...s]);
    setActiveSessionId(id);
    setSessionsOpen(false);
    setBaseline(responses.length); // [focus] a fresh conversation: the dashboard comes back
    setChatting(false);
    input.current?.focus();
  };

  const copy = (id: string, code: string) => {
    void navigator.clipboard?.writeText(code).catch(() => undefined);
    setCopiedId(id);
    window.setTimeout(() => setCopiedId(null), 2000);
  };

  const say = (text: string) => {
    setChatting(true); // [focus]
    onSendMessage(text);
  };
  const focusShown = showDashboard({ messages: convo.length, processing: isProcessing, chatting, query, featureMatches: shown.length }); // [focus]
  const openThread = (thread: string, draft?: string) => {
    close(true);
    if (onOpenThread) onOpenThread(thread, draft);
    else openOrion('messages');
  };

  const quick: Array<{ label: string; icon: LucideIcon; run: () => void }> = [
    { label: 'Look at my screen', icon: Monitor, run: () => say('Inspect the current screen and report all visible windows and text.') },
    { label: 'Check game bugs', icon: Gamepad2, run: () => say('Summarize latest game bug reports and Gemini search diagnosis.') },
    {
      label: 'Consolidate memories',
      icon: Database,
      run: () => (onConsolidateMemories ? (setChatting(true), onConsolidateMemories()) : say('Consolidate recent vector memories and outline open loops.')),
    },
  ];

  // grow out of the Orion button
  const b = typeof window !== 'undefined' ? getButtonPos() : { x: 16, y: 600 };
  const origin = `${b.x + BUTTON_SIZE / 2}px ${b.y + BUTTON_SIZE / 2}px`;
  const concise = activePersonality === 'concise_companion' || !activePersonality;
  const deep = activePersonality === 'detailed_researcher';

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          key="orion-console"
          data-console
          role="dialog"
          aria-modal="true"
          aria-label="Orion console"
          className="orion-console"
          style={{ transformOrigin: origin }}
          initial={reduce ? { opacity: 0 } : { opacity: 0, scale: 0.06, borderRadius: 400 }}
          animate={reduce ? { opacity: 1 } : { opacity: 1, scale: 1, borderRadius: 0 }}
          exit={reduce ? { opacity: 0 } : { opacity: 0, scale: 0.06, borderRadius: 400, transition: { duration: 0.32, ease: [0.5, 0, 0.75, 0] } }}
          transition={reduce ? { duration: 0.15 } : { type: 'spring', stiffness: 210, damping: 28, mass: 0.9 }}
          onClick={(e) => e.stopPropagation()}
        >
          {/* top bar */}
          <div className="orion-console__bar">
            <div className="flex items-center gap-3 min-w-0">
              <OrionMark size={26} />
              <div className="min-w-0">
                <div className="font-display text-[15px] font-bold tracking-wide text-ink">Orion</div>
                {!mobile && <div className="text-[12.5px] font-semibold text-ink-3 truncate">Ask anything, or type to find a feature</div>}
              </div>
            </div>
            <div className="flex items-center gap-2">
              {!mobile && <span className="hidden md:inline text-[12.5px] font-semibold text-ink-3">Esc closes</span>}
              <button type="button" onClick={() => close()} className="orion-console__close" aria-label="Close the console">
                <X size={18} strokeWidth={2.4} />
              </button>
            </div>
          </div>

          <div className="orion-console__grid">
            {/* left: Work, Life and play */}
            <aside className="orion-console__side" aria-label="Work and life">
              <SectionBlock id="work" badges={badges} hits={hits} onPick={pickItem} />
              <SectionBlock id="life" badges={badges} hits={hits} onPick={pickItem} />
            </aside>

            {/* middle: the chat */}
            <main className="orion-console__chat" aria-label="Chat with Orion">
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-white/[0.08] px-4 py-2.5">
                <div className="flex items-center gap-2 min-w-0">
                  <button
                    type="button"
                    onClick={() => setSessionsOpen((v) => !v)}
                    aria-expanded={sessionsOpen}
                    className="orion-chip"
                    data-active={sessionsOpen || undefined}
                  >
                    <History size={15} className="text-cobalt-light" />
                    Sessions ({sessions.length})
                    <ChevronDown size={14} className={`transition-transform ${sessionsOpen ? 'rotate-180' : ''}`} />
                  </button>
                  <button type="button" onClick={newConversation} className="orion-chip" aria-label="New conversation" title="New conversation">
                    <Plus size={15} />
                    {!mobile && 'New'}
                  </button>
                  {currentTask && (
                    <span className="hidden xl:inline-block max-w-[220px] truncate rounded-lg border border-[#3DFFA8]/25 bg-[#3DFFA8]/10 px-2 py-0.5 text-[12.5px] font-semibold text-[#8CFFCB]">
                      {currentTask}
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-1.5" role="group" aria-label="How Orion answers">
                  <button type="button" className="orion-chip" data-active={concise || undefined} aria-pressed={concise} onClick={() => onSetPersonality?.('concise_companion')}>
                    <Zap size={14} /> Concise
                  </button>
                  <button
                    type="button"
                    className="orion-chip"
                    data-active={deep || undefined}
                    aria-pressed={deep}
                    title="Longer answers. Deep mode also lets you change Orion by voice (with your OK)."
                    onClick={() => onSetPersonality?.('detailed_researcher')}
                  >
                    <ScrollText size={14} /> Deep
                  </button>
                </div>
              </div>

              <AnimatePresence initial={false}>
                {sessionsOpen && (
                  <motion.div
                    initial={{ height: 0, opacity: 0 }}
                    animate={{ height: 'auto', opacity: 1 }}
                    exit={{ height: 0, opacity: 0 }}
                    className="overflow-hidden border-b border-white/[0.08] bg-black/20"
                  >
                    <div className="space-y-2 p-3">
                      <label className="flex items-center gap-2 rounded-lg border border-white/10 bg-black/30 px-2.5 py-1.5">
                        <Search size={14} className="text-ink-3" aria-hidden />
                        <input
                          value={historySearch}
                          onChange={(e) => setHistorySearch(e.target.value)}
                          placeholder="Find a past conversation"
                          aria-label="Find a past conversation"
                          className="min-w-0 flex-1 bg-transparent text-[13.5px] text-ink focus:outline-none"
                        />
                      </label>
                      <div className="max-h-44 space-y-1 overflow-y-auto pr-1">
                        {sessions
                          .filter((s) => `${s.title} ${s.preview}`.toLowerCase().includes(historySearch.toLowerCase()))
                          .map((s) => (
                            <button
                              key={s.id}
                              type="button"
                              onClick={() => {
                                setActiveSessionId(s.id);
                                setSessionsOpen(false);
                              }}
                              className={`flex w-full items-center justify-between gap-3 rounded-lg border px-2.5 py-2 text-left ${
                                activeSessionId === s.id ? 'border-cobalt/60 bg-cobalt/15' : 'border-white/[0.06] bg-white/[0.03] hover:bg-white/[0.07]'
                              }`}
                            >
                              <span className="min-w-0">
                                <span className="block truncate text-[14px] font-semibold text-ink">{s.title}</span>
                                <span className="block truncate text-[12.5px] text-ink-3">{s.preview}</span>
                              </span>
                              <span className="shrink-0 text-[12px] font-semibold text-ink-3">{s.timestamp}</span>
                            </button>
                          ))}
                      </div>
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>

              {/* the conversation */}
              <div ref={feed} className="min-h-0 flex-1 space-y-4 overflow-y-auto px-4 py-4 selectable" aria-live="polite">
                <AnimatePresence initial={false}>
                  {focusShown && (
                    <motion.div
                      key="focus"
                      initial={reduce ? { opacity: 0 } : { opacity: 0, y: 12 }}
                      animate={{ opacity: 1, y: 0 }}
                      exit={reduce ? { opacity: 0, transition: { duration: 0.1 } } : { opacity: 0, y: -16, scale: 0.98, transition: { duration: 0.22 } }}
                    >
                      <FocusDashboard
                        onOpenWindow={pickItem}
                        onOpenThread={openThread}
                        fallback={
                          <AllClear
                            title="Ask Orion anything"
                            text="Ask a question, run a task on your desktop, or drop a file anywhere on the screen. Type a feature's name to find it."
                          />
                        }
                      />
                    </motion.div>
                  )}
                </AnimatePresence>
                {focusShown || convo.length === 0 ? null : (
                  convo.map((r) => {
                    const codeAction = (r.response.actions || []).find((a) => a.type === 'code_suggestion');
                    const code = (codeAction?.payload as { code?: string } | undefined)?.code;
                    const lang = (codeAction?.payload as { language?: string } | undefined)?.language || 'code';
                    return (
                      <div key={r.request_id} className="space-y-1.5">
                        <div className="flex items-center justify-between px-1 text-[12.5px] font-semibold">
                          <span className="flex items-center gap-2 text-cobalt-light">
                            <span className="h-2 w-2 rounded-full bg-cobalt" />
                            Orion
                            {r.response.tier && <span className="rounded bg-white/10 px-1.5 text-[12px] text-ink-2">{r.response.tier}</span>}
                          </span>
                          <span className="text-ink-3">
                            {new Date((r.timestamp || Date.now() / 1000) * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                          </span>
                        </div>
                        <div className="orion-bubble">
                          <p>{r.response.summary}</p>
                          {code && (
                            <div className="mt-3 rounded-xl border border-white/15 bg-[#0A0D12] p-3">
                              <div className="mb-2 flex items-center justify-between border-b border-white/10 pb-2 text-[12.5px] font-semibold text-ink-3">
                                <span>{lang}</span>
                                <button type="button" onClick={() => copy(r.request_id, code)} className="flex items-center gap-1 rounded px-2 py-0.5 text-ink-2 hover:bg-white/10 hover:text-white">
                                  {copiedId === r.request_id ? <><Check size={13} className="text-[#3DFFA8]" /> Copied</> : <><Copy size={13} /> Copy</>}
                                </button>
                              </div>
                              <pre className="overflow-x-auto font-mono text-[12.5px] leading-normal text-[#BFE3FF]"><code>{code}</code></pre>
                            </div>
                          )}
                          {onSendTelegram && (
                            <div className="mt-3 flex justify-end border-t border-white/10 pt-2">
                              <button
                                type="button"
                                onClick={() => onSendTelegram(`Orion:\n${r.response.summary}`)}
                                className="flex items-center gap-1.5 text-[12.5px] font-semibold text-cobalt-light hover:underline"
                                title="Forward to your Telegram"
                              >
                                <SendHorizontal size={13} /> Send to my Telegram
                              </button>
                            </div>
                          )}
                        </div>
                      </div>
                    );
                  })
                )}
                {isProcessing && (
                  <div className="flex items-center gap-2.5 rounded-xl border border-cobalt/35 bg-cobalt/10 p-3 text-[14px] font-semibold text-[#CFE0FF]">
                    <Activity size={16} className="animate-spin text-cobalt-light" /> Orion is working on it...
                  </div>
                )}
              </div>

              {/* quick actions */}
              <div className="flex items-center gap-2 overflow-x-auto px-4 pb-2">
                {quick.map((q) => (
                  <button key={q.label} type="button" onClick={q.run} className="orion-chip whitespace-nowrap">
                    <q.icon size={14} className="text-cobalt-light" /> {q.label}
                  </button>
                ))}
                <span className="hidden sm:flex items-center gap-1.5 whitespace-nowrap px-1 text-[12.5px] font-semibold text-ink-3">
                  <Upload size={13} /> Drop files anywhere
                </span>
              </div>

              {/* matches for what is typed */}
              {shown.length > 0 && (
                <div className="mx-3 mb-1 rounded-xl border border-cobalt/30 bg-[#141A24]/95 p-1.5" role="listbox" aria-label="Matching features">
                  <div className="px-2 pb-1 text-[12.5px] font-bold text-ink-3">Open a feature</div>
                  {shown.map((i, n) => {
                    const Icon = ICONS[i.icon] ?? LayoutGrid;
                    return (
                      <button
                        key={i.id}
                        type="button"
                        role="option"
                        aria-selected={n === pick}
                        data-menu-match={i.id}
                        onMouseEnter={() => setPick(n)}
                        onClick={() => pickItem(i.id)}
                        className={`flex w-full items-center gap-3 rounded-lg px-2.5 py-1.5 text-left ${n === pick ? 'bg-cobalt/25' : 'hover:bg-white/[0.06]'}`}
                      >
                        <Icon size={16} className="shrink-0" style={{ color: `var(--cat-${i.section ?? 'work'})` }} />
                        <span className="text-[14px] font-semibold text-ink">{i.label}</span>
                        <span className="min-w-0 truncate text-[12.5px] text-ink-3">{i.description}</span>
                      </button>
                    );
                  })}
                </div>
              )}

              {/* the one input */}
              <form onSubmit={submit} className="flex items-center gap-2 px-3 pb-3 pt-1">
                {onToggleMic && (
                  <button
                    type="button"
                    onClick={onToggleMic}
                    className={`orion-console__icon-btn ${micActive ? 'is-live' : ''}`}
                    aria-pressed={micActive}
                    aria-label={micActive ? 'Stop listening' : 'Speak to Orion'}
                    title={micActive ? 'Listening (click to stop)' : 'Speak to Orion'}
                  >
                    <Mic size={18} />
                  </button>
                )}
                <input
                  ref={input}
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  onKeyDown={onInputKey}
                  placeholder={mobile ? "Ask Orion or find a feature" : "Ask Orion, or type a feature like Phone or Games"}
                  aria-label="Ask Orion or find a feature"
                  aria-autocomplete="list"
                  className="orion-console__input"
                />
                <button type="submit" disabled={!query.trim()} className="orion-console__send" aria-label={pick >= 0 ? 'Open' : 'Send'} title={pick >= 0 ? 'Open' : 'Send'}>
                  <Send size={17} />
                </button>
              </form>
              <div className="hidden px-4 pb-3 text-[12px] font-semibold text-ink-3 sm:block">
                Enter sends to Orion · Up and Down pick a feature · Super+Space or Alt+Space opens this · Esc closes
              </div>
            </main>

            {/* right: Connect, System */}
            <aside className="orion-console__side" aria-label="Connect and system">
              <SectionBlock id="connect" badges={badges} hits={hits} onPick={pickItem} />
              <SectionBlock id="system" badges={badges} hits={hits} onPick={pickItem} />
            </aside>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
