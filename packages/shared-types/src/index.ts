/**
 * Orion WebSocket IPC protocol (ws://localhost:9120).
 * Mirrors apps/daemon/schemas.py — keep both in sync.
 */

export const ORION_WS_URL = 'ws://localhost:9120';
export const ORION_VERSION = '2.0.0';

export type MemorySource = 'ocr' | 'browser' | 'voice' | 'discord' | 'user' | 'consolidated' | 'telegram' | 'messenger';
export type AgentId = 'coding_agent' | 'routine_optimizer' | 'discord_monitor' | 'system_capturer' | 'proactive_observer';
export type AgentStates = Record<AgentId, boolean>;
export type CoreState = 'idle' | 'listening' | 'processing';
export type PersonalityMode = 'concise_companion' | 'detailed_researcher';

export type Intent = 'summary' | 'question' | 'coding' | 'automation' | 'observation';
export type Tier = 'local' | 'cloud' | 'offline';
export type Trigger = 'user' | 'voice' | 'screen' | 'browser' | 'discord' | 'routine' | 'consolidation' | 'telegram';

// ---------------------------------------------------------------- records

export interface MemoryRecord {
  id: string;
  timestamp: number;
  source: MemorySource;
  content: string;
  /** Cosine similarity, present on search results. */
  score?: number;
}

export interface TelemetrySnapshot {
  uptime_s: number;
  capture_running: boolean;
  ocr_available: boolean;
  frames_total: number;
  frames_skipped: number;
  frames_processed: number;
  skip_ratio: number;
  est_tokens_saved: number;
  last_phash_diff: number | null;
  llm_calls_local: number;
  llm_calls_cloud: number;
  llm_tokens_in: number;
  llm_tokens_out: number;
  llm_failures: number;
  memory_commits: number;
  memory_rows: number;
  embedding_backend: string;
  personality_mode?: PersonalityMode;
  tts_settings?: Record<string, unknown>;
  telegram_active?: boolean;
  discord_bot_active?: boolean;
  // V2
  telegram_user_active?: boolean;
  extension_clients?: number;
  approvals_pending?: number;
  alignment_examples?: number;
  comms_threads?: number;
  comms_unread?: number;
  triage?: { seen: number; flagged: number; drafted: number };
  pacing?: PacingState;
  // Feature Bible
  preload?: PreloadSummary;
  hydration?: HydrationState;
  circadian?: CircadianState;
  recording_macro?: boolean;
  producer?: ProducerState | null;
  active_station?: string | null;
  runs_active?: number;
  momentum?: MomentumState;
}

// ------------------------------------------------------------- V2 records

export type PacingPhase = 'idle' | 'focus' | 'warming' | 'break_due' | 'resting';
/** Routine category (calisthenics | yoga | breathwork | hydration | custom deck categories). */
export type RoutineKind = string;

export interface RoutineSuggestion {
  id: string;
  kind: RoutineKind;
  duration_min?: number;
  voice_prompt?: string;
  title: string;
  detail: string;
  voice?: string;
}

export interface PacingCheckin {
  id: string;
  minutes: number;
  suggestion: RoutineSuggestion;
  options: RoutineSuggestion[];
}

export interface PacingState {
  phase: PacingPhase;
  session_minutes: number;
  limit_minutes: number;
  warn_minutes?: number;
  snooze_minutes?: number;
  waiting_for_pause?: boolean;
  /** 0..1 of the focus window (90 min by default). */
  progress: number;
  checkins: number;
  breaks_taken: number;
  pending_checkin: PacingCheckin | null;
}

export type TriageCategory = 'prompt' | 'realm_pitch' | 'bug' | 'question';
export type ApprovalKind = 'discord_prompt' | 'self_evolve' | 'grimoire_run' | 'devlog' | 'boardroom';
export type ApprovalStatus = 'pending' | 'sending' | 'sent' | 'rejected' | 'applied' | 'reverted';

export interface PatchFile {
  path: string;
  action: 'create' | 'modify';
  additions: number;
  deletions: number;
  original_sha?: string | null;
}

export interface PatchProposal {
  id: string;
  request: string;
  summary: string;
  files: PatchFile[];
  diff: string;
  status: 'proposed' | 'applied' | 'reverted' | 'rejected';
  created_at: number;
  model?: string | null;
  latency_ms?: number;
  touches_daemon: boolean;
  touches_ui: boolean;
  touches_extension: boolean;
}

