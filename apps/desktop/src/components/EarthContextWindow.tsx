import { useState, useRef, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  HeartPulse,
  MessageSquare,
  History,
  FolderGit2,
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
  Wrench,
  MousePointer,
  Users,
  Chrome,
  Layers,
  ScanEye,
  ChevronDown,
  Inbox,
  ShieldCheck,
  Settings2,
} from 'lucide-react';
import type {
  ApprovalItem,
  CommsDraftEvent,
  CommsReplyResultEvent,
  CommsThread,
  MemoryRecord,
  OrchestratorResponseEvent,
  PersonalityMode,
  SelfEvolveStatusEvent,
  TelemetrySnapshot,
  TrainingExportedEvent,
} from '@orion/shared-types';
import type { FeedItem } from '../hooks/useOrionSocket';
import { cosmicAudio } from '../lib/cosmicAudio';
import ApprovalQueuePanel from './ApprovalQueuePanel';
import CommsPanel from './CommsPanel';
import GlassWindow, { useWindowShown } from './glass/GlassWindow';
import OrionMark from './glass/OrionMark';
import { windowManager } from '../lib/windowManager';
import ProjectsPanel from './bible/ProjectsPanel';
import GrimoirePanel from './bible/GrimoirePanel';
import LifePanel from './bible/LifePanel';
import type { BibleSend, BibleState } from '../hooks/bibleState';

export type ContextTab = 'chat' | 'comms' | 'queue' | 'project' | 'tools' | 'life';

export type ToolOptionId =
  | 'dialogue'
  | 'screen_tasks'
  | 'game_bugs'
  | 'personality'
  | 'agents'
  | 'analytics'
  | 'chrome'
  | 'discord'
  | 'screen'
  | 'telegram';

interface ToolItem {
  id: ToolOptionId;
  label: string;
  tagline: string;
  description: string;
  icon: any;
  color: string;
  borderHover: string;
  badge?: string;
}

const TOOLS_CATALOG: ToolItem[] = [
  {
    id: 'screen_tasks',
    label: 'Autonomous Screen Tasks',
    tagline: 'OS Mouse & Keyboard Engine',
    description: 'Execute coordinate clicks, keystrokes, and multi-step UI workflows directly on your desktop.',
    icon: MousePointer,
    color: '#3DFFA8',
    borderHover: 'hover:border-[#3DFFA8]/60',
    badge: 'Automation',
  },
  {
    id: 'game_bugs',
    label: 'Discord Game Bug Hub',
    tagline: 'Gemini Screengrab Triage',
    description: 'Collect game bug reports from Discord and in-line Gemini visual diagnosis with one-click responses.',
    icon: Gamepad2,
    color: '#00E5FF',
    borderHover: 'hover:border-[#00E5FF]/60',
    badge: 'Game Hub',
  },
  {
    id: 'personality',
    label: 'Personality & Voice',
    tagline: 'Neural Tone & Kokoro TTS',
    description: 'Toggle between Concise Companion and Deep Researcher modes, and customize neural voices.',
    icon: Bot,
    color: '#FFB74D',
    borderHover: 'hover:border-[#FFB74D]/60',
    badge: 'Voice',
  },
  {
    id: 'agents',
    label: 'Autonomous Agent Crew',
    tagline: 'Multi-Agent Subroutines',
    description: 'Manage active subagents: Coding Agent, Routine Optimizer, Discord Monitor, and Proactive Observer.',
    icon: Users,
    color: '#3DFFA8',
    borderHover: 'hover:border-[#3DFFA8]/60',
    badge: 'Crew',
  },
  {
    id: 'analytics',
    label: 'Cosmic Telemetry',
    tagline: 'System Load & Vector Rows',
    description: 'Monitor token savings, OCR pipeline latency, memory vector growth, and daemon health.',
    icon: Activity,
    color: '#00E5FF',
    borderHover: 'hover:border-[#00E5FF]/60',
    badge: 'Metrics',
  },
  {
    id: 'chrome',
    label: 'Chrome Live Scraper',
    tagline: 'Active Tab DOM Extraction',
    description: 'Real-time Chrome tab inspection and deep-scrape pipeline feeding LanceDB vector memory.',
    icon: Chrome,
    color: '#FF7043',
    borderHover: 'hover:border-[#FF7043]/60',
    badge: 'Scraper',
  },
  {
    id: 'discord',
    label: 'Discord Community Bot',
    tagline: 'Server Support & Webhooks',
    description: 'Live community sync, automated member triage, and bot interaction hub.',
    icon: Layers,
    color: '#5865F2',
    borderHover: 'hover:border-[#5865F2]/60',
    badge: 'Community',
  },
  {
    id: 'screen',
    label: 'Instant Screen Inspector',
    tagline: 'Screen OCR & Delta Capture',
    description: 'Inspect the active display now and report all visible applications, window titles, and text.',
    icon: ScanEye,
    color: '#3DFFA8',
    borderHover: 'hover:border-[#3DFFA8]/60',
    badge: 'Live OCR',
  },
  {
    id: 'telegram',
    label: 'Telegram Remote Bridge',
    tagline: 'Mobile Outputs & Alerts',
    description: 'Forward Orion summaries, task completions, and security alerts directly to your mobile Telegram chat.',
    icon: SendHorizontal,
    color: '#26A5E4',
    borderHover: 'hover:border-[#26A5E4]/60',
    badge: 'Bridge',
  },
  {
    id: 'dialogue',
    label: 'Neural Dialogue Stream',
    tagline: 'Full Conversational Mode',
    description: 'Primary interactive discussion channel with context-aware memory recall and code drafting.',
    icon: MessageSquare,
    color: '#C084FC',
    borderHover: 'hover:border-[#C084FC]/60',
    badge: 'Primary',
  },
];

