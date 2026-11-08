import { AnimatePresence, motion } from 'framer-motion';
import {
  Activity, Bot, Chrome, Code2, Cpu, Database, Globe, MessageSquare, Repeat, ScanEye, X, Zap,
} from 'lucide-react';
import type { AgentId, AgentStates, ClientInfo, TelemetrySnapshot } from '@orion/shared-types';
import type { FeedItem } from '../hooks/useOrionSocket';
import { colors } from '../theme/colors';
import PairingPanel from './shell/PairingPanel';

interface Props {
  open: boolean;
  onClose: () => void;
  connected: boolean;
  agents: AgentStates;
  onToggleAgent: (agent: AgentId, enabled: boolean) => void;
  clients: ClientInfo[];
  telemetry: TelemetrySnapshot | null;
  feed: FeedItem[];
}

const AGENTS: { id: AgentId; name: string; desc: string; icon: typeof Bot }[] = [
  { id: 'coding_agent', name: 'Coding Agent', desc: 'IDE focus detection · inline repair suggestions', icon: Code2 },
  { id: 'routine_optimizer', name: 'Routine Optimizer', desc: 'Detects repetition · proposes macros & routines', icon: Repeat },
  { id: 'discord_monitor', name: 'Discord Monitor', desc: 'Headless gateway listener for actionable tasks', icon: MessageSquare },
  { id: 'system_capturer', name: 'System Capturer', desc: 'pHash visual diff + local OCR engine', icon: ScanEye },
];

const BROWSERS = [
  { id: 'chrome', name: 'Chrome', icon: Chrome },
  { id: 'edge', name: 'Edge', icon: Globe },
  { id: 'brave', name: 'Brave', icon: Globe },
  { id: 'firefox', name: 'Firefox', icon: Globe },
];

function Switch({ on, onChange, disabled }: { on: boolean; onChange: (v: boolean) => void; disabled?: boolean }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      disabled={disabled}
      onClick={() => onChange(!on)}
      className="relative h-5 w-9 shrink-0 rounded-full border border-glass transition-colors disabled:opacity-40"
      style={{ background: on ? `${colors.cyan}33` : 'rgba(255,255,255,0.05)' }}
    >
      <motion.span
        className="absolute top-0.5 h-3.5 w-3.5 rounded-full"
        animate={{ left: on ? 18 : 2, backgroundColor: on ? colors.cyan : colors.dust }}
        style={{ boxShadow: on ? `0 0 10px ${colors.cyan}` : 'none' }}
        transition={{ type: 'spring', stiffness: 500, damping: 32 }}
      />
    </button>
  );
}

function Dot({ on }: { on: boolean }) {
  return (
    <span
      className="inline-block h-2 w-2 rounded-full"
      style={{ background: on ? colors.success : '#3a3d52', boxShadow: on ? `0 0 8px ${colors.success}` : 'none' }}
    />
  );
}

function Stat({ label, value, accent }: { label: string; value: string; accent?: string }) {
  return (
    <div className="rounded-lg border border-glass bg-white/[0.02] px-3 py-2">
      <div className="text-[10px] uppercase tracking-wider text-dust">{label}</div>
      <div className="font-display text-sm" style={{ color: accent ?? colors.starlight }}>{value}</div>
    </div>
  );
}