export interface ApprovalItem {
  id: string;
  kind: ApprovalKind;
  status: ApprovalStatus;
  created_at: number;
  resolved_at?: number;
  platform: string;
  category?: TriageCategory | 'self_evolve' | 'grimoire' | 'devlog' | 'boardroom';
  channel?: string;
  guild?: string;
  jump_url?: string;
  author?: string;
  original?: string;
  draft?: string;
  final?: string;
  decision?: 'approved' | 'edited' | 'rejected';
  error?: string | null;
  triage?: { needs_admin_approval: boolean; category: TriageCategory; confidence?: number; reason?: string; model?: string };
  models?: { tier1?: string | null; tier2?: string | null };
  examples_used?: number;
  request?: string;
  patch?: PatchProposal;
  // Feature Bible
  entry_id?: string;
  destructive?: boolean;
  title?: string;
  changelog?: string;
  posted?: boolean;
}

export type CommsPlatform = 'telegram' | 'discord' | 'messenger';
export type CommsRoute = 'telegram_user' | 'telegram_api' | 'discord_bot' | 'extension_cdp_inject';

export interface CommsMessage {
  id: string;
  platform: CommsPlatform;
  thread_id: string;
  thread_key: string;
  thread_name: string;
  thread_url?: string | null;
  route: CommsRoute;
  author: string;
  text: string;
  direction: 'in' | 'out';
  timestamp: number;
  backfill?: boolean;
}

export interface CommsThread {
  key: string;
  platform: CommsPlatform;
  thread_id: string;
  thread_name: string;
  thread_url?: string | null;
  route: CommsRoute;
  unread: number;
  last_ts: number;
  messages: CommsMessage[];
  vip?: boolean;
  muted?: boolean;
}

export interface ClientInfo {
  id: string;
  kind: 'desktop' | 'extension' | 'unknown';
  browser: string | null;
  connected_at: number;
}

export interface GameBugRecord {
  id: string;
  game: string;
  channel: string;
  channel_id: number;
  message_id: number;
  author: string;
  description: string;
  status: 'open' | 'investigating' | 'resolved';
  diagnosis?: string;
  fix_snippet?: string;
  developer_response?: string;
  timestamp: number;
}

export type HudActionType =
  | 'hud_notify'
  | 'suggest_routine'
  | 'memory_note'
  | 'open_url'
  | 'code_suggestion'
  | 'screen_action'
  | 'screen_workflow'
  | 'telegram_alert'
  | 'save_skill'
  | 'none';

export interface HudNotifyPayload { title: string; body: string; level?: 'info' | 'warn' | 'success' }
export interface SuggestRoutinePayload { items: string[] }
export interface MemoryNotePayload { content: string }
export interface OpenUrlPayload { url: string; label?: string }
export interface CodeSuggestionPayload { language?: string; code: string; explanation?: string }
export interface SaveSkillPayload { name: string; code: string; triggers: string[] }
export interface ScreenActionPayload {
  action: 'click' | 'double_click' | 'right_click' | 'move' | 'type' | 'press' | 'hotkey' | 'scroll' | 'drag' | 'wait' | 'open_url';
  x?: number;
  y?: number;
  text?: string;
  key?: string;
  keys?: string[];
  clicks?: number;
  button?: string;
  interval?: number;
  duration?: number;
  seconds?: number;
}
export interface ScreenWorkflowPayload { steps: ScreenActionPayload[] }
export interface TelegramAlertPayload { message: string }

export interface HudAction {
  type: HudActionType;
  payload: Record<string, unknown>;
}

export interface OrchestratorResponse {
  intent: Intent;
  tier: Tier;
  summary: string;
  actions: HudAction[];
  model?: string | null;
  latency_ms?: number | null;
}

// ------------------------------------------------------- daemon -> client

interface Base { timestamp: number }