interface ConvoSession {
  id: string;
  title: string;
  preview: string;
  timestamp: string;
  messageCount: number;
}

interface Props {
  responses: OrchestratorResponseEvent[];
  feed: FeedItem[];
  memories: MemoryRecord[];
  onSendMessage: (text: string) => void;
  isProcessing: boolean;
  currentTask?: string;
  activeWindow?: string | null;
  onSendTelegram?: (text: string) => void;
  micActive?: boolean;
  onToggleMic?: () => void;
  activePersonality?: PersonalityMode;
  onSetPersonality?: (mode: PersonalityMode) => void;
  latestBrowserTab?: { title: string; url: string } | null;
  onSelectTool: (id: ToolOptionId) => void;
  activeWidgets?: Partial<Record<string, boolean>> | Record<string, any>;
  isOpen?: boolean;
  onToggleOpen?: (open: boolean) => void;
  // ---- V2: Comms hub, approval queue, self-training
  telemetry?: TelemetrySnapshot | null;
  approvals?: ApprovalItem[];
  selfEvolveStatus?: SelfEvolveStatusEvent | null;
  trainingExport?: TrainingExportedEvent['result'] | null;
  commsThreads?: CommsThread[];
  commsDrafts?: Record<string, CommsDraftEvent>;
  commsReplyResult?: CommsReplyResultEvent | null;
  onDecideApproval?: (id: string, decision: 'approve' | 'edit' | 'reject', text?: string) => void;
  onPatchDecision?: (id: string, action: 'apply' | 'reject' | 'revert') => void;
  onRequestSelfEvolve?: (request: string) => void;
  onCommsReply?: (threadKey: string, text: string, draftId?: string | null) => void;
  onRequestCommsDraft?: (threadKey: string) => void;
  onMarkCommsRead?: (threadKey: string) => void;
  onConsolidateMemories?: () => void;
  connected?: boolean;
  // Feature Bible
  bible?: BibleState;
  bibleSend?: BibleSend;
  /** Bump `nonce` to switch tabs from outside (e.g. clicking a VIP satellite). */
  focusTab?: { tab: ContextTab; nonce: number; threadKey?: string; draft?: string } | null;
  /** Phone layout: the panel becomes a full-height sheet sliding up from the bottom. */
  mobile?: boolean;
  /** Phones have no taskbar, so the sheet's title bar carries the Settings button. */
  onOpenSettings?: () => void;
}

