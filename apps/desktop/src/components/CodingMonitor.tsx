import { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Code2, Bug, Check, Copy, Terminal } from 'lucide-react';
import type { OrchestratorResponseEvent } from '@orion/shared-types';

interface Props {
  responses: OrchestratorResponseEvent[];
  activeWindowTitle?: string | null;
}

export default function CodingMonitor({ responses, activeWindowTitle }: Props) {
  const [copiedId, setCopiedId] = useState<string | null>(null);

  // Extract recent coding suggestion actions from responses
  const codingEvents = responses.filter(
    (r) =>
      r.response.intent === 'coding' ||
      r.response.actions.some((a) => a.type === 'code_suggestion')
  );

  const handleCopy = (id: string, text: string) => {
    navigator.clipboard.writeText(text);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 2000);
  };

  const isIdeFocused = activeWindowTitle
    ? /(visual studio|vs ?code|\bcode\b|cursor|pycharm|intellij|webstorm|rider|neovim|\bvim\b)/i.test(
        activeWindowTitle
      )
    : false;

  return (
    <div className="glass flex h-full flex-col p-4">
      <div className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Code2 size={14} className="text-pulse" />
          <div className="panel-title">Active Coding Monitor</div>
        </div>
        <div className="flex items-center gap-1.5 text-[11px]">
          <span
            className={`inline-block h-1.5 w-1.5 rounded-full ${
              isIdeFocused ? 'bg-success' : 'bg-dust'
            }`}
          />
          <span className="text-dust font-mono truncate max-w-[150px]">
            {activeWindowTitle ? activeWindowTitle : 'No IDE Focus'}
          </span>
        </div>
      </div>

      <div className="flex-1 space-y-3 overflow-y-auto pr-1">
        {codingEvents.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center text-center p-4">
            <Terminal size={24} className="text-dust/72 mb-2" />
            <p className="text-xs text-dust">No active code suggestions or repairs.</p>
            <p className="text-[11px] text-dust/80 mt-1">
              Switch focus to an IDE window with the Coding Agent enabled to observe live AST analysis.
            </p>
          </div>
        ) : (
          <AnimatePresence initial={false}>
            {codingEvents.map((ev) => {
              const codeAction = ev.response.actions.find((a) => a.type === 'code_suggestion');
              const codePayload = (codeAction?.payload as {
                code?: string;
                explanation?: string;
                language?: string;
              }) || { code: '', explanation: '', language: 'ts' };

              return (
                <motion.div
                  key={ev.request_id}
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0 }}
                  className="rounded-lg border border-glass bg-white/[0.02] p-3 space-y-2 text-xs"
                >
                  <div className="flex items-center justify-between">
                    <span className="flex items-center gap-1.5 font-display text-[11px] text-pulse">
                      <Bug size={12} />
                      AGENT SUGGESTION ({codePayload.language || 'code'})
                    </span>
                    <span className="text-[11px] font-mono text-dust">
                      {ev.response.latency_ms ? `${ev.response.latency_ms}ms` : ''}
                    </span>
                  </div>

                  <p className="selectable text-starlight/90 text-[11.5px] leading-relaxed">
                    {codePayload.explanation || ev.response.summary}
                  </p>

                  {codePayload.code && (
                    <div className="relative rounded bg-black/60 p-2.5 font-mono text-[11.5px] text-starlight border border-glass">
                      <button
                        onClick={() => handleCopy(ev.request_id, codePayload.code!)}
                        className="absolute right-2 top-2 rounded p-1 text-dust hover:text-pulse bg-white/5 hover:bg-white/10"
                        title="Copy snippet"
                      >
                        {copiedId === ev.request_id ? <Check size={12} /> : <Copy size={12} />}
                      </button>
                      <pre className="selectable overflow-x-auto pr-6">
                        <code>{codePayload.code}</code>
                      </pre>
                    </div>
                  )}
                </motion.div>
              );
            })}
          </AnimatePresence>
        )}
      </div>
    </div>
  );
}
