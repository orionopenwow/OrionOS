import { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Sparkles, Code2, Globe, Gamepad2, Terminal, X, Copy, Check, ArrowRight } from 'lucide-react';
import type { ProactiveInsightEvent } from '@orion/shared-types';
import '../styles/glass.css';

interface Props {
  insights: ProactiveInsightEvent[];
  onDismiss: (id: string) => void;
  onApplyAction?: (actionText: string) => void;
  onAskOrion?: (prompt: string) => void;
}

export default function ProactiveInsightCard({ insights, onDismiss, onApplyAction, onAskOrion }: Props) {
  const [copiedId, setCopiedId] = useState<string | null>(null);

  if (!insights || insights.length === 0) return null;

  const current = insights[0];

  const getContextIcon = (type: string) => {
    switch (type) {
      case 'code':
        return <Code2 className="text-[#3DFFA8]" size={16} />;
      case 'game_dev':
        return <Gamepad2 className="text-[#C084FC]" size={16} />;
      case 'browser_docs':
      case 'browser_article':
        return <Globe className="text-[#00E5FF]" size={16} />;
      default:
        return <Terminal className="text-[#E9D5FF]" size={16} />;
    }
  };

  const handleCopy = (text: string, id: string) => {
    navigator.clipboard.writeText(text);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 2000);
  };

  return (
    <AnimatePresence>
      <motion.div
        key={current.id}
        initial={{ opacity: 0, y: -20, scale: 0.95 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        exit={{ opacity: 0, y: -20, scale: 0.95 }}
        transition={{ type: 'spring', stiffness: 350, damping: 25 }}
        className="orion-glass orion-glass--dense pointer-events-auto absolute top-16 right-6 z-40 w-96 p-4"
        style={{
          boxShadow: '0 0 25px rgba(0, 229, 255, 0.15), inset 0 0 15px rgba(192, 132, 252, 0.1)',
        }}
      >
        <div className="flex items-center justify-between pb-2 border-b border-white/5">
          <div className="flex items-center gap-2">
            <span className="p-1.5 rounded-lg bg-white/5 border border-white/10">
              {getContextIcon(current.context_type)}
            </span>
            <div>
              <div className="flex items-center gap-1.5">
                <span className="text-[11px] font-mono uppercase tracking-wider text-[#00E5FF]">
                  Proactive {current.context_type.replace('_', ' ')}
                </span>
                <Sparkles size={12} className="text-[#C084FC] animate-pulse" />
              </div>
              <div className="text-xs font-semibold text-white/90 truncate max-w-[220px]" title={current.window_title}>
                {current.window_title || current.title}
              </div>
            </div>
          </div>
          <button
            onClick={() => onDismiss(current.id)}
            className="rounded-lg p-1 text-white/40 hover:bg-white/10 hover:text-white transition-colors"
            title="Dismiss"
          >
            <X size={14} />
          </button>
        </div>

        <div className="mt-3 text-xs text-white/80 leading-relaxed font-sans whitespace-pre-line line-clamp-4">
          {current.summary}
        </div>

        {/* Dynamic Action Chips */}
        {current.actions && current.actions.length > 0 && onApplyAction && (
          <div className="mt-2.5 flex flex-wrap gap-1">
            {current.actions.map((act, idx) => (
              <button
                key={idx}
                onClick={() => onApplyAction(JSON.stringify(act.payload))}
                className="px-2 py-0.5 rounded-md text-[10px] font-mono bg-white/5 hover:bg-white/10 text-[#3DFFA8] border border-[#3DFFA8]/20 transition-colors"
              >
                {act.type}
              </button>
            ))}
          </div>
        )}

        {/* Action buttons */}
        <div className="mt-3 flex items-center justify-between gap-2 pt-2 border-t border-white/5">
          <button
            onClick={() => handleCopy(current.summary, current.id)}
            className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-[11px] font-mono text-white/70 bg-white/5 hover:bg-white/10 hover:text-white border border-white/10 transition-colors"
          >
            {copiedId === current.id ? (
              <>
                <Check size={12} className="text-[#3DFFA8]" />
                <span className="text-[#3DFFA8]">Copied</span>
              </>
            ) : (
              <>
                <Copy size={12} />
                <span>Copy</span>
              </>
            )}
          </button>

          {onAskOrion && (
            <button
              onClick={() => onAskOrion(`Tell me more about what you noticed in ${current.window_title}: ${current.summary}`)}
              className="flex items-center gap-1.5 px-3 py-1 rounded-lg text-[11px] font-mono text-[#00E5FF] bg-[#00E5FF]/10 hover:bg-[#00E5FF]/20 border border-[#00E5FF]/30 transition-all shadow-[0_0_10px_rgba(0,229,255,0.2)]"
            >
              <span>Explore</span>
              <ArrowRight size={12} />
            </button>
          )}
        </div>
      </motion.div>
    </AnimatePresence>
  );
}
