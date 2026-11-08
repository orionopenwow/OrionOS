import { useState } from 'react';
import {
  Gamepad2,
  Bug,
  Sparkles,
  Send,
  Copy,
  Check,
  SendHorizontal,
} from 'lucide-react';
import type { GameBugRecord } from '@orion/shared-types';

interface Props {
  bugs: GameBugRecord[];
  onSendResponse: (bugId: string, response: string) => void;
  onSendTelegram?: (text: string) => void;
}

export default function GameBugTrackerHub({ bugs, onSendResponse, onSendTelegram }: Props) {
  const [selectedBugId, setSelectedBugId] = useState<string | null>(bugs[0]?.id || null);
  const [responseText, setResponseText] = useState('');
  const [copiedSnippet, setCopiedSnippet] = useState(false);
  const [telegramSent, setTelegramSent] = useState(false);

  const selectedBug = bugs.find((b) => b.id === selectedBugId) || bugs[0];

  const handleCopySnippet = (snippet: string) => {
    navigator.clipboard.writeText(snippet);
    setCopiedSnippet(true);
    setTimeout(() => setCopiedSnippet(false), 2000);
  };

  const handleSendReply = () => {
    if (!selectedBug || !responseText.trim()) return;
    onSendResponse(selectedBug.id, responseText.trim());
    setResponseText('');
  };

  const handleForwardTelegram = () => {
    if (!selectedBug || !onSendTelegram) return;
    const msg = `🎮 *Game Bug Report #${selectedBug.id}*\n*Reporter:* ${selectedBug.author}\n*Issue:* ${selectedBug.description}\n\n*Gemini Diagnosis:* ${selectedBug.diagnosis || 'None'}`;
    onSendTelegram(msg);
    setTelegramSent(true);
    setTimeout(() => setTelegramSent(false), 2500);
  };

  return (
    <div className="flex h-full w-full gap-5 p-6 font-sans">
      {/* Left Column: Bug List */}
      <div className="flex w-80 shrink-0 flex-col rounded-2xl border border-white/10 bg-[#070913]/80 p-4 backdrop-blur-xl shadow-2xl">
        <div className="flex items-center justify-between pb-3 border-b border-white/10">
          <div className="flex items-center gap-2">
            <span className="p-1.5 rounded-lg bg-[#C084FC]/20 text-[#C084FC] border border-[#C084FC]/30">
              <Gamepad2 size={16} />
            </span>
            <div>
              <div className="font-display text-sm font-bold tracking-wider text-white">DISCORD BUG HUB</div>
              <div className="text-[10px] font-mono text-white/50">Auto-captured from Discord & Gemini</div>
            </div>
          </div>
          <span className="px-2 py-0.5 rounded-full bg-white/5 border border-white/10 text-[11px] font-mono text-[#3DFFA8]">
            {bugs.length} {bugs.length === 1 ? 'bug' : 'bugs'}
          </span>
        </div>

        <div className="mt-3 flex-1 overflow-y-auto space-y-2 pr-1 custom-scrollbar">
          {bugs.length === 0 ? (
            <div className="flex h-48 flex-col items-center justify-center text-center text-white/40">
              <Bug size={32} className="mb-2 opacity-30" />
              <p className="text-xs">No game bugs reported yet.</p>
              <p className="text-[10px] text-white/30 mt-1">
                Post in your Discord #bug-reports channel or mention `!bug`.
              </p>
            </div>
          ) : (
            bugs.map((b) => (
              <button
                key={b.id}
                onClick={() => setSelectedBugId(b.id)}
                className={`w-full text-left p-3 rounded-xl border transition-all ${
                  selectedBug?.id === b.id
                    ? 'border-[#C084FC]/50 bg-[#C084FC]/10 shadow-[0_0_15px_rgba(192,132,252,0.15)]'
                    : 'border-white/5 bg-white/[0.02] hover:bg-white/[0.05] hover:border-white/10'
                }`}
              >
                <div className="flex items-center justify-between">
                  <span className="font-mono text-xs font-semibold text-[#00E5FF]">#{b.id}</span>
                  <span
                    className={`text-[9px] font-mono px-1.5 py-0.5 rounded-full uppercase ${
                      b.status === 'resolved'
                        ? 'bg-[#3DFFA8]/20 text-[#3DFFA8] border border-[#3DFFA8]/30'
                        : 'bg-[#FFB74D]/20 text-[#FFB74D] border border-[#FFB74D]/30'
                    }`}
                  >
                    {b.status}
                  </span>
                </div>
                <div className="mt-1 text-xs text-white/90 line-clamp-2 font-medium">{b.description}</div>
                <div className="mt-2 flex items-center justify-between text-[10px] text-white/40 font-mono">
                  <span>{b.author}</span>
                  <span>{b.channel}</span>
                </div>
              </button>
            ))
          )}
        </div>
      </div>

      {/* Right Column: Detailed Diagnosis & Actions */}
      <div className="flex flex-1 flex-col rounded-2xl border border-white/10 bg-[#070913]/80 p-5 backdrop-blur-xl shadow-2xl overflow-y-auto custom-scrollbar">
        {selectedBug ? (
          <div className="space-y-4">
            {/* Header info */}
            <div className="flex items-center justify-between pb-3 border-b border-white/10">
              <div>
                <div className="flex items-center gap-2">
                  <span className="text-lg font-bold font-display text-white">Bug #{selectedBug.id}</span>
                  <span className="text-xs font-mono text-[#00E5FF] bg-[#00E5FF]/10 px-2 py-0.5 rounded-md border border-[#00E5FF]/20">
                    {selectedBug.game}
                  </span>
                </div>
                <div className="text-xs text-white/50 font-mono mt-0.5">
                  Reported by <span className="text-white/80">{selectedBug.author}</span> in{' '}
                  <span className="text-white/80">{selectedBug.channel}</span>
                </div>
              </div>

              {onSendTelegram && (
                <button
                  onClick={handleForwardTelegram}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-mono text-[#00E5FF] bg-[#00E5FF]/10 hover:bg-[#00E5FF]/20 border border-[#00E5FF]/30 transition-all"
                  title="Forward this bug to Telegram"
                >
                  <SendHorizontal size={13} />
                  <span>{telegramSent ? 'Sent to Phone!' : 'Text to Telegram'}</span>
                </button>
              )}
            </div>

            {/* Issue Description */}
            <div className="rounded-xl border border-white/10 bg-white/[0.02] p-4">
              <div className="text-[11px] font-mono uppercase tracking-wider text-white/50 mb-1">
                Player Description
              </div>
              <p className="text-sm text-white/90 leading-relaxed whitespace-pre-wrap">
                {selectedBug.description}
              </p>
            </div>

            {/* Gemini Search & Diagnosis */}
            <div className="rounded-xl border border-[#00E5FF]/30 bg-gradient-to-br from-[#00E5FF]/5 to-transparent p-4 shadow-[0_0_20px_rgba(0,229,255,0.06)]">
              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center gap-2">
                  <Sparkles size={16} className="text-[#00E5FF]" />
                  <span className="text-xs font-mono uppercase tracking-wider font-semibold text-[#00E5FF]">
                    Gemini Search Diagnosis
                  </span>
                </div>
                <span className="text-[10px] font-mono text-white/40">In-line AI Analysis</span>
              </div>
              <div className="text-xs text-white/80 leading-relaxed font-sans whitespace-pre-line">
                {selectedBug.diagnosis || 'Analyzing bug reports against documentation and engine telemetry...'}
              </div>
            </div>

            {/* Ready-to-paste Code Snippet */}
            {selectedBug.fix_snippet && (
              <div className="rounded-xl border border-[#3DFFA8]/30 bg-[#3DFFA8]/5 p-4">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs font-mono uppercase tracking-wider font-semibold text-[#3DFFA8]">
                    Ready-To-Paste Fix Snippet
                  </span>
                  <button
                    onClick={() => handleCopySnippet(selectedBug.fix_snippet!)}
                    className="flex items-center gap-1 px-2.5 py-1 rounded-lg bg-[#3DFFA8]/10 hover:bg-[#3DFFA8]/20 text-[#3DFFA8] text-xs font-mono transition-colors border border-[#3DFFA8]/30"
                  >
                    {copiedSnippet ? <Check size={12} /> : <Copy size={12} />}
                    <span>{copiedSnippet ? 'Copied' : 'Paste Fix'}</span>
                  </button>
                </div>
                <pre className="p-3 rounded-lg bg-black/40 border border-white/10 text-xs font-mono text-[#3DFFA8] overflow-x-auto">
                  {selectedBug.fix_snippet}
                </pre>
              </div>
            )}

            {/* Response Section */}
            <div className="rounded-xl border border-white/10 bg-white/[0.02] p-4">
              <div className="text-[11px] font-mono uppercase tracking-wider text-white/50 mb-2">
                Send Response to Discord
              </div>
              <div className="flex gap-2">
                <input
                  type="text"
                  value={responseText}
                  onChange={(e) => setResponseText(e.target.value)}
                  placeholder="Type an official reply or workaround to post into Discord..."
                  className="flex-1 rounded-xl border border-white/10 bg-black/30 px-3.5 py-2 text-xs text-white placeholder-white/30 focus:border-[#C084FC]/50 focus:outline-none"
                  onKeyDown={(e) => e.key === 'Enter' && handleSendReply()}
                />
                <button
                  onClick={handleSendReply}
                  disabled={!responseText.trim()}
                  className="flex items-center gap-1.5 px-4 py-2 rounded-xl text-xs font-mono font-medium text-white bg-gradient-to-r from-[#C084FC] to-[#5865F2] hover:opacity-90 disabled:opacity-40 transition-all shadow-md"
                >
                  <Send size={13} />
                  <span>Send</span>
                </button>
              </div>
              {selectedBug.developer_response && (
                <div className="mt-3 p-2.5 rounded-lg bg-white/5 border border-white/10 text-xs text-white/70">
                  <span className="text-[10px] font-mono text-[#3DFFA8] block mb-0.5">Last response sent:</span>
                  {selectedBug.developer_response}
                </div>
              )}
            </div>
          </div>
        ) : (
          <div className="flex h-full flex-col items-center justify-center text-white/40">
            <Gamepad2 size={48} className="mb-3 opacity-20" />
            <p className="text-sm font-medium">Select a bug to inspect Gemini diagnosis</p>
          </div>
        )}
      </div>
    </div>
  );
}
