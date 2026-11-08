import { useState, useRef, useEffect } from 'react';
import { motion } from 'framer-motion';
import {
  MessageSquare,
  ChevronLeft,
  ChevronsRight,
  Copy,
  Check,
  Bot,
  Sparkles,
  Terminal,
} from 'lucide-react';
import GlassWindow from './glass/GlassWindow';
import AssetInputBar from './AssetInputBar';
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
  responses: OrchestratorResponseEvent[];
  onSendMessage: (text: string) => void;
  isProcessing: boolean;
  micActive: boolean;
  onToggleMic: () => void;
  voiceTranscript?: string;
  onClose?: () => void;
}

export default function ChatPanelFrame({
  responses,
  onSendMessage,
  isProcessing,
  micActive,
  onToggleMic,
  voiceTranscript = '',
  onClose,
}: Props) {
  const [inputText, setInputText] = useState('');
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [pageIndex, setPageIndex] = useState(0);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // Sync real-time spoken transcripts into the input field
  useEffect(() => {
    if (voiceTranscript) {
      setInputText(voiceTranscript);
    }
  }, [voiceTranscript]);

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
  }, [messages.length]);

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
    <GlassWindow
      id="chat-frame"
      title="Orion"
      icon={<MessageSquare size={14} className="text-[#8DB0FF]" />}
      subtitle={micActive ? <span className="text-[#FF8AAD]">• Listening…</span> : undefined}
      onClose={onClose ?? (() => {})}
      maximisable={false}
      className="w-full max-w-3xl mx-auto my-3"
      style={{ position: 'relative' }}
      bodyClassName="px-5 pb-4"
    >
        {/* Conversation Feed */}
        <div className="my-4 max-h-72 min-h-[160px] overflow-y-auto space-y-3 pr-2">
          {messages.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-8 text-center text-dust">
              <Sparkles size={26} className="text-[#00E5FF] mb-2 animate-pulse" />
              <p className="text-xs text-starlight/90 font-medium">Chat ready</p>
              <p className="text-[11.5px] text-dust/85 max-w-sm mt-1">
                Ask Orion to orchestrate tasks, analyze code, inspect the screen, or import data.
              </p>
            </div>
          ) : (
            messages.map((m) => (
              <motion.div
                key={m.id}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                className="space-y-1.5"
              >
                <div className="flex items-center justify-between text-[11px] text-dust px-1">
                  <span className="flex items-center gap-1 font-mono text-[#00E5FF]">
                    <Bot size={12} /> Orion {m.tier ? `(${m.tier})` : ''}
                  </span>
                  <span className="font-mono">{m.time}</span>
                </div>

                <div className="rounded-xl border border-white/10 bg-white/[0.04] p-3 text-xs text-starlight leading-relaxed shadow-md">
                  <p className="selectable">{m.text}</p>

                  {m.code && (
                    <div className="mt-2.5 relative rounded-lg bg-black/80 p-2.5 font-mono text-[11.5px] text-starlight border border-white/10">
                      <button
                        onClick={() => handleCopy(m.id, m.code!)}
                        className="absolute right-2 top-2 rounded p-1 text-dust hover:text-[#00E5FF] bg-white/5 hover:bg-white/10"
                        title="Copy code snippet"
                      >
                        {copiedId === m.id ? <Check size={12} className="text-[#3DFFA8]" /> : <Copy size={12} />}
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
            <div className="flex items-center gap-2 rounded-lg border border-[#00E5FF]/20 bg-[#00E5FF]/5 p-2.5 text-xs text-[#00E5FF]">
              <Terminal size={13} className="animate-spin" />
              <span>Synthesizing neural intent & memories...</span>
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>

        {/* Bottom Navigation Capsule matching reference sheet (chamfered button with [ < ] [ >> ]) */}
        <div className="flex items-center justify-between pt-2 border-t border-white/[0.06]">
          <div className="text-[11px] font-mono text-dust flex items-center gap-2">
            <span>THREAD STATUS:</span>
            <span className="text-[#3DFFA8]">REAL-TIME ACTIVE (PAGE {pageIndex + 1})</span>
          </div>

          <div className="flex items-center gap-1 px-1.5 py-0.5 rounded-lg border border-[#8A9AB5]/40 bg-white/5">
            <button
              onClick={() => setPageIndex((p) => Math.max(0, p - 1))}
              className="p-1 text-dust hover:text-starlight rounded hover:bg-white/10 transition-colors"
              title="Previous Message"
            >
              <ChevronLeft size={13} />
            </button>
            <button
              onClick={() => setPageIndex((p) => p + 1)}
              className="p-1 text-dust hover:text-starlight rounded hover:bg-white/10 transition-colors"
              title="Fast-forward / Next"
            >
              <ChevronsRight size={13} />
            </button>
          </div>
        </div>

        {/* Asset Input Bar */}
        <div className="mt-2">
          <AssetInputBar
            value={inputText}
            onChange={setInputText}
            onSubmit={handleSubmit}
            micActive={micActive}
            onToggleMic={onToggleMic}
            placeholder="Ask Orion, trigger automation..."
          />
        </div>

    </GlassWindow>
  );
}
