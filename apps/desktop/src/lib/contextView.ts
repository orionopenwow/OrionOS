/**
 * Rules for the console's "Focus and context" middle and its side lists (components/console/ContextPanel.tsx and
 * CommandWidgets.tsx), built from data Orion already has:
 *
 *   - nextBestAction  Orion's short read of what matters now: two to four plain sentences, one suggestion, up to two
 *                     actions and the reasons behind them. Built here from GET /focus (focus.py) with fixed
 *                     sentence patterns: no cloud model is asked. If the daemon later sends its own summary
 *                     (`summary` on /focus, from the local model), that text replaces the sentences.
 *   - dayTimeline     today in time order: open promises, calendar items (`agenda` on /focus, when the daemon has
 *                     any), the wind-down and bedtime, a break when one is due, and when running missions should
 *                     be done (their start plus their time limit).
 *   - waitingOnYou / waitingOnOthers / projectsSummary for the side panels.
 *
 * No imports, so the tests run it directly with `node --test` (tests/context.test.ts).
 */

// --------------------------------------------------------------------------------------- shapes (structural)
export interface CardLike {
  id: string;
  kind: string;
  title: string;
  detail: string;
  why: string;
  open: { window?: string; thread?: string };
  meta: { sender?: string; app_label?: string; unread?: number; thread?: string; waiting_on?: string; count?: number; progress?: number | null; due?: number; [k: string]: unknown };
}
export interface PromiseLike { id: string; task: string; to?: string; due: number | null }
export interface MissionLike {
  id: string;
  title: string;
  state: string;
  agent_label?: string;
  started?: number | null;
  time_limit_min?: number;
  summary?: string;
  repo_name?: string;
}
export interface AgendaItem { at: number; title: string; kind?: string; until?: number | null }
export interface ThreadLike {
  key?: string;
  platform?: string;
  thread_name?: string;
  unread?: number;
  muted?: boolean;
  last_ts?: number;
  messages?: Array<{ author?: string; text?: string; direction?: string; timestamp?: number }>;
}
export interface ApprovalLike { id: string; status: string; kind?: string; title?: string; author?: string; channel?: string; platform?: string; category?: string; created_at?: number }

// --------------------------------------------------------------------------------------- small words
const lowerFirst = (s: string) => (s && /^[A-Z][a-z]/.test(s) ? s[0].toLowerCase() + s.slice(1) : s);
const upperFirst = (s: string) => (s ? s[0].toUpperCase() + s.slice(1) : s);
const stop = (s: string) => (/[.!?]$/.test(s) ? s : `${s}.`);
/** "Star catcher: add a high-score table" -> "Star catcher". */
export const shortTitle = (t: string) => (t.includes(':') ? t.split(':')[0].trim() : t).slice(0, 48);
/** "Claude: Checking the work..." -> "Claude"; "Claude Code" -> "Claude". */
export const agentName = (s: string | undefined) => {
  const a = (s ?? '').split(':')[0].trim().replace(/\s+(Code|CLI)$/i, '');
  return a && a.length <= 24 && !/\s{2}/.test(a) ? a : 'The agent';
};
const possessive = (name: string) => (name.endsWith('s') ? `${name}'` : `${name}'s`);

/** "in 3 hours", "in 20 min", "now", "2 hours ago" for sentences. */
export function inWords(at: number, now: number): string {
  const d = at - now;
  const a = Math.abs(d);
  const span = a < 90 ? '' : a < 3600 ? `${Math.round(a / 60)} min` : a < 86400 * 1.5 ? `${Math.round(a / 3600)} ${Math.round(a / 3600) === 1 ? 'hour' : 'hours'}` : `${Math.round(a / 86400)} days`;
  if (!span) return 'now';
  return d > 0 ? `in ${span}` : `${span} ago`;
}

/** The promise a message card is about: same chat, or the sender's name in the task. */
export function promiseFor(card: CardLike, promises: PromiseLike[]): PromiseLike | undefined {
  const thread = card.open.thread || card.meta.thread;
  const sender = (card.meta.sender || '').toLowerCase();
  return (
    promises.find((p) => thread && p.to === thread) ??
    (sender.length > 1 ? promises.find((p) => new RegExp(`\\b${sender.replace(/[^\w]/g, '')}\\b`, 'i').test(p.task)) : undefined)
  );
}

/** "Send Maya the demo track" + "Maya" -> "the demo track"; otherwise null. */
export function promiseObject(task: string, sender: string): string | null {
  const m = new RegExp(`^(?:send|give|share|show|get)\\s+${sender.replace(/[^\w]/g, '')}\\s+(.+)$`, 'i').exec(task.trim());
  return m ? m[1].replace(/[.!]$/, '') : null;
}