export interface HelloEvent extends Base {
  event: 'HELLO';
  version: string;
  client_id: string;
  agents: AgentStates;
  telemetry: TelemetrySnapshot;
  clients: ClientInfo[];
  recent_memories: MemoryRecord[];
  personality_mode?: PersonalityMode;
  tts_voices?: Array<{ id: string; name: string }>;
  tts_settings?: Record<string, unknown>;
  game_bugs?: GameBugRecord[];
  approvals?: ApprovalItem[];
  comms_threads?: CommsThread[];
  pacing?: PacingState;
  self_evolve_status?: SelfEvolveStatusEvent | null;
  // Feature Bible
  workspaces?: Workspace[];
  stations?: Array<{ name: string; auto: boolean }>;
  grimoire?: GrimoireEntry[];
  runs?: RunInfo[];
  ideas?: IdeaRecord[];
  rules?: UserRule[];
  staged_reply?: StagedReply | null;
  producer?: ProducerState | null;
  assets?: AssetRecord[];
  media?: MediaState | null;
  agent?: AgentActivity;
  xp?: XPState;
  location?: GeoLocation | null;
  active_venture?: string | null;
}
export interface ScreenDeltaEvent extends Base {
  event: 'SCREEN_DELTA_DETECTED';
  phash_diff: number;
  extracted_text: string;
  window_title?: string | null;
  focus_changed?: boolean;
  ocr_available?: boolean;
}
export interface BrowserTabMutatedEvent {
  event: 'BROWSER_TAB_MUTATED';
  url: string;
  title: string;
  text_sample: string;
  timestamp?: number;
  browser?: string;
  tab_id?: number;
}
export interface MemoryCommittedEvent extends Base { event: 'MEMORY_COMMITTED'; record: MemoryRecord }
export interface MemoryResultsEvent extends Base { event: 'MEMORY_RESULTS'; query: string; results: MemoryRecord[] }
export interface TelemetryEvent extends Base { event: 'TELEMETRY'; data: TelemetrySnapshot }
export interface AgentStateEvent extends Base { event: 'AGENT_STATE'; agents: AgentStates }
export interface ClientsStateEvent extends Base { event: 'CLIENTS_STATE'; clients: ClientInfo[] }
export interface OrchestratorThinkingEvent extends Base {
  event: 'ORCHESTRATOR_THINKING';
  request_id: string;
  trigger: Trigger;
}
export interface OrchestratorResponseEvent extends Base {
  event: 'ORCHESTRATOR_RESPONSE';
  request_id: string;
  trigger: Trigger;
  prompt: string;
  response: OrchestratorResponse;
}
export interface DiscordMessageEvent extends Base {
  event: 'DISCORD_MESSAGE';
  channel: string;
  author: string;
  content: string;
  actionable: boolean;
}
export interface GameBugReportReceivedEvent extends Base {
  event: 'GAME_BUG_REPORT_RECEIVED';
  bug: GameBugRecord;
}
export interface AudioCueEvent extends Base {
  event: 'AUDIO_CUE';
  cue: 'listen_start' | 'command_detected' | 'task_finished' | 'proactive_alert' | 'celestial_chime' | 'approval_required';
  detail?: string;
}
export interface ProactiveInsightEvent extends Base {
  event: 'PROACTIVE_INSIGHT';
  id: string;
  context_type: 'code' | 'game_dev' | 'browser_docs' | 'browser_article' | 'general';
  window_title: string;
  title: string;
  summary: string;
  actions: HudAction[];
}
export interface ScreenWorkflowStartedEvent extends Base {
  event: 'SCREEN_WORKFLOW_STARTED';
  request_id: string;
  total_steps: number;
}
export interface ScreenWorkflowProgressEvent extends Base {
  event: 'SCREEN_WORKFLOW_PROGRESS';
  step_index: number;
  total_steps: number;
  step: Record<string, unknown>;
}
export interface ScreenWorkflowCompleteEvent extends Base {
  event: 'SCREEN_WORKFLOW_COMPLETE';
  request_id: string;
  result: Record<string, unknown>;
}
export interface PersonalityUpdatedEvent extends Base {
  event: 'PERSONALITY_UPDATED';
  personality: PersonalityMode;
}
export interface TtsSettingsUpdatedEvent extends Base {
  event: 'TTS_SETTINGS_UPDATED';
  settings: Record<string, unknown>;
}
export interface AudioPlayEvent {
  event: 'AUDIO_PLAY';
  stream_id: string;
  text: string;
  audio_base64: string;
  sample_rate: number;
  duration_s: number;
  timestamp?: number;
}
export interface AudioWaveformDataEvent {
  event: 'AUDIO_WAVEFORM_DATA';
  stream_id: string;
  amplitude: number;
  fft: number[];
  is_playing: boolean;
  timestamp?: number;
}
export interface SkillCrystallizedEvent {
  event: 'SKILL_CRYSTALLIZED';
  result: Record<string, unknown>;
  timestamp?: number;
}
export interface SkillSavedEvent {
  event: 'SKILL_SAVED';
  result: Record<string, unknown>;
  timestamp?: number;
}
export interface SkillsListEvent {
  event: 'SKILLS_LIST';
  skills: Array<Record<string, unknown>>;
  timestamp?: number;
}
// ---- V2 daemon -> client
export interface ApprovalRequiredEvent extends Base { event: 'APPROVAL_REQUIRED'; item: ApprovalItem }
export interface ApprovalResolvedEvent extends Base { event: 'APPROVAL_RESOLVED'; item: ApprovalItem }
export interface SelfEvolveStatusEvent extends Base {
  event: 'SELF_EVOLVE_STATUS';
  stage: 'planning' | 'ready' | 'failed';
  request: string;
  patch_id?: string;
  error?: string;
}
export interface CommsMessageEvent extends Base {
  event: 'COMMS_MESSAGE';
  message: CommsMessage;
  thread: Omit<CommsThread, 'messages'>;
}
export interface CommsReplyResultEvent extends Base { event: 'COMMS_REPLY_RESULT'; thread_key: string; ok: boolean; detail: string }
export interface CommsDraftEvent extends Base {
  event: 'COMMS_DRAFT';
  thread_key: string;
  draft_id: string | null;
  draft: string;
  model?: string | null;
  examples_used?: number;
  error?: string;
}
export interface PacingCheckinEvent extends Base { event: 'PACING_CHECKIN'; checkin: PacingCheckin; pacing: PacingState }
export interface PacingStateEvent extends Base { event: 'PACING_STATE'; pacing: PacingState }
export interface TrainingExportedEvent extends Base {
  event: 'TRAINING_EXPORTED';
  result: { path: string; examples: number; edited?: number; error?: string };
}
export interface ConsolidationResultEvent extends Base { event: 'CONSOLIDATION_RESULT'; result: Record<string, unknown> }

