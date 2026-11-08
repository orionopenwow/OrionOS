/**
 * The command console's side panels (owner feedback: "fill the panes to the full screen too, it should be the
 * ultimate life and project command center with tools of all kinds"). Small solid gunmetal panels, each with a
 * coloured group marker, a plain title and at most five rows:
 *
 *   - Projects  GET /projects (projects.py): status from each project's latest mission, one button each.
 *   - Today     a focus timer (local, survives closing the console) and the day's timeline: open promises from
 *               GET /promises (promise_keeper.py), wind-down and bedtime from Life (telemetry.circadian) and a
 *               break when Life says one is due (telemetry.pacing).
 *   - Quick tools  an inline calculator and unit converter (local), and a quick note saved to the vault
 *               (POST /vault/drop, the same route as dropping a file on the core). "Look at my screen" stays one of
 *               the middle's quick actions.
 *   - Computer  CPU and battery from GET /core/signals, memory and disk from GET /health/status, the phone link
 *               from GET /phones.
 *   - Messages  unread counts by app from the live chat list (the same threads Messages shows, muted ones left
 *               out); a row opens Messages.
 *
 * Every panel copes with Orion's brain being away: it shows a short line instead of numbers.
 * Rules live in lib/commandCentreView.ts (tested in tests/commandCentre.test.ts).
 */
import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { Calculator, Check, Pause, Play, Plus, RotateCcw, StickyNote } from 'lucide-react';
import type { TelemetrySnapshot } from '@orion/shared-types';
import { ORION_HTTP_URL, dropToVault } from '../../lib/assets';
import { useDaemonPoll } from '../../lib/swarmCompute';
import { projectsApi, type ProjectsSnapshot } from '../../lib/projects';
import {
  appName, calc, clock, meterLevel, PROJECT_STATUS_LABEL, projectStatus, sortProjects, timeParts, todayTimeline, unreadByApp, usedFromFree,
  type ProjectStatus,
} from '../../lib/commandCentreView';
import './command.css';

export type PanelGroup = 'work' | 'life' | 'connect' | 'system';

/** One side panel: a coloured marker, a title, an optional count or action, then its rows. */
export function Panel({
  title, group, meta, action, grow, children, label,
}: { title: string; group: PanelGroup; meta?: ReactNode; action?: ReactNode; grow?: boolean; children: ReactNode; label?: string }) {
  return (
    <section className="cc-panel" data-group={group} data-grow={grow || undefined} aria-label={label ?? title}>
      <header className="cc-panel__head">
        <h2 className="cc-panel__title">{title}</h2>
        {meta !== undefined && <span className="cc-panel__meta">{meta}</span>}
        {action}
      </header>
      <div className="cc-panel__body">{children}</div>
    </section>
  );
}

const Empty = ({ children }: { children: ReactNode }) => <p className="cc-empty">{children}</p>;

// --------------------------------------------------------------------------------------- Projects
interface WidgetProps {
  /** Open a window by its menu id (closes the console). */
  onOpen: (id: string) => void;
}

const STATUS_BUTTON: Record<ProjectStatus, string> = { needs: 'Review', running: 'Watch', idle: 'Open' };

