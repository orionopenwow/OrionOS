/**
 * Rules for the command console's side panels (components/console/CommandWidgets.tsx): the inline calculator and
 * unit converter, project status words, the Today timeline, unread counts by app and the computer meters.
 * No imports, so tests can run it directly with `node --test` (tests/commandCentre.test.ts).
 */

// --------------------------------------------------------------------------------------- calculator
type Tok = { t: 'n'; v: number } | { t: 'op'; v: string };

function tokens(src: string): Tok[] | null {
  const out: Tok[] = [];
  const s = src.replace(/,/g, '').replace(/×/g, '*').replace(/÷/g, '/').replace(/\bx\b/g, '*');
  let i = 0;
  while (i < s.length) {
    const c = s[i];
    if (c === ' ') { i++; continue; }
    const num = /^(\d+\.?\d*|\.\d+)(e[+-]?\d+)?/i.exec(s.slice(i));
    if (num) { out.push({ t: 'n', v: Number(num[0]) }); i += num[0].length; continue; }
    if ('+-*/%^()'.includes(c)) { out.push({ t: 'op', v: c }); i++; continue; }
    return null;
  }
  return out;
}

/** A small, safe arithmetic reader (no eval): + - * / ^, % as "percent of", brackets. */
function evaluate(src: string): number | null {
  const ts = tokens(src);
  if (!ts || !ts.length) return null;
  let at = 0;
  const peek = () => ts[at];
  const isOp = (v: string) => peek()?.t === 'op' && peek()!.v === v;
  const expr = (): number => {
    let v = term();
    while (isOp('+') || isOp('-')) {
      const op = (ts[at++] as { v: string }).v;
      const r = term();
      v = op === '+' ? v + r : v - r;
    }
    return v;
  };
  const term = (): number => {
    let v = power();
    while (isOp('*') || isOp('/')) {
      const op = (ts[at++] as { v: string }).v;
      const r = power();
      v = op === '*' ? v * r : v / r;
    }
    return v;
  };
  const power = (): number => {
    const b = unary();
    if (isOp('^')) { at++; return b ** power(); }
    return b;
  };
  const unary = (): number => {
    if (isOp('-')) { at++; return -unary(); }
    if (isOp('+')) { at++; return unary(); }
    let v: number;
    if (isOp('(')) {
      at++;
      v = expr();
      if (!isOp(')')) throw new Error('bracket');
      at++;
    } else {
      const n = ts[at++];
      if (!n || n.t !== 'n') throw new Error('number');
      v = n.v;
    }
    if (isOp('%')) { at++; v = v / 100; }
    return v;
  };
  try {
    const v = expr();
    if (at !== ts.length || !Number.isFinite(v)) return null;
    return v;
  } catch {
    return null;
  }
}

const UNITS: Record<string, { kind: string; f: number }> = {
  mm: { kind: 'len', f: 0.001 }, cm: { kind: 'len', f: 0.01 }, m: { kind: 'len', f: 1 }, km: { kind: 'len', f: 1000 },
  in: { kind: 'len', f: 0.0254 }, ft: { kind: 'len', f: 0.3048 }, yd: { kind: 'len', f: 0.9144 }, mi: { kind: 'len', f: 1609.344 },
  g: { kind: 'mass', f: 0.001 }, kg: { kind: 'mass', f: 1 }, oz: { kind: 'mass', f: 0.0283495 }, lb: { kind: 'mass', f: 0.453592 },
  ml: { kind: 'vol', f: 0.001 }, l: { kind: 'vol', f: 1 }, gal: { kind: 'vol', f: 3.78541 }, cup: { kind: 'vol', f: 0.236588 },
  kb: { kind: 'data', f: 1e3 }, mb: { kind: 'data', f: 1e6 }, gb: { kind: 'data', f: 1e9 }, tb: { kind: 'data', f: 1e12 },
  s: { kind: 'time', f: 1 }, min: { kind: 'time', f: 60 }, h: { kind: 'time', f: 3600 }, day: { kind: 'time', f: 86400 },
  c: { kind: 'temp', f: 1 }, f: { kind: 'temp', f: 1 }, k: { kind: 'temp', f: 1 },
};
const ALIAS: Record<string, string> = {
  meter: 'm', meters: 'm', metre: 'm', metres: 'm', kilometers: 'km', kilometres: 'km', miles: 'mi', mile: 'mi', inch: 'in', inches: 'in',
  foot: 'ft', feet: 'ft', yards: 'yd', grams: 'g', kilos: 'kg', kgs: 'kg', pounds: 'lb', lbs: 'lb', ounces: 'oz', litre: 'l', liter: 'l',
  litres: 'l', liters: 'l', gallons: 'gal', cups: 'cup', sec: 's', secs: 's', seconds: 's', mins: 'min', minutes: 'min', hr: 'h', hrs: 'h',
  hours: 'h', days: 'day', celsius: 'c', fahrenheit: 'f', kelvin: 'k', '°c': 'c', '°f': 'f',
};
const unit = (u: string) => {
  const k = u.toLowerCase();
  const id = UNITS[k] ? k : ALIAS[k];
  return id ? { id, ...UNITS[id] } : null;
};
function toKelvin(v: number, u: string) { return u === 'c' ? v + 273.15 : u === 'f' ? (v - 32) * (5 / 9) + 273.15 : v; }
function fromKelvin(v: number, u: string) { return u === 'c' ? v - 273.15 : u === 'f' ? (v - 273.15) * (9 / 5) + 32 : v; }