// --------------------------------------------------------------------------------------- next best action
export type ActionKind = 'window' | 'draft';
export interface NbaAction {
  id: string;
  label: string;
  kind: ActionKind;
  /** The window to open (kind "window") or the focus card to draft a reply for (kind "draft"). */
  window?: string;
  cardId?: string;
  thread?: string;
}
export interface NextBestAction {
  sentences: string[];
  suggestion: string;
  actions: NbaAction[];
  why: string[];
  /** True when nothing needs the user. */
  clear: boolean;
}

interface Step { verb: string; action?: NbaAction; why: string }

/**
 * Orion's synthesis from the ranked focus cards (most important first), the unread total and open promises.
 * At most four sentences: up to three from cards and one about unread messages. The suggestion names the top two
 * things to do, in order; each comes with a button that opens the right place or drafts a reply (never sends).
 */
export function nextBestAction(cards: CardLike[], o: { unread: number; promises: PromiseLike[]; now: number; summary?: string }): NextBestAction {
  const sentences: string[] = [];
  const steps: Step[] = [];
  const used = new Set<string>(); // promise tasks already mentioned
  for (const c of cards) {
    if (sentences.length >= 3) break;
    if (c.kind === 'now') {
      const who = agentName(c.detail);
      const what = shortTitle(c.title);
      const waiting = c.meta.waiting_on || '';
      if (/paused/i.test(waiting)) {
        sentences.push(`${what} is paused.`);
        steps.push({ verb: `carry on with ${what}`, action: { id: c.id, label: `Open ${what}`, kind: 'window', window: c.open.window || 'missions' }, why: `${what}: you paused it.` });
      } else if (/\bOK\b/.test(waiting)) {
        sentences.push(`${possessive(who)} work on ${what} needs your OK${/once/i.test(waiting) ? ' once the checks pass' : ''}.`);
        steps.push({ verb: `check ${possessive(who)} work`, action: { id: c.id, label: `Review ${possessive(who)} work`, kind: 'window', window: c.open.window || 'missions' }, why: `${what}: ${c.why}` });
      } else {
        const pct = typeof c.meta.progress === 'number' ? ` (${Math.round(c.meta.progress * 100)}% done)` : '';
        sentences.push(`${who} is working on ${what}${pct}.`);
      }
    } else if (c.kind === 'message') {
      const who = c.meta.sender || c.title.split(' on ')[0] || 'Someone';
      const app = c.meta.app_label ? ` (${c.meta.app_label})` : '';
      const p = promiseFor(c, o.promises);
      const thing = p ? promiseObject(p.task, who) : null;
      if (p) used.add(p.task);
      const due = p && p.due ? `, due ${inWords(p.due, o.now)}` : '';
      sentences.push(thing ? `${who} is waiting for ${thing}${app}${due}.` : (c.meta.unread ?? 0) > 1 ? `${who} sent ${c.meta.unread} messages${app}.` : `${who} is waiting for a reply${app}.`);
      steps.push({ verb: `reply to ${who}`, action: { id: c.id, label: `Reply to ${who}`, kind: 'draft', cardId: c.id, thread: c.open.thread || c.meta.thread }, why: `${who}: ${c.why}` });
    } else if (c.kind === 'approval') {
      sentences.push(stop(c.title));
      const n = typeof c.meta.count === 'number' ? c.meta.count : 1;
      steps.push({ verb: n > 1 ? `go through the ${n} waiting for your OK` : 'look at what is waiting for your OK', action: { id: c.id, label: 'Open the queue', kind: 'window', window: c.open.window || 'queue' }, why: `Waiting for your OK: ${c.why}` });
    } else if (c.kind === 'needs_you') {
      sentences.push(stop(c.title));
      steps.push({ verb: `look at ${lowerFirst(shortTitle(c.title))}`, action: { id: c.id, label: 'Take a look', kind: 'window', window: c.open.window || 'chat' }, why: `${shortTitle(c.title)}: ${c.why}` });
    } else if (c.kind === 'next_up') {
      if (used.has(c.title)) continue;
      sentences.push(`Next: ${lowerFirst(c.title)}${c.detail ? `, ${lowerFirst(c.detail.replace(/\.$/, ''))}` : ''}.`);
      steps.push({ verb: lowerFirst(c.title), action: { id: c.id, label: 'Open Life', kind: 'window', window: c.open.window || 'life' }, why: `${c.title}: ${c.why}` });
    }
  }
  if (o.unread > 0) sentences.push(`You have ${o.unread} unread ${o.unread === 1 ? 'message' : 'messages'}.`);
  const clear = cards.length === 0;
  if (clear && !sentences.length) sentences.push('Nothing needs you right now.');
  else if (clear) sentences.unshift('Nothing urgent needs you.');
  const top = steps.slice(0, 2);
  const suggestion = top.length
    ? `Suggested: ${upperFirst(top.map((s) => s.verb).join(', then '))}.`
    : o.unread > 0
      ? 'Suggested: Catch up on your messages when you have a minute.'
      : 'Suggested: Pick one thing and start a focus block.';
  const summary = o.summary?.trim();
  return {
    sentences: summary ? [summary] : sentences.slice(0, 4),
    suggestion,
    actions: top.flatMap((s) => (s.action ? [s.action] : [])),
    why: steps.map((s) => s.why).slice(0, 5),
    clear,
  };
}