export interface LogEvent extends Base { event: 'LOG'; level: 'info' | 'warn' | 'error'; message: string }
export interface PongEvent extends Base { event: 'PONG' }

export type DaemonEvent =
  | BibleEvent
  | HelloEvent
  | ScreenDeltaEvent
  | (BrowserTabMutatedEvent & Base)
  | MemoryCommittedEvent
  | MemoryResultsEvent
  | TelemetryEvent
  | AgentStateEvent
  | ClientsStateEvent
  | OrchestratorThinkingEvent
  | OrchestratorResponseEvent
  | DiscordMessageEvent
  | GameBugReportReceivedEvent
  | AudioCueEvent
  | ProactiveInsightEvent
  | ScreenWorkflowStartedEvent
  | ScreenWorkflowProgressEvent
  | ScreenWorkflowCompleteEvent
  | PersonalityUpdatedEvent
  | TtsSettingsUpdatedEvent
  | AudioPlayEvent
  | AudioWaveformDataEvent
  | SkillCrystallizedEvent
  | SkillSavedEvent
  | SkillsListEvent
  | ApprovalRequiredEvent
  | ApprovalResolvedEvent
  | SelfEvolveStatusEvent
  | CommsMessageEvent
  | CommsReplyResultEvent
  | CommsDraftEvent
  | PacingCheckinEvent
  | PacingStateEvent
  | TrainingExportedEvent
  | ConsolidationResultEvent
  | LogEvent
  | PongEvent;

// ------------------------------------------------------- client -> daemon

