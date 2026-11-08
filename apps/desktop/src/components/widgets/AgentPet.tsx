/**
 * Coding-agent pet: a little star-bot that naps while no coding agent is busy, types away while Claude is
 * working (with a bubble saying what it's doing), and hops when the job is done.
 */
import { useEffect, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import type { AgentActivity } from '@orion/shared-types';

function elapsed(since: number) {
  const s = Math.max(0, Math.floor(Date.now() / 1000 - since));
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, '0')}s`;
}

export default function AgentPet({ agent }: { agent: AgentActivity | null }) {
  const state = agent?.state ?? 'idle';
  const [pinned, setPinned] = useState(false);
  const [, tick] = useState(0);
  useEffect(() => {
    if (state !== 'working') return;
    const t = setInterval(() => tick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, [state]);

  const showBubble = state !== 'idle' || pinned;
  const bubble =
    state === 'working'
      ? { title: (agent!.probable ? 'Probably: ' : '') + (agent!.status || 'Working'), sub: `${agent!.project}${agent!.agents > 1 ? ` · ${agent!.agents} agents` : ''} · ${elapsed(agent!.since)}` }
      : state === 'done'
      ? { title: 'All done!', sub: `${agent!.project}${agent!.duration ? ` · took ${Math.max(1, Math.round(agent!.duration / 60))} min` : ''}` }
      : {
          title: 'Napping',
          sub: agent?.watching?.length
            ? `Watching ${agent.watching.join(', ')}`
            : 'No coding agents found on this PC yet',
        };

  return (
    <div className="flex flex-col items-end gap-1.5 select-none" onClick={(e) => e.stopPropagation()}>
      <AnimatePresence>
        {showBubble && (
          <motion.div
            key={state + bubble.title}
            initial={{ opacity: 0, y: 6, scale: 0.96 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 6, scale: 0.96 }}
            className="max-w-[230px] rounded-2xl rounded-br-md border border-white/10 bg-black/65 backdrop-blur-xl px-3 py-2 text-white shadow-[0_10px_30px_rgba(0,0,0,0.5)]"
            role="status"
            aria-live="polite"
          >
            <div className="text-[12.5px] font-bold leading-snug">{bubble.title}</div>
            <div className="text-[11.5px] text-white/76 truncate">{bubble.sub}</div>
          </motion.div>
        )}
      </AnimatePresence>

      <motion.button
        type="button"
        onClick={() => setPinned((p) => !p)}
        className="relative w-[68px] h-[68px] cursor-pointer bg-transparent border-0 p-0"
        aria-label={`Coding agent: ${bubble.title}`}
        title="Your coding agent buddy"
        animate={
          state === 'working'
            ? { y: [0, -3, 0] }
            : state === 'done'
            ? { y: [0, -16, 0, -8, 0] }
            : { y: [0, 2, 0] }
        }
        transition={
          state === 'working'
            ? { duration: 0.6, repeat: Infinity, ease: 'easeInOut' }
            : state === 'done'
            ? { duration: 1.1, ease: 'easeOut' }
            : { duration: 3.2, repeat: Infinity, ease: 'easeInOut' }
        }
      >
        <svg viewBox="0 0 80 80" width="68" height="68" aria-hidden="true">
          <defs>
            <radialGradient id="pet-body" cx="35%" cy="30%" r="75%">
              <stop offset="0%" stopColor="#ffffff" stopOpacity="0.95" />
              <stop offset="45%" stopColor="#c9d6ff" stopOpacity="0.85" />
              <stop offset="100%" stopColor="#6a6fd8" stopOpacity="0.9" />
            </radialGradient>
            <filter id="pet-glow" x="-50%" y="-50%" width="200%" height="200%">
              <feGaussianBlur stdDeviation="2.5" />
            </filter>
          </defs>

          {/* antenna with a little star */}
          <line x1="40" y1="20" x2="40" y2="11" stroke="#c9d6ff" strokeWidth="2" strokeLinecap="round" />
          <motion.circle
            cx="40"
            cy="9"
            r="3.2"
            fill={state === 'working' ? '#7df0ff' : state === 'done' ? '#86efac' : '#ffffff'}
            animate={{ opacity: state === 'idle' ? [0.35, 0.6, 0.35] : [0.6, 1, 0.6] }}
            transition={{ duration: state === 'working' ? 0.8 : 2.4, repeat: Infinity }}
          />
          <circle cx="40" cy="9" r="5" fill={state === 'working' ? '#7df0ff' : '#ffffff'} opacity="0.35" filter="url(#pet-glow)" />

          {/* body */}
          <ellipse cx="40" cy="62" rx="20" ry="3.5" fill="#000" opacity="0.35" />
          <circle cx="40" cy="38" r="20" fill="url(#pet-body)" />
          <ellipse cx="40" cy="41" rx="13" ry="9.5" fill="#0b0c1c" opacity="0.92" />

          {/* face */}
          {state === 'idle' && (
            <g stroke="#9FB8FF" strokeWidth="2" strokeLinecap="round" fill="none">
              <path d="M33 41 q3 2 6 0" />
              <path d="M41 41 q3 2 6 0" />
            </g>
          )}
          {state === 'working' && (
            <g>
              <motion.g animate={{ x: [-1.5, 1.5, -1.5] }} transition={{ duration: 1.6, repeat: Infinity }}>
                <circle cx="35" cy="41" r="2.6" fill="#7df0ff" />
                <circle cx="45" cy="41" r="2.6" fill="#7df0ff" />
              </motion.g>
            </g>
          )}
          {state === 'done' && (
            <g stroke="#86efac" strokeWidth="2.2" strokeLinecap="round" fill="none">
              <path d="M32 42 l3 -3 l3 3" />
              <path d="M42 42 l3 -3 l3 3" />
            </g>
          )}

          {/* laptop + typing hands while working */}
          {state === 'working' && (
            <g>
              <rect x="25" y="55" width="30" height="3" rx="1.5" fill="#c9d6ff" opacity="0.9" />
              <rect x="28" y="47" width="24" height="8" rx="1.5" fill="#1c1f3d" stroke="#7df0ff" strokeOpacity="0.6" />
              <motion.rect x="30" y="53" width="6" height="4" rx="2" fill="#ffffff" animate={{ y: [53, 51, 53] }} transition={{ duration: 0.25, repeat: Infinity }} />
              <motion.rect x="44" y="53" width="6" height="4" rx="2" fill="#ffffff" animate={{ y: [51, 53, 51] }} transition={{ duration: 0.25, repeat: Infinity }} />
            </g>
          )}
        </svg>

        {/* sleepy z's / work sparkles */}
        {state === 'idle' && (
          <motion.span
            className="absolute -top-1 right-0 text-[11.5px] font-bold text-white/76"
            animate={{ opacity: [0, 1, 0], y: [4, -6, -12] }}
            transition={{ duration: 2.8, repeat: Infinity }}
          >
            z
          </motion.span>
        )}
        {state === 'working' &&
          [0, 1, 2].map((i) => (
            <motion.span
              key={i}
              className="absolute w-1 h-1 rounded-full bg-cyan-200"
              style={{ left: 10 + i * 22, top: 6 }}
              animate={{ opacity: [0, 1, 0], y: [6, -6] }}
              transition={{ duration: 1.2, repeat: Infinity, delay: i * 0.4 }}
            />
          ))}
      </motion.button>
    </div>
  );
}
