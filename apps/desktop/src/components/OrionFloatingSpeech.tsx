import { motion, AnimatePresence } from 'framer-motion';
import { Sparkles, Terminal } from 'lucide-react';

interface Props {
  taskText?: string;
  isProcessing: boolean;
  latestResponse?: string;
}

export default function OrionFloatingSpeech({ taskText, isProcessing, latestResponse }: Props) {
  // Determine what to display above Orion's head
  const displayText =
    taskText ||
    (isProcessing ? 'Synthesizing neural intent & document analysis...' : latestResponse);

  if (!displayText && !isProcessing) return null;

  return (
    <AnimatePresence>
      <motion.div
        initial={{ opacity: 0, y: 15, scale: 0.95 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        exit={{ opacity: 0, y: -10, scale: 0.95 }}
        transition={{ type: 'spring', stiffness: 350, damping: 25 }}
        className="absolute -top-24 left-1/2 -translate-x-1/2 z-30 pointer-events-none select-none max-w-md w-max"
      >
        {/* Ornate Gold & Lavender Filigree Wings Accent */}
        <div className="flex items-center justify-center -mb-1 opacity-80">
          <svg width="120" height="14" viewBox="0 0 120 14" fill="none">
            <path
              d="M 5 10 Q 35 2, 55 8 Q 58 9, 60 4 Q 62 9, 65 8 Q 85 2, 115 10"
              stroke="rgba(249, 214, 137, 0.7)"
              strokeWidth="1.2"
              fill="none"
            />
            <polygon points="60,2 64,8 60,13 56,8" fill="#C084FC" stroke="#F9D689" strokeWidth="0.8" />
          </svg>
        </div>

        {/* Floating Ethereal Pill Bubble */}
        <div
          className="relative px-5 py-2.5 rounded-full border border-[#C084FC]/40 bg-gradient-to-r from-[#0B0D17]/90 via-[#1a1333]/85 to-[#0B0D17]/90 backdrop-blur-2xl shadow-[0_0_30px_rgba(192,132,252,0.25)] flex items-center gap-2.5 text-center"
        >
          {isProcessing ? (
            <Terminal size={14} className="text-[#00E5FF] animate-spin shrink-0" />
          ) : (
            <Sparkles size={14} className="text-[#3DFFA8] shrink-0 animate-pulse" />
          )}

          <span className="font-sans text-xs tracking-wide text-starlight leading-tight drop-shadow-sm line-clamp-2">
            {displayText}
          </span>
        </div>

        {/* Delicate Connecting Pointer Glow Line to Orion */}
        <div className="mx-auto w-[1px] h-3 bg-gradient-to-b from-[#C084FC]/60 to-transparent" />
      </motion.div>
    </AnimatePresence>
  );
}