export function ProjectsPanel({ onOpen, grow }: WidgetProps & { grow?: boolean }) {
  const [data] = useDaemonPoll<ProjectsSnapshot>('/projects', 15_000);
  const [note, setNote] = useState('');
  const rows = useMemo(
    () => sortProjects((data?.projects ?? []).map((p) => ({ ...p, status: projectStatus(p.latest?.state) }))),
    [data],
  );
  const busy = rows.filter((r) => r.status !== 'idle').length;
  const open = async (name: string, status: ProjectStatus) => {
    if (status !== 'idle') return onOpen('missions');
    try {
      setNote((await projectsApi.open(name)).message || 'Opened.');
    } catch (e) {
      setNote(e instanceof Error ? e.message : "Couldn't open it just now.");
    }
  };
  return (
    <Panel
      title="Projects"
      group="work"
      grow={grow}
      meta={data ? (busy ? `${busy} active` : `${data.projects.length}`) : undefined}
      action={
        <button type="button" className="cc-mini" onClick={() => onOpen('projects')}>
          <Plus size={14} /> New project
        </button>
      }
    >
      {!data ? (
        <Empty>Projects show here when Orion's brain is running.</Empty>
      ) : rows.length === 0 ? (
        <Empty>No projects yet. Start one with New project.</Empty>
      ) : (
        <ul className="cc-rows">
          {rows.map((p) => (
            <li key={p.name} className="cc-row" data-status={p.status}>
              <span className="cc-status" aria-hidden />
              <span className="cc-row__text">
                <span className="cc-row__name">{p.name}</span>
                <span className="cc-row__sub">
                  {PROJECT_STATUS_LABEL[p.status]}
                  {p.status !== 'idle' && p.latest?.phase ? `: ${p.latest.phase}` : ''}
                </span>
              </span>
              <button type="button" className="cc-mini" onClick={() => void open(p.name, p.status)} aria-label={`${STATUS_BUTTON[p.status]} ${p.name}`}>
                {STATUS_BUTTON[p.status]}
              </button>
            </li>
          ))}
        </ul>
      )}
      {note && <p className="cc-note" role="status">{note}</p>}
    </Panel>
  );
}

// --------------------------------------------------------------------------------------- the focus timer (local)
interface TimerState {
  endsAt: number | null; // ms; null = not running
  left: number; // seconds left when paused
  length: number; // seconds
}
const TIMER_KEY = 'orion-focus-timer';
const FOCUS_S = 25 * 60;
const BREAK_S = 5 * 60;
let timer: TimerState = (() => {
  try {
    const t = JSON.parse(localStorage.getItem(TIMER_KEY) || 'null') as TimerState | null;
    if (t && typeof t.length === 'number') return t;
  } catch {
    /* storage blocked: start fresh */
  }
  return { endsAt: null, left: FOCUS_S, length: FOCUS_S };
})();
function saveTimer(t: TimerState) {
  timer = t;
  try {
    localStorage.setItem(TIMER_KEY, JSON.stringify(t));
  } catch {
    /* storage blocked: the timer still runs for this visit */
  }
}

function FocusTimer() {
  const [, tick] = useState(0);
  const [t, setT] = useState<TimerState>(timer);
  const set = (n: TimerState) => {
    saveTimer(n);
    setT(n);
  };
  useEffect(() => {
    if (t.endsAt === null) return;
    const id = window.setInterval(() => tick((n) => n + 1), 1000);
    return () => window.clearInterval(id);
  }, [t.endsAt]);
  const left = t.endsAt === null ? t.left : Math.max(0, (t.endsAt - Date.now()) / 1000);
  const finished = t.endsAt !== null && left <= 0;
  const running = t.endsAt !== null && !finished;
  const start = (len: number) => set({ endsAt: Date.now() + len * 1000, left: len, length: len });
  const pause = () => set({ ...t, endsAt: null, left });
  const resume = () => set({ ...t, endsAt: Date.now() + t.left * 1000 });
  const reset = () => set({ endsAt: null, left: FOCUS_S, length: FOCUS_S });
  const fresh = !running && !finished && t.left === t.length;
  const pct = t.length ? Math.round(((t.length - left) / t.length) * 100) : 0;
  return (
    <div className="cc-timer" data-running={running || undefined}>
      <div className="cc-timer__clock" role="timer" aria-label={`Focus timer: ${clock(left)} left`}>
        {finished ? 'Done' : clock(left)}
      </div>
      <div className="cc-timer__side">
        <span className="cc-timer__what">
          {finished ? (t.length === BREAK_S ? 'Break over.' : 'Nice work. Take a break.') : running ? (t.length === BREAK_S ? 'On a break' : 'Focusing') : fresh ? 'Focus timer' : 'Paused'}
        </span>
        <div className="cc-timer__btns">
          {running ? (
            <button type="button" className="cc-mini" onClick={pause}>
              <Pause size={14} /> Pause
            </button>
          ) : fresh || finished ? (
            <>
              <button type="button" className="cc-mini cc-mini--solid" onClick={() => start(FOCUS_S)}>
                <Play size={14} /> Start 25 min
              </button>
              <button type="button" className="cc-mini" onClick={() => start(BREAK_S)}>
                5 min break
              </button>
            </>
          ) : (
            <button type="button" className="cc-mini cc-mini--solid" onClick={resume}>
              <Play size={14} /> Carry on
            </button>
          )}
          {!fresh && (
            <button type="button" className="cc-mini cc-mini--icon" onClick={reset} aria-label="Reset the timer" title="Reset">
              <RotateCcw size={14} />
            </button>
          )}
        </div>
      </div>
      {(running || (!fresh && !finished)) && (
        <span className="cc-timer__bar" aria-hidden>
          <span style={{ width: `${pct}%` }} />
        </span>
      )}
    </div>
  );
}