export interface ClientHelloMessage { event: 'CLIENT_HELLO'; client: 'desktop' | 'extension'; browser?: string }
export interface UserPromptMessage { event: 'USER_PROMPT'; text: string; request_id?: string }
export interface VoiceTranscriptMessage { event: 'VOICE_TRANSCRIPT'; text: string }
export interface AgentToggleMessage { event: 'AGENT_TOGGLE'; agent: AgentId; enabled: boolean }
export interface MemoryQueryMessage { event: 'MEMORY_QUERY'; query: string; top_k?: number }
export interface RoutinesRequestMessage { event: 'ROUTINES_REQUEST' }
export interface PingMessage { event: 'PING' }
export interface SetPersonalityMessage { event: 'SET_PERSONALITY'; personality: PersonalityMode }
export interface SetTtsVoiceMessage { event: 'SET_TTS_VOICE'; voice_id?: string; rate?: number; volume?: number }
export interface ExecuteScreenActionMessage { event: 'EXECUTE_SCREEN_ACTION'; action: Record<string, unknown> }
export interface ExecuteScreenWorkflowMessage { event: 'EXECUTE_SCREEN_WORKFLOW'; steps: Array<Record<string, unknown>>; request_id?: string }
export interface SendTelegramMessage { event: 'SEND_TELEGRAM'; text: string; include_screen?: boolean }
export interface DiscordSendResponseMessage { event: 'DISCORD_SEND_RESPONSE'; bug_id: string; response: string }
export interface AudioCueMessage { event: 'AUDIO_CUE'; cue: string }
export interface SaveSkillMessage { event: 'SAVE_SKILL'; name: string; code: string; triggers: string[] }
export interface SpeakMessage { event: 'SPEAK'; text: string; voice?: string; speed?: number }
export interface GetSkillsMessage { event: 'GET_SKILLS' }

export interface DiscordImportMessage {
  event: 'DISCORD_IMPORT';
  messages: Array<{ author: string; content: string; channel?: string; timestamp?: string }>;
}
export interface ChromeScrapeRequestMessage {
  event: 'CHROME_SCRAPE_REQUEST';
  url: string;
}

// ---- V2 client -> daemon
export interface ApprovalDecisionMessage { event: 'APPROVAL_DECISION'; id: string; decision: 'approve' | 'edit' | 'reject'; text?: string }
export interface PatchDecisionMessage { event: 'PATCH_DECISION'; id: string; action: 'apply' | 'reject' | 'revert' }
export interface SelfEvolveRequestMessage { event: 'SELF_EVOLVE_REQUEST'; request: string }
export interface CommsReplyMessage { event: 'COMMS_REPLY'; thread_key: string; text: string; draft_id?: string | null }
export interface CommsDraftRequestMessage { event: 'COMMS_DRAFT_REQUEST'; thread_key: string }
export interface CommsMarkReadMessage { event: 'COMMS_MARK_READ'; thread_key: string }
export interface PacingAckMessage { event: 'PACING_ACK'; action: 'start_break' | 'snooze'; routine?: RoutineKind }
export interface ConsolidateRequestMessage { event: 'CONSOLIDATE_REQUEST' }
export interface ExportTrainingMessage { event: 'EXPORT_TRAINING' }

export type ClientMessage =
  | BibleClientMessage
  | ClientHelloMessage
  | UserPromptMessage
  | VoiceTranscriptMessage
  | AgentToggleMessage
  | MemoryQueryMessage
  | RoutinesRequestMessage
  | PingMessage
  | SetPersonalityMessage
  | SetTtsVoiceMessage
  | ExecuteScreenActionMessage
  | ExecuteScreenWorkflowMessage
  | SendTelegramMessage
  | DiscordSendResponseMessage
  | AudioCueMessage
  | SaveSkillMessage
  | SpeakMessage
  | GetSkillsMessage
  | DiscordImportMessage
  | ChromeScrapeRequestMessage
  | ApprovalDecisionMessage
  | PatchDecisionMessage
  | SelfEvolveRequestMessage
  | CommsReplyMessage
  | CommsDraftRequestMessage
  | CommsMarkReadMessage
  | PacingAckMessage
  | ConsolidateRequestMessage
  | ExportTrainingMessage
  | BrowserTabMutatedEvent;

// =============================================================== Feature Bible

export interface PreloadSummary { dir: string; rules: number; workspaces: number; grimoire: number; vips: number; routines: number }
export interface HydrationState { today: number; goal: number }
export interface CircadianState {
  phase: 'normal' | 'wind_down' | 'night';
  bedtime: string;
  wind_down_starts?: string;
  trigger?: 'sunset';
  sun?: { elevation: number; is_day: boolean; sunset: string | null; sunrise: string | null };
}

export interface Workspace {
  id: string;
  name: string;
  path: string;
  exists: boolean;
  start_cmd: string;
  ports: number[];
  tags: string[];
  scripts: string[];
  harvested?: boolean;
}

