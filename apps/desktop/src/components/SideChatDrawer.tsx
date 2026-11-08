import { useState, useRef, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { X, Send, Bot, Sparkles, Copy, Check, Terminal, Mic, MicOff } from 'lucide-react';
import type { OrchestratorResponseEvent } from '@orion/shared-types';

interface Message {
  id: string;
  sender: 'orion' | 'user';
  text: string;
  time: string;
  code?: string;
  tier?: string;
}

interface Props {
  open: boolean;
  onClose: () => void;
  responses: OrchestratorResponseEvent[];
  onSendMessage: (text: string) => void;
  isProcessing: boolean;
  micActive: boolean;
  onToggleMic: () => void;
  voiceTranscript?: string;
}

export default function SideChatDrawer({
  open,
  onClose,
  responses,
  onSendMessage,
  isProcessing,
  micActive,
  onToggleMic,
  voiceTranscript = '',
}: Props) {
  const [inputText, setInputText] = useState('');
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // Sync incoming real-time speech into the input field instantly as you speak
  useEffect(() => {
    if (voiceTranscript) {
      setInputText(voiceTranscript);
    }
  }, [voiceTranscript]);

  // Derive conversation stream from responses
  const messages: Message[] = responses.map((r) => {
    const codeAction = (r.response.actions || []).find((a) => a.type === 'code_suggestion');
    const codeSnippet = (codeAction?.payload as any)?.code;
    return {
      id: r.request_id,
      sender: 'orion',
      text: r.response.summary,
      time: new Date((r.timestamp || Date.now() / 1000) * 1000).toLocaleTimeString([], {
        hour: '2-digit',
        minute: '2-digit',
      }),
      code: codeSnippet,
      tier: r.response.tier,
    };
  });

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages.length, open]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!inputText.trim()) return;
    onSendMessage(inputText.trim());
    setInputText('');
  };

  const handleCopy = (id: string, code: string) => {
    navigator.clipboard.writeText(code);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 2000);
  };

  return (
    <AnimatePresence>
      {open && (
        <motion.aside
          key="side-chat"
          initial={{ x: 420, opacity: 0 }}
          animate={{ x: 0, opacity: 1 }}
          exit={{ x: 420, opacity: 0 }}
          transition={{ type: 'spring', stiffness: 280, damping: 30 }}
          className="glass-strong absolute bottom-0 right-0 top-12 z-40 flex w-[400px] flex-col border-l border-glass shadow-2xl backdrop-blur-2xl"
          style={{
            boxShadow: '-12px 0 40px rgba(0,0,0,0.6), -1px 0 0 rgba(138, 154, 181, 0.25)',
          }}
        >
          {/* Header */}
          <header className="flex items-center justify-between border-b border-glass px-5 py-3.5 bg-cosmos/60">
            <div className="flex items-center gap-2.5">
              <div className="relative">
                <Bot size={18} className="text-pulse" />
                <span className="absolute -bottom-0.5 -right-0.5 h-2 w-2 rounded-full bg-success shadow-[0_0_8px_#3DFFA8]" />
              </div>
              <div>
                <div className="font-display text-xs font-bold tracking-widest text-starlight">
                  ORION INTELLIGENCE
                </div>
                <div className="text-[11px] text-dust font-mono flex items-center gap-1.5">
                  <span>Neural Chat Thread</span>
                  {micActive && (
                    <span className="text-danger flex items-center gap-1 animate-pulse font-semibold">
                      • Listening live...
                    </span>
                  )}
                </div>
              </div>
            </div>
            <button
              onClick={onClose}
              className="rounded-md p-1.5 text-dust hover:bg-white/5 hover:text-starlight"
            >
              <X size={16} />
            </button>
          </header>

          {/* Messages Feed */}
          <div className="flex-1 space-y-3.5 overflow-y-auto px-4 py-4">
            {messages.length === 0 ? (
              <div className="flex h-full flex-col items-center justify-center p-6 text-center text-dust">
                <Sparkles size={28} className="text-pulse/50 mb-3 animate-pulse" />
                <p className="text-xs text-starlight/80">Orion is standing by.</p>
                <p className="text-[11.5px] text-dust/85 mt-1 max-w-[240px]">
                  Click the mic to speak directly, and Orion will type your words live.
                </p>
              </div>
            ) : (
              messages.map((m) => (
                <motion.div
                  key={m.id}
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  className="space-y-1.5"
                >
                  <div className="flex items-center justify-between text-[11px] text-dust px-1">
                    <span className="flex items-center gap-1 font-mono text-pulse">
                      <Bot size={12} /> Orion {m.tier ? `(${m.tier})` : ''}
                    </span>
                    <span>{m.time}</span>
                  </div>

                  <div className="rounded-xl border border-glass bg-white/[0.03] p-3 text-xs text-starlight leading-relaxed shadow-lg">
                    <p className="selectable">{m.text}</p>

                    {m.code && (
                      <div className="mt-2.5 relative rounded bg-black/75 p-2.5 font-mono text-[11.5px] text-starlight border border-glass">
                        <button
                          onClick={() => handleCopy(m.id, m.code!)}
                          className="absolute right-2 top-2 rounded p-1 text-dust hover:text-pulse bg-white/5 hover:bg-white/10"
                          title="Copy snippet"
                        >
                          {copiedId === m.id ? <Check size={12} /> : <Copy size={12} />}
                        </button>
                        <pre className="selectable overflow-x-auto pr-6">
                          <code>{m.code}</code>
                        </pre>
                      </div>
                    )}
                  </div>
                </motion.div>
              ))
            )}

            {isProcessing && (
              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                className="flex items-center gap-2 rounded-lg border border-glass bg-white/[0.02] p-2.5 text-xs text-dust"
              >
                <Terminal size={13} className="text-pulse animate-spin" />
                <span>Orion is synthesizing neural context...</span>
              </motion.div>
            )}

            <div ref={messagesEndRef} />
          </div>

          {/* Prompt Input Footer with Instant Mic Streaming */}
          <form
            onSubmit={handleSubmit}
            className="border-t border-glass p-3 bg-cosmos/80 flex items-center gap-2"
          >
            <button
              type="button"
              onClick={onToggleMic}
              title={micActive ? 'Stop Listening' : 'Speak to Orion'}
              className={`p-2 rounded-lg transition-colors ${
                micActive
                  ? 'text-danger bg-danger/20 shadow-[0_0_12px_#FF5C8A] animate-pulse'
                  : 'text-dust hover:text-pulse hover:bg-white/5'
              }`}
            >
              {micActive ? <MicOff size={16} /> : <Mic size={16} />}
            </button>

            <input
              type="text"
              value={inputText}
              onChange={(e) => setInputText(e.target.value)}
              placeholder={micActive ? 'Listening to your voice...' : 'Type or speak to Orion...'}
              className={`selectable flex-1 rounded-lg border bg-white/[0.03] px-3 py-2 text-xs text-starlight placeholder:text-dust focus:outline-none transition-colors ${
                micActive
                  ? 'border-danger/60 ring-1 ring-danger/40'
                  : 'border-glass focus:border-pulse/50'
              }`}
            />

            <button
              type="submit"
              disabled={!inputText.trim()}
              className="rounded-lg p-2 text-dust hover:text-pulse hover:bg-white/5 disabled:opacity-30 transition-colors"
            >
              <Send size={15} />
            </button>
          </form>
        </motion.aside>
      )}
    </AnimatePresence>
  );
}
