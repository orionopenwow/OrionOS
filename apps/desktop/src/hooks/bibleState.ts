/** Feature Bible client state: one reducer fed by every daemon event (unknown events are ignored). */
import type {
  GeoLocation,
  MomentumState,
  XPAward,
  XPState,
  AgentActivity,
  AssetRecord,
  MediaState,
  BibleClientEvent,
  CircadianState,
  ClipboardSuggestion,
  CommsDigest,
  DaemonEvent,
  GitRepoStatus,
  GrimoireEntry,
  HydrationState,
  IdeaRecord,
  MemoryRecord,
  PreloadSummary,
  ProducerState,
  RoutineSuggestion,
  RunInfo,
  RunwaySummary,
  StagedReply,
  UserRule,
  Workspace,
} from '@orion/shared-types';

export interface BibleState {
  preload: PreloadSummary | null;
  workspaces: Workspace[];
  stations: Array<{ name: string; auto: boolean }>;
  activeStation: string | null;
  grimoire: GrimoireEntry[];
  grimoireResults: { query: string; results: GrimoireEntry[] } | null;
  snippet: GrimoireEntry | null;
  runs: RunInfo[];
  clipboard: ClipboardSuggestion | null;
  recording: boolean;
  recordingMessage: string | null;
  pendingMacro: { id: string; summary: string; steps: Array<Record<string, unknown>> } | null;
  radar: GitRepoStatus[];
  projectContext: { workspace: Workspace; memories: MemoryRecord[]; git: GitRepoStatus | null } | null;
  ideas: IdeaRecord[];
  assets: AssetRecord[];
  producer: ProducerState | null;
  producerCard: { title: string; hits: MemoryRecord[]; at: number } | null;
  producerNotesPath: string | null;
  hydration: HydrationState | null;
  circadian: CircadianState | null;
  windDownNotice: boolean;
  breathwork: RoutineSuggestion | null;
  runway: RunwaySummary | null;
  vault: { dir: string; files: Array<{ name: string; bytes: number; indexed?: boolean }> } | null;
  rules: UserRule[];
  harvest: { message: string; progress: number; running: boolean; result?: Record<string, unknown>; error?: string } | null;
  stagedReply: StagedReply | null;
  replyResult: { sent?: boolean; detail?: string; at: number } | null;
  digest: CommsDigest | null;
  media: MediaState | null;
  agent: AgentActivity | null;
  suggestions: Record<string, { for_id: string; replies: string[]; model: string }>;
  xp: XPState | null;
  xpToast: XPAward | null;
  momentum: MomentumState | null;
  location: GeoLocation | null;
  sanctuary: RoutineSuggestion | null;
  briefing: { name: string; text: string; at: number } | null;
  activeVenture: string | null;
}

export const initialBible: BibleState = {
  preload: null,
  workspaces: [],
  stations: [],
  activeStation: null,
  grimoire: [],
  grimoireResults: null,
  snippet: null,
  runs: [],
  clipboard: null,
  recording: false,
  recordingMessage: null,
  pendingMacro: null,
  radar: [],
  projectContext: null,
  ideas: [],
  assets: [],
  producer: null,
  producerCard: null,
  producerNotesPath: null,
  hydration: null,
  circadian: null,
  windDownNotice: false,
  breathwork: null,
  runway: null,
  vault: null,
  rules: [],
  harvest: null,
  stagedReply: null,
  replyResult: null,
  digest: null,
  media: null,
  agent: null,
  suggestions: {},
  xp: null,
  xpToast: null,
  momentum: null,
  location: null,
  sanctuary: null,
  briefing: null,
  activeVenture: null,
};

export type BibleAction =
  | DaemonEvent
  | { event: '__LOCAL'; patch: Partial<BibleState> }
  | { event: '__ASSET_ADDED'; asset: AssetRecord };

const MAX_RUNS = 40;
const MAX_TAIL = 400;

function upsertRun(runs: RunInfo[], run: RunInfo): RunInfo[] {
  const prev = runs.find((r) => r.id === run.id);
  // keep the longer local tail (live RUN_OUTPUT lines) when the daemon sends a 60-line snapshot
  const tail = prev && prev.tail.length > run.tail.length ? prev.tail : run.tail;
  return [{ ...run, tail }, ...runs.filter((r) => r.id !== run.id)].slice(0, MAX_RUNS);
}