export interface GrimoireEntry {
  id: string;
  kind: 'shell' | 'workflow' | 'internal' | 'snippet' | 'userscript';
  description: string;
  voice_triggers: string[];
  command?: string;
  path?: string;
  language?: string;
  source?: string;
  requires_approval?: boolean;
  has_steps?: boolean;
  snippet?: string;
  score?: number;
}

export interface RunDiagnosis { diagnosis: string; fix_command?: string | null; source?: string; destructive?: boolean }
export interface RunInfo {
  id: string;
  command: string;
  cwd?: string | null;
  label: string;
  kind: 'task' | 'service';
  started_at: number;
  ended_at?: number | null;
  exit_code?: number | null;
  running: boolean;
  diagnosis?: RunDiagnosis | null;
  restarts: number;
  tail: string[];
}

export interface IdeaRecord { id: string; at: number; kind: string; project: string; text: string; file?: string }
export interface UserRule { id: string; timestamp: number; text: string; source: string; tier: number }
export interface StagedReply { id: string; thread_key: string; name: string; platform: string; text: string; at: number }
export interface ProducerState {
  title: string;
  started_at: number;
  elapsed: number;
  topics: Array<{ title: string; at: number }>;
  highlights: Array<{ at: number; note: string }>;
}
export interface AssetAction { id: string; label: string; enabled: boolean; reason?: string | null }
export interface AssetRecord {
  id: string;
  name: string;
  path: string;
  kind: string;
  bytes: number;
  actions: AssetAction[];
  outputs: Array<{ action: string; path: string; name: string; bytes: number }>;
  error?: string;
}
export interface GitRepoStatus {
  id: string;
  name: string;
  path: string;
  branch: string;
  dirty_files: number;
  recent: Array<{ hash: string; subject: string; time: number; author: string }>;
  last_commit_at: number | null;
  ahead?: number;
}
export interface RunwaySummary {
  balances: Array<{ name: string; amount: number }>;
  monthly_costs: Array<{ name: string; amount: number; due_day?: number }>;
  monthly_revenue: Array<{ name: string; amount: number }>;
  milestones: Array<{ name: string; metric?: string; target: number; current: number; progress: number }>;
  opencollective_slug?: string;
  opencollective_balance: number;
  total_balance: number;
  monthly_costs_total: number;
  monthly_revenue_total: number;
  monthly_net: number;
  runway_months: number | null;
  milestone_progress: Array<{ name: string; metric?: string; target: number; current: number; progress: number }>;
}
export interface CommsDigest { at: number; threads: string[]; count: number; summary: string }
export interface ClipboardSuggestion { id: string; kind: string; preview: string; actions: Array<{ id: string; label: string }> }
export interface ConditioningRoutine extends RoutineSuggestion {}