/** Round for reading: up to 6 significant digits, no trailing zeros, thousands separated. */
export function tidy(n: number): string {
  if (Number.isInteger(n) && Math.abs(n) < 1e15) return n.toLocaleString('en-US');
  const p = Number(n.toPrecision(6));
  return Math.abs(p) >= 1e15 || (Math.abs(p) < 1e-6 && p !== 0) ? p.toExponential(3) : p.toLocaleString('en-US', { maximumFractionDigits: 6 });
}

/**
 * What the calculator shows for what was typed: "12*7+3" -> "87", "5 km in mi" -> "3.10686 mi", "70 f to c" -> "21.1111 c".
 * Null when it isn't a sum or a conversion (then nothing is shown).
 */
export function calc(input: string): string | null {
  const s = input.trim().replace(/^=\s*/, '');
  if (!s) return null;
  const conv = /^(.+?)\s*([a-z°]+)\s+(?:in|to|as)\s+([a-z°]+)$/i.exec(s);
  if (conv) {
    const v = evaluate(conv[1]);
    const a = unit(conv[2]);
    const b = unit(conv[3]);
    if (v === null || !a || !b || a.kind !== b.kind) return null;
    const out = a.kind === 'temp' ? fromKelvin(toKelvin(v, a.id), b.id) : (v * a.f) / b.f;
    return `${tidy(out)} ${b.id}`;
  }
  if (!/[\d)]/.test(s) || /^\d+(\.\d+)?$/.test(s)) return null; // a lone number isn't a sum
  const v = evaluate(s);
  return v === null ? null : tidy(v);
}

// --------------------------------------------------------------------------------------- projects
export type ProjectStatus = 'needs' | 'running' | 'idle';
export const PROJECT_STATUS_LABEL: Record<ProjectStatus, string> = { needs: 'Needs you', running: 'Running', idle: 'Idle' };

/** A project's status from its latest mission state. */
export function projectStatus(state: string | null | undefined): ProjectStatus {
  if (state === 'needs_you' || state === 'review') return 'needs';
  if (state === 'running' || state === 'queued') return 'running';
  return 'idle';
}

/** Projects that need you first, then running ones, then the most recent; at most `max`. */
export function sortProjects<T extends { status: ProjectStatus; created: number }>(list: T[], max = 5): T[] {
  const rank: Record<ProjectStatus, number> = { needs: 0, running: 1, idle: 2 };
  return [...list].sort((a, b) => rank[a.status] - rank[b.status] || b.created - a.created).slice(0, max);
}

// --------------------------------------------------------------------------------------- today
export interface TodayItem {
  id: string;
  at: number | null; // seconds; null = no set time
  label: string;
  kind: 'promise' | 'wind' | 'bed' | 'break';
  promiseId?: string;
}

