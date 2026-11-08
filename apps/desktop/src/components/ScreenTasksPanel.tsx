import { useState } from 'react';
import {
  MousePointer,
  Play,
  AlertTriangle,
  Layers,
} from 'lucide-react';
import type { ScreenWorkflowProgressEvent } from '@orion/shared-types';

interface Props {
  workflowProgress: ScreenWorkflowProgressEvent | null;
  onExecuteWorkflow: (steps: Array<Record<string, unknown>>) => void;
  onExecuteAction: (action: Record<string, unknown>) => void;
}

const PRESET_WORKFLOWS: { name: string; desc: string; steps: any[] }[] = [
  {
    name: 'Open Notepad & Type Greeting',
    desc: 'Automates Win+R -> notepad -> Enter -> types an Orion greeting',
    steps: [
      { action: 'hotkey', keys: ['win', 'r'], delay_after: 0.5 },
      { action: 'type', text: 'notepad', delay_after: 0.3 },
      { action: 'press', key: 'enter', delay_after: 0.8 },
      {
        action: 'type',
        text: 'Hello from Orion! Autonomous desktop mouse & keyboard automation is fully operational.\n\n- System Status: Nominal\n- Autonomous Screen Agent: Active\n',
        delay_after: 0.2,
      },
    ],
  },
  {
    name: 'Minimize Windows to Desktop',
    desc: 'Executes Win+D shortcut and pauses',
    steps: [
      { action: 'hotkey', keys: ['win', 'd'], delay_after: 0.5 },
    ],
  },
  {
    name: 'Open Command Prompt & Check Git',
    desc: 'Win+R -> cmd -> Enter -> git status',
    steps: [
      { action: 'hotkey', keys: ['win', 'r'], delay_after: 0.5 },
      { action: 'type', text: 'cmd', delay_after: 0.3 },
      { action: 'press', key: 'enter', delay_after: 0.8 },
      { action: 'type', text: 'git status', delay_after: 0.2 },
      { action: 'press', key: 'enter', delay_after: 0.2 },
    ],
  },
];

