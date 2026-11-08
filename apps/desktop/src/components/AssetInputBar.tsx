import { Mic, MicOff, Search, Send, FileText } from 'lucide-react';

interface Props {
  value: string;
  onChange: (val: string) => void;
  onSubmit: (e: React.FormEvent) => void;
  micActive: boolean;
  onToggleMic: () => void;
  placeholder?: string;
  onOpenContext?: () => void;
  onSearchClick?: () => void;
}

export default function AssetInputBar({
  value,
  onChange,
  onSubmit,
  micActive,
  onToggleMic,
  placeholder = 'Ask Orion, trigger automation...',
  onOpenContext,
  onSearchClick,
}: Props) {
  return (
    <div className="relative flex items-center justify-center w-full max-w-xl mx-auto py-2">
      
      {/* 4 Corner Bracket Accents matching reference asset sheet */}
      <span className="absolute -top-1 -left-2 text-xs font-mono text-[#00E5FF]/40 select-none">┌</span>
      <span className="absolute -top-1 -right-2 text-xs font-mono text-[#00E5FF]/40 select-none">┐</span>
      <span className="absolute -bottom-1 -left-2 text-xs font-mono text-[#00E5FF]/40 select-none">└</span>
      <span className="absolute -bottom-1 -right-2 text-xs font-mono text-[#00E5FF]/40 select-none">┘</span>

      <form onSubmit={onSubmit} className="flex items-center gap-2.5 w-full">
        
        {/* Main Glowing Capsule Input Container */}
        <div
          className={`relative flex-1 flex items-center gap-2 rounded-full px-4 py-2.5 backdrop-blur-2xl transition-all duration-300 ${
            micActive
              ? 'border-2 border-[#FF5C8A] bg-[#FF5C8A]/10 shadow-[0_0_20px_rgba(255,92,138,0.35)]'
              : 'border border-cyan-400/35 bg-gradient-to-r from-[#00E5FF]/10 via-[#3DFFA8]/5 to-[#7FA6FF]/10 shadow-[0_0_20px_rgba(0,229,255,0.15)] focus-within:border-[#00E5FF] focus-within:shadow-[0_0_25px_rgba(0,229,255,0.35)]'
          }`}
        >
          {/* Microphone Icon Button on Left */}
          <button
            type="button"
            onClick={onToggleMic}
            className={`p-1.5 rounded-full transition-all ${
              micActive
                ? 'text-[#FF5C8A] bg-[#FF5C8A]/20 shadow-[0_0_12px_#FF5C8A] animate-pulse'
                : 'text-dust hover:text-[#00E5FF] hover:bg-white/5'
            }`}
            title={micActive ? 'Stop Listening' : 'Speak to Orion'}
          >
            {micActive ? <MicOff size={16} /> : <Mic size={16} />}
          </button>

          {/* Text Input */}
          <input
            type="text"
            value={value}
            onChange={(e) => onChange(e.target.value)}
            placeholder={micActive ? 'Listening to your voice live...' : placeholder}
            className="selectable flex-1 bg-transparent text-xs text-starlight placeholder:text-dust/70 focus:outline-none font-sans"
          />

          {/* Inside Right: Document / Context List Icon */}
          <button
            type="button"
            onClick={onOpenContext}
            className="p-1 text-dust hover:text-[#3DFFA8] transition-colors"
            title="Inspect Context & Memory"
          >
            <FileText size={15} />
          </button>
        </div>

        {/* External Circular Search Button matching reference sheet */}
        <button
          type="button"
          onClick={onSearchClick}
          className="h-10 w-10 shrink-0 rounded-full border border-cyan-400/35 bg-gradient-to-b from-[#00E5FF]/15 to-transparent flex items-center justify-center text-dust hover:text-[#00E5FF] hover:border-[#00E5FF] hover:shadow-[0_0_15px_rgba(0,229,255,0.4)] transition-all"
          title="Search Memory & Web"
        >
          <Search size={16} />
        </button>

        {/* External Circular Send Button matching reference sheet */}
        <button
          type="submit"
          disabled={!value.trim()}
          className="h-10 w-10 shrink-0 rounded-full border border-[#7FA6FF]/40 bg-gradient-to-b from-[#7FA6FF]/25 to-transparent flex items-center justify-center text-dust hover:text-[#7FA6FF] hover:border-[#7FA6FF] hover:shadow-[0_0_15px_rgba(127,166,255,0.4)] transition-all disabled:opacity-30 disabled:pointer-events-none"
          title="Send to Orion"
        >
          <Send size={15} className="ml-0.5" />
        </button>
      </form>
    </div>
  );
}
