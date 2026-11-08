import { useState, useRef, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  MessageSquare,
  History,
  FolderGit2,
  X,
  Bot,
  Copy,
  Check,
  Send,
  Sparkles,
  Search,
  Plus,
  Terminal,
  Activity,
  SendHorizontal,
  ChevronRight,
  Mic,
  Monitor,
  Gamepad2,
  Database,
  ExternalLink,
} from 'lucide-react';
import type { OrchestratorResponseEvent, MemoryRecord, PersonalityMode } from '@orion/shared-types';
import type { FeedItem } from '../hooks/useOrionSocket';

interface Props {
  open: boolean;
  onClose: () => void;
  responses: OrchestratorResponseEvent[];
  feed: FeedItem[];
  memories: MemoryRecord[];
  onSendMessage: (text: string) => void;
  isProcessing: boolean;
  activeWindow?: string | null;
  onSendTelegram?: (text: string) => void;
  micActive?: boolean;
  onToggleMic?: () => void;
  activePersonality?: string;
  onSetPersonality?: (mode: PersonalityMode) => void;
  latestBrowserTab?: { title: string; url: string } | null;
}

type ConsoleView = 'chat' | 'history' | 'project';

interface ConvoSession {
  id: string;
  title: string;
  preview: string;
  timestamp: string;
  messageCount: number;
  active?: boolean;
}