export default function ScreenTasksPanel({ workflowProgress, onExecuteWorkflow, onExecuteAction }: Props) {
  const [customText, setCustomText] = useState('');
  const [clickX, setClickX] = useState('');
  const [clickY, setClickY] = useState('');

  const handleQuickClick = () => {
    const x = parseInt(clickX, 10);
    const y = parseInt(clickY, 10);
    if (!isNaN(x) && !isNaN(y)) {
      onExecuteAction({ action: 'click', x, y });
    }
  };

  const handleQuickType = () => {
    if (customText.trim()) {
      onExecuteAction({ action: 'type', text: customText });
      setCustomText('');
    }
  };

  return (
    <div className="flex h-full w-full gap-5 p-6 font-sans">
      {/* Preset Workflows */}
      <div className="flex w-96 shrink-0 flex-col rounded-2xl border border-white/10 bg-[#070913]/80 p-5 backdrop-blur-xl shadow-2xl">
        <div className="flex items-center gap-2 pb-3 border-b border-white/10">
          <span className="p-1.5 rounded-lg bg-[#3DFFA8]/20 text-[#3DFFA8] border border-[#3DFFA8]/30">
            <Layers size={16} />
          </span>
          <div>
            <div className="font-display text-sm font-bold tracking-wider text-white">AUTONOMOUS SCREEN TASKS</div>
            <div className="text-[11px] font-mono text-white/76">Desktop mouse & keyboard automation</div>
          </div>
        </div>

        <div className="mt-4 flex-1 space-y-3 overflow-y-auto pr-1 custom-scrollbar">
          <div className="text-[11.5px] font-mono uppercase tracking-wider text-white/72">
            Automated Workflows
          </div>
          {PRESET_WORKFLOWS.map((wf, idx) => (
            <div
              key={idx}
              className="p-3.5 rounded-xl border border-white/10 bg-white/[0.02] hover:bg-white/[0.05] transition-all"
            >
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold text-white font-sans">{wf.name}</span>
                <button
                  onClick={() => onExecuteWorkflow(wf.steps)}
                  disabled={Boolean(workflowProgress)}
                  className="flex items-center gap-1 px-2.5 py-1 rounded-lg bg-[#3DFFA8]/15 hover:bg-[#3DFFA8]/25 text-[#3DFFA8] text-xs font-mono border border-[#3DFFA8]/30 transition-all disabled:opacity-30"
                >
                  <Play size={11} />
                  <span>Run</span>
                </button>
              </div>
              <p className="mt-1 text-[11.5px] text-white/76">{wf.desc}</p>
              <div className="mt-2 text-[11px] font-mono text-[#00E5FF]">
                {wf.steps.length} sequential step{wf.steps.length > 1 ? 's' : ''}
              </div>
            </div>
          ))}

          {/* Quick Direct Actions */}
          <div className="mt-5 pt-3 border-t border-white/10">
            <div className="text-[11.5px] font-mono uppercase tracking-wider text-white/72 mb-2">
              Direct Desktop Actions
            </div>

            {/* Quick Type */}
            <div className="space-y-1.5 mb-3">
              <label className="text-[11px] font-mono text-white/80">Type Text to Active Window</label>
              <div className="flex gap-2">
                <input
                  type="text"
                  value={customText}
                  onChange={(e) => setCustomText(e.target.value)}
                  placeholder="Type characters..."
                  className="flex-1 rounded-lg border border-white/10 bg-black/40 px-3 py-1.5 text-xs text-white placeholder-white/30 focus:border-[#3DFFA8]/50 focus:outline-none"
                  onKeyDown={(e) => e.key === 'Enter' && handleQuickType()}
                />
                <button
                  onClick={handleQuickType}
                  disabled={!customText.trim()}
                  className="px-3 py-1.5 rounded-lg bg-white/10 hover:bg-white/20 text-xs font-mono text-white border border-white/10 disabled:opacity-30"
                >
                  Type
                </button>
              </div>
            </div>

            {/* Quick Click */}
            <div className="space-y-1.5">
              <label className="text-[11px] font-mono text-white/80">Click Coordinates (X, Y)</label>
              <div className="flex gap-2">
                <input
                  type="number"
                  placeholder="X"
                  value={clickX}
                  onChange={(e) => setClickX(e.target.value)}
                  className="w-20 rounded-lg border border-white/10 bg-black/40 px-2 py-1.5 text-xs text-white placeholder-white/30 focus:border-[#3DFFA8]/50 focus:outline-none"
                />
                <input
                  type="number"
                  placeholder="Y"
                  value={clickY}
                  onChange={(e) => setClickY(e.target.value)}
                  className="w-20 rounded-lg border border-white/10 bg-black/40 px-2 py-1.5 text-xs text-white placeholder-white/30 focus:border-[#3DFFA8]/50 focus:outline-none"
                />
                <button
                  onClick={handleQuickClick}
                  disabled={!clickX || !clickY}
                  className="flex-1 px-3 py-1.5 rounded-lg bg-white/10 hover:bg-white/20 text-xs font-mono text-white border border-white/10 disabled:opacity-30"
                >
                  Click
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Right Column: Execution Monitor & Safety Safeguards */}
      <div className="flex flex-1 flex-col rounded-2xl border border-white/10 bg-[#070913]/80 p-5 backdrop-blur-xl shadow-2xl">
        <div className="flex items-center justify-between pb-3 border-b border-white/10">
          <div>
            <div className="font-display text-sm font-bold tracking-wider text-white">WORKFLOW EXECUTION MONITOR</div>
            <div className="text-[11px] font-mono text-white/76">Failsafe: Move mouse to any screen corner to abort</div>
          </div>
          {workflowProgress && (
            <span className="flex items-center gap-1.5 px-3 py-1 rounded-full bg-[#3DFFA8]/15 border border-[#3DFFA8]/30 text-xs font-mono text-[#3DFFA8] animate-pulse">
              Running Step {workflowProgress.step_index + 1} of {workflowProgress.total_steps}
            </span>
          )}
        </div>

        <div className="mt-4 flex-1 overflow-y-auto space-y-3 custom-scrollbar">
          {workflowProgress ? (
            <div className="rounded-xl border border-[#3DFFA8]/40 bg-[#3DFFA8]/5 p-5">
              <div className="flex items-center justify-between mb-3">
                <span className="text-sm font-semibold text-white font-mono">
                  Executing Action: <span className="text-[#3DFFA8]">{String(workflowProgress.step?.action || 'unknown')}</span>
                </span>
                <span className="text-xs font-mono text-white/80">
                  {Math.round(((workflowProgress.step_index + 1) / workflowProgress.total_steps) * 100)}%
                </span>
              </div>
              <div className="w-full bg-black/40 rounded-full h-2 overflow-hidden border border-white/10">
                <div
                  className="bg-gradient-to-r from-[#3DFFA8] to-[#00E5FF] h-full transition-all duration-300"
                  style={{
                    width: `${((workflowProgress.step_index + 1) / workflowProgress.total_steps) * 100}%`,
                  }}
                />
              </div>
              <div className="mt-3 p-3 rounded-lg bg-black/30 border border-white/10 text-xs font-mono text-white/80">
                {String(workflowProgress.step?.detail || 'Executing step...')}
              </div>
            </div>
          ) : (
            <div className="flex h-64 flex-col items-center justify-center text-center text-white/72">
              <MousePointer size={40} className="mb-2 opacity-30" />
              <p className="text-sm font-medium">Ready to execute autonomous tasks</p>
              <p className="text-xs text-white/65 max-w-sm mt-1">
                You can ask Orion in voice or chat: "Click on X", "Open notepad and write a summary", or run preset workflows.
              </p>
            </div>
          )}

          {/* Safety Safeguards Note */}
          <div className="mt-6 rounded-xl border border-white/10 bg-white/[0.02] p-4">
            <div className="flex items-center gap-2 text-xs font-semibold text-white/80 mb-1">
              <AlertTriangle size={14} className="text-[#FFB74D]" />
              <span>Autonomous Safeguards & Failsafe</span>
            </div>
            <p className="text-xs text-white/76 leading-relaxed">
              PyAutoGUI FailSafe is strictly enforced. If an automation moves unexpectedly, slam your mouse into any corner of the screen (0, 0) to instantly abort execution.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