/** The time and the day for the timeline: {time: "18:30", day: ""}, {time: "01:30", day: "Tomorrow"}, {time: "Overdue"}. */
export function timeParts(at: number | null, now: number): { time: string; day: string } {
  if (at === null) return { time: 'Any time', day: '' };
  if (at < now - 60) return { time: 'Overdue', day: '' };
  const d = new Date(at * 1000);
  const n = new Date(now * 1000);
  const time = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  const days = Math.round((new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime() - new Date(n.getFullYear(), n.getMonth(), n.getDate()).getTime()) / 86400000);
  if (days <= 0) return { time, day: '' };
  if (days === 1) return { time, day: 'Tomorrow' };
  return { time, day: d.toLocaleDateString('en-US', { weekday: 'long' }) };
}

/** "18:30" for today's timeline; "Overdue" when the time has passed; "Tomorrow 18:30" or a weekday further out. */
export function whenWords(at: number | null, now: number): string {
  const { time, day } = timeParts(at, now);
  return day ? `${day} ${time}` : time;
}

/** "23:00" today (or tomorrow if it has passed) as seconds. */
export function clockToday(hm: string | undefined, now: number): number | null {
  const m = /^(\d{1,2}):(\d{2})$/.exec(hm ?? '');
  if (!m) return null;
  const d = new Date(now * 1000);
  d.setHours(Number(m[1]), Number(m[2]), 0, 0);
  return d.getTime() / 1000;
}

/**
 * The day's timeline: open promises due within two days, the wind-down and bedtime from Life, and a break when
 * one is due. In time order, overdue first; at most `max` rows.
 */
export function todayTimeline(
  promises: Array<{ id: string; task: string; due: number | null }>,
  life: { wind_down_starts?: string; bedtime?: string; break_due?: boolean } | null,
  now: number,
  max = 5,
): TodayItem[] {
  const items: TodayItem[] = [];
  if (life?.break_due) items.push({ id: 'break', at: now, label: 'Take a short break', kind: 'break' });
  for (const p of promises) {
    if (p.due !== null && p.due > now + 2 * 86400) continue;
    items.push({ id: `p-${p.id}`, at: p.due, label: p.task, kind: 'promise', promiseId: p.id });
  }
  const wind = clockToday(life?.wind_down_starts, now);
  if (wind !== null && wind > now) items.push({ id: 'wind', at: wind, label: 'Wind down', kind: 'wind' });
  const bed = clockToday(life?.bedtime, now);
  if (bed !== null && bed > now) items.push({ id: 'bed', at: bed, label: 'Bedtime', kind: 'bed' });
  return items.sort((a, b) => (a.at ?? Infinity) - (b.at ?? Infinity)).slice(0, max);
}

/** 1500 -> "25:00". */
export function clock(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
}

// --------------------------------------------------------------------------------------- messages
const APP_NAMES: Record<string, string> = {
  telegram: 'Telegram', telegram_user: 'Telegram', discord: 'Discord', whatsapp: 'WhatsApp', signal: 'Signal', sms: 'Texts',
  phone: 'Texts', instagram: 'Instagram', slack: 'Slack', email: 'Email', gmail: 'Email', x: 'X', twitter: 'X', messenger: 'Messenger',
};
export const appName = (platform: string) => APP_NAMES[platform.toLowerCase()] ?? platform.charAt(0).toUpperCase() + platform.slice(1);

/** Unread counts per app, most unread first. */
export function unreadByApp(threads: Array<{ platform?: string; unread?: number }>): Array<{ app: string; unread: number }> {
  const by = new Map<string, number>();
  for (const t of threads) {
    const n = Number(t.unread) || 0;
    if (!n || !t.platform) continue;
    const app = appName(t.platform);
    by.set(app, (by.get(app) ?? 0) + n);
  }
  return [...by.entries()].map(([app, unread]) => ({ app, unread })).sort((a, b) => b.unread - a.unread || a.app.localeCompare(b.app));
}

// --------------------------------------------------------------------------------------- computer
/** The share in use, 0..100, from a health line like "5.1 GB of 16.0 GB free (32%)." (the brackets hold the share free). */
export function usedFromFree(reason: string | undefined): number | null {
  const m = /\((\d{1,3})%\)/.exec(reason ?? '');
  return m ? Math.max(0, Math.min(100, 100 - Number(m[1]))) : null;
}

/** Calm / busy / high for a meter. */
export function meterLevel(pct: number | null): 'ok' | 'warn' | 'high' | 'none' {
  if (pct === null) return 'none';
  return pct >= 90 ? 'high' : pct >= 75 ? 'warn' : 'ok';
}