// --------------------------------------------------------------------------------------- Today
interface PromiseRow { id: string; task: string; due: number | null }

export function TodayPanel({ onOpen, telemetry, grow, rows = 4 }: WidgetProps & { telemetry?: TelemetrySnapshot | null; grow?: boolean; rows?: number }) {
  const [data, reload] = useDaemonPoll<{ promises: PromiseRow[] }>('/promises', 60_000);
  const [done, setDone] = useState<Set<string>>(new Set());
  const now = Date.now() / 1000;
  const circ = telemetry?.circadian;
  const items = todayTimeline(
    (data?.promises ?? []).filter((p) => !done.has(p.id)),
    { wind_down_starts: circ?.wind_down_starts, bedtime: circ?.bedtime, break_due: telemetry?.pacing?.phase === 'break_due' },
    now,
    rows,
  );
  const markDone = async (id: string) => {
    setDone((s) => new Set(s).add(id));
    try {
      await fetch(`${ORION_HTTP_URL}/promises/${encodeURIComponent(id)}/done`, { method: 'POST' });
    } catch {
      /* offline: it comes back on the next look */
    }
    reload();
  };
  return (
    <Panel
      title="Today"
      group="life"
      grow={grow}
      action={
        <button type="button" className="cc-mini" onClick={() => onOpen('life')}>
          Open Life
        </button>
      }
    >
      <FocusTimer />
      {items.length === 0 ? (
        <Empty>{data ? 'Nothing else planned for today.' : 'Promises and your wind-down show here when Orion is running.'}</Empty>
      ) : (
        <ul className="cc-rows cc-rows--timeline">
          {items.map((it) => {
            const when = it.kind === 'break' ? { time: 'Now', day: '' } : timeParts(it.at, now);
            return (
            <li key={it.id} className="cc-row" data-kind={it.kind} data-overdue={it.at !== null && it.at < now - 60 ? true : undefined}>
              <span className="cc-row__time">{when.time}</span>
              <span className="cc-row__text">
                <span className="cc-row__name">{it.label}</span>
                {when.day && <span className="cc-row__sub">{when.day}</span>}
              </span>
              {it.promiseId && (
                <button type="button" className="cc-mini cc-mini--icon" onClick={() => void markDone(it.promiseId!)} aria-label={`Mark done: ${it.label}`} title="Mark done">
                  <Check size={14} />
                </button>
              )}
            </li>
            );
          })}
        </ul>
      )}
    </Panel>
  );
}

// --------------------------------------------------------------------------------------- Quick tools
const NOTE_KEY = 'orion-quick-note';
const readNote = () => {
  try {
    return localStorage.getItem(NOTE_KEY) ?? '';
  } catch {
    return '';
  }
};

