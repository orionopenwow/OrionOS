/** Right-hand widget column: Now Playing, Messages (all platforms in one list) and Alerts. Night-glass style. */
import { useEffect, useMemo, useRef, useState } from 'react';
import { useCoreLightAll } from '../glass/useCoreLight';
import { AnimatePresence, motion } from 'framer-motion';
import { Bell, MessageCircle, Music2, Pause, Play, SkipBack, SkipForward, X } from 'lucide-react';
import type { ApprovalItem, CommsThread, PacingState } from '@orion/shared-types';
import type { BibleSend, BibleState } from '../../hooks/bibleState';

const PLATFORM: Record<string, { label: string; color: string }> = {
  telegram: { label: 'Telegram', color: '#2AABEE' },
  discord: { label: 'Discord', color: '#7C8BFF' },
  messenger: { label: 'Messenger', color: '#C084FC' },
};

export interface AlertItem {
  id: string;
  title: string;
  detail?: string;
  tone: 'info' | 'warn' | 'good';
  open?: { tab: 'queue' | 'tools' | 'comms' | 'life'; threadKey?: string };
}

interface Props {
  bible: BibleState;
  send: BibleSend;
  threads: CommsThread[];
  approvals: ApprovalItem[];
  pacing: PacingState | null;
  connected: boolean;
  onOpen: (target: { tab: 'queue' | 'tools' | 'comms' | 'life'; threadKey?: string; draft?: string }) => void;
}

function ago(ts: number) {
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return 'now';
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  return `${Math.floor(s / 86400)}d`;
}

/** Orion Glass cards (styles/glass.css), lit from the side facing the core. */
const card = 'orion-glass orion-lit text-white';

export default function WidgetDock({ bible, send, threads, approvals, pacing, connected, onOpen }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  useCoreLightAll(ref);
  return (
    <div ref={ref} className="flex flex-col gap-2.5 w-[290px]" onClick={(e) => e.stopPropagation()}>
      {bible.media && <MusicWidget media={bible.media} send={send} />}
      <MessagesWidget threads={threads} bible={bible} send={send} onOpen={onOpen} />
      <AlertsWidget bible={bible} approvals={approvals} pacing={pacing} connected={connected} onOpen={onOpen} />
    </div>
  );
}

