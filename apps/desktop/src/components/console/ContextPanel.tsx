/**
 * [context] "Focus and context": the lead of the command console before a conversation starts (owner mockup,
 * 2026-11-07). Top to bottom:
 *
 *   1. The focus timer: the console's one timer (big clock, one start/pause button, short label).
 *   2. Next best action: Orion's short read of what matters now in two to four plain sentences, one suggestion,
 *      up to two buttons that do real things (open the right window, or open a chat with a drafted reply in its
 *      box: drafting only, sending stays your tap), and "Why Orion suggests this" with the reasons the focus
 *      ranking gave. Built locally from GET /focus with fixed sentence patterns (lib/contextView.ts); no cloud
 *      model is asked. "Show all (5)" opens the five focus cards (the owner's "at most five things").
 *   3. Today's timeline: one list by time of day: promises, calendar items the daemon has, wind-down, bedtime,
 *      breaks and when running missions should be done. It scrolls on its own.
 *
 * The chat bar stays under it; once a conversation starts this area gives way to it, and "New" brings it back.
 */
import { useEffect, useMemo, useState } from 'react';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import { ChevronDown, Loader2, MessageSquareReply, PanelTop, Sparkles } from 'lucide-react';
import type { CommsThread, TelemetrySnapshot } from '@orion/shared-types';
import { emojiUrl, type EmojiName } from '../../lib/emoji';
import { focusApi, topCards } from '../../lib/focus';
import { useCalmMotion } from '../../lib/hudPrefs';
import { useDaemonPoll } from '../../lib/swarmCompute';
import { dayTimeline, nextBestAction, nowLineAt, rowTime, type MissionLike, type NbaAction, type PromiseLike, type TimelineKind } from '../../lib/contextView';
import { FocusTimer } from './CommandWidgets';
import { AllClear, FocusCardList, type FocusState } from './FocusDashboard';
import './focus.css';
import './context.css';

const KIND_EMOJI: Record<TimelineKind, EmojiName> = {
  promise: 'check-mark-button',
  agenda: 'spiral-calendar',
  wind: 'crescent-moon',
  bed: 'crescent-moon',
  break: 'hot-beverage',
  mission: 'robot',
};
const KIND_WORD: Record<TimelineKind, string> = {
  promise: 'Promise',
  agenda: 'Calendar',
  wind: 'Routine',
  bed: 'Routine',
  break: 'Break',
  mission: 'Mission',
};

interface Props {
  focus: FocusState;
  telemetry?: TelemetrySnapshot | null;
  threads?: CommsThread[];
  onOpenWindow: (id: string) => void;
  onOpenThread: (thread: string, draft?: string) => void;
}

/** Ticks once a minute so times and "Now" stay right while the console is open. */
function useMinute(): number {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now() / 1000), 60_000);
    return () => window.clearInterval(id);
  }, []);
  return now;
}