export function QuickToolsPanel({ grow }: { grow?: boolean }) {
  const [sum, setSum] = useState('');
  const [copied, setCopied] = useState(false);
  const [noteText, setNoteTextRaw] = useState(readNote);
  const setNoteText = (v: string) => {
    setNoteTextRaw(v);
    try {
      localStorage.setItem(NOTE_KEY, v); // an unsaved note survives closing the console
    } catch {
      /* storage blocked: kept until the console closes */
    }
  };
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState('');
  const answer = calc(sum);

  const copy = () => {
    if (!answer) return;
    void navigator.clipboard?.writeText(answer.replace(/,/g, '')).catch(() => undefined);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  };
  const save = async (e: React.SyntheticEvent) => {
    e.preventDefault();
    const text = noteText.trim();
    if (!text) return;
    setSaving(true);
    setStatus('');
    const stamp = new Date().toISOString().slice(0, 16).replace('T', ' ').replace(':', '.');
    try {
      await dropToVault(new File([`${text}\n`], `Quick note ${stamp}.md`, { type: 'text/markdown' }));
      setNoteText('');
      setStatus('Saved to your vault.');
    } catch (err) {
      setStatus(err instanceof Error ? `Not saved: ${err.message}` : 'Not saved. Is Orion running?');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Panel title="Quick tools" group="system" grow={grow}>
      <label className="cc-field">
        <Calculator size={15} aria-hidden className="cc-field__icon" />
        <input
          value={sum}
          onChange={(e) => setSum(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') copy();
          }}
          placeholder="Sum or convert: 12*7, 5 km in mi"
          aria-label="Calculator: type a sum or a conversion"
          className="cc-field__input"
        />
        {answer && (
          <button type="button" className="cc-field__answer" onClick={copy} title="Copy the answer" aria-live="polite">
            {copied ? 'Copied' : `= ${answer}`}
          </button>
        )}
      </label>
      {grow ? (
        <form className="cc-pad" onSubmit={save}>
          <textarea
            value={noteText}
            onChange={(e) => setNoteText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) void save(e);
            }}
            placeholder="Jot something down. Save puts it in your vault."
            aria-label="Quick note to your vault"
            className="cc-pad__text"
          />
          <div className="cc-pad__foot">
            <span className="cc-pad__hint">Ctrl+Enter saves</span>
            <button type="submit" className="cc-mini cc-mini--solid" disabled={!noteText.trim() || saving}>
              <StickyNote size={14} /> {saving ? 'Saving' : 'Save to vault'}
            </button>
          </div>
        </form>
      ) : (
        <form className="cc-field" onSubmit={save}>
          <StickyNote size={15} aria-hidden className="cc-field__icon" />
          <input
            value={noteText}
            onChange={(e) => setNoteText(e.target.value)}
            placeholder="Quick note to your vault"
            aria-label="Quick note to your vault"
            className="cc-field__input"
          />
          <button type="submit" className="cc-mini" disabled={!noteText.trim() || saving}>
            {saving ? 'Saving' : 'Save'}
          </button>
        </form>
      )}
      {status && <p className="cc-note" role="status">{status}</p>}
    </Panel>
  );
}

// --------------------------------------------------------------------------------------- Computer
interface HealthCheck { id: string; status: string; reason: string }
interface Phone { name: string; online: boolean; last_seen: number | null; battery: number | null }