const fmt = (n: number) => (n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${(n / 1e3).toFixed(1)}k` : `${n}`);
const time = (ts: number) => new Date(ts * 1000).toLocaleTimeString([], { hour12: false });

export default function ControlDrawer({ open, onClose, connected, agents, onToggleAgent, clients, telemetry, feed }: Props) {
  const extensions = clients.filter((c) => c.kind === 'extension');
  const t = telemetry;

  return (
    <AnimatePresence>
      {open && (
        <motion.aside
          key="drawer"
          initial={{ x: -400, opacity: 0 }}
          animate={{ x: 0, opacity: 1 }}
          exit={{ x: -400, opacity: 0 }}
          transition={{ type: 'spring', stiffness: 260, damping: 30 }}
          className="glass-strong absolute bottom-0 left-0 top-10 z-40 flex w-[380px] flex-col border-r border-glass"
          style={{ boxShadow: `12px 0 40px rgba(0,0,0,0.5), 1px 0 0 ${colors.purple}33` }}
        >
          <header className="flex items-center justify-between border-b border-glass px-5 py-4">
            <div>
              <div className="font-display text-sm tracking-[0.2em] text-starlight">BROWSER & AGENT HUB</div>
              <div className="text-[11px] text-dust">Ctrl + Space to toggle</div>
            </div>
            <button onClick={onClose} className="rounded-md p-1.5 text-dust hover:bg-white/5 hover:text-starlight">
              <X size={16} />
            </button>
          </header>

          <div className="flex-1 space-y-5 overflow-y-auto px-5 py-4">
            {/* Cross-browser sync */}
            <section>
              <div className="panel-title mb-2">Cross-Browser Sync</div>
              <div className="space-y-1.5">
                <div className="flex items-center justify-between rounded-lg border border-glass bg-white/[0.02] px-3 py-2 text-sm">
                  <span className="flex items-center gap-2"><Cpu size={14} className="text-pulse" /> Orion Daemon · :9120</span>
                  <Dot on={connected} />
                </div>
                {BROWSERS.map(({ id, name, icon: Icon }) => {
                  const count = extensions.filter((c) => c.browser === id).length;
                  return (
                    <div key={id} className="flex items-center justify-between rounded-lg border border-glass bg-white/[0.02] px-3 py-2 text-sm">
                      <span className="flex items-center gap-2"><Icon size={14} className="text-nebula" /> {name}</span>
                      <span className="flex items-center gap-2 text-[11px] text-dust">
                        {count > 0 ? `${count} WS link${count > 1 ? 's' : ''}` : 'offline'} <Dot on={count > 0} />
                      </span>
                    </div>
                  );
                })}
              </div>
            </section>

            {/* Review B1/B2: one-time codes for the browser extension and Telegram */}
            <PairingPanel connected={connected} />

            {/* Agents */}
            <section>
              <div className="panel-title mb-2">Agent Sub-Routines</div>
              <div className="space-y-1.5">
                {AGENTS.map(({ id, name, desc, icon: Icon }) => (
                  <div key={id} className="flex items-center gap-3 rounded-lg border border-glass bg-white/[0.02] px-3 py-2.5">
                    <Icon size={16} style={{ color: agents[id] ? colors.cyan : colors.dust }} />
                    <div className="min-w-0 flex-1">
                      <div className="text-sm text-starlight">{name}</div>
                      <div className="truncate text-[11px] text-dust">{desc}</div>
                    </div>
                    <Switch on={agents[id]} disabled={!connected} onChange={(v) => onToggleAgent(id, v)} />
                  </div>
                ))}
              </div>
            </section>

            {/* Telemetry */}
            <section>
              <div className="panel-title mb-2 flex items-center gap-2"><Activity size={12} /> Telemetry</div>
              <div className="grid grid-cols-2 gap-1.5">
                <Stat label="Frame skip ratio" value={t ? `${(t.skip_ratio * 100).toFixed(1)}%` : '—'} accent={colors.cyan} />
                <Stat label="Frames (skip/total)" value={t ? `${t.frames_skipped}/${t.frames_total}` : '—'} />
                <Stat label="Est. tokens saved" value={t ? fmt(t.est_tokens_saved) : '—'} accent={colors.success} />
                <Stat label="LLM tokens in/out" value={t ? `${fmt(t.llm_tokens_in)}/${fmt(t.llm_tokens_out)}` : '—'} />
                <Stat label="LLM calls local/cloud" value={t ? `${t.llm_calls_local}/${t.llm_calls_cloud}` : '—'} accent={colors.purple} />
                <Stat label="Vector rows" value={t ? `${fmt(t.memory_rows)}` : '—'} />
              </div>
              {t && (
                <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[10px] text-dust">
                  <span className="flex items-center gap-1"><Database size={10} /> embed: {t.embedding_backend}</span>
                  <span className="flex items-center gap-1"><ScanEye size={10} /> OCR {t.ocr_available ? 'ready' : 'unavailable'}</span>
                  <span className="flex items-center gap-1"><Zap size={10} /> capture {t.capture_running ? 'live' : 'paused'}</span>
                  {t.last_phash_diff !== null && <span>last Δ {t.last_phash_diff}</span>}
                </div>
              )}
            </section>

            {/* Event waterfall */}
            <section>
              <div className="panel-title mb-2">Event Stream</div>
              {feed.length === 0 && (
                <div className="rounded-lg border border-dashed border-glass p-4 text-center text-xs text-dust">
                  {connected ? 'Waiting for events…' : 'Daemon offline — run `npm run dev:daemon`'}
                </div>
              )}
              <ul className="space-y-1">
                <AnimatePresence initial={false}>
                  {feed.slice(0, 80).map((item) => (
                    <motion.li
                      key={item.id}
                      layout
                      initial={{ opacity: 0, y: -8 }}
                      animate={{ opacity: 1, y: 0 }}
                      exit={{ opacity: 0 }}
                      className="flex gap-2 rounded-md px-2 py-1.5 text-[11px] hover:bg-white/[0.03]"
                    >
                      <span className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full" style={{ background: item.color, boxShadow: `0 0 6px ${item.color}` }} />
                      <div className="min-w-0 flex-1">
                        <div className="flex justify-between gap-2">
                          <span style={{ color: item.color }}>{item.label}</span>
                          <span className="font-mono text-dust">{time(item.ts)}</span>
                        </div>
                        <div className="truncate text-dust">{item.detail}</div>
                      </div>
                    </motion.li>
                  ))}
                </AnimatePresence>
              </ul>
            </section>
          </div>
        </motion.aside>
      )}
    </AnimatePresence>
  );
}