export default function ContextPanel({ focus, telemetry, threads, onOpenWindow, onOpenThread }: Props) {
  const now = useMinute();
  const reduce = !!useReducedMotion() || useCalmMotion();
  const [promiseData] = useDaemonPoll<{ promises: PromiseLike[] }>('/promises', 60_000);
  const [missionData] = useDaemonPoll<{ missions: MissionLike[] }>('/missions', 15_000);
  const [whyOpen, setWhyOpen] = useState(false);
  const [allOpen, setAllOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState('');

  const { view, failed } = focus;
  const cards = topCards(view?.cards ?? []);
  const unread = (threads ?? []).filter((t) => !t.muted).reduce((n, t) => n + (t.unread || 0), 0);
  const promises = promiseData?.promises ?? [];
  const nba = useMemo(
    () => nextBestAction(cards, { unread, promises, now, summary: view?.summary }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [view, unread, promiseData, now],
  );
  const circ = telemetry?.circadian;
  const timeline = useMemo(
    () =>
      dayTimeline(
        {
          promises,
          life: { wind_down_starts: circ?.wind_down_starts, bedtime: circ?.bedtime, break_due: telemetry?.pacing?.phase === 'break_due' },
          missions: missionData?.missions,
          agenda: view?.agenda,
        },
        now,
      ),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [promiseData, missionData, view, circ?.wind_down_starts, circ?.bedtime, telemetry?.pacing?.phase, now],
  );
  const nowAt = nowLineAt(timeline, now);

  const run = async (a: NbaAction) => {
    setNote('');
    void focusApi.feedback(a.cardId ?? a.id, 'open').catch(() => undefined); // teaches the ranking what you acted on
    if (a.kind === 'window') return onOpenWindow(a.window || 'chat');
    setBusy(a.id);
    try {
      const d = await focusApi.draft(a.cardId ?? a.id);
      onOpenThread(d.thread || a.thread || '', d.draft); // the draft sits in the reply box; nothing is sent
    } catch {
      if (a.thread) onOpenThread(a.thread);
      else setNote("Couldn't write a draft just now.");
    } finally {
      setBusy(null);
    }
  };

  const offline = failed && !view;

  return (
    <section className="ctx" aria-label="Focus and context" data-focus-dashboard>
      <header className="ctx__head">
        <h2 className="ctx__title">Focus and context</h2>
      </header>

      <FocusTimer />

      {/* Next best action */}
      {offline ? (
        <AllClear
          title="Ask Orion anything"
          text="Orion's brain isn't answering, so there is nothing to sum up yet. Ask a question, run a task on your desktop, or type a feature's name to find it."
        />
      ) : (
        <article className="ctx-nba" aria-label="Next best action" data-clear={nba.clear || undefined}>
          <div className="ctx-nba__top">
            <span className="ctx-nba__chip" aria-hidden>
              <Sparkles size={16} />
            </span>
            <h3 className="ctx-nba__title">Next best action</h3>
            {cards.length > 0 && (
              <button type="button" className="cc-mini ctx-nba__all" aria-expanded={allOpen} onClick={() => setAllOpen((v) => !v)}>
                <PanelTop size={14} /> {allOpen ? 'Hide cards' : `Show all (${cards.length})`}
              </button>
            )}
          </div>
          {!view ? (
            <p className="ctx-nba__loading">
              <Loader2 size={16} className="animate-spin" /> Looking at what needs you
            </p>
          ) : (
            <>
              <p className="ctx-nba__read">{nba.sentences.join(' ')}</p>
              <p className="ctx-nba__suggest">{nba.suggestion}</p>
              <div className="ctx-nba__foot">
                {nba.actions.map((a, i) => (
                  <button
                    key={a.id}
                    type="button"
                    className={i === 0 ? 'ctx-btn ctx-btn--main' : 'ctx-btn'}
                    onClick={() => void run(a)}
                    disabled={busy === a.id}
                    title={a.kind === 'draft' ? 'Opens the chat with a reply drafted in your style. Nothing is sent until you send it.' : undefined}
                  >
                    {a.kind === 'draft' && <MessageSquareReply size={15} />}
                    {busy === a.id ? 'Drafting...' : a.label}
                  </button>
                ))}
                {nba.why.length > 0 && (
                  <button type="button" className="ctx-why-btn" aria-expanded={whyOpen} onClick={() => setWhyOpen((v) => !v)}>
                    Why Orion suggests this
                    <ChevronDown size={14} className={whyOpen ? 'rotate-180' : ''} />
                  </button>
                )}
              </div>
              <AnimatePresence initial={false}>
                {whyOpen && (
                  <motion.ul
                    className="ctx-why"
                    initial={reduce ? { opacity: 0 } : { opacity: 0, height: 0 }}
                    animate={reduce ? { opacity: 1 } : { opacity: 1, height: 'auto' }}
                    exit={reduce ? { opacity: 0 } : { opacity: 0, height: 0 }}
                  >
                    {nba.why.map((w) => (
                      <li key={w}>{w}</li>
                    ))}
                  </motion.ul>
                )}
              </AnimatePresence>
              {note && (
                <p className="focus__note" role="status">
                  {note}
                </p>
              )}
            </>
          )}
        </article>
      )}

      {allOpen && cards.length > 0 && (
        <div className="ctx-cards">
          <FocusCardList focus={focus} onOpenWindow={onOpenWindow} onOpenThread={onOpenThread} />
        </div>
      )}

      {/* Today's timeline */}
      <section className="ctx-day" aria-label="Today's timeline">
        <header className="ctx-day__head">
          <h3 className="ctx-day__title">Today's timeline</h3>
          <button type="button" className="cc-mini" onClick={() => onOpenWindow('life')}>
            Open Life
          </button>
        </header>
        <ol className="ctx-day__list">
          {timeline.map((it, i) => {
            const t = rowTime(it.at, now);
            return (
              <li key={it.id} className="ctx-row-wrap">
                {i === nowAt && <NowLine now={now} />}
                <div className="ctx-row" data-kind={it.kind} data-past={it.past || undefined} data-overdue={it.overdue || undefined}>
                  <span className="ctx-row__time">
                    {it.kind === 'break' ? 'Now' : t.time}
                    {t.day && <span className="ctx-row__day">{t.day}</span>}
                  </span>
                  <span className="ctx-row__mark" aria-hidden>
                    <img src={emojiUrl(KIND_EMOJI[it.kind])} alt="" draggable={false} />
                  </span>
                  <span className="ctx-row__text">
                    <span className="ctx-row__title">{it.title}</span>
                    {it.sub && <span className="ctx-row__sub">{it.sub}</span>}
                  </span>
                  <span className="ctx-row__kind">{KIND_WORD[it.kind]}</span>
                </div>
              </li>
            );
          })}
          {nowAt === timeline.length && timeline.length > 0 && (
            <li className="ctx-row-wrap">
              <NowLine now={now} />
            </li>
          )}
          <li className="ctx-day__end">{timeline.length ? 'Nothing else today' : promiseData || view ? 'Nothing planned today' : 'Your day shows here when Orion is running'}</li>
        </ol>
      </section>
    </section>
  );
}

function NowLine({ now }: { now: number }) {
  return (
    <div className="ctx-now" aria-label="Now">
      <span className="ctx-now__time">{rowTime(now, now).time}</span>
      <span className="ctx-now__line" />
      <span className="ctx-now__word">Now</span>
    </div>
  );
}
