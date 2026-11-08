/** Focus dashboard: types and the pure rules (no imports, so node tests can load it). API calls: focus.ts. */
export type FocusKind = 'now' | 'message' | 'approval' | 'needs_you' | 'next_up';
export type FocusAction = 'open' | 'reply' | 'draft' | 'dismiss' | 'pin' | 'unpin';

export interface FocusCard {
  id: string;
  kind: FocusKind;
  title: string;
  detail: string;
  /** Why this card is here, in plain words. */
  why: string;
  score: number;
  pinned: boolean;
  /** The window that opens it ("messages", "queue", "missions", "agents", "tests", "life"...) and a thread for messages. */
  open: { window?: string; thread?: string };
  actions: string[];
  meta: {
    sender?: string;
    app_label?: string;
    unread?: number;
    thread?: string;
    progress?: number | null;
    step?: string;
    waiting_on?: string;
    count?: number;
    risk?: string;
    [k: string]: unknown;
  };
}

export interface FocusView {
  cards: FocusCard[];
  all_clear: boolean;
  line: string;
  generated_at: number;
}

/** The console never shows more than this many cards. */
export const FOCUS_MAX = 5;

export const KIND_LABEL: Record<FocusKind, string> = {
  now: 'Now',
  message: 'Message',
  approval: 'Waiting for your OK',
  needs_you: 'Needs you',
  next_up: 'Next up',
};

/** Keep at most FOCUS_MAX cards, pinned first, then by score (the daemon already does this; this guards the view). */
export function topCards(cards: FocusCard[]): FocusCard[] {
  return [...cards].sort((a, b) => Number(b.pinned) - Number(a.pinned) || b.score - a.score).slice(0, FOCUS_MAX);
}

/** "Morning" / "Afternoon" / "Evening" (no names: [no-owner-name]). */
export function greeting(d = new Date()): string {
  const h = d.getHours();
  return h < 12 ? 'Morning' : h < 18 ? 'Afternoon' : 'Evening';
}

/**
 * The small live detail after the greeting: "1 thing running, 2 waiting for your OK and 2 messages."
 * Empty when there are no cards (the "All clear" view says it instead).
 */
export function summaryLine(cards: FocusCard[]): string {
  let running = 0, waiting = 0, messages = 0, needs = 0, next = 0;
  for (const c of cards) {
    const n = typeof c.meta.count === 'number' && c.meta.count > 0 ? c.meta.count : 1;
    if (c.kind === 'now') running += 1;
    else if (c.kind === 'approval') waiting += n;
    else if (c.kind === 'message') messages += 1;
    else if (c.kind === 'needs_you') needs += 1;
    else if (c.kind === 'next_up') next += 1;
  }
  const parts: string[] = [];
  if (running) parts.push(`${running} ${running === 1 ? 'thing' : 'things'} running`);
  if (needs) parts.push(`${needs} ${needs === 1 ? 'needs' : 'need'} you`);
  if (waiting) parts.push(`${waiting} waiting for your OK`);
  if (messages) parts.push(`${messages} ${messages === 1 ? 'message' : 'messages'}`);
  if (next) parts.push(`${next} coming up`);
  if (!parts.length) return '';
  const last = parts.pop()!;
  return `${parts.length ? `${parts.join(', ')} and ${last}` : last}.`;
}

/** The one button each kind of card shows (the rest live in its More menu). */
export const PRIMARY_LABEL: Record<FocusKind, string> = {
  now: 'Open',
  message: 'Open chat',
  approval: 'Review',
  needs_you: 'Take a look',
  next_up: 'Open',
};

/**
 * Whether the dashboard shows in the middle of the console: only while the current session has no conversation
 * and nothing chat-like is being typed. A feature search (the text matches features) keeps it; a sentence or
 * text that matches no feature counts as a chat message.
 */
export function showDashboard(o: { messages: number; processing: boolean; chatting: boolean; query: string; featureMatches: number }): boolean {
  if (o.messages > 0 || o.processing || o.chatting) return false;
  const q = o.query.trim();
  if (!q) return true;
  return o.featureMatches > 0 && q.split(/\s+/).length < 3;
}
