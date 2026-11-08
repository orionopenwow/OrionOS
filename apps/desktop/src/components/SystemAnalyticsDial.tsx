import { useState, useEffect } from 'react';
import { Activity, Radio } from 'lucide-react';
import type { TelemetrySnapshot } from '@orion/shared-types';

interface Props {
  telemetry: TelemetrySnapshot | null;
}

export default function SystemAnalyticsDial({ telemetry }: Props) {
  const [ticker, setTicker] = useState(0);

  // Generate dynamic hex / data telemetry streams like the reference sheet
  useEffect(() => {
    const interval = setInterval(() => {
      setTicker((prev) => (prev + 1) % 1000);
    }, 1200);
    return () => clearInterval(interval);
  }, []);

  const hexLines = [
    ['0060041', `500${(ticker % 90) + 10}`, '001002', '99.00', '001.00'],
    ['0350123', '010116', `B0000000${(telemetry?.memory_rows ?? 15) % 99}`, '$0.01', '$2.00'],
    ['0850234', '503017', 'B000000005', '$0.02', '$1.00'],
    ['0200101', '013118', 'B000000020', '$0.03', '$1.50'],
    ['0300180', '503119', 'B000000005', '$0.00', '$1.00'],
    ['0700052', '001012', 'B000000005', '$0.055', '$1.00'],
  ];

  return (
    <div className="w-full rounded-2xl border border-white/10 bg-[#151A21]/80 p-5 backdrop-blur-2xl shadow-2xl">
      {/* Header */}
      <div className="flex items-center justify-between mb-4 border-b border-white/10 pb-3">
        <div className="flex items-center gap-2">
          <Activity size={16} className="text-[#00E5FF] animate-pulse" />
          <span className="font-display text-xs font-bold tracking-[0.2em] text-[#00E5FF]">
            SYSTEM ANALYTICS & WAVEFORM TELEMETRY
          </span>
        </div>
        <div className="flex items-center gap-3 text-[11px] font-mono text-dust">
          <span className="flex items-center gap-1.5">
            <span className="h-1.5 w-1.5 rounded-full bg-[#3DFFA8]" />
            RADAR: ACTIVE #{ticker}
          </span>
          <span className="text-[#7FA6FF]">SAMPLE RATE: 48kHz</span>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-center">
        
        {/* Left Column: Rotating Concentric Radar Dial (matching reference asset sheet) */}
        <div className="lg:col-span-5 flex flex-col items-center justify-center p-3 relative">
          <div className="relative h-56 w-56 flex items-center justify-center">
            
            {/* Outer Decorative Track */}
            <svg className="absolute inset-0 h-full w-full animate-spin" style={{ animationDuration: '40s' }} viewBox="0 0 200 200">
              <circle
                cx="100"
                cy="100"
                r="92"
                fill="none"
                stroke="rgba(0, 229, 255, 0.2)"
                strokeWidth="1.5"
                strokeDasharray="4 8"
              />
              <circle
                cx="100"
                cy="100"
                r="84"
                fill="none"
                stroke="rgba(127, 166, 255, 0.35)"
                strokeWidth="3"
                strokeDasharray="60 40 20 50"
              />
            </svg>

            {/* Mid Track (Counter-clockwise rotation) */}
            <svg className="absolute inset-0 h-full w-full animate-spin" style={{ animationDuration: '24s', animationDirection: 'reverse' }} viewBox="0 0 200 200">
              <circle
                cx="100"
                cy="100"
                r="70"
                fill="none"
                stroke="rgba(0, 229, 255, 0.5)"
                strokeWidth="4"
                strokeDasharray="40 10 90 20"
              />
              <circle
                cx="100"
                cy="100"
                r="58"
                fill="none"
                stroke="rgba(61, 255, 168, 0.4)"
                strokeWidth="2"
                strokeDasharray="15 15"
              />
            </svg>

            {/* Inner High-Density Track */}
            <svg className="absolute inset-0 h-full w-full animate-spin" style={{ animationDuration: '14s' }} viewBox="0 0 200 200">
              <circle
                cx="100"
                cy="100"
                r="44"
                fill="none"
                stroke="rgba(127, 166, 255, 0.7)"
                strokeWidth="5"
                strokeDasharray="30 20 50 10"
              />
              <circle
                cx="100"
                cy="100"
                r="32"
                fill="none"
                stroke="rgba(0, 229, 255, 0.3)"
                strokeWidth="1.5"
              />
            </svg>

            {/* Center Core Pip & Telemetry */}
            <div className="relative flex flex-col items-center justify-center">
              <div className="h-10 w-10 rounded-full border border-[#00E5FF]/50 bg-[#00E5FF]/10 flex items-center justify-center">
                <Radio size={16} className="text-[#00E5FF] animate-pulse" />
              </div>
              <span className="text-[10.5px] font-mono font-bold text-starlight mt-1">420.8 GHz</span>
            </div>

            {/* Corner Markings */}
            <div className="absolute top-1 left-1 text-[8px] font-mono text-dust/80">┌ [SYS.01]</div>
            <div className="absolute top-1 right-1 text-[8px] font-mono text-dust/80">[SYNC] ┐</div>
            <div className="absolute bottom-1 left-1 text-[8px] font-mono text-dust/80">└ 0x7E3A</div>
            <div className="absolute bottom-1 right-1 text-[8px] font-mono text-dust/80">DIAL.OK ┘</div>
          </div>
        </div>

        {/* Center Column: Live Hex / Code Matrix Stream (matching reference asset sheet) */}
        <div className="lg:col-span-3 font-mono text-[11px] space-y-1 bg-black/40 p-3 rounded-xl border border-white/5">
          <div className="text-[10.5px] uppercase tracking-wider text-[#7FA6FF] mb-2 flex items-center justify-between">
            <span>Live Stats</span>
            <span className="text-[#3DFFA8]">SYNCED</span>
          </div>
          <div className="space-y-1">
            {hexLines.map((row, idx) => (
              <div key={idx} className="flex justify-between text-dust hover:text-starlight transition-colors">
                <span className="text-[#00E5FF]/80">{row[0]}</span>
                <span className="text-dust/85">{row[1]}</span>
                <span className="text-[#7FA6FF]/80">{row[2]}</span>
                <span className="text-[#3DFFA8]">{row[3]}</span>
              </div>
            ))}
          </div>
        </div>

        {/* Right Column: Multi-layer Waveform Area Graphs (matching reference asset sheet) */}
        <div className="lg:col-span-4 space-y-3">
          
          {/* Waveform Graph 1: Sine / Frequency Curve */}
          <div className="rounded-xl border border-white/10 bg-black/40 p-3">
            <div className="flex items-center justify-between text-[11px] font-mono mb-2">
              <span className="text-[#00E5FF] font-semibold">NETWORK RESPONSE</span>
              <span className="text-[#3DFFA8]">Ping: 25 ms</span>
            </div>
            
            {/* SVG Area Waveform with Cyan-to-Purple Gradient Fill */}
            <div className="h-16 w-full">
              <svg className="h-full w-full overflow-visible" viewBox="0 0 300 70" preserveAspectRatio="none">
                <defs>
                  <linearGradient id="waveGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#00E5FF" stopOpacity="0.45" />
                    <stop offset="100%" stopColor="#7FA6FF" stopOpacity="0.02" />
                  </linearGradient>
                </defs>
                {/* Horizontal Coordinate Grid Lines */}
                <line x1="0" y1="18" x2="300" y2="18" stroke="rgba(255,255,255,0.06)" strokeDasharray="3 3" />
                <line x1="0" y1="35" x2="300" y2="35" stroke="rgba(255,255,255,0.06)" strokeDasharray="3 3" />
                <line x1="0" y1="52" x2="300" y2="52" stroke="rgba(255,255,255,0.06)" strokeDasharray="3 3" />

                {/* Filled Area */}
                <path
                  d="M 0 50 Q 30 10, 60 40 T 120 15 T 180 45 T 240 20 T 300 35 L 300 70 L 0 70 Z"
                  fill="url(#waveGrad)"
                />
                {/* Smooth Sine Stroke */}
                <path
                  d="M 0 50 Q 30 10, 60 40 T 120 15 T 180 45 T 240 20 T 300 35"
                  fill="none"
                  stroke="#00E5FF"
                  strokeWidth="2"
                  strokeLinecap="round"
                />
                {/* Secondary Violet Harmonic Wave */}
                <path
                  d="M 0 35 Q 40 55, 80 25 T 160 40 T 220 25 T 300 45"
                  fill="none"
                  stroke="#7FA6FF"
                  strokeWidth="1.5"
                  strokeDasharray="4 2"
                  opacity="0.8"
                />
              </svg>
            </div>
          </div>

          {/* Diagnostics Telemetry Metrics Box */}
          <div className="grid grid-cols-3 gap-2 text-[11px] font-mono">
            <div className="rounded-lg border border-white/5 bg-white/[0.02] p-2">
              <span className="text-dust block text-[8px]">DIAGNOSTICS</span>
              <span className="text-starlight font-bold">Fling 39ms</span>
            </div>
            <div className="rounded-lg border border-white/5 bg-white/[0.02] p-2">
              <span className="text-dust block text-[8px]">LATENCY</span>
              <span className="text-[#3DFFA8] font-bold">Fling 30ms</span>
            </div>
            <div className="rounded-lg border border-white/5 bg-white/[0.02] p-2">
              <span className="text-dust block text-[8px]">JITTER</span>
              <span className="text-[#00E5FF] font-bold">Fling 0.6</span>
            </div>
          </div>

        </div>

      </div>
    </div>
  );
}