// --------------------------------------------------------------------------------------- today's timeline
export type TimelineKind = 'promise' | 'agenda' | 'wind' | 'bed' | 'break' | 'mission';
export interface TimelineItem {
  id: string;
  at: number | null; // seconds; null = no set time (listed last)
  title: string;
  kind: TimelineKind;
  sub?: string;
  /** Already happened (calendar items earlier today), shown dimmed. */
  past?: boolean;
  overdue?: boolean;
  promiseId?: string;
}

/** "23:00" as seconds: today, or tomorrow when that time is more than six hours gone (a 00:30 bedtime). */
export function clockAt(hm: string | undefined, now: number): number | null {
  const m = /^(\d{1,2}):(\d{2})$/.exec(hm ?? '');
  if (!m) return null;
  const d = new Date(now * 1000);
  d.setHours(Number(m[1]), Number(m[2]), 0, 0);
  let t = d.getTime() / 1000;
  if (t < now - 6 * 3600) t += 86400;
  return t;
}

const startOfDay = (now: number) => {
  const d = new Date(now * 1000);
  d.setHours(0, 0, 0, 0);
  return d.getTime() / 1000;
};

/**
 * One list for the day, in time order: everything from the start of today until six in the morning tomorrow
 * (so tonight's bedtime counts), overdue promises at their place in time, promises with no time last.
 */
export function dayTimeline(
  o: {
    promises?: PromiseLike[];
    life?: { wind_down_starts?: string; bedtime?: string; break_due?: boolean } | null;
    missions?: MissionLike[];
    agenda?: AgendaItem[];
  },
  now: number,
  max = 30,
): TimelineItem[] {
  const from = startOfDay(now);
  const to = from + 86400 + 6 * 3600;
  const items: TimelineItem[] = [];
  if (o.life?.break_due) items.push({ id: 'break', at: now, title: 'Take a short break', kind: 'break', sub: 'Life says one is due' });
  for (const a of o.agenda ?? []) {
    if (!(a.at >= from && a.at < to)) continue;
    const ended = (a.until ?? a.at + 30 * 60) < now;
    items.push({ id: `a-${a.at}-${a.title}`, at: a.at, title: a.title, kind: 'agenda', past: ended });
  }
  for (const p of o.promises ?? []) {
    if (p.due !== null && p.due >= to) continue;
    const overdue = p.due !== null && p.due < now - 60;
    items.push({ id: `p-${p.id}`, at: p.due, title: p.task, kind: 'promise', overdue, promiseId: p.id, sub: overdue ? 'Overdue' : 'You said you would' });
  }
  for (const m of o.missions ?? []) {
    if (m.state !== 'running' || !m.started || !m.time_limit_min) continue;
    const at = m.started + m.time_limit_min * 60;
    if (at < now || at >= to) continue;
    items.push({ id: `m-${m.id}`, at, title: `${shortTitle(m.title)} should be done`, kind: 'mission', sub: `${m.agent_label || 'An agent'}, at the latest` });
  }
  const wind = clockAt(o.life?.wind_down_starts, now);
  if (wind !== null && wind > now && wind < to) items.push({ id: 'wind', at: wind, title: 'Wind down', kind: 'wind', sub: 'Screens dim, phone on quiet' });
  const bed = clockAt(o.life?.bedtime, now);
  if (bed !== null && bed > now && bed < to) items.push({ id: 'bed', at: bed, title: 'Bedtime', kind: 'bed' });
  return items.sort((a, b) => (a.at ?? Infinity) - (b.at ?? Infinity)).slice(0, max);
}

/** The time column: "09:30", "Any time"; with "Tomorrow" when it falls after midnight. */
export function rowTime(at: number | null, now: number): { time: string; day: string } {
  if (at === null) return { time: 'Any time', day: '' };
  const d = new Date(at * 1000);
  const time = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  return { time, day: at >= startOfDay(now) + 86400 ? 'Tomorrow' : '' };
}