export function bibleReducer(state: BibleState, ev: BibleAction): BibleState {
  const e = ev as any;
  switch (ev.event) {
    case '__LOCAL':
      return { ...state, ...e.patch };
    case '__ASSET_ADDED':
      return { ...state, assets: [e.asset, ...state.assets.filter((a) => a.id !== e.asset.id)].slice(0, 30) };
    case 'HELLO': {
      const t = e.telemetry ?? {};
      return {
        ...state,
        workspaces: e.workspaces ?? state.workspaces,
        stations: e.stations ?? state.stations,
        grimoire: e.grimoire ?? state.grimoire,
        runs: e.runs ?? state.runs,
        ideas: e.ideas ?? state.ideas,
        rules: e.rules ?? state.rules,
        stagedReply: e.staged_reply ?? null,
        producer: e.producer ?? null,
        preload: t.preload ?? state.preload,
        hydration: t.hydration ?? state.hydration,
        circadian: t.circadian ?? state.circadian,
        recording: !!t.recording_macro,
        activeStation: t.active_station ?? null,
        media: e.media ?? null,
        agent: e.agent ?? null,
        xp: e.xp ?? state.xp,
        location: e.location ?? null,
        activeVenture: e.active_venture ?? null,
        momentum: t.momentum ?? state.momentum,
      };
    }
    case 'TELEMETRY': {
      const t = e.data ?? {};
      if (!('preload' in t)) return state;
      const producer = t.producer ?? null;
      if (
        state.recording === !!t.recording_macro &&
        state.activeStation === (t.active_station ?? null) &&
        state.hydration?.today === t.hydration?.today &&
        state.circadian?.phase === t.circadian?.phase &&
        state.preload?.rules === t.preload?.rules &&
        state.preload?.workspaces === t.preload?.workspaces &&
        JSON.stringify(state.momentum) === JSON.stringify(t.momentum ?? null) &&
        (state.producer?.elapsed ?? -1) === (producer?.elapsed ?? -1)
      ) {
        return state; // avoid re-rendering the whole HUD every 2 s for nothing
      }
      return {
        ...state,
        preload: t.preload,
        hydration: t.hydration,
        circadian: t.circadian,
        recording: !!t.recording_macro,
        activeStation: t.active_station ?? null,
        producer,
        momentum: t.momentum ?? null,
      };
    }
    case 'PRELOAD_UPDATED':
      return { ...state, preload: e.summary, workspaces: e.workspaces };
    case 'GRIMOIRE':
      return { ...state, grimoire: e.grimoire };
    case 'GRIMOIRE_RESULTS':
      return { ...state, grimoireResults: { query: e.query, results: e.results } };
    case 'SNIPPET':
      return { ...state, snippet: e.entry };
    case 'RUN_STARTED':
    case 'RUN_FINISHED':
    case 'RUN_DIAGNOSIS':
      return { ...state, runs: upsertRun(state.runs, e.run) };
    case 'RUN_OUTPUT':
      return {
        ...state,
        runs: state.runs.map((r) => (r.id === e.run_id ? { ...r, tail: [...r.tail, ...e.lines].slice(-MAX_TAIL) } : r)),
      };
    case 'RUNS':
      return { ...state, runs: e.runs };
    case 'CLIPBOARD_SUGGESTION':
      return { ...state, clipboard: { id: e.id, kind: e.kind, preview: e.preview, actions: e.actions } };
    case 'MACRO_STATE':
      return { ...state, recording: e.recording, recordingMessage: e.message ?? null };
    case 'MACRO_RECORDED':
      return { ...state, recording: false, pendingMacro: { id: e.id, summary: e.summary, steps: e.steps } };
    case 'BATTLE_STATION':
      return { ...state, activeStation: e.station };
    case 'GIT_RADAR':
      return { ...state, radar: e.repos };
    case 'IDEA_LOGGED':
      return { ...state, ideas: [...state.ideas.filter((i) => i.id !== e.idea.id), e.idea].slice(-60) };
    case 'IDEAS':
      return { ...state, ideas: e.ideas };
    case 'ASSET_RESULT':
      return {
        ...state,
        assets: state.assets.map((a) =>
          a.id === e.asset_id && e.result ? { ...a, outputs: [...a.outputs, e.result], error: undefined }
            : a.id === e.asset_id && e.error ? { ...a, error: e.error } : a,
        ),
      };
    case 'PRODUCER_STATE':
      return { ...state, producer: e.producer, producerNotesPath: e.notes_path ?? state.producerNotesPath };
    case 'PRODUCER_CARD':
      return { ...state, producerCard: { title: e.title, hits: e.hits, at: Date.now() } };
    case 'HYDRATION':
      return { ...state, hydration: e.hydration };
    case 'WIND_DOWN':
      return { ...state, circadian: e.circadian, windDownNotice: true };
    case 'BREATHWORK_START':
      return { ...state, breathwork: e.routine };
    case 'RUNWAY':
      return { ...state, runway: e.runway };
    case 'VAULT':
      return { ...state, vault: { dir: e.dir, files: e.files } };
    case 'RULES':
      return { ...state, rules: e.rules };
    case 'HARVEST_PROGRESS':
      return { ...state, harvest: { message: e.message, progress: e.progress, running: true } };
    case 'HARVEST_RESULT':
      return {
        ...state,
        harvest: { message: e.error ? 'Harvest failed' : 'Harvest complete', progress: 1, running: false, result: e.result, error: e.error },
        workspaces: e.workspaces ?? state.workspaces,
        grimoire: e.grimoire ?? state.grimoire,
      };
    case 'VOICE_REPLY_STAGED':
      return { ...state, stagedReply: e.reply, replyResult: null };
    case 'VOICE_REPLY_CLEARED':
      return {
        ...state,
        stagedReply: null,
        replyResult: e.sent === undefined ? state.replyResult : { sent: e.sent, detail: e.detail, at: Date.now() },
      };
    case 'XP':
      return { ...state, xp: e.xp, xpToast: e.award ?? state.xpToast };
    case 'CIRCADIAN':
      return { ...state, circadian: e.circadian, location: e.location };
    case 'SANCTUARY_START':
      return { ...state, sanctuary: e.routine };
    case 'BRIEFING':
      return { ...state, briefing: { name: e.name, text: e.text, at: Date.now() } };
    case 'PROJECT_CONTEXT':
      return { ...state, projectContext: { workspace: e.workspace, memories: e.memories, git: e.git }, activeVenture: e.workspace.id };
    case 'MEDIA_STATE':
      return { ...state, media: e.media };
    case 'AGENT_STATUS':
      return { ...state, agent: e.agent };
    case 'REPLY_SUGGESTIONS':
      return { ...state, suggestions: { ...state.suggestions, [e.thread_key]: { for_id: e.for_id, replies: e.replies, model: e.model } } };
    case 'COMMS_DIGEST':
      return { ...state, digest: e.digest };
    default:
      return state;
  }
}

export type BibleSend = (event: BibleClientEvent, payload?: Record<string, unknown>) => boolean;