type E<N extends string, P = {}> = Base & { event: N } & P;
export type BibleEvent =
  | E<'PRELOAD_UPDATED', { summary: PreloadSummary; workspaces: Workspace[] }>
  | E<'GRIMOIRE', { grimoire: GrimoireEntry[] }>
  | E<'GRIMOIRE_RESULTS', { query: string; results: GrimoireEntry[] }>
  | E<'SNIPPET', { entry: GrimoireEntry }>
  | E<'RUN_STARTED', { run: RunInfo }>
  | E<'RUN_OUTPUT', { run_id: string; lines: string[] }>
  | E<'RUN_FINISHED', { run: RunInfo }>
  | E<'RUN_DIAGNOSIS', { run: RunInfo }>
  | E<'RUNS', { runs: RunInfo[] }>
  | E<'CLIPBOARD_SUGGESTION', ClipboardSuggestion>
  | E<'MACRO_STATE', { recording: boolean; message?: string }>
  | E<'MACRO_RECORDED', { id: string; steps: Array<Record<string, unknown>>; summary: string }>
  | E<'BATTLE_STATION', { station: string; report: Record<string, unknown> }>
  | E<'PROJECT_CONTEXT', { workspace: Workspace; memories: MemoryRecord[]; git: GitRepoStatus | null }>
  | E<'GIT_RADAR', { repos: GitRepoStatus[] }>
  | E<'IDEA_LOGGED', { idea: IdeaRecord }>
  | E<'IDEAS', { ideas: IdeaRecord[] }>
  | E<'ASSET_RESULT', { asset_id: string; result?: { action: string; path: string; name: string; bytes: number }; error?: string }>
  | E<'PRODUCER_STATE', { producer: ProducerState | null; notes_path?: string | null }>
  | E<'PRODUCER_CARD', { title: string; hits: MemoryRecord[] }>
  | E<'HYDRATION', { hydration: HydrationState }>
  | E<'WIND_DOWN', { circadian: CircadianState }>
  | E<'BREATHWORK_START', { routine: RoutineSuggestion }>
  | E<'RUNWAY', { runway: RunwaySummary }>
  | E<'VAULT', { dir: string; files: Array<{ name: string; bytes: number; indexed?: boolean }>; synced?: Record<string, unknown> }>
  | E<'RULES', { rules: UserRule[] }>
  | E<'HARVEST_PROGRESS', { message: string; progress: number }>
  | E<'HARVEST_RESULT', { result?: Record<string, unknown>; error?: string; workspaces?: Workspace[]; grimoire?: GrimoireEntry[] }>
  | E<'VOICE_REPLY_STAGED', { reply: StagedReply }>
  | E<'VOICE_REPLY_CLEARED', { sent?: boolean; detail?: string }>
  | E<'COMMS_DIGEST', { digest: CommsDigest }>
  | E<'COMMS_ROUTING', { routing: Record<string, unknown>; threads: CommsThread[] }>
  | E<'MEDIA_STATE', { media: MediaState | null }>
  | E<'AGENT_STATUS', { agent: AgentActivity }>
  | E<'REPLY_SUGGESTIONS', { thread_key: string; for_id: string; replies: string[]; model: string }>
  | E<'XP', { xp: XPState; award?: XPAward }>
  | E<'CIRCADIAN', { circadian: CircadianState; location: GeoLocation | null }>
  | E<'SANCTUARY_START', { routine: RoutineSuggestion }>
  | E<'BRIEFING', { name: string; text: string }>;

export interface XPAward { at: number; kind: string; xp: number; note: string; level_up?: number | null }
export interface XPState {
  xp: number;
  level: number;
  level_start: number;
  next_level: number;
  today: number;
  skin: string;
  skins: Array<{ id: string; name: string; level: number; unlocked: boolean }>;
  recent: XPAward[];
}
export interface MomentumState {
  score: number;
  swarm: number;
  debris: Array<{ id: string; label: string; tab: string; count: number }>;
}
export interface GeoLocation { lat: number; lon: number; label?: string }

export interface MediaState {
  title: string;
  artist: string;
  album: string;
  app: string;
  playing: boolean;
  position: number | null;
  duration: number | null;
  art: string | null;
}
export interface AgentActivity {
  state: 'idle' | 'working' | 'done';
  status: string;
  project: string;
  since: number;
  agents: number;
  duration?: number;
  agent?: string;
  watching?: string[];
  probable?: boolean;
}

export type BibleClientEvent =
  | 'GRIMOIRE_LIST' | 'GRIMOIRE_SEARCH' | 'GRIMOIRE_RUN' | 'RUN_STOP' | 'RUN_FIX' | 'RUNS_LIST'
  | 'CLIPBOARD_ACTION' | 'MACRO_START' | 'MACRO_STOP' | 'MACRO_SAVE' | 'MACRO_DISCARD'
  | 'BATTLE_STATION' | 'WORKSPACE_LAUNCH' | 'GIT_RADAR' | 'DEVLOG_REQUEST'
  | 'VOICE_REPLY_SEND' | 'VOICE_REPLY_CANCEL' | 'COMMS_MUTE' | 'DIGEST_REQUEST'
  | 'IDEAS_LIST' | 'ASSET_ACTION' | 'ASSET_REVEAL' | 'PRODUCER' | 'HYDRATION_LOG'
  | 'RUNWAY_GET' | 'RUNWAY_SAVE' | 'VAULT_LIST' | 'VAULT_SYNC'
  | 'RULES_LIST' | 'RULE_ADD' | 'RULE_DELETE' | 'HARVEST_REQUEST' | 'PRELOAD_INFO'
  | 'MEDIA_CONTROL' | 'SUGGEST_REPLIES'
  | 'LOCATION_SET' | 'SANCTUARY_DONE' | 'XP_SKIN' | 'ACTIVE_VENTURE';
export interface BibleClientMessage { event: BibleClientEvent; [key: string]: unknown }