/** Where the "Now" line goes: before the first item that is not in the past. -1 when no item is past. */
export function nowLineAt(items: TimelineItem[], now: number): number {
  if (!items.some((i) => i.past || (i.at !== null && i.at < now - 60))) return -1;
  const i = items.findIndex((x) => !x.past && (x.at === null || x.at >= now - 60) && !x.overdue);
  return i === -1 ? items.length : i;
}

// --------------------------------------------------------------------------------------- waiting on you / others
export interface WaitRow { id: string; title: string; sub: string; open: { window?: string; thread?: string }; tone: 'approval' | 'needs' | 'running' | 'reply' }

function approvalTitle(a: ApprovalLike): string {
  if (a.title) return a.title;
  if (a.kind === 'discord_prompt') return `Reply to ${a.author || 'someone'}${a.channel ? ` in ${a.channel}` : ''}`;
  const k = (a.category || a.kind || 'item').replace(/_/g, ' ');
  return upperFirst(`${k}${a.platform ? ` on ${upperFirst(a.platform)}` : ''}`);
}

/** Things only you can move on: approvals, missions that finished or asked a question, and focus "needs you" cards. */
export function waitingOnYou(o: { approvals?: ApprovalLike[]; missions?: MissionLike[]; cards?: CardLike[] }, max = 6): WaitRow[] {
  const rows: WaitRow[] = [];
  for (const a of (o.approvals ?? []).filter((x) => x.status === 'pending').sort((x, y) => (y.created_at ?? 0) - (x.created_at ?? 0))) {
    rows.push({ id: `ap-${a.id}`, title: approvalTitle(a), sub: 'Waiting for your OK', open: { window: 'queue' }, tone: 'approval' });
  }
  for (const m of o.missions ?? []) {
    if (m.state === 'needs_you') rows.push({ id: `m-${m.id}`, title: m.title, sub: m.summary ? m.summary : 'Has a question for you', open: { window: 'missions' }, tone: 'needs' });
    else if (m.state === 'review') rows.push({ id: `m-${m.id}`, title: m.title, sub: `${m.agent_label ? `${agentName(m.agent_label)} finished. ` : ''}Ready for your review`, open: { window: 'missions' }, tone: 'approval' });
  }
  for (const c of o.cards ?? []) {
    if (c.kind !== 'needs_you' || c.id.startsWith('needs:mission:')) continue;
    rows.push({ id: c.id, title: c.title, sub: c.detail || c.why, open: c.open, tone: 'needs' });
  }
  return rows.slice(0, max);
}

/** Work that is moving without you (running and queued missions) and chats where you wrote last and await a reply. */
export function waitingOnOthers(o: { missions?: MissionLike[]; threads?: ThreadLike[]; now: number }, max = 5): WaitRow[] {
  const rows: WaitRow[] = [];
  for (const m of o.missions ?? []) {
    if (m.state === 'running') rows.push({ id: `m-${m.id}`, title: m.title, sub: `${agentName(m.agent_label)} is on it`, open: { window: 'missions' }, tone: 'running' });
  }
  for (const m of o.missions ?? []) {
    if (m.state === 'queued') rows.push({ id: `m-${m.id}`, title: m.title, sub: `Waiting to start${m.agent_label ? ` (${agentName(m.agent_label)})` : ''}`, open: { window: 'missions' }, tone: 'running' });
  }
  const replies = (o.threads ?? [])
    .filter((t) => !t.muted && t.key && (t.messages ?? []).length)
    .map((t) => ({ t, last: t.messages![t.messages!.length - 1] }))
    .filter(({ last }) => last.direction === 'out' && (last.timestamp ?? 0) > o.now - 3 * 86400)
    .sort((a, b) => (b.last.timestamp ?? 0) - (a.last.timestamp ?? 0));
  for (const { t, last } of replies) {
    rows.push({ id: `t-${t.key}`, title: t.thread_name || 'A chat', sub: `You wrote ${inWords(last.timestamp ?? o.now, o.now)}: ${last.text ?? ''}`, open: { window: 'messages', thread: t.key }, tone: 'reply' });
  }
  return rows.slice(0, max);
}

/** "4 projects, 1 running, 1 needs you" for the Mission Control panel. */
export function projectsSummary(statuses: string[]): string {
  if (!statuses.length) return 'No projects yet';
  const running = statuses.filter((s) => s === 'running').length;
  const needs = statuses.filter((s) => s === 'needs').length;
  const parts = [`${statuses.length} ${statuses.length === 1 ? 'project' : 'projects'}`];
  if (running) parts.push(`${running} running`);
  if (needs) parts.push(`${needs} ${needs === 1 ? 'needs' : 'need'} you`);
  return parts.join(', ');
}
