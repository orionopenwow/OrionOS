/**
 * Agent swarm: every coding agent Orion can see becomes a small robot orbiting the black hole core,
 * with a plain-words status bubble. Click a robot for its recent steps, the files it touched and the
 * Open / Pause / Stop buttons (Pause and Stop only for agents Orion started itself).
 * Built from the same star-bot as the coding pet (widgets/AgentPet.tsx).
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import { useCalmMotion } from '../../lib/hudPrefs';
import '../../styles/glass.css';
import { ago, swarmAction, usd, useDaemonPoll, type SwarmAgent, type SwarmSnapshot, type SwarmState } from '../../lib/swarmCompute';
import { missionsApi } from '../../lib/missions';

const TOOL_COLORS: Record<string, string> = {
  claude: '#C792FF',
  codex: '#7DF0FF',
  gemini: '#6FA8FF',
  antigravity: '#9B8CFF',
  cursor: '#E4E8FF',
  aider: '#3DFFA8',
};
/** Ember (#FFC66B): Orion's only warm colour, for things that need you, like a stuck robot. */
const AMBER = '#FFC66B';
const DONE = '#86EFAC';
const CLASH = '#FF4D6D';

/** Toolkit: Collision Guard adds the robot pairs that are about to change the same files. */
type Collision = { id: string; a: string; b: string; files: string[]; message: string };
type ToolkitSwarmSnapshot = SwarmSnapshot & { collisions?: Collision[] };

export function toolColor(tool: string): string {
  const key = tool.toLowerCase().split(/\s+/)[0];
  return TOOL_COLORS[key] ?? '#9FB0FF';
}

const STATE_LABEL: Record<SwarmState, string> = {
  working: 'Working',
  waiting: 'Waiting',
  stuck: 'Stuck',
  failed: 'Needs help',
  done: 'Done',
};

const alarm = (s: SwarmState) => s === 'stuck' || s === 'failed';

function Robot({ agent, reduce, size = 40 }: { agent: SwarmAgent; reduce: boolean; size?: number }) {
  const tint = toolColor(agent.tool);
  const s = agent.state;
  const light = alarm(s) ? AMBER : s === 'done' ? DONE : s === 'waiting' ? '#FFE08A' : tint;
  const gid = `swarm-body-${agent.id.replace(/[^a-z0-9]/gi, '')}`;
  return (
    <svg viewBox="0 0 80 80" width={size} height={size} aria-hidden="true">
      <defs>
        <radialGradient id={gid} cx="35%" cy="30%" r="75%">
          <stop offset="0%" stopColor="#ffffff" stopOpacity="0.95" />
          <stop offset="45%" stopColor={tint} stopOpacity="0.85" />
          <stop offset="100%" stopColor="#3B2A8C" stopOpacity="0.95" />
        </radialGradient>
      </defs>
      <line x1="40" y1="20" x2="40" y2="11" stroke={tint} strokeWidth="2.5" strokeLinecap="round" />
      <motion.circle
        cx="40"
        cy="9"
        r="4"
        fill={light}
        animate={reduce ? undefined : { opacity: s === 'working' ? [0.55, 1, 0.55] : alarm(s) ? [1, 0.2, 1] : [0.7, 1, 0.7] }}
        transition={{ duration: alarm(s) ? 0.9 : s === 'working' ? 0.8 : 2.4, repeat: Infinity }}
      />
      <circle cx="40" cy="40" r="21" fill={`url(#${gid})`} />
      <ellipse cx="40" cy="42" rx="13.5" ry="9.5" fill="#0b0c1c" opacity="0.92" />
      {s === 'working' && (
        <motion.g animate={reduce ? undefined : { x: [-1.5, 1.5, -1.5] }} transition={{ duration: 1.6, repeat: Infinity }}>
          <circle cx="35" cy="42" r="2.7" fill="#7df0ff" />
          <circle cx="45" cy="42" r="2.7" fill="#7df0ff" />
        </motion.g>
      )}
      {s === 'waiting' && (
        <g fill="#FFE08A">
          <circle cx="33" cy="42" r="1.8" />
          <circle cx="40" cy="42" r="1.8" />
          <circle cx="47" cy="42" r="1.8" />
        </g>
      )}
      {alarm(s) && (
        <g stroke={AMBER} strokeWidth="2.4" strokeLinecap="round">
          <path d="M32 39 l5 5 M37 39 l-5 5" />
          <path d="M43 39 l5 5 M48 39 l-5 5" />
        </g>
      )}
      {s === 'done' && (
        <g stroke={DONE} strokeWidth="2.4" strokeLinecap="round" fill="none">
          <path d="M32 43 l3 -3 l3 3" />
          <path d="M42 43 l3 -3 l3 3" />
        </g>
      )}
      <rect x="18" y="38" width="4" height="9" rx="2" fill={tint} opacity="0.8" />
      <rect x="58" y="38" width="4" height="9" rx="2" fill={tint} opacity="0.8" />
    </svg>
  );
}

