import { motion, AnimatePresence } from 'framer-motion';
import { Mic, Check, X } from 'lucide-react';

interface Props {
  active: boolean;
  transcript: string;
  onStop: () => void;
  onSubmit: (text: string) => void;
}

export default function FloatingVoiceTranscript({
  active,
  transcript,
  onStop,
  onSubmit,
}: Props) {
  if (!active) return null;

  return (
    <AnimatePresence>
      <motion.div
        initial={{ opacity: 0, scale: 0.9, y: 20 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.9, y: 15 }}
        transition={{ type: 'spring', stiffness: 300, damping: 24 }}
        className="absolute top-[82%] left-1/2 -translate-x-1/2 z-40 flex flex-col items-center justify-center max-w-xl w-full px-4 pointer-events-auto"
      >
        {/* Kinetic Floating Spoken Words (Not inside a chat input box!) */}
        <div className="relative flex flex-col items-center text-center">
          
          {/* Neon Voice Wave Halo in Light Purple */}
          <div className="relative mb-3 flex items-center justify-center">
            <div className="h-12 w-12 rounded-full border border-[#7FA6FF]/50 bg-[#7FA6FF]/15 flex items-center justify-center animate-pulse">
              <Mic size={20} className="text-[#DCE7FF]" />
            </div>
            <span className="absolute h-16 w-16 rounded-full border border-[#DCE7FF]/40 animate-ping opacity-70" />
          </div>

          {/* Floating Ethereal Text with Glowing Light Purple / Starlight Typography */}
          <div className="min-h-[48px] flex items-center justify-center px-6 py-2">
            <p className="font-sans text-base md:text-lg font-medium tracking-wide text-transparent bg-clip-text bg-gradient-to-r from-[#FFFFFF] via-[#DCE7FF] to-[#7FA6FF] drop- leading-relaxed select-text">
              {transcript || 'Receiving speech...'}
            </p>
          </div>

          {/* Minimal Floating Action Controls */}
          {transcript && (
            <motion.div
              initial={{ opacity: 0, y: 5 }}
              animate={{ opacity: 1, y: 0 }}
              className="flex items-center gap-3 mt-2"
            >
              <button
                onClick={() => {
                  onSubmit(transcript);
                  onStop();
                }}
                className="flex items-center gap-1.5 px-4 py-1.5 rounded-full border border-[#3DFFA8]/50 bg-[#3DFFA8]/15 hover:bg-[#3DFFA8]/25 text-[#3DFFA8] text-xs font-mono tracking-wider transition-all"
                title="Send instruction to Orion"
              >
                <Check size={13} />
                <span>EXECUTE SPEECH</span>
              </button>

              <button
                onClick={onStop}
                className="p-1.5 rounded-full border border-white/10 bg-white/5 hover:bg-white/10 text-dust hover:text-white transition-colors"
                title="Cancel voice mode"
              >
                <X size={14} />
              </button>
            </motion.div>
          )}

          {!transcript && (
            <span className="text-[11.5px] font-mono text-dust/85 tracking-widest mt-1">
              SPEAK FREELY · ORION IS LISTENING
            </span>
          )}
        </div>
      </motion.div>
    </AnimatePresence>
  );
}