// ------------------------------------------------------------------ music
function MusicWidget({ media, send }: { media: NonNullable<BibleState['media']>; send: BibleSend }) {
  // progress ticks locally between daemon updates
  const [pos, setPos] = useState(media.position ?? 0);
  useEffect(() => setPos(media.position ?? 0), [media.position, media.title]);
  useEffect(() => {
    if (!media.playing) return;
    const t = setInterval(() => setPos((p) => p + 1), 1000);
    return () => clearInterval(t);
  }, [media.playing, media.title]);
  const pct = media.duration ? Math.min(1, pos / media.duration) : 0;

  return (
    <div className={`${card} p-3 flex items-center gap-3`}>
      <div className="w-12 h-12 rounded-xl overflow-hidden bg-white/[0.06] shrink-0 flex items-center justify-center">
        {media.art ? <img src={media.art} alt="" className="w-full h-full object-cover" /> : <Music2 size={18} className="text-white/50" />}
      </div>
      <div className="min-w-0 flex-1">
        <div className="text-[13px] font-bold truncate">{media.title}</div>
        <div className="text-[12px] text-white/55 truncate">{media.artist || media.app}</div>
        <div className="mt-1.5 h-[3px] rounded-full bg-white/10 overflow-hidden">
          <div className="h-full bg-white/70 rounded-full transition-[width] duration-1000 ease-linear" style={{ width: `${pct * 100}%` }} />
        </div>
      </div>
      <div className="flex items-center gap-0.5 shrink-0">
        <button className="p-1.5 rounded-full text-white/60 hover:text-white hover:bg-white/10" onClick={() => send('MEDIA_CONTROL', { action: 'previous' })} aria-label="Previous track">
          <SkipBack size={14} />
        </button>
        <button className="p-2 rounded-full bg-white text-black hover:scale-105 transition-transform" onClick={() => send('MEDIA_CONTROL', { action: 'toggle' })} aria-label={media.playing ? 'Pause' : 'Play'}>
          {media.playing ? <Pause size={14} fill="currentColor" /> : <Play size={14} fill="currentColor" />}
        </button>
        <button className="p-1.5 rounded-full text-white/60 hover:text-white hover:bg-white/10" onClick={() => send('MEDIA_CONTROL', { action: 'next' })} aria-label="Next track">
          <SkipForward size={14} />
        </button>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ messages
function MessagesWidget({
  threads,
  bible,
  send,
  onOpen,
}: {
  threads: CommsThread[];
  bible: BibleState;
  send: BibleSend;
  onOpen: Props['onOpen'];
}) {
  const unread = threads.filter((t) => t.unread > 0 && !t.muted).sort((a, b) => (Number(b.vip) - Number(a.vip)) || b.last_ts - a.last_ts);
  const shown = (unread.length ? unread : threads.slice().sort((a, b) => b.last_ts - a.last_ts)).slice(0, 4);
  const total = unread.reduce((n, t) => n + t.unread, 0);
  const top = unread[0];

  // fetch style-matched quick replies for the newest unread conversation
  const lastIn = top ? [...top.messages].reverse().find((m) => m.direction === 'in') : undefined;
  useEffect(() => {
    if (top && lastIn && bible.suggestions[top.key]?.for_id !== lastIn.id) send('SUGGEST_REPLIES', { thread_key: top.key });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [top?.key, lastIn?.id]);
  const chips = top && lastIn && bible.suggestions[top.key]?.for_id === lastIn.id ? bible.suggestions[top.key].replies : [];

  return (
    <div className={`${card} p-3`}>
      <div className="flex items-center justify-between mb-2">
        <span className="flex items-center gap-2 text-[13px] font-bold">
          <MessageCircle size={14} className="text-white/70" /> Messages
        </span>
        <span className="text-[11px] text-white/45">{total ? `${total} unread` : 'All caught up'}</span>
      </div>
      {shown.length === 0 ? (
        <p className="text-[12px] text-white/45 leading-relaxed">
          Telegram, Discord and Messenger chats show up here together once they're connected.
        </p>
      ) : (
        <div className="flex flex-col gap-1">
          {shown.map((t) => {
            const last = t.messages[t.messages.length - 1];
            const p = PLATFORM[t.platform] ?? { label: t.platform, color: '#fff' };
            return (
              <button
                key={t.key}
                onClick={() => onOpen({ tab: 'comms', threadKey: t.key })}
                className="text-left rounded-xl px-2 py-1.5 hover:bg-white/[0.06] transition-colors"
              >
                <div className="flex items-center gap-2">
                  <span className="w-1.5 h-1.5 rounded-full shrink-0" style={{ background: p.color }} title={p.label} />
                  <span className={`text-[12.5px] truncate ${t.unread ? 'font-bold text-white' : 'text-white/70'}`}>{t.thread_name}</span>
                  {t.vip && <span className="text-[10px] text-amber-300/90">★</span>}
                  <span className="ml-auto text-[10.5px] text-white/40 shrink-0">{ago(t.last_ts)}</span>
                  {t.unread > 0 && <span className="text-[10px] font-bold bg-white text-black rounded-full px-1.5">{t.unread}</span>}
                </div>
                {last && (
                  <div className="pl-3.5 text-[11.5px] text-white/50 truncate">
                    {last.direction === 'out' ? 'You: ' : ''}
                    {last.text}
                  </div>
                )}
              </button>
            );
          })}
        </div>
      )}
      {top && chips.length > 0 && (
        <div className="mt-2 pt-2 border-t border-white/[0.07]">
          <div className="text-[10.5px] text-white/40 mb-1.5">Reply to {top.thread_name} in your style</div>
          <div className="flex flex-wrap gap-1.5">
            {chips.map((c) => (
              <button
                key={c}
                onClick={() => onOpen({ tab: 'comms', threadKey: top.key, draft: c })}
                className="rounded-full border border-white/12 bg-white/[0.06] hover:bg-white/[0.12] px-2.5 py-1 text-[11.5px] text-white/85 max-w-full truncate"
                title="Opens the chat with this reply ready — you press send"
              >
                {c}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ alerts
function AlertsWidget({
  bible,
  approvals,
  pacing,
  connected,
  onOpen,
}: {
  bible: BibleState;
  approvals: ApprovalItem[];
  pacing: PacingState | null;
  connected: boolean;
  onOpen: Props['onOpen'];
}) {
  const [dismissed, setDismissed] = useState<Set<string>>(new Set());

  const alerts = useMemo<AlertItem[]>(() => {
    const out: AlertItem[] = [];
    if (!connected) out.push({ id: 'offline', title: "Orion's brain is offline", detail: 'Reconnecting…', tone: 'warn' });
    const pending = approvals.filter((a) => a.status === 'pending');
    if (pending.length) {
      out.push({
        id: `approvals-${pending.length}-${pending[0].id}`,
        title: `${pending.length} waiting for your OK`,
        detail: pending.slice(0, 2).map((a) => a.title || a.author || a.category).filter(Boolean).join(', '),
        tone: 'info',
        open: { tab: 'queue' },
      });
    }
    const failed = bible.runs.filter((r) => !r.running && r.exit_code && r.ended_at && Date.now() / 1000 - r.ended_at < 1800);
    failed.slice(0, 2).forEach((r) =>
      out.push({ id: `run-${r.id}`, title: `${r.label || 'A command'} failed`, detail: r.diagnosis?.diagnosis, tone: 'warn', open: { tab: 'tools' } }),
    );
    if (pacing?.phase === 'break_due') out.push({ id: `break-${pacing.checkins}`, title: 'Time for a break', detail: `${Math.floor(pacing.session_minutes)} minutes of focus`, tone: 'info' });
    if (bible.agent?.state === 'done')
      out.push({ id: `agent-${bible.agent.since}`, title: 'Claude finished', detail: bible.agent.project, tone: 'good' });
    if (bible.digest && bible.digest.count) out.push({ id: `digest-${bible.digest.at}`, title: 'Muted chats summary is ready', detail: `${bible.digest.count} messages`, tone: 'info', open: { tab: 'comms' } });
    if (bible.harvest?.error) out.push({ id: 'harvest', title: 'Project scan failed', detail: bible.harvest.error, tone: 'warn', open: { tab: 'life' } });
    if (bible.circadian?.phase === 'wind_down') out.push({ id: `wind-${new Date().toDateString()}`, title: 'Wind-down time', detail: `Bedtime ${bible.circadian.bedtime}`, tone: 'info' });
    return out.filter((a) => !dismissed.has(a.id));
  }, [approvals, bible.runs, bible.agent, bible.digest, bible.harvest, bible.circadian, pacing, connected, dismissed]);

  // Sun & Moon: after sunset, non-critical work alerts wait until morning (offline/break/wind-down still show).
  const evening = bible.circadian && bible.circadian.phase !== 'normal';
  const CRITICAL = /^(offline|break-|wind-)/;
  const shown = evening ? alerts.filter((a) => CRITICAL.test(a.id)) : alerts;
  const held = alerts.length - shown.length;

  if (!shown.length && !held) return null;
  const dot = { info: '#ffffff', warn: '#FDBA74', good: '#86EFAC' } as const;

  return (
    <div className={`${card} p-3`}>
      <div className="flex items-center gap-2 text-[13px] font-bold mb-1.5">
        <Bell size={14} className="text-white/70" /> Alerts
      </div>
      <div className="flex flex-col">
        <AnimatePresence initial={false}>
          {shown.map((a) => (
            <motion.div
              key={a.id}
              layout
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: 'auto' }}
              exit={{ opacity: 0, height: 0 }}
              className="group flex items-start gap-2 rounded-xl px-2 py-1.5 hover:bg-white/[0.05]"
            >
              <span className="mt-1.5 w-1.5 h-1.5 rounded-full shrink-0" style={{ background: dot[a.tone] }} />
              <button className="min-w-0 flex-1 text-left" onClick={() => a.open && onOpen(a.open)} disabled={!a.open}>
                <div className="text-[12.5px] text-white/90">{a.title}</div>
                {a.detail && <div className="text-[11.5px] text-white/45 truncate">{a.detail}</div>}
              </button>
              <button
                className="opacity-0 group-hover:opacity-100 focus:opacity-100 p-0.5 text-white/40 hover:text-white"
                onClick={() => setDismissed((d) => new Set(d).add(a.id))}
                aria-label="Dismiss"
              >
                <X size={12} />
              </button>
            </motion.div>
          ))}
        </AnimatePresence>
        {held > 0 && (
          <div className="px-2 pt-1 text-[11px] text-white/40">
            {held} work alert{held === 1 ? '' : 's'} held until morning
          </div>
        )}
      </div>
    </div>
  );
}