function SwarmPanel({ agent, onClose }: { agent: SwarmAgent; onClose: () => void }) {
  const [, tick] = useState(0);
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const t = window.setInterval(() => tick((n) => n + 1), 1000);
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => {
      window.clearInterval(t);
      window.removeEventListener('keydown', onKey);
    };
  }, [onClose]);

  const tint = toolColor(agent.tool);
  const act = async (action: 'open' | 'pause' | 'resume' | 'stop') => {
    setBusy(true);
    // Mission robots are paused and stopped through Mission Control (missions.py); Open stays a swarm action.
    const r = agent.mission_id && action !== 'open' ? await missionsApi.act(agent.mission_id, action) : await swarmAction(agent.id, action);
    setNote(r.message || (r.ok ? 'Done.' : 'That did not work.'));
    setBusy(false);
  };
  const lockedTip = agent.control_note || 'Orion did not start this agent, so it will not pause or stop it.';
  const btn =
    'px-3 py-1.5 rounded-lg text-[12px] font-semibold border transition-colors disabled:opacity-35 disabled:cursor-not-allowed';
  const events = [...agent.events].reverse();

  return (
    <div className="fixed inset-x-0 bottom-24 z-[60] flex justify-center px-4 pointer-events-none">
    <motion.div
      initial={{ opacity: 0, y: 12, scale: 0.97 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, y: 12, scale: 0.97 }}
      className="orion-glass orion-glass--dense pointer-events-auto w-full max-w-[380px] p-4 text-white"
      style={{ borderColor: `${tint}55`, boxShadow: `0 18px 50px rgba(0,0,0,0.6), 0 0 26px ${tint}22` }}
      role="dialog"
      aria-label={`${agent.name} details`}
      onClick={(e) => e.stopPropagation()}
    >
      <div className="flex items-start gap-3">
        <Robot agent={agent} reduce size={44} />
        <div className="min-w-0 flex-1">
          <div className="text-[14px] font-bold truncate">{agent.name}</div>
          <div className="text-[12px] text-white/70 truncate">{agent.status}</div>
          {agent.reason && (
            <div className="text-[11.5px] mt-0.5" style={{ color: alarm(agent.state) ? AMBER : '#FFE08A' }}>
              {agent.reason}
            </div>
          )}
        </div>
        <span
          className="shrink-0 rounded-full px-2 py-0.5 text-[10.5px] font-bold uppercase tracking-wide"
          style={{
            color: alarm(agent.state) ? AMBER : agent.state === 'done' ? DONE : tint,
            background: 'rgba(255,255,255,0.06)',
          }}
        >
          {STATE_LABEL[agent.state]}
        </span>
        <button type="button" onClick={onClose} className="shrink-0 text-white/50 hover:text-white text-[16px] leading-none px-1" aria-label="Close">
          ×
        </button>
      </div>

      <div className="mt-3 flex gap-4 text-[11.5px] text-white/55">
        <span>Active {agent.started ? ago(agent.started) : 'just now'}</span>
        <span>Last move {ago(agent.last_activity)} ago</span>
      </div>
      <div className="mt-1 text-[11.5px] text-white/55" title={agent.cost_estimated ? 'Estimated from token counts or the amount of text' : undefined}>
        {agent.cost_usd == null ? (
          <span className="text-white/40">Cost: not tracked for agents Orion didn't start</span>
        ) : (
          <span>
            Cost so far <span className="text-white/85">{usd(agent.cost_usd)}</span>
            {agent.cost_estimated ? ' (estimate)' : ''}
            {agent.budget_usd ? ` of ${usd(agent.budget_usd)}` : ''}
          </span>
        )}
      </div>

      <div className="mt-3">
        <div className="text-[10.5px] font-bold uppercase tracking-wider text-white/40 mb-1">Recent steps</div>
        {events.length ? (
          <ul className="max-h-[132px] overflow-y-auto space-y-1 pr-1">
            {events.map((e, i) => (
              <li key={i} className="flex gap-2 text-[12px]">
                <span className="w-12 shrink-0 text-right text-white/35">{e.at ? ago(e.at) : ''}</span>
                <span className="text-white/85 truncate">{e.text}</span>
              </li>
            ))}
          </ul>
        ) : (
          <div className="text-[12px] text-white/45">No step details for this agent.</div>
        )}
      </div>

      {agent.files.length > 0 && (
        <div className="mt-3">
          <div className="text-[10.5px] font-bold uppercase tracking-wider text-white/40 mb-1">Files it changed</div>
          <div className="flex flex-wrap gap-1">
            {agent.files.map((f) => (
              <span key={f} className="rounded-md bg-white/[0.06] border border-white/10 px-1.5 py-0.5 text-[11px] text-white/80">
                {f}
              </span>
            ))}
          </div>
        </div>
      )}

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <button
          type="button"
          className={`${btn} border-cyan-300/40 text-cyan-100 hover:bg-cyan-300/10`}
          disabled={busy || !agent.can_open}
          title={agent.can_open ? 'Open the project folder' : 'No project folder is known for this agent'}
          onClick={() => act('open')}
        >
          Open
        </button>
        <button
          type="button"
          className={`${btn} border-violet-300/40 text-violet-100 hover:bg-violet-300/10`}
          disabled={busy || !agent.controllable}
          title={agent.controllable ? (agent.paused ? 'Let it run again' : 'Pause it for now') : lockedTip}
          onClick={() => act(agent.paused ? 'resume' : 'pause')}
        >
          {agent.paused ? 'Resume' : 'Pause'}
        </button>
        <button
          type="button"
          className={`${btn} border-rose-300/40 text-rose-100 hover:bg-rose-300/10`}
          disabled={busy || !agent.controllable}
          title={agent.controllable ? 'Stop it' : lockedTip}
          onClick={() => act('stop')}
        >
          Stop
        </button>
        {note && <span className="text-[11.5px] text-white/60">{note}</span>}
      </div>
      {!agent.controllable && <div className="mt-2 text-[11px] text-white/40">{lockedTip}</div>}
    </motion.div>
    </div>
  );
}