export default function LeftDialogueConsole({
  open,
  onClose,
  responses,
  feed,
  memories,
  onSendMessage,
  isProcessing,
  activeWindow,
  onSendTelegram,
  micActive = false,
  onToggleMic,
  activePersonality = 'cosmic_vanguard',
  onSetPersonality,
  latestBrowserTab,
}: Props) {
  const [activeView, setActiveView] = useState<ConsoleView>('chat');
  const [inputText, setInputText] = useState('');
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [historySearch, setHistorySearch] = useState('');
  const [activeSessionId, setActiveSessionId] = useState('session-current');
  const bottomRef = useRef<HTMLDivElement>(null);

  // Simulated & dynamic session history
  const [sessions, setSessions] = useState<ConvoSession[]>([
    {
      id: 'session-current',
      title: 'Active Orion Orchestration',
      preview: responses[0]?.response?.summary || 'Session initialized with autonomous daemon.',
      timestamp: 'Just now',
      messageCount: Math.max(1, responses.length),
      active: true,
    },
    {
      id: 'session-screen-tasks',
      title: 'Desktop Automation & Click Workflows',
      preview: 'Executing coordinate automation and hotkey workflows on Windows.',
      timestamp: '25m ago',
      messageCount: 8,
    },
    {
      id: 'session-game-bugs',
      title: 'Discord Game Bug Triage & Screengrabs',
      preview: 'Gemini inline visual diagnostic on player report #142.',
      timestamp: '2h ago',
      messageCount: 14,
    },
    {
      id: 'session-chrome-scrape',
      title: 'Chrome Live Web Extraction',
      preview: 'Deep scraping active DOM tree and storing vectors in LanceDB.',
      timestamp: 'Yesterday',
      messageCount: 19,
    },
    {
      id: 'session-vector-memory',
      title: 'Neural Vector Memory Consolidation',
      preview: 'Synthesized 35 knowledge vectors into crystallized skills.',
      timestamp: 'Oct 12',
      messageCount: 22,
    },
  ]);

  // Quick suggestions above chat input
  const quickActions = [
    {
      label: 'Inspect Screen',
      prompt: 'Inspect the current screen and report all visible windows and text.',
      icon: Monitor,
    },
    {
      label: 'Check Game Bugs',
      prompt: 'Summarize latest game bug reports and Gemini search diagnosis.',
      icon: Gamepad2,
    },
    {
      label: 'Consolidate Memories',
      prompt: 'Consolidate recent vector memories and outline open loops.',
      icon: Database,
    },
  ];

  // Auto-scroll chat on response
  useEffect(() => {
    if (activeView === 'chat') {
      bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
  }, [responses.length, activeView]);

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

  const handleNewConversation = () => {
    const newId = `session-${Date.now()}`;
    const newSession: ConvoSession = {
      id: newId,
      title: `Orion Session #${sessions.length + 1}`,
      preview: 'New conversation initiated.',
      timestamp: 'Just now',
      messageCount: 0,
    };
    setSessions([newSession, ...sessions]);
    setActiveSessionId(newId);
    setActiveView('chat');
  };

  const filteredSessions = sessions.filter(
    (s) =>
      s.title.toLowerCase().includes(historySearch.toLowerCase()) ||
      s.preview.toLowerCase().includes(historySearch.toLowerCase())
  );

  return (
    <AnimatePresence>
      {open && (
        <motion.aside
          key="left-dialogue-console"
          initial={{ x: -520, opacity: 0 }}
          animate={{ x: 0, opacity: 1 }}
          exit={{ x: -520, opacity: 0 }}
          transition={{ type: 'spring', stiffness: 290, damping: 28 }}
          className="fixed left-0 top-0 bottom-0 z-40 flex w-[460px] md:w-[500px] flex-col border-r border-[#7FA6FF]/30 bg-[#070913]/96 backdrop-blur-3xl shadow-[20px_0_60px_rgba(0,0,0,0.85)] select-none text-starlight"
        >
          {/* Top Filigree Astrological Wings Accent */}
          <div className="absolute top-0 left-1/2 -translate-x-1/2 -translate-y-1/2 pointer-events-none z-50">
            <svg width="180" height="22" viewBox="0 0 180 22" fill="none">
              <path
                d="M 10 16 Q 50 4, 80 12 Q 86 13, 90 6 Q 94 13, 100 12 Q 130 4, 170 16"
                stroke="rgba(249, 214, 137, 0.85)"
                strokeWidth="1.4"
                fill="none"
              />
              <polygon points="90,2 96,10 90,18 84,10" fill="#7FA6FF" stroke="#F9D689" strokeWidth="1" />
            </svg>
          </div>

          {/* =========================================================================
              HEADER SECTION: CONSOLE BRANDING & CLOSE
             ========================================================================= */}
          <header className="relative flex items-center justify-between border-b border-white/10 px-5 pt-4 pb-3 bg-gradient-to-r from-[#0E1022] to-[#070913]">
            <div className="flex items-center gap-3">
              <div className="relative p-2 rounded-xl bg-gradient-to-br from-[#7FA6FF]/25 to-[#2E6BFF]/15 border border-[#7FA6FF]/40 text-[#DCE7FF]">
                <Bot size={18} />
                <span className="absolute -top-1 -right-1 w-2 h-2 rounded-full bg-[#00E5FF] animate-pulse" />
              </div>
              <div>
                <span className="font-display text-xs font-bold tracking-widest text-starlight block">
                  ORION ARCHITECTURAL CONSOLE
                </span>
                <span className="font-mono text-[11.5px] text-white/80 tracking-wider">
                  Neural Companion · Workspace & Vector Engine
                </span>
              </div>
            </div>

            <button
              onClick={onClose}
              className="p-1.5 rounded-lg text-white/80 hover:text-white hover:bg-white/10 transition-colors"
              title="Close panel (Esc)"
            >
              <X size={16} />
            </button>
          </header>

          {/* =========================================================================
              NAVIGATION TABS: CHAT PANEL · CONVO HISTORY · PROJECT AREA
             ========================================================================= */}
          <div className="grid grid-cols-3 border-b border-white/10 bg-black/30 font-mono text-xs">
            <button
              onClick={() => setActiveView('chat')}
              className={`flex items-center justify-center gap-2 py-3 transition-all border-b-2 font-medium ${
                activeView === 'chat'
                  ? 'border-[#7FA6FF] text-[#DCE7FF] bg-white/[0.04]'
                  : 'border-transparent text-white/80 hover:text-white hover:bg-white/[0.02]'
              }`}
            >
              <MessageSquare size={14} className={activeView === 'chat' ? 'text-[#7FA6FF]' : ''} />
              <span>Chat Panel</span>
            </button>

            <button
              onClick={() => setActiveView('history')}
              className={`flex items-center justify-center gap-2 py-3 transition-all border-b-2 font-medium ${
                activeView === 'history'
                  ? 'border-[#00E5FF] text-[#00E5FF] bg-white/[0.04]'
                  : 'border-transparent text-white/80 hover:text-white hover:bg-white/[0.02]'
              }`}
            >
              <History size={14} className={activeView === 'history' ? 'text-[#00E5FF]' : ''} />
              <span>Convo History</span>
            </button>

            <button
              onClick={() => setActiveView('project')}
              className={`flex items-center justify-center gap-2 py-3 transition-all border-b-2 font-medium ${
                activeView === 'project'
                  ? 'border-[#3DFFA8] text-[#3DFFA8] bg-white/[0.04]'
                  : 'border-transparent text-white/80 hover:text-white hover:bg-white/[0.02]'
              }`}
            >
              <FolderGit2 size={14} className={activeView === 'project' ? 'text-[#3DFFA8]' : ''} />
              <span>Project Area</span>
            </button>
          </div>

          {/* =========================================================================
              VIEW 1: CHAT PANEL
             ========================================================================= */}
          {activeView === 'chat' && (
            <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
              {/* Persona Selection Header Strip */}
              <div className="px-4 py-2 border-b border-white/5 bg-white/[0.02] flex items-center justify-between text-xs font-mono">
                <span className="text-white/76 text-[11.5px] tracking-wider uppercase">Tone Persona:</span>
                <div className="flex items-center gap-1.5">
                  <button
                    onClick={() => onSetPersonality?.('concise_companion')}
                    className={`px-3 py-1 rounded-full text-xs font-medium transition-all ${
                      activePersonality === 'concise_companion' || !activePersonality
                        ? 'bg-[#7FA6FF]/30 text-[#DCE7FF] border border-[#7FA6FF]/60'
                        : 'text-white/76 hover:text-white'
                    }`}
                  >
                    ⚡ Concise Companion
                  </button>
                  <button
                    onClick={() => onSetPersonality?.('detailed_researcher')}
                    className={`px-3 py-1 rounded-full text-xs font-medium transition-all ${
                      activePersonality === 'detailed_researcher'
                        ? 'bg-[#00E5FF]/30 text-[#00E5FF] border border-[#00E5FF]/60'
                        : 'text-white/76 hover:text-white'
                    }`}
                  >
                    📜 Deep Researcher
                  </button>
                </div>
              </div>

              {/* Chat Message Stream */}
              <div className="flex-1 overflow-y-auto p-4 space-y-4">
                {responses.length === 0 ? (
                  <div className="py-20 text-center space-y-3 px-6">
                    <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-[#7FA6FF]/20 to-[#00E5FF]/10 border border-[#7FA6FF]/30 mx-auto flex items-center justify-center">
                      <Sparkles size={24} className="text-[#DCE7FF] animate-pulse" />
                    </div>
                    <h3 className="font-display text-sm font-bold tracking-wider text-starlight">
                      Orion Neural Stream Online
                    </h3>
                    <p className="font-sans text-xs text-white/80 leading-relaxed max-w-sm mx-auto">
                      Ask anything, execute autonomous desktop screen tasks, triage game bugs, or drop files directly onto the display.
                    </p>
                  </div>
                ) : (
                  responses.map((r) => {
                    const codeAction = (r.response.actions || []).find((a) => a.type === 'code_suggestion');
                    const code = (codeAction?.payload as any)?.code;
                    const codeLang = (codeAction?.payload as any)?.language || 'bash';

                    return (
                      <div key={r.request_id} className="space-y-1.5 animate-in fade-in duration-300">
                        {/* Orion Message Header */}
                        <div className="flex items-center justify-between text-xs font-mono px-1">
                          <div className="flex items-center gap-1.5">
                            <span className="w-2 h-2 rounded-full bg-[#7FA6FF]" />
                            <span className="text-[#DCE7FF] font-semibold">Orion</span>
                            <span className="text-[11.5px] px-1.5 py-0.2 rounded bg-white/10 text-white/85">
                              {r.response.tier}
                            </span>
                          </div>
                          <span className="text-white/72 text-[11.5px]">
                            {new Date((r.timestamp || Date.now() / 1000) * 1000).toLocaleTimeString()}
                          </span>
                        </div>

                        {/* Orion Bubble */}
                        <div className="rounded-2xl border border-white/15 bg-gradient-to-br from-[#121429]/95 via-[#0A0D1D]/90 to-[#070913]/95 p-3.5 text-[13px] text-white/95 leading-relaxed shadow-lg">
                          <p className="selectable font-sans">{r.response.summary}</p>

                          {/* Code Action Card */}
                          {code && (
                            <div className="mt-3 relative rounded-xl bg-[#030408] p-3 font-mono text-xs text-starlight border border-white/15 shadow-inner">
                              <div className="flex items-center justify-between pb-2 mb-2 border-b border-white/10 text-[11.5px] text-white/76">
                                <span>{codeLang}</span>
                                <button
                                  onClick={() => handleCopy(r.request_id, code)}
                                  className="flex items-center gap-1 px-2 py-0.5 rounded hover:bg-white/10 text-white/85 hover:text-white transition-colors"
                                >
                                  {copiedId === r.request_id ? (
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
                              </div>
                              <pre className="selectable overflow-x-auto text-[#A5F3FC] leading-normal font-mono">
                                <code>{code}</code>
                              </pre>
                            </div>
                          )}

                          {/* Response Actions & Telegram Forwarder */}
                          <div className="mt-3 pt-2 flex items-center justify-between border-t border-white/10 text-xs text-white/76">
                            <span className="font-mono text-[11.5px]">Intent: {r.response.intent}</span>
                            {onSendTelegram && (
                              <button
                                onClick={() => onSendTelegram(`🌌 *Orion Output:*\n${r.response.summary}`)}
                                className="flex items-center gap-1 text-[#00E5FF] hover:underline font-mono text-[11.5px]"
                                title="Forward output to Telegram"
                              >
                                <SendHorizontal size={12} />
                                <span>Send to Telegram</span>
                              </button>
                            )}
                          </div>
                        </div>
                      </div>
                    );
                  })
                )}

                {isProcessing && (
                  <div className="flex items-center gap-2.5 rounded-xl border border-[#7FA6FF]/30 bg-[#7FA6FF]/10 p-3 text-xs text-[#DCE7FF] font-mono animate-pulse">
                    <Activity size={15} className="animate-spin text-[#7FA6FF]" />
                    <span>Orion is synthesizing neural context and executing tasks...</span>
                  </div>
                )}
                <div ref={bottomRef} />
              </div>

              {/* Fast Suggested Prompt Chips */}
              <div className="px-4 py-2 border-t border-white/10 bg-black/40 flex items-center gap-2 overflow-x-auto">
                {quickActions.map((action, idx) => {
                  const Icon = action.icon;
                  return (
                    <button
                      key={idx}
                      onClick={() => onSendMessage(action.prompt)}
                      className="flex items-center gap-1.5 px-3 py-1.5 rounded-full border border-white/15 bg-white/[0.04] hover:bg-white/[0.1] hover:border-[#7FA6FF]/60 text-xs font-mono text-white/80 hover:text-white whitespace-nowrap transition-all shadow-sm"
                    >
                      <Icon size={12} className="text-[#7FA6FF]" />
                      <span>{action.label}</span>
                    </button>
                  );
                })}
              </div>

              {/* Bottom Chat Prompt Form */}
              <form onSubmit={handleSubmit} className="p-3 border-t border-white/10 bg-black/50 flex items-center gap-2">
                {onToggleMic && (
                  <button
                    type="button"
                    onClick={onToggleMic}
                    className={`p-2.5 rounded-xl border transition-all ${
                      micActive
                        ? 'border-[#7FA6FF] bg-[#7FA6FF]/30 text-[#DCE7FF] animate-pulse'
                        : 'border-white/15 bg-white/5 text-white/85 hover:text-white hover:bg-white/10'
                    }`}
                    title={micActive ? 'Listening (Click to stop)' : 'Speak to Orion'}
                  >
                    <Mic size={16} />
                  </button>
                )}

                <input
                  type="text"
                  value={inputText}
                  onChange={(e) => setInputText(e.target.value)}
                  placeholder="Ask Orion, inspect screen, or execute task..."
                  className="flex-1 bg-white/[0.05] border border-white/15 rounded-xl px-3.5 py-2.5 text-xs text-starlight placeholder:text-white/55 focus:outline-none focus:border-[#7FA6FF]/80 focus:ring-1 focus:ring-[#7FA6FF]/60 font-sans tracking-wide"
                />

                <button
                  type="submit"
                  disabled={!inputText.trim()}
                  className="p-2.5 rounded-xl bg-gradient-to-r from-[#7FA6FF] to-[#2E6BFF] text-white hover:brightness-110 disabled:opacity-30 disabled:hover:brightness-100 transition-all shadow-md shadow-[#7FA6FF]/25"
                  title="Send prompt"
                >
                  <Send size={15} />
                </button>
              </form>
            </div>
          )}

          {/* =========================================================================
              VIEW 2: CONVO HISTORY
             ========================================================================= */}
          {activeView === 'history' && (
            <div className="flex-1 flex flex-col min-h-0 overflow-hidden p-4 space-y-3">
              {/* Top Controls: Search & New Chat Button */}
              <div className="flex items-center gap-2">
                <div className="relative flex-1">
                  <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-white/72" />
                  <input
                    type="text"
                    value={historySearch}
                    onChange={(e) => setHistorySearch(e.target.value)}
                    placeholder="Search past conversations..."
                    className="w-full bg-white/[0.04] border border-white/15 rounded-xl pl-9 pr-3 py-2 text-xs text-starlight placeholder:text-white/55 focus:outline-none focus:border-[#00E5FF]/60 font-sans"
                  />
                </div>

                <button
                  onClick={handleNewConversation}
                  className="flex items-center gap-1.5 px-3 py-2 rounded-xl border border-[#00E5FF]/40 bg-[#00E5FF]/15 hover:bg-[#00E5FF]/25 text-[#00E5FF] text-xs font-mono font-medium transition-all whitespace-nowrap"
                >
                  <Plus size={14} />
                  <span>New Chat</span>
                </button>
              </div>

              {/* Sessions List */}
              <div className="flex-1 overflow-y-auto space-y-2 pr-1">
                {filteredSessions.map((session) => {
                  const isSelected = session.id === activeSessionId;
                  return (
                    <div
                      key={session.id}
                      onClick={() => {
                        setActiveSessionId(session.id);
                        setActiveView('chat');
                      }}
                      className={`p-3 rounded-xl border transition-all cursor-pointer space-y-1 ${
                        isSelected
                          ? 'border-[#00E5FF]/60 bg-gradient-to-r from-[#00E5FF]/15 via-[#00E5FF]/5 to-transparent'
                          : 'border-white/10 bg-white/[0.02] hover:bg-white/[0.06] hover:border-white/20'
                      }`}
                    >
                      <div className="flex items-center justify-between">
                        <span className="font-semibold text-xs text-starlight truncate pr-2">
                          {session.title}
                        </span>
                        <span className="font-mono text-[11px] text-white/72 whitespace-nowrap">
                          {session.timestamp}
                        </span>
                      </div>
                      <p className="font-sans text-[11.5px] text-white/80 line-clamp-1 leading-snug">
                        {session.preview}
                      </p>
                      <div className="flex items-center justify-between pt-1 text-[11px] font-mono text-white/72">
                        <span>{session.messageCount} messages</span>
                        <ChevronRight size={12} className="text-white/72" />
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* =========================================================================
              VIEW 3: PROJECT AREA (WORKSPACE CONTEXT & MEMORY NODES)
             ========================================================================= */}
          {activeView === 'project' && (
            <div className="flex-1 flex flex-col min-h-0 overflow-y-auto p-4 space-y-4">
              {/* Active Workspace Card */}
              <div className="p-3.5 rounded-2xl border border-[#3DFFA8]/30 bg-gradient-to-br from-[#3DFFA8]/10 to-transparent space-y-2">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <FolderGit2 size={16} className="text-[#3DFFA8]" />
                    <span className="font-display text-xs font-bold tracking-wider text-starlight">
                      ACTIVE WORKSPACE
                    </span>
                  </div>
                  <span className="font-mono text-[11px] px-2 py-0.5 rounded-full bg-[#3DFFA8]/20 text-[#3DFFA8] border border-[#3DFFA8]/40">
                    ONLINE
                  </span>
                </div>
                <div className="font-mono text-xs text-white/80 bg-black/40 p-2.5 rounded-xl border border-white/10 space-y-1">
                  <div className="text-[#3DFFA8]">wonderful-meitner</div>
                  <div className="text-[11.5px] text-white/76 truncate">
                    Local workspace
                  </div>
                </div>
              </div>

              {/* Active Focused Window & OCR Context */}
              <div className="p-3.5 rounded-2xl border border-white/10 bg-white/[0.02] space-y-2">
                <div className="flex items-center justify-between text-xs font-mono">
                  <span className="text-white/76 uppercase tracking-wider text-[11.5px]">Focused OS Window</span>
                  <span className="text-[#00E5FF] flex items-center gap-1">
                    <Monitor size={12} />
                    <span>Live Tracking</span>
                  </span>
                </div>
                <div className="font-sans text-xs text-starlight bg-black/40 p-2.5 rounded-xl border border-white/10">
                  <div className="font-semibold text-[#00E5FF] truncate">
                    {activeWindow || 'Desktop Workspace'}
                  </div>
                  {latestBrowserTab && (
                    <div className="mt-1 text-[11.5px] text-white/80 truncate flex items-center gap-1">
                      <ExternalLink size={10} />
                      <span>{latestBrowserTab.title}</span>
                    </div>
                  )}
                </div>
              </div>

              {/* Active Daemons & Integration Bridges */}
              <div className="space-y-2">
                <span className="font-mono text-xs text-white/76 uppercase tracking-wider block px-1">
                  Connected Bridges & Daemons
                </span>
                <div className="grid grid-cols-2 gap-2 text-xs font-mono">
                  <div className="p-2.5 rounded-xl border border-white/10 bg-white/[0.02] space-y-1">
                    <div className="flex items-center gap-1.5 text-[#3DFFA8]">
                      <Terminal size={13} />
                      <span className="font-semibold">OS Automation</span>
                    </div>
                    <span className="text-[11px] text-white/76 block">Click, Type & Hotkeys</span>
                  </div>

                  <div className="p-2.5 rounded-xl border border-white/10 bg-white/[0.02] space-y-1">
                    <div className="flex items-center gap-1.5 text-[#00E5FF]">
                      <Gamepad2 size={13} />
                      <span className="font-semibold">Discord Bot</span>
                    </div>
                    <span className="text-[11px] text-white/76 block">Game Bugs Hub</span>
                  </div>

                  <div className="p-2.5 rounded-xl border border-white/10 bg-white/[0.02] space-y-1">
                    <div className="flex items-center gap-1.5 text-[#26A5E4]">
                      <SendHorizontal size={13} />
                      <span className="font-semibold">Telegram Bridge</span>
                    </div>
                    <span className="text-[11px] text-white/76 block">Instant Remote Alerts</span>
                  </div>

                  <div className="p-2.5 rounded-xl border border-white/10 bg-white/[0.02] space-y-1">
                    <div className="flex items-center gap-1.5 text-[#7FA6FF]">
                      <Database size={13} />
                      <span className="font-semibold">LanceDB Vectors</span>
                    </div>
                    <span className="text-[11px] text-white/76 block">{memories.length} Active Records</span>
                  </div>
                </div>
              </div>

              {/* Recent Vector Memories */}
              <div className="space-y-2">
                <span className="font-mono text-xs text-white/76 uppercase tracking-wider block px-1">
                  Recent Knowledge Vectors ({memories.length})
                </span>
                <div className="space-y-2">
                  {memories.slice(0, 4).map((m) => (
                    <div
                      key={m.id}
                      className="p-2.5 rounded-xl border border-white/10 bg-white/[0.02] space-y-1 font-sans text-xs"
                    >
                      <div className="flex items-center justify-between text-[11.5px] font-mono text-white/72">
                        <span className="text-[#00E5FF] font-semibold uppercase">{m.source}</span>
                        <span>{new Date(m.timestamp * 1000).toLocaleTimeString()}</span>
                      </div>
                      <p className="text-white/80 text-[11.5px] line-clamp-2 leading-relaxed">
                        {m.content}
                      </p>
                    </div>
                  ))}
                </div>
              </div>

              {/* Live System Activity Feed */}
              <div className="space-y-2">
                <span className="font-mono text-xs text-white/76 uppercase tracking-wider block px-1">
                  Live System Activity ({feed.length})
                </span>
                <div className="space-y-1.5 font-mono text-[11.5px]">
                  {feed.slice(0, 5).map((item) => (
                    <div
                      key={item.id}
                      className="p-2 rounded-lg bg-black/40 border border-white/5 flex items-center justify-between gap-2"
                    >
                      <div className="flex items-center gap-2 truncate">
                        <span
                          className="w-1.5 h-1.5 rounded-full shrink-0"
                          style={{ backgroundColor: item.color }}
                        />
                        <span style={{ color: item.color }} className="font-semibold shrink-0">
                          {item.label}
                        </span>
                        <span className="text-white/85 truncate">{item.detail}</span>
                      </div>
                      <span className="text-white/72 text-[11px] shrink-0">
                        {new Date(item.ts * 1000).toLocaleTimeString()}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}
        </motion.aside>
      )}
    </AnimatePresence>
  );
}
