import { useState, useEffect } from 'react';
import { AnimatePresence } from 'framer-motion';
import {
  HeartPulse,
  MessageSquare,
  FolderGit2,
  Bot,
  Terminal,
  Activity,
  SendHorizontal,
  ChevronRight,
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
    color: '#7FA6FF',
    borderHover: 'hover:border-[#7FA6FF]/60',
    badge: 'Primary',
  },
];


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
  /** [look] Chat props are kept so callers don't change; the chat itself lives in the command console. */
  /** Phones have no taskbar, so the sheet's title bar carries the Settings button. */
  onOpenSettings?: () => void;
}

export default function EarthContextWindow({
  feed,
  memories,
  activeWindow,
  activePersonality = 'concise_companion',
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

  const [activeTab, setActiveTab] = useState<ContextTab>('comms'); // [look] chat moved to the command console
  useEffect(() => {
    if (!focusTab || focusTab.tab === 'chat') return;
    setActiveTab(focusTab.tab);
    windowManager.restore('orion');
    windowManager.focus('orion');
  }, [focusTab]);
  const pendingApprovals = approvals.filter((a) => a.status === 'pending').length;
  const unreadComms = commsThreads.reduce((n, t) => n + (t.unread || 0), 0);
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

  const handleToolClick = (tool: ToolItem) => {
    cosmicAudio.playCommandDetected();
    onSelectTool(tool.id);
  };

  const tabs: Array<{ id: ContextTab; label: string; icon: typeof Monitor; color: string; badge?: number }> = [
    { id: 'comms', label: 'Comms', icon: Inbox, color: '#26A5E4', badge: unreadComms },
    { id: 'queue', label: 'Queue', icon: ShieldCheck, color: '#FFB300', badge: pendingApprovals },
    { id: 'project', label: 'Projects', icon: FolderGit2, color: '#3DFFA8' },
    { id: 'tools', label: `Tools`, icon: Wrench, color: '#00E5FF' },
    ...(bible ? [{ id: 'life' as ContextTab, label: 'Life', icon: HeartPulse, color: '#FF2A85' }] : []),
  ];

  const shown = useWindowShown('orion', isOpen);

  return (
    <>
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
                    <span className="text-white/76 uppercase tracking-wider text-[11.5px]">Focused OS Window</span>
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
                      <div className="mt-1 text-[11.5px] text-white/80 truncate flex items-center gap-1">
                        <ExternalLink size={10} />
                        <span>{latestBrowserTab.title}</span>
                      </div>
                    )}
                  </div>
                </div>

                {/* Connected Daemons & Bridges */}
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

                {/* Recent Knowledge Vectors */}
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

            {/* =====================================================================
                TAB 3: TOOLS HUB (EXPANSIVE VISUAL TOOL LAUNCHPAD)
               ===================================================================== */}
            {activeTab === 'tools' && (
              <div className="relative z-10 flex-1 flex flex-col min-h-0 overflow-y-auto p-4 space-y-3">
                {bible && bibleSend && <GrimoirePanel bible={bible} send={bibleSend} />}
                <div className="flex items-center justify-between px-1">
                  <span className="font-mono text-xs text-white/80 tracking-wider uppercase">
                    Available Orion Toolsets
                  </span>
                  <span className="text-[11.5px] font-mono text-[#00E5FF]">
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
                                background: `radial-gradient(circle, ${tool.color}33 0%, rgba(26,31,39,0.9) 80%)`,
                                border: `1px solid ${tool.color}55`,
                              }}
                            >
                              <Icon size={16} style={{ color: tool.color }} />
                            </div>

                            <div className="flex items-center gap-1.5">
                              {isActive && (
                                <span className="px-2 py-0.5 rounded-full text-[11px] font-mono bg-[#3DFFA8]/20 text-[#3DFFA8] border border-[#3DFFA8]/40 animate-pulse">
                                  ACTIVE
                                </span>
                              )}
                              <span className="text-[11px] font-mono text-white/76 px-2 py-0.5 rounded bg-white/5">
                                {tool.badge}
                              </span>
                            </div>
                          </div>

                          <h4 className="font-sans font-semibold text-xs text-starlight group-hover:text-white transition-colors">
                            {tool.label}
                          </h4>
                          <span className="font-mono text-[11px] text-white/76 block mb-1">
                            {tool.tagline}
                          </span>
                          <p className="font-sans text-[11.5px] text-white/85 line-clamp-2 leading-relaxed">
                            {tool.description}
                          </p>
                        </div>

                        <div className="mt-3 pt-2 border-t border-white/5 flex items-center justify-between text-[11.5px] font-mono">
                          <span style={{ color: tool.color }} className="font-medium">
                            {isActive ? 'Click to Toggle' : 'Launch Tool'}
                          </span>
                          <ChevronRight
                            size={13}
                            className="text-white/72 group-hover:text-white group-hover:translate-x-0.5 transition-all"
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
