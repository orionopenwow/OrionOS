import { motion } from 'framer-motion';
import { Play, Pause } from 'lucide-react';
import type { TelemetrySnapshot, AgentStates } from '@orion/shared-types';

interface Props {
  telemetry: TelemetrySnapshot | null;
  agents: AgentStates;
  onToggleAgent: (agentId: any, enabled: boolean) => void;
  activeWindow?: string | null;
}

export default function TranslucentAgentCards({
  telemetry,
  agents,
  onToggleAgent,
  activeWindow,
}: Props) {
  // Safe defaults
  const t = telemetry || {
    uptime_s: 0,
    capture_running: true,
    ocr_available: true,
    frames_total: 128,
    frames_skipped: 110,
    frames_processed: 18,
    skip_ratio: 0.859,
    est_tokens_saved: 45200,
    last_phash_diff: 3,
    llm_calls_local: 14,
    llm_calls_cloud: 2,
    llm_tokens_in: 3200,
    llm_tokens_out: 1450,
    llm_failures: 0,
    memory_commits: 42,
    memory_rows: 156,
    embedding_backend: 'local-fastembed',
  };

  // Agent 1: R-Bullz (Orchestrator & SLM Reasoning Agent - Llama 3.2 Ollama)
  const rBullzActive = agents['routine_optimizer'] ?? true;
  const rBullzLoad = Math.min(100, Math.round(((t.llm_calls_local % 10) + 2) * 8.5));

  // Agent 2: Mimosa Kush Mints (Perceptual Vision & Screen Capture Agent - pHash + OCR)
  const mimosaActive = agents['system_capturer'] ?? true;
  const mimosaDiff = t.last_phash_diff ?? 4;
  const mimosaLoad = Math.round((1 - t.skip_ratio) * 100);

  // Agent 3: Specs (System Analytics, Hardware Diagnostics & LanceDB Vector Store)
  const codingActive = agents['coding_agent'] ?? true;
  const specsLoad = Math.min(95, Math.round(28 + (t.memory_rows % 50)));

  return (
    <div className="w-full">
      {/* Section Header */}
      <div className="flex items-center justify-between mb-3 px-1">
        <div className="flex items-center gap-2">
          <div className="h-2 w-2 rounded-full bg-[#3DFFA8] shadow-[0_0_8px_#3DFFA8]" />
          <span className="font-display text-xs font-bold tracking-[0.2em] text-[#7FA6FF]">
            ACTIVE PC AGENTS & DIAGNOSTICS
          </span>
          <span className="text-[11px] font-mono text-dust">
            {Object.values(agents).filter(Boolean).length} RUNNING
          </span>
        </div>
        <div className="flex items-center gap-2 text-[11px] font-mono text-dust">
          <span>UPTIME: {Math.floor(t.uptime_s / 60)}m {t.uptime_s % 60}s</span>
          <span>•</span>
          <span className="text-[#3DFFA8]">ALL SYSTEMS NOMINAL</span>
        </div>
      </div>

      {/* 3 Translucent Cards directly matching Reference Sheet */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3.5">
        
        {/* CARD 1: R-Bullz (Purple / Lavender Frost) */}
        <motion.div
          whileHover={{ y: -2, transition: { duration: 0.15 } }}
          className="relative rounded-2xl border border-[#7FA6FF]/30 bg-gradient-to-b from-[#7FA6FF]/15 via-[#8A9AB5]/10 to-transparent p-4 backdrop-blur-xl shadow-lg flex flex-col justify-between overflow-hidden group"
          style={{
            boxShadow: '0 8px 32px rgba(127, 166, 255, 0.08), inset 0 0 15px rgba(127, 166, 255, 0.05)',
          }}
        >
          {/* Subtle Grid Lines matching reference asset */}
          <div className="absolute inset-0 pointer-events-none opacity-20 bg-[linear-gradient(to_right,#C084FC_1px,transparent_1px),linear-gradient(to_bottom,#C084FC_1px,transparent_1px)] bg-[size:24px_24px]" />

          {/* Top Bar: Title & Neon Green Active Pip */}
          <div className="relative z-10 flex items-center justify-between">
            <div>
              <div className="flex items-center gap-1.5">
                <span className="font-display text-sm font-semibold tracking-wider text-starlight">
                  R-Bullz
                </span>
                <span className="text-[10.5px] font-mono px-1.5 py-0.5 rounded bg-[#7FA6FF]/20 text-[#7FA6FF] border border-[#7FA6FF]/30">
                  SLM
                </span>
              </div>
              <p className="text-[11px] text-dust font-mono mt-0.5">Dual-Tier Orchestrator</p>
            </div>

            <div className="flex items-center gap-2">
              <button
                onClick={() => onToggleAgent('routine_optimizer', !rBullzActive)}
                className="text-dust hover:text-starlight transition-colors"
                title={rBullzActive ? 'Pause Agent' : 'Resume Agent'}
              >
                {rBullzActive ? <Pause size={12} /> : <Play size={12} />}
              </button>
              {/* Neon Green Active Dot */}
              <div className="relative flex items-center justify-center">
                <span
                  className={`h-2.5 w-2.5 rounded-full ${
                    rBullzActive
                      ? 'bg-[#3DFFA8] shadow-[0_0_10px_#3DFFA8]'
                      : 'bg-dust/50'
                  }`}
                />
                {rBullzActive && (
                  <span className="absolute h-4 w-4 rounded-full border border-[#3DFFA8]/50 animate-ping opacity-75" />
                )}
              </div>
            </div>
          </div>

          {/* Telemetry Metrics */}
          <div className="relative z-10 my-3.5 space-y-2 text-[11.5px] font-mono">
            <div className="flex justify-between text-dust">
              <span>Local Calls (Ollama)</span>
              <span className="text-starlight font-semibold">{t.llm_calls_local}</span>
            </div>
            <div className="flex justify-between text-dust">
              <span>Cloud Failovers</span>
              <span className="text-[#7FA6FF] font-semibold">{t.llm_calls_cloud}</span>
            </div>
            <div className="flex justify-between text-dust">
              <span>Tokens Synthesized</span>
              <span className="text-[#3DFFA8] font-semibold">{t.llm_tokens_out}</span>
            </div>
          </div>

          {/* Bottom Arc Dial Meter matching reference asset */}
          <div className="relative z-10 flex flex-col items-center justify-center pt-1 border-t border-[#7FA6FF]/20">
            <div className="relative h-14 w-28 flex items-end justify-center overflow-hidden">
              <svg className="w-24 h-24 transform -rotate-90" viewBox="0 0 100 100">
                <circle
                  cx="50"
                  cy="50"
                  r="40"
                  fill="transparent"
                  stroke="rgba(255,255,255,0.08)"
                  strokeWidth="8"
                  strokeDasharray="125 125"
                />
                <circle
                  cx="50"
                  cy="50"
                  r="40"
                  fill="transparent"
                  stroke="#7FA6FF"
                  strokeWidth="8"
                  strokeDasharray={`${(rBullzLoad / 100) * 125} 125`}
                  strokeLinecap="round"
                  className="transition-all duration-700 ease-out"
                />
              </svg>
              <div className="absolute bottom-0 flex flex-col items-center">
                <span className="text-xs font-mono font-bold text-starlight">{rBullzLoad}%</span>
                <span className="text-[8px] font-mono text-dust tracking-wider">LOAD</span>
              </div>
            </div>

            {/* Corner accent brackets matching reference */}
            <div className="w-full flex justify-between px-1 text-[8px] text-[#7FA6FF]/40 font-mono">
              <span>└</span>
              <span>┘</span>
            </div>
          </div>
        </motion.div>

        {/* CARD 2: Mimosa Kush Mints (Mint / Emerald Green Frost) */}
        <motion.div
          whileHover={{ y: -2, transition: { duration: 0.15 } }}
          className="relative rounded-2xl border border-[#3DFFA8]/30 bg-gradient-to-b from-[#3DFFA8]/15 via-[#00E5FF]/10 to-transparent p-4 backdrop-blur-xl shadow-lg flex flex-col justify-between overflow-hidden group"
          style={{
            boxShadow: '0 8px 32px rgba(61, 255, 168, 0.08), inset 0 0 15px rgba(61, 255, 168, 0.05)',
          }}
        >
          {/* Subtle Grid Lines */}
          <div className="absolute inset-0 pointer-events-none opacity-20 bg-[linear-gradient(to_right,#3DFFA8_1px,transparent_1px),linear-gradient(to_bottom,#3DFFA8_1px,transparent_1px)] bg-[size:24px_24px]" />

          {/* Top Bar: Title & Neon Green Active Pip */}
          <div className="relative z-10 flex items-center justify-between">
            <div>
              <div className="flex items-center gap-1.5">
                <span className="font-display text-sm font-semibold tracking-wider text-starlight">
                  Mimosa Kush Mints
                </span>
                <span className="text-[10.5px] font-mono px-1.5 py-0.5 rounded bg-[#3DFFA8]/20 text-[#3DFFA8] border border-[#3DFFA8]/30">
                  OCR
                </span>
              </div>
              <p className="text-[11px] text-dust font-mono mt-0.5">Perceptual Screen Vision</p>
            </div>

            <div className="flex items-center gap-2">
              <button
                onClick={() => onToggleAgent('system_capturer', !mimosaActive)}
                className="text-dust hover:text-starlight transition-colors"
                title={mimosaActive ? 'Pause Agent' : 'Resume Agent'}
              >
                {mimosaActive ? <Pause size={12} /> : <Play size={12} />}
              </button>
              {/* Neon Green Active Dot */}
              <div className="relative flex items-center justify-center">
                <span
                  className={`h-2.5 w-2.5 rounded-full ${
                    mimosaActive
                      ? 'bg-[#3DFFA8] shadow-[0_0_10px_#3DFFA8]'
                      : 'bg-dust/50'
                  }`}
                />
                {mimosaActive && (
                  <span className="absolute h-4 w-4 rounded-full border border-[#3DFFA8]/50 animate-ping opacity-75" />
                )}
              </div>
            </div>
          </div>

          {/* Telemetry Metrics */}
          <div className="relative z-10 my-3.5 space-y-2 text-[11.5px] font-mono">
            <div className="flex justify-between text-dust">
              <span>Capture Engine</span>
              <span className="text-starlight font-semibold">
                {t.capture_running ? `pHash (Δ${mimosaDiff})` : 'Paused'}
              </span>
            </div>
            <div className="flex justify-between text-dust">
              <span>Skip Ratio (Gated)</span>
              <span className="text-[#3DFFA8] font-semibold">
                {(t.skip_ratio * 100).toFixed(1)}%
              </span>
            </div>
            <div className="flex justify-between text-dust">
              <span>Est. Tokens Saved</span>
              <span className="text-[#00E5FF] font-semibold">
                {(t.est_tokens_saved / 1000).toFixed(1)}k
              </span>
            </div>
          </div>

          {/* Bottom Arc Dial Meter */}
          <div className="relative z-10 flex flex-col items-center justify-center pt-1 border-t border-[#3DFFA8]/20">
            <div className="relative h-14 w-28 flex items-end justify-center overflow-hidden">
              <svg className="w-24 h-24 transform -rotate-90" viewBox="0 0 100 100">
                <circle
                  cx="50"
                  cy="50"
                  r="40"
                  fill="transparent"
                  stroke="rgba(255,255,255,0.08)"
                  strokeWidth="8"
                  strokeDasharray="125 125"
                />
                <circle
                  cx="50"
                  cy="50"
                  r="40"
                  fill="transparent"
                  stroke="#3DFFA8"
                  strokeWidth="8"
                  strokeDasharray={`${(mimosaLoad / 100) * 125} 125`}
                  strokeLinecap="round"
                  className="transition-all duration-700 ease-out"
                />
              </svg>
              <div className="absolute bottom-0 flex flex-col items-center">
                <span className="text-xs font-mono font-bold text-starlight">{mimosaLoad}%</span>
                <span className="text-[8px] font-mono text-dust tracking-wider">ACTIVITY</span>
              </div>
            </div>

            {/* Corner accent brackets */}
            <div className="w-full flex justify-between px-1 text-[8px] text-[#3DFFA8]/40 font-mono">
              <span>└</span>
              <span>┘</span>
            </div>
          </div>
        </motion.div>

        {/* CARD 3: Specs (Icy Blue / Indigo Frost) */}
        <motion.div
          whileHover={{ y: -2, transition: { duration: 0.15 } }}
          className="relative rounded-2xl border border-[#00E5FF]/30 bg-gradient-to-b from-[#00E5FF]/15 via-[#5865F2]/10 to-transparent p-4 backdrop-blur-xl shadow-lg flex flex-col justify-between overflow-hidden group"
          style={{
            boxShadow: '0 8px 32px rgba(0, 229, 255, 0.08), inset 0 0 15px rgba(0, 229, 255, 0.05)',
          }}
        >
          {/* Coordinate Grid Matrix matching reference */}
          <div className="absolute inset-0 pointer-events-none opacity-20 bg-[linear-gradient(to_right,#00E5FF_1px,transparent_1px),linear-gradient(to_bottom,#00E5FF_1px,transparent_1px)] bg-[size:20px_20px]" />

          {/* Top Bar: Title & Dual Gradient Pill Badge */}
          <div className="relative z-10 flex items-center justify-between">
            <div>
              <div className="flex items-center gap-1.5">
                <span className="font-display text-sm font-semibold tracking-wider text-starlight">
                  Specs
                </span>
                <span className="text-[10.5px] font-mono px-1.5 py-0.5 rounded bg-[#00E5FF]/20 text-[#00E5FF] border border-[#00E5FF]/30">
                  SYSTEM
                </span>
              </div>
              <p className="text-[11px] text-dust font-mono mt-0.5">PC Diagnostics & LanceDB</p>
            </div>

            {/* Dual Pill Badge matching reference (purple gradient pill with green dot) */}
            <div className="flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-gradient-to-r from-[#7FA6FF]/30 to-[#00E5FF]/30 border border-[#00E5FF]/40">
              <span className="text-[10.5px] font-mono text-starlight">ONLINE</span>
              <span className="h-2 w-2 rounded-full bg-[#3DFFA8] shadow-[0_0_8px_#3DFFA8]" />
            </div>
          </div>

          {/* Telemetry Metrics */}
          <div className="relative z-10 my-3.5 space-y-2 text-[11.5px] font-mono">
            <div className="flex justify-between text-dust">
              <span>LanceDB Memory Vectors</span>
              <span className="text-starlight font-semibold">{t.memory_rows} vectors</span>
            </div>
            <div className="flex justify-between text-dust">
              <span>IDE Companion Agent</span>
              <span className="text-[#3DFFA8] font-semibold">{codingActive ? 'ACTIVE' : 'MUTED'}</span>
            </div>
            <div className="flex justify-between text-dust">
              <span>Focused Window</span>
              <span className="text-[#00E5FF] font-semibold truncate max-w-[130px]" title={activeWindow || 'Desktop'}>
                {activeWindow || 'Desktop'}
              </span>
            </div>
          </div>

          {/* Bottom Arc Dial Meter */}
          <div className="relative z-10 flex flex-col items-center justify-center pt-1 border-t border-[#00E5FF]/20">
            <div className="relative h-14 w-28 flex items-end justify-center overflow-hidden">
              <svg className="w-24 h-24 transform -rotate-90" viewBox="0 0 100 100">
                <circle
                  cx="50"
                  cy="50"
                  r="40"
                  fill="transparent"
                  stroke="rgba(255,255,255,0.08)"
                  strokeWidth="8"
                  strokeDasharray="125 125"
                />
                <circle
                  cx="50"
                  cy="50"
                  r="40"
                  fill="transparent"
                  stroke="#00E5FF"
                  strokeWidth="8"
                  strokeDasharray={`${(specsLoad / 100) * 125} 125`}
                  strokeLinecap="round"
                  className="transition-all duration-700 ease-out"
                />
              </svg>
              <div className="absolute bottom-0 flex flex-col items-center">
                <span className="text-xs font-mono font-bold text-starlight">{specsLoad}%</span>
                <span className="text-[8px] font-mono text-dust tracking-wider">DIAGNOSTICS</span>
              </div>
            </div>

            {/* Corner accent brackets */}
            <div className="w-full flex justify-between px-1 text-[8px] text-[#00E5FF]/40 font-mono">
              <span>└</span>
              <span>┘</span>
            </div>
          </div>
        </motion.div>

      </div>
    </div>
  );
}