function ago(ts: number | null): string {
  if (!ts) return 'a while ago';
  const s = Date.now() / 1000 - ts;
  return s < 120 ? 'just now' : s < 7200 ? `${Math.round(s / 60)} min ago` : s < 172800 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} days ago`;
}

function Meter({ label, pct, text }: { label: string; pct: number | null; text?: string }) {
  const level = meterLevel(pct);
  return (
    <li className="cc-meter" data-level={level}>
      <span className="cc-meter__label">{label}</span>
      <span className="cc-meter__bar" role="meter" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct ?? undefined}>
        <span style={{ width: `${pct ?? 0}%` }} />
      </span>
      <span className="cc-meter__val">{text ?? (pct === null ? 'n/a' : `${pct}%`)}</span>
    </li>
  );
}

export function ComputerPanel({ onOpen, grow }: WidgetProps & { grow?: boolean }) {
  const [core] = useDaemonPoll<{ cpu?: number | null; battery?: number | null }>('/core/signals', 5_000);
  const [health] = useDaemonPoll<{ status: string; words: string; checks: HealthCheck[] }>('/health/status', 20_000);
  const [phoneData] = useDaemonPoll<{ phones: Phone[] }>('/phones', 15_000);
  const check = (id: string) => health?.checks.find((c) => c.id === id);
  const cpu = typeof core?.cpu === 'number' ? Math.round(core.cpu * 100) : null;
  const mem = usedFromFree(check('memory')?.reason);
  const disk = usedFromFree(check('disk')?.reason);
  const battery = typeof core?.battery === 'number' ? Math.round(core.battery) : null;
  const phone = phoneData?.phones?.[0];
  const online = !!(core || health);
  return (
    <Panel
      title="Computer"
      group="system"
      grow={grow}
      meta={health ? (health.status === 'ok' ? 'All good' : 'Needs a look') : undefined}
      action={
        <button type="button" className="cc-mini" onClick={() => onOpen('analytics')}>
          Details
        </button>
      }
    >
      {!online ? (
        <Empty>CPU, memory and disk show here when Orion's brain is running.</Empty>
      ) : (
        <ul className="cc-meters">
          <Meter label="CPU" pct={cpu} />
          <Meter label="Memory" pct={mem} />
          <Meter label="Disk" pct={disk} />
          {battery !== null && <Meter label="Battery" pct={battery} text={`${battery}%`} />}
        </ul>
      )}
      <button type="button" className="cc-link-row" onClick={() => onOpen('phone')} data-on={phone?.online || undefined}>
        <span className="cc-status" aria-hidden />
        <span className="cc-row__text">
          <span className="cc-row__name">Phone link</span>
          <span className="cc-row__sub">
            {!phoneData
              ? 'Not checked yet'
              : !phone
                ? 'No phone paired. Tap to pair one.'
                : phone.online
                  ? `${phone.name} is connected${phone.battery !== null ? `, battery ${phone.battery}%` : ''}`
                  : `${phone.name}, last seen ${ago(phone.last_seen)}`}
          </span>
        </span>
      </button>
    </Panel>
  );
}

// --------------------------------------------------------------------------------------- Messages
interface ThreadLike {
  key?: string;
  platform?: string;
  thread_name?: string;
  unread?: number;
  muted?: boolean;
  last_ts?: number;
  messages?: Array<{ author?: string; text?: string; direction?: string }>;
}

export function MessagesPanel({
  onOpen, onOpenThread, threads, grow, rows = 5, latest = 0,
}: WidgetProps & { onOpenThread?: (key: string) => void; threads?: ThreadLike[]; grow?: boolean; rows?: number; latest?: number }) {
  const live = (threads ?? []).filter((t) => !t.muted);
  const apps = unreadByApp(live);
  const total = apps.reduce((n, a) => n + a.unread, 0);
  const recent = live
    .filter((t) => (t.unread ?? 0) > 0 && t.key)
    .sort((a, b) => (b.last_ts ?? 0) - (a.last_ts ?? 0))
    .slice(0, latest);
  const lastIn = (t: ThreadLike) => [...(t.messages ?? [])].reverse().find((m) => m.direction !== 'out');
  return (
    <Panel
      title="Messages"
      group="connect"
      grow={grow}
      meta={threads ? (total ? `${total} unread` : 'All read') : undefined}
      action={
        <button type="button" className="cc-mini" onClick={() => onOpen('messages')}>
          Open
        </button>
      }
    >
      {apps.length === 0 ? (
        <Empty>{threads?.length ? "Nothing unread. You're caught up." : 'Chats from your phone and socials show here.'}</Empty>
      ) : (
        <ul className="cc-rows">
          {apps.slice(0, rows).map((a) => (
            <li key={a.app}>
              <button type="button" className="cc-row cc-row--button" onClick={() => onOpen('messages')} aria-label={`${a.app}: ${a.unread} unread. Open Messages`}>
                <span className="cc-status cc-status--app" aria-hidden />
                <span className="cc-row__text">
                  <span className="cc-row__name">{a.app}</span>
                </span>
                <span className="cc-count">{a.unread > 99 ? '99+' : a.unread}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {recent.length > 0 && (
        <>
          <h3 className="cc-sub">Latest</h3>
          <ul className="cc-rows">
            {recent.map((t) => {
              const m = lastIn(t);
              return (
                <li key={t.key}>
                  <button
                    type="button"
                    className="cc-row cc-row--button"
                    onClick={() => (onOpenThread ? onOpenThread(t.key!) : onOpen('messages'))}
                    aria-label={`Open the chat with ${m?.author || t.thread_name}`}
                  >
                    <span className="cc-row__text">
                      <span className="cc-row__name">
                        {m?.author || t.thread_name}
                        <span className="cc-row__app"> {appName(t.platform ?? '')}</span>
                      </span>
                      {m?.text && <span className="cc-row__sub">{m.text}</span>}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        </>
      )}
    </Panel>
  );
}