export default function AgentSwarm({ size }: { size: number }) {
  const [snap] = useDaemonPoll<ToolkitSwarmSnapshot>('/agents/swarm', 2500);
  const [selected, setSelected] = useState<string | null>(null);
  const calm = useCalmMotion();
  const reduce = !!useReducedMotion() || calm;
  const agents = snap?.agents ?? [];
  // A robot that just finished leaves a short sparkle.
  const prevStates = useRef<Record<string, SwarmState>>({});
  const [sparkles, setSparkles] = useState<Record<string, number>>({});
  useEffect(() => {
    const prev = prevStates.current;
    const fresh: string[] = [];
    for (const a of agents) {
      if (a.state === 'done' && prev[a.id] && prev[a.id] !== 'done') fresh.push(a.id);
      prev[a.id] = a.state;
    }
    if (!fresh.length) return;
    const at = Date.now();
    setSparkles((s) => ({ ...s, ...Object.fromEntries(fresh.map((id) => [id, at])) }));
    window.setTimeout(() => setSparkles((s) => Object.fromEntries(Object.entries(s).filter(([, v]) => v !== at))), 1400);
  }, [agents]);
  const current = agents.find((a) => a.id === selected) ?? null;
  const close = useCallback(() => setSelected(null), []);

  useEffect(() => {
    if (selected && snap && !current) setSelected(null);
  }, [selected, snap, current]);

  if (!agents.length) return null;
  // Keep the orbit (and the status labels) on screen on narrow phones.
  const r = Math.min(size * 0.8, typeof window !== 'undefined' ? window.innerWidth / 2 - 72 : size * 0.8);
  const spin = reduce ? undefined : { rotate: 360 };
  const counter = reduce ? undefined : { rotate: -360 };
  const spinT = { duration: 180, repeat: Infinity, ease: 'linear' as const };
  const spot = (i: number) => {
    const angle = (i / agents.length) * Math.PI * 2 + Math.PI / 5;
    return { x: Math.cos(angle) * r, y: Math.sin(angle) * r };
  };
  const clashes = (snap?.collisions ?? []).flatMap((c) => {
    const ia = agents.findIndex((a) => a.id === c.a);
    const ib = agents.findIndex((a) => a.id === c.b);
    return ia >= 0 && ib >= 0 ? [{ ...c, p: spot(ia), q: spot(ib) }] : [];
  });

  return (
    <>
      <div className="pointer-events-none absolute inset-0 flex items-center justify-center" aria-label="Coding agents">
        <motion.div className="relative" style={{ width: 0, height: 0 }} animate={spin} transition={spinT}>
          {/* Faint trails behind each robot along its orbit (they turn with the orbit, so they stay behind). */}
          {!reduce && (
            <svg className="absolute overflow-visible" style={{ left: 0, top: 0, overflow: "visible" }} width={1} height={1} aria-hidden="true">
              {agents.map((a, i) => {
                const angle = (i / agents.length) * Math.PI * 2 + Math.PI / 5;
                const tint = alarm(a.state) ? AMBER : toolColor(a.tool);
                const segs = 7;
                const span = 0.36;
                return (
                  <g key={a.id}>
                    {Array.from({ length: segs }, (_, k) => {
                      const a0 = angle - 0.07 - span * ((k + 1) / segs);
                      const a1 = angle - 0.07 - span * (k / segs);
                      return (
                        <path
                          key={k}
                          d={`M ${Math.cos(a0) * r} ${Math.sin(a0) * r} A ${r} ${r} 0 0 1 ${Math.cos(a1) * r} ${Math.sin(a1) * r}`}
                          fill="none"
                          stroke={tint}
                          strokeWidth={2.2 - k * 0.22}
                          strokeLinecap="round"
                          opacity={(0.34 * (segs - k)) / segs}
                        />
                      );
                    })}
                  </g>
                );
              })}
            </svg>
          )}
          {clashes.length > 0 && (
            <svg className="absolute overflow-visible" style={{ left: 0, top: 0 }} width="1" height="1" aria-hidden="true">
              {clashes.map((c) => (
                <motion.line
                  key={c.id}
                  x1={c.p.x}
                  y1={c.p.y}
                  x2={c.q.x}
                  y2={c.q.y}
                  stroke={CLASH}
                  strokeWidth={2}
                  strokeLinecap="round"
                  strokeDasharray="6 5"
                  style={{ filter: `drop-shadow(0 0 4px ${CLASH})` }}
                  animate={reduce ? undefined : { strokeDashoffset: [0, -22], opacity: [0.95, 0.55, 0.95] }}
                  transition={{ duration: 1.2, repeat: Infinity, ease: 'linear' }}
                >
                  <title>{c.message}</title>
                </motion.line>
              ))}
            </svg>
          )}
          {agents.map((a, i) => {
            const angle = (i / agents.length) * Math.PI * 2 + Math.PI / 5;
            const tint = toolColor(a.tool);
            const warn = alarm(a.state);
            return (
              <div
                key={a.id}
                className="absolute -translate-x-1/2 -translate-y-1/2"
                style={{ left: Math.cos(angle) * r, top: Math.sin(angle) * r }}
              >
                <motion.div animate={counter} transition={spinT} className="flex flex-col items-center">
                  <motion.button
                    type="button"
                    className="pointer-events-auto relative rounded-full bg-transparent border-0 p-0 cursor-pointer"
                    title={`${a.name}: ${a.status}${a.reason ? ` (${a.reason})` : ''}`}
                    aria-label={`${a.name}: ${a.status}. Show details`}
                    onClick={(e) => {
                      e.stopPropagation();
                      setSelected((s) => (s === a.id ? null : a.id));
                    }}
                    animate={
                      reduce
                        ? undefined
                        : a.state === 'working'
                        ? { y: [0, -3, 0] }
                        : a.state === 'done'
                        ? { y: [0, -10, 0] }
                        : { y: [0, 1.5, 0] }
                    }
                    transition={
                      a.state === 'done'
                        ? { duration: 0.9, repeat: 2, ease: 'easeOut' }
                        : { duration: a.state === 'working' ? 0.7 : 3, repeat: Infinity, ease: 'easeInOut' }
                    }
                  >
                    <motion.span
                      className="absolute inset-[-5px] rounded-full"
                      style={{
                        border: `1.5px solid ${warn ? AMBER : tint}`,
                        boxShadow: `0 0 16px ${warn ? AMBER : tint}`,
                        opacity: warn ? 0.9 : selected === a.id ? 0.8 : 0.3,
                      }}
                      animate={warn && !reduce ? { opacity: [0.95, 0.15, 0.95] } : undefined}
                      transition={{ duration: 1.1, repeat: Infinity }}
                    />
                    {warn && (
                      <span
                        className="absolute inset-[-16px] rounded-full pointer-events-none"
                        style={{ background: `radial-gradient(circle, ${AMBER}55 0%, ${AMBER}1f 45%, transparent 70%)` }}
                        aria-hidden="true"
                      />
                    )}
                    <Robot agent={a} reduce={reduce} />
                    {sparkles[a.id] && (
                      <span className="orion-sparkle" key={sparkles[a.id]} aria-hidden="true">
                        {Array.from({ length: 8 }, (_, k) => (
                          <i
                            key={k}
                            style={{
                              ['--a' as string]: `${k * 45 + (k % 2) * 12}deg`,
                              ['--r' as string]: `${k % 2 ? 22 : 34}px`,
                              ['--d' as string]: `${(k % 3) * 60}ms`,
                            }}
                          />
                        ))}
                      </span>
                    )}
                  </motion.button>
                  <div
                    className="mt-1 max-w-[150px] rounded-lg border bg-black/60 backdrop-blur-md px-2 py-0.5 text-center"
                    style={{ borderColor: warn ? `${AMBER}88` : 'rgba(255,255,255,0.1)' }}
                  >
                    <div className="text-[10.5px] font-semibold leading-tight text-white truncate">
                      {a.state === 'done' ? 'Done' : a.status}
                    </div>
                    <div className="text-[9.5px] leading-tight truncate" style={{ color: warn ? AMBER : `${tint}cc` }}>
                      {warn && a.reason ? a.reason : a.name}
                    </div>
                  </div>
                </motion.div>
              </div>
            );
          })}
        </motion.div>
      </div>
      {typeof document !== 'undefined' &&
        createPortal(
          <AnimatePresence>{current && <SwarmPanel key={current.id} agent={current} onClose={close} />}</AnimatePresence>,
          document.body,
        )}
    </>
  );
}