export default function EarthContextWindow({
  responses,
  feed,
  memories,
  onSendMessage,
  isProcessing,
  currentTask,
  activeWindow,
  onSendTelegram,
  micActive = false,
  onToggleMic,
  activePersonality = 'concise_companion',
  onSetPersonality,
  latestBrowserTab,
  onSelectTool,
  activeWidgets = {},
  isOpen: externalIsOpen,
  onToggleOpen,
  telemetry = null,
  approvals = [],
  selfEvolveStatus = null,
  trainingExport = null,
  commsThreads = [],
  commsDrafts = {},
  commsReplyResult = null,
  onDecideApproval = () => {},
  onPatchDecision = () => {},
  onRequestSelfEvolve = () => {},
  onCommsReply = () => {},
  onRequestCommsDraft = () => {},
  onMarkCommsRead = () => {},
  onConsolidateMemories,
  connected = true,
  bible,
  bibleSend,
  focusTab = null,
  mobile = false,
  onOpenSettings,
}: Props) {
  const [internalIsOpen, setInternalIsOpen] = useState(false);
  const isOpen = externalIsOpen !== undefined ? externalIsOpen : internalIsOpen;

  const setIsOpen = (next: boolean) => {
    if (onToggleOpen) onToggleOpen(next);
    else setInternalIsOpen(next);
  };

  const [activeTab, setActiveTab] = useState<ContextTab>('chat');
  useEffect(() => {
    if (!focusTab) return;
    setActiveTab(focusTab.tab);
    windowManager.restore('orion');
    windowManager.focus('orion');
  }, [focusTab]);
  const pendingApprovals = approvals.filter((a) => a.status === 'pending').length;
  const unreadComms = commsThreads.reduce((n, t) => n + (t.unread || 0), 0);
  const [inputText, setInputText] = useState('');
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [historySearch, setHistorySearch] = useState('');
  const [isSessionsOpen, setIsSessionsOpen] = useState(false);
  const [activeSessionId, setActiveSessionId] = useState('session-current');
  const bottomRef = useRef<HTMLDivElement>(null);

  // Past conversation sessions
  const [sessions, setSessions] = useState<ConvoSession[]>([
    {
      id: 'session-current',
      title: 'Active Orion Orchestration',
      preview: responses[0]?.response?.summary || 'Session active with autonomous daemon.',
      timestamp: 'Just now',
      messageCount: Math.max(1, responses.length),
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
  ]);

  // Close on Escape key
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && isOpen) {
        if ((e.target as HTMLElement | null)?.matches?.('input, textarea, [contenteditable="true"]')) return;
        setIsOpen(false);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isOpen]);

  // Scroll to bottom on response in chat
  useEffect(() => {
    if (activeTab === 'chat' && isOpen) {
      bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
  }, [responses.length, activeTab, isOpen]);


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

  const handleToolClick = (tool: ToolItem) => {
    cosmicAudio.playCommandDetected();
    onSelectTool(tool.id);
  };

  const handleNewConversation = () => {
    const newId = `session-${Date.now()}`;
    const newSession: ConvoSession = {
      id: newId,
      title: `Orion Session #${sessions.length + 1}`,
      preview: 'New session initiated.',
      timestamp: 'Just now',
      messageCount: 0,
    };
    setSessions([newSession, ...sessions]);
    setActiveSessionId(newId);
    setActiveTab('chat');
  };

  const quickActions: Array<{ label: string; prompt: string; icon: typeof Monitor; run?: () => void }> = [
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
      // V2: runs the real consolidation + fine-tune dataset export instead of a chat prompt
      run: onConsolidateMemories,
    },
  ];

  const tabs: Array<{ id: ContextTab; label: string; icon: typeof Monitor; color: string; badge?: number }> = [
    { id: 'chat', label: 'Chat', icon: MessageSquare, color: '#C084FC' },
    { id: 'comms', label: 'Comms', icon: Inbox, color: '#26A5E4', badge: unreadComms },
    { id: 'queue', label: 'Queue', icon: ShieldCheck, color: '#FFB300', badge: pendingApprovals },
    { id: 'project', label: 'Projects', icon: FolderGit2, color: '#3DFFA8' },
    { id: 'tools', label: `Tools`, icon: Wrench, color: '#00E5FF' },
    ...(bible ? [{ id: 'life' as ContextTab, label: 'Life', icon: HeartPulse, color: '#FF2A85' }] : []),
  ];

  const shown = useWindowShown('orion', isOpen);

  return (
    <>
      {/* Phones have no taskbar: the Orion mark opens the sheet. */}
      {mobile && !shown && (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            cosmicAudio.playTap();
            windowManager.restore('orion');
            setIsOpen(true);
          }}
          className="orion-glass orion-mobile-launcher fixed bottom-4 left-4 z-50 grid h-14 w-14 place-items-center rounded-2xl"
          title="Open Orion"
          aria-label="Open Orion panel"
          aria-expanded={false}
        >
          <OrionMark size={30} />
        </button>
      )}

      <AnimatePresence>
        {shown && (
          <GlassWindow
            key="orion-window"
            id="orion"
            title="Orion"
            ariaLabel="Orion"
            tabs={tabs.map((t) => {
              const Icon = t.icon;
              return { id: t.id, label: t.label, badge: t.badge, icon: <Icon size={14} strokeWidth={1.9} style={{ color: activeTab === t.id ? t.color : undefined }} /> };
            })}
            activeTab={activeTab}
            onTab={(id) => {
              cosmicAudio.playTap();
              setActiveTab(id as ContextTab);
            }}
            compactTabs
            onNewTab={mobile ? undefined : handleNewConversation}
            newTabLabel="New conversation"
            onClose={() => setIsOpen(false)}
            mobile={mobile}
            titleExtra={
              mobile && onOpenSettings ? (
                <button type="button" onClick={onOpenSettings} className="orion-tab-add" aria-label="Settings" title="Settings">
                  <Settings2 size={15} />
                </button>
              ) : undefined
            }
            className="left-6 top-[56px] bottom-[88px] w-[min(520px,calc(100vw-48px))] text-white"
            bodyClassName="overflow-hidden"
          >
            {/* =====================================================================
                TAB 1: CHAT HISTORY (CONVERSATION STREAM & PROMPT BAR)
               ===================================================================== */}
            {activeTab === 'chat' && (
              <div className="relative z-10 flex-1 flex flex-col min-h-0 overflow-hidden">
                {/* Tone Persona Strip & Session Switcher */}
                <div className="px-4 py-2 border-b border-white/[0.05] flex items-center justify-between text-xs">
                  <div className="flex items-center gap-2">
                    <button
                      onClick={() => setIsSessionsOpen(!isSessionsOpen)}
                      data-active={isSessionsOpen}
                      className={`emboss-pill flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs ${
                        isSessionsOpen ? 'text-[#E9D5FF]' : 'text-white/75'
                      }`}
                    >
                      <History size={13} className="text-[#C084FC]" />
                      <span>Sessions ({sessions.length})</span>
                      <ChevronDown
                        size={12}
                        className={`transition-transform duration-200 ${isSessionsOpen ? 'rotate-180' : ''}`}
                      />
                    </button>

                    {currentTask && (
                      <span className="hidden sm:inline-block text-[11px] text-[#3DFFA8] truncate max-w-[170px] px-2 py-0.5 rounded bg-[#3DFFA8]/10 border border-[#3DFFA8]/20">
                        ⚡ {currentTask}
                      </span>
                    )}
                  </div>

                  <div className="flex items-center gap-1.5">
                    <button
                      onClick={() => onSetPersonality?.('concise_companion')}
                      data-active={activePersonality === 'concise_companion' || !activePersonality}
                      className={`emboss-pill px-2.5 py-1 rounded-full text-xs ${
                        activePersonality === 'concise_companion' || !activePersonality ? 'text-[#E9D5FF]' : 'text-white/55'
                      }`}
                    >
                      ⚡ Concise
                    </button>
                    <button
                      onClick={() => onSetPersonality?.('detailed_researcher')}
                      data-active={activePersonality === 'detailed_researcher'}
                      title="Deep mode also enables voice Talk-to-Develop patches"
                      className={`emboss-pill px-2.5 py-1 rounded-full text-xs ${
                        activePersonality === 'detailed_researcher' ? 'text-[#00E5FF]' : 'text-white/55'
                      }`}
                    >
                      📜 Deep
                    </button>
                  </div>
                </div>

                {/* Collapsible Session History Drawer */}
                <AnimatePresence>
                  {isSessionsOpen && (
                    <motion.div
                      initial={{ height: 0, opacity: 0 }}
                      animate={{ height: 'auto', opacity: 1 }}
                      exit={{ height: 0, opacity: 0 }}
                      className="border-b border-white/10 bg-[#0B0F1E]/70 p-3 space-y-2.5 overflow-hidden font-sans"
                    >
                      <div className="flex items-center gap-2">
                        <div className="relative flex-1">
                          <Search size={13} className="absolute left-2.5 top-2.5 text-white/40" />
                          <input
                            type="text"
                            value={historySearch}
                            onChange={(e) => setHistorySearch(e.target.value)}
                            placeholder="Filter session history..."
                            className="w-full pl-8 pr-3 py-1.5 rounded-lg bg-black/40 border border-white/10 text-xs text-starlight placeholder:text-white/40 focus:outline-none focus:border-[#C084FC]/60 font-mono"
                          />
                        </div>
                        <button
                          onClick={handleNewConversation}
                          className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg bg-[#C084FC]/20 hover:bg-[#C084FC]/30 text-[#E9D5FF] border border-[#C084FC]/40 text-xs font-mono font-medium transition-all"
                        >
                          <Plus size={13} />
                          <span>New</span>
                        </button>
                      </div>

                      <div className="max-h-36 overflow-y-auto space-y-1.5 pr-1 font-sans">
                        {sessions
                          .filter(
                            (s) =>
                              s.title.toLowerCase().includes(historySearch.toLowerCase()) ||
                              s.preview.toLowerCase().includes(historySearch.toLowerCase())
                          )
                          .map((session) => (
                            <div
                              key={session.id}
                              onClick={() => {
                                setActiveSessionId(session.id);
                                setIsSessionsOpen(false);
                              }}
                              className={`p-2 rounded-lg cursor-pointer transition-all flex items-center justify-between text-xs border ${
                                activeSessionId === session.id
                                  ? 'bg-white/10 border-[#C084FC]/50 text-white'
                                  : 'bg-black/30 border-white/5 text-white/70 hover:bg-white/5 hover:text-white'
                              }`}
                            >
                              <div className="truncate mr-2">
                                <div className="font-semibold truncate">{session.title}</div>
                                <div className="text-[11px] text-white/40 truncate">{session.preview}</div>
                              </div>
                              <span className="text-[10px] font-mono text-white/40 shrink-0">
                                {session.timestamp}
                              </span>
                            </div>
                          ))}
                      </div>
                    </motion.div>
                  )}
                </AnimatePresence>

                {/* Messages Stream */}
                <div className="flex-1 overflow-y-auto p-4 space-y-3.5">
                  {responses.length === 0 ? (
                    <div className="py-20 text-center space-y-3 px-6">
                      <div className="w-12 h-12 rounded-2xl bg-[#C084FC]/15 border border-[#C084FC]/30 mx-auto flex items-center justify-center shadow-[0_0_20px_rgba(192,132,252,0.3)]">
                        <Sparkles size={24} className="text-[#E9D5FF] animate-pulse" />
                      </div>
                      <h3 className="font-display text-sm font-bold tracking-wider text-starlight">
                        Planetary Dialogue Stream Ready
                      </h3>
                      <p className="font-sans text-xs text-white/60 leading-relaxed max-w-sm mx-auto">
                        Ask questions, triage bugs, run automated desktop workflows, or drop files directly on the viewport.
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
                              <span className="w-2 h-2 rounded-full bg-[#C084FC] shadow-[0_0_6px_#C084FC]" />
                              <span className="text-[#E9D5FF] font-semibold">Orion</span>
                              <span className="text-[11px] px-1.5 py-0.2 rounded bg-white/10 text-white/70">
                                {r.response.tier}
                              </span>
                            </div>
                            <span className="text-white/40 text-[11px]">
                              {new Date((r.timestamp || Date.now() / 1000) * 1000).toLocaleTimeString()}
                            </span>
                          </div>

                          {/* Message Bubble */}
                          <div className="emboss rounded-2xl p-3.5 text-[13px] text-white/95 leading-relaxed">
                            <p className="selectable font-sans">{r.response.summary}</p>

                            {/* Code Card */}
                            {code && (
                              <div className="mt-3 relative rounded-xl bg-[#030408] p-3 font-mono text-xs text-starlight border border-white/15 shadow-inner">
                                <div className="flex items-center justify-between pb-2 mb-2 border-b border-white/10 text-[11px] text-white/50">
                                  <span>{codeLang}</span>
                                  <button
                                    onClick={() => handleCopy(r.request_id, code)}
                                    className="flex items-center gap-1 px-2 py-0.5 rounded hover:bg-white/10 text-white/70 hover:text-white transition-colors"
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

                            {/* Intent & Telegram Action */}
                            <div className="mt-3 pt-2 flex items-center justify-between border-t border-white/10 text-xs text-white/50">
                              <span className="font-mono text-[11px]">Intent: {r.response.intent}</span>
                              {onSendTelegram && (
                                <button
                                  onClick={() => onSendTelegram(`🌌 *Orion Output:*\n${r.response.summary}`)}
                                  className="flex items-center gap-1 text-[#00E5FF] hover:underline font-mono text-[11px]"
                                  title="Forward to Telegram"
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
                    <div className="flex items-center gap-2.5 rounded-xl border border-[#C084FC]/30 bg-[#C084FC]/10 p-3 text-xs text-[#E9D5FF] font-mono animate-pulse">
                      <Activity size={15} className="animate-spin text-[#C084FC]" />
                      <span>Orion is synthesizing neural context and executing tasks...</span>
                    </div>
                  )}
                  <div ref={bottomRef} />
                </div>

                {/* Quick Action Chips */}
                <div className="px-4 py-2 flex items-center gap-2 overflow-x-auto">
                  {quickActions.map((action, idx) => {
                    const Icon = action.icon;
                    return (
                      <button
                        key={idx}
                        onClick={() => (action.run ? action.run() : onSendMessage(action.prompt))}
                        className="emboss-pill flex items-center gap-1.5 px-3.5 py-1.5 rounded-full text-xs text-white/90 hover:text-white whitespace-nowrap"
                      >
                        <Icon size={12} className="text-[#C084FC]" />
                        <span>{action.label}</span>
                      </button>
                    );
                  })}
                </div>

                {/* Input Bar */}
                <form onSubmit={handleSubmit} className="px-3 pb-3 pt-1 flex items-center gap-2">
                  {onToggleMic && (
                    <button
                      type="button"
                      onClick={onToggleMic}
                      className={`p-2.5 rounded-xl border transition-all ${
                        micActive
                          ? 'border-[#C084FC] bg-[#C084FC]/30 text-[#E9D5FF] shadow-[0_0_15px_rgba(192,132,252,0.5)] animate-pulse'
                          : 'border-white/15 bg-white/5 text-white/70 hover:text-white hover:bg-white/10'
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
                    placeholder="Ask Orion, or just say “Orion”"
                    className="emboss-well flex-1 rounded-xl px-3.5 py-2.5 text-[13px] text-starlight placeholder:text-white/40 focus:outline-none focus:border-[#C084FC]/80 focus:ring-1 focus:ring-[#C084FC]/60"
                  />

                  <button
                    type="submit"
                    disabled={!inputText.trim()}
                    className="p-2.5 rounded-xl bg-gradient-to-r from-[#C084FC] to-[#7B5CFF] text-white hover:brightness-110 disabled:opacity-30 disabled:hover:brightness-100 transition-all shadow-md shadow-[#C084FC]/25"
                    title="Send prompt"
                  >
                    <Send size={15} />
                  </button>
                </form>
              </div>
            )}

            {/* =====================================================================
                V2 TAB: UNIFIED COMMS HUB (Telegram · Discord DMs · Messenger)
               ===================================================================== */}
            {activeTab === 'comms' && (
              <CommsPanel
                threads={commsThreads}
                drafts={commsDrafts}
                replyResult={commsReplyResult}
                telemetry={telemetry}
                onReply={onCommsReply}
                onRequestDraft={onRequestCommsDraft}
                onMarkRead={onMarkCommsRead}
                connected={connected}
                onMute={bibleSend ? (thread_key, muted) => bibleSend('COMMS_MUTE', { thread_key, muted }) : undefined}
                onDigest={bibleSend ? () => bibleSend('DIGEST_REQUEST') : undefined}
                focusThreadKey={focusTab?.threadKey ?? null}
                focusDraft={focusTab?.draft ?? null}
                suggestions={bible?.suggestions}
                onRequestSuggestions={bibleSend ? (thread_key) => bibleSend('SUGGEST_REPLIES', { thread_key }) : undefined}
              />
            )}

            {/* =====================================================================
                V2 TAB: APPROVAL QUEUE (Discord drafts + self-evolve patches)
               ===================================================================== */}
            {activeTab === 'queue' && (
              <ApprovalQueuePanel
                approvals={approvals}
                selfEvolveStatus={selfEvolveStatus}
                trainingExport={trainingExport}
                alignmentExamples={telemetry?.alignment_examples}
                isDeepMode={activePersonality === 'detailed_researcher'}
                onDecide={onDecideApproval}
                onPatch={onPatchDecision}
                onRequestEvolve={onRequestSelfEvolve}
              />
            )}

            {/* =====================================================================
                TAB 2: PROJECTS (WORKSPACE, SCREEN DELTAS & VECTOR MEMORY)
               ===================================================================== */}
            {activeTab === 'project' && (
              <div className="relative z-10 flex-1 flex flex-col min-h-0 overflow-y-auto p-4 space-y-4">
                {bible && bibleSend ? (
                  <ProjectsPanel bible={bible} send={bibleSend} connected={connected} />
                ) : null}

                {/* Focused OS Window Tracking */}
                <div className="p-3.5 rounded-2xl border border-white/10 bg-white/[0.02] space-y-2">
                  <div className="flex items-center justify-between text-xs font-mono">
                    <span className="text-white/50 uppercase tracking-wider text-[11px]">Focused OS Window</span>
                    <span className="text-[#00E5FF] flex items-center gap-1">
                      <Monitor size={12} />
                      <span>Live OCR Tracking</span>
                    </span>
                  </div>
                  <div className="font-sans text-xs text-starlight bg-black/40 p-2.5 rounded-xl border border-white/10">
                    <div className="font-semibold text-[#00E5FF] truncate">
                      {activeWindow || 'Desktop Workspace'}
                    </div>
                    {latestBrowserTab && (
                      <div className="mt-1 text-[11px] text-white/60 truncate flex items-center gap-1">
                        <ExternalLink size={10} />
                        <span>{latestBrowserTab.title}</span>
                      </div>
                    )}
                  </div>
                </div>

                {/* Connected Daemons & Bridges */}
                <div className="space-y-2">
                  <span className="font-mono text-xs text-white/50 uppercase tracking-wider block px-1">
                    Connected Bridges & Daemons
                  </span>
                  <div className="grid grid-cols-2 gap-2 text-xs font-mono">
                    <div className="p-2.5 rounded-xl border border-white/10 bg-white/[0.02] space-y-1">
                      <div className="flex items-center gap-1.5 text-[#3DFFA8]">
                        <Terminal size={13} />
                        <span className="font-semibold">OS Automation</span>
                      </div>
                      <span className="text-[10px] text-white/50 block">Click, Type & Hotkeys</span>
                    </div>

                    <div className="p-2.5 rounded-xl border border-white/10 bg-white/[0.02] space-y-1">
                      <div className="flex items-center gap-1.5 text-[#00E5FF]">
                        <Gamepad2 size={13} />
                        <span className="font-semibold">Discord Bot</span>
                      </div>
                      <span className="text-[10px] text-white/50 block">Game Bugs Hub</span>
                    </div>

                    <div className="p-2.5 rounded-xl border border-white/10 bg-white/[0.02] space-y-1">
                      <div className="flex items-center gap-1.5 text-[#26A5E4]">
                        <SendHorizontal size={13} />
                        <span className="font-semibold">Telegram Bridge</span>
                      </div>
                      <span className="text-[10px] text-white/50 block">Instant Remote Alerts</span>
                    </div>

                    <div className="p-2.5 rounded-xl border border-white/10 bg-white/[0.02] space-y-1">
                      <div className="flex items-center gap-1.5 text-[#C084FC]">
                        <Database size={13} />
                        <span className="font-semibold">LanceDB Vectors</span>
                      </div>
                      <span className="text-[10px] text-white/50 block">{memories.length} Active Records</span>
                    </div>
                  </div>
                </div>

                {/* Recent Knowledge Vectors */}
                <div className="space-y-2">
                  <span className="font-mono text-xs text-white/50 uppercase tracking-wider block px-1">
                    Recent Knowledge Vectors ({memories.length})
                  </span>
                  <div className="space-y-2">
                    {memories.slice(0, 4).map((m) => (
                      <div
                        key={m.id}
                        className="p-2.5 rounded-xl border border-white/10 bg-white/[0.02] space-y-1 font-sans text-xs"
                      >
                        <div className="flex items-center justify-between text-[11px] font-mono text-white/40">
                          <span className="text-[#00E5FF] font-semibold uppercase">{m.source}</span>
                          <span>{new Date(m.timestamp * 1000).toLocaleTimeString()}</span>
                        </div>
                        <p className="text-white/80 text-[11px] line-clamp-2 leading-relaxed">
                          {m.content}
                        </p>
                      </div>
                    ))}
                  </div>
                </div>

                {/* Live System Activity Feed */}
                <div className="space-y-2">
                  <span className="font-mono text-xs text-white/50 uppercase tracking-wider block px-1">
                    Live System Activity ({feed.length})
                  </span>
                  <div className="space-y-1.5 font-mono text-[11px]">
                    {feed.slice(0, 6).map((item) => (
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
                          <span className="text-white/70 truncate">{item.detail}</span>
                        </div>
                        <span className="text-white/40 text-[10px] shrink-0">
                          {new Date(item.ts * 1000).toLocaleTimeString()}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            )}

            {/* =====================================================================
                TAB 3: TOOLS HUB (EXPANSIVE VISUAL TOOL LAUNCHPAD)
               ===================================================================== */}
            {activeTab === 'tools' && (
              <div className="relative z-10 flex-1 flex flex-col min-h-0 overflow-y-auto p-4 space-y-3">
                {bible && bibleSend && <GrimoirePanel bible={bible} send={bibleSend} />}
                <div className="flex items-center justify-between px-1">
                  <span className="font-mono text-xs text-white/60 tracking-wider uppercase">
                    Available Orion Toolsets
                  </span>
                  <span className="text-[11px] font-mono text-[#00E5FF]">
                    {Object.values(activeWidgets).filter(Boolean).length} Active Now
                  </span>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-2.5">
                  {TOOLS_CATALOG.map((tool) => {
                    const Icon = tool.icon;
                    const isActive = activeWidgets[tool.id] ?? false;

                    return (
                      <div
                        key={tool.id}
                        onClick={() => handleToolClick(tool)}
                        className={`emboss p-3.5 rounded-2xl transition-all cursor-pointer group flex flex-col justify-between ${
                          isActive ? 'ring-1 ring-white/30' : tool.borderHover
                        }`}
                      >
                        <div>
                          <div className="flex items-center justify-between mb-2">
                            <div
                              className="w-8 h-8 rounded-xl flex items-center justify-center shadow-md transition-transform group-hover:scale-110"
                              style={{
                                background: `radial-gradient(circle, ${tool.color}33 0%, rgba(10,13,26,0.9) 80%)`,
                                border: `1px solid ${tool.color}55`,
                              }}
                            >
                              <Icon size={16} style={{ color: tool.color }} />
                            </div>

                            <div className="flex items-center gap-1.5">
                              {isActive && (
                                <span className="px-2 py-0.5 rounded-full text-[10px] font-mono bg-[#3DFFA8]/20 text-[#3DFFA8] border border-[#3DFFA8]/40 animate-pulse">
                                  ACTIVE
                                </span>
                              )}
                              <span className="text-[10px] font-mono text-white/50 px-2 py-0.5 rounded bg-white/5">
                                {tool.badge}
                              </span>
                            </div>
                          </div>

                          <h4 className="font-sans font-semibold text-xs text-starlight group-hover:text-white transition-colors">
                            {tool.label}
                          </h4>
                          <span className="font-mono text-[10px] text-white/50 block mb-1">
                            {tool.tagline}
                          </span>
                          <p className="font-sans text-[11px] text-white/70 line-clamp-2 leading-relaxed">
                            {tool.description}
                          </p>
                        </div>

                        <div className="mt-3 pt-2 border-t border-white/5 flex items-center justify-between text-[11px] font-mono">
                          <span style={{ color: tool.color }} className="font-medium">
                            {isActive ? 'Click to Toggle' : 'Launch Tool'}
                          </span>
                          <ChevronRight
                            size={13}
                            className="text-white/40 group-hover:text-white group-hover:translate-x-0.5 transition-all"
                          />
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
            {/* =====================================================================
                FEATURE BIBLE TAB: LIFE (health, runway, ideas, producer, rules, vault)
               ===================================================================== */}
            {activeTab === 'life' && bible && bibleSend && (
              <div className="relative z-10 flex-1 flex flex-col min-h-0 overflow-y-auto p-4">
                <LifePanel bible={bible} send={bibleSend} connected={connected} />
              </div>
            )}
          </GlassWindow>
        )}
      </AnimatePresence>
    </>
  );
}
