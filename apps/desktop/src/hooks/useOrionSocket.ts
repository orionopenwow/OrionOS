import { useCallback, useEffect, useReducer, useRef, useState } from 'react';
import { DEMO, DemoSocket } from '../demo/demoDaemon';
import { withToken } from '../lib/daemonAuth';
import {
  ORION_WS_URL,
  type AgentStates,
  type ApprovalItem,
  type CommsDraftEvent,
  type CommsReplyResultEvent,
  type CommsThread,
  type PacingCheckin,
  type PacingState,
  type RoutineKind,
  type SelfEvolveStatusEvent,
  type TrainingExportedEvent,
  type ClientInfo,
  type ClientMessage,
  type DaemonEvent,
  type GameBugRecord,
  type MemoryRecord,
  type MemoryResultsEvent,
  type OrchestratorResponseEvent,
  type PersonalityMode,
  type ProactiveInsightEvent,
  type ScreenWorkflowProgressEvent,
  type TelemetrySnapshot,
} from '@orion/shared-types';
import { colors, sourceColors } from '../theme/colors';
import { cosmicAudio } from '../lib/cosmicAudio';
import { bibleReducer, initialBible, type BibleSend, type BibleState } from './bibleState';
import { emitDaemonEvent } from '../lib/daemonEvents';

export interface FeedItem {
  id: string;
  ts: number;
  label: string;
  detail: string;
  color: string;
}

const DEFAULT_AGENTS: AgentStates = {
  coding_agent: true,
  routine_optimizer: true,
  discord_monitor: false,
  system_capturer: true,
  proactive_observer: true,
};

const MAX_FEED = 150;
const MAX_MEMORIES = 120;
const MAX_APPROVALS = 60;
const MAX_THREAD_MESSAGES = 200;

function upsertApproval(list: ApprovalItem[], item: ApprovalItem): ApprovalItem[] {
  const next = [item, ...list.filter((i) => i.id !== item.id)];
  // Only resolved history is trimmed — an item still awaiting a decision is never dropped.
  const active = next.filter((i) => i.status === 'pending' || i.status === 'sending');
  const resolved = next.filter((i) => i.status !== 'pending' && i.status !== 'sending');
  return [...active, ...resolved.slice(0, MAX_APPROVALS)];
}
let seq = 0;

function toFeed(ev: DaemonEvent): FeedItem | null {
  const base = { id: `${Date.now()}-${seq++}`, ts: (ev as any).timestamp ?? Date.now() / 1000 };
  switch (ev.event) {
    case 'SCREEN_DELTA_DETECTED':
      return {
        ...base,
        label: `Screen Δ${ev.phash_diff}${ev.focus_changed ? ' · focus' : ''}`,
        detail: `${ev.window_title ?? 'Unknown window'} — ${ev.extracted_text.length} chars OCR`,
        color: colors.cyan,
      };
    case 'BROWSER_TAB_MUTATED':
      return { ...base, label: `Browser · ${ev.browser ?? 'tab'}`, detail: ev.title || ev.url, color: colors.purple };
    case 'MEMORY_COMMITTED':
      return {
        ...base,
        label: `Vector commit · ${ev.record.source}`,
        detail: ev.record.content.slice(0, 120),
        color: sourceColors[ev.record.source] || colors.cyan,
      };
    case 'ORCHESTRATOR_THINKING':
      return { ...base, label: 'LLM inference', detail: `trigger: ${ev.trigger}`, color: colors.violet };
    case 'ORCHESTRATOR_RESPONSE':
      return {
        ...base,
        label: `LLM ${ev.response.tier}${ev.response.latency_ms ? ` · ${ev.response.latency_ms}ms` : ''}`,
        detail: ev.response.summary.slice(0, 140),
        color: ev.response.tier === 'offline' ? colors.warn : colors.success,
      };
    case 'DISCORD_MESSAGE':
      return { ...base, label: `Discord ${ev.channel}`, detail: `${ev.author}: ${ev.content}`, color: '#7289DA' };
    case 'GAME_BUG_REPORT_RECEIVED':
      return {
        ...base,
        label: `Bug #${ev.bug.id}`,
        detail: `${ev.bug.author}: ${ev.bug.description.slice(0, 80)}`,
        color: colors.magenta,
      };
    case 'PROACTIVE_INSIGHT':
      return {
        ...base,
        label: `Insight · ${ev.context_type}`,
        detail: ev.title,
        color: colors.cyan,
      };
    case 'SCREEN_WORKFLOW_STARTED':
      return {
        ...base,
        label: 'Screen Workflow',
        detail: `Starting ${ev.total_steps} step(s)`,
        color: colors.success,
      };
    case 'SCREEN_WORKFLOW_COMPLETE':
      return {
        ...base,
        label: 'Workflow Complete',
        detail: `Executed steps: ${(ev.result as any).executed_steps ?? 0}`,
        color: colors.success,
      };
    case 'APPROVAL_REQUIRED':
      return {
        ...base,
        label: ev.item.kind === 'self_evolve' ? 'Patch ready' : `Approval · ${ev.item.category ?? 'prompt'}`,
        detail: ev.item.kind === 'self_evolve' ? ev.item.patch?.summary ?? '' : `${ev.item.author}: ${ev.item.original ?? ''}`.slice(0, 120),
        color: colors.amber,
      };
    case 'COMMS_MESSAGE':
      if (ev.message.backfill || ev.message.direction === 'out') return null;
      return {
        ...base,
        label: `Comms · ${ev.message.platform}`,
        detail: `${ev.message.author}: ${ev.message.text}`.slice(0, 140),
        color: sourceColors[ev.message.platform] || colors.cyan,
      };
    case 'PACING_CHECKIN':
      return {
        ...base,
        label: 'Pacing check-in',
        detail: `${ev.checkin.minutes} min focus · ${ev.checkin.suggestion.title}`,
        color: colors.amber,
      };
    case 'TRAINING_EXPORTED':
      return {
        ...base,
        label: 'Fine-tune export',
        detail: `${ev.result.examples} approved examples → ${ev.result.path}`,
        color: colors.success,
      };
    case 'SELF_EVOLVE_STATUS':
      return {
        ...base,
        label: `Self-evolve · ${ev.stage}`,
        detail: ev.error ? `${ev.request} — ${ev.error}` : ev.request,
        color: ev.stage === 'failed' ? colors.danger : colors.violet,
      };
    case 'LOG':
      return {
        ...base,
        label: ev.level.toUpperCase(),
        detail: ev.message,
        color: ev.level === 'error' ? colors.danger : ev.level === 'warn' ? colors.warn : colors.dust,
      };
    default:
      return null;
  }
}

export function useOrionSocket(url: string = ORION_WS_URL) {
  const [connected, setConnected] = useState(false);
  const [feed, setFeed] = useState<FeedItem[]>([]);
  const [telemetry, setTelemetry] = useState<TelemetrySnapshot | null>(null);
  const [agents, setAgents] = useState<AgentStates>(DEFAULT_AGENTS);
  const [clients, setClients] = useState<ClientInfo[]>([]);
  const [memories, setMemories] = useState<MemoryRecord[]>([]);
  const [responses, setResponses] = useState<OrchestratorResponseEvent[]>([]);
  const [pending, setPending] = useState<string[]>([]);
  const [searchResults, setSearchResults] = useState<MemoryResultsEvent | null>(null);
  const [proactiveInsights, setProactiveInsights] = useState<ProactiveInsightEvent[]>([]);
  const [gameBugs, setGameBugs] = useState<GameBugRecord[]>([]);
  const [personalityMode, setPersonalityMode] = useState<PersonalityMode>('concise_companion');
  const [ttsVoices, setTtsVoices] = useState<Array<{ id: string; name: string }>>([]);
  const [ttsSettings, setTtsSettings] = useState<Record<string, unknown>>({});
  const [workflowProgress, setWorkflowProgress] = useState<ScreenWorkflowProgressEvent | null>(null);

  // ---- V2 state
  const [approvals, setApprovals] = useState<ApprovalItem[]>([]);
  const [selfEvolveStatus, setSelfEvolveStatus] = useState<SelfEvolveStatusEvent | null>(null);
  const [commsThreads, setCommsThreads] = useState<CommsThread[]>([]);
  const [commsDrafts, setCommsDrafts] = useState<Record<string, CommsDraftEvent>>({});
  const [commsReplyResult, setCommsReplyResult] = useState<CommsReplyResultEvent | null>(null);
  const [pacing, setPacing] = useState<PacingState | null>(null);
  const [pacingCheckin, setPacingCheckin] = useState<PacingCheckin | null>(null);
  const [trainingExport, setTrainingExport] = useState<TrainingExportedEvent['result'] | null>(null);

  // Native audio playback & real-time waveform modulation
  const [audioAnalyser, setAudioAnalyser] = useState<AnalyserNode | null>(null);
  const [isAudioPlaying, setIsAudioPlaying] = useState(false);
  const [waveformBroadcast, setWaveformBroadcast] = useState<{
    amplitude: number;
    fft: number[];
    is_playing: boolean;
  } | null>(null);

  const audioCtxRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);

  const [latestBrowserTab, setLatestBrowserTab] = useState<{
    url: string;
    title: string;
    text_sample: string;
    timestamp?: number;
    browser?: string;
    tab_id?: number;
  } | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const [bible, bibleDispatch] = useReducer(bibleReducer, initialBible);
  // Sun & Moon: after sunset Orion's voice plays 15% softer.
  const voiceGainRef = useRef(1);
  useEffect(() => {
    voiceGainRef.current = bible.circadian && bible.circadian.phase !== 'normal' ? 0.85 : 1;
  }, [bible.circadian]);

  const handle = useCallback((ev: DaemonEvent) => {
    emitDaemonEvent(ev as unknown as { event: string }); // Art stage 6: the core's smart reactions listen here
    bibleDispatch(ev);
    const item = toFeed(ev);
    if (item) setFeed((f) => [item, ...f].slice(0, MAX_FEED));

    switch (ev.event) {
      case 'HELLO':
        setAgents(ev.agents);
        setTelemetry(ev.telemetry);
        setClients(ev.clients);
        setMemories(ev.recent_memories);
        if (ev.personality_mode) setPersonalityMode(ev.personality_mode);
        if (ev.tts_voices) setTtsVoices(ev.tts_voices);
        if (ev.tts_settings) setTtsSettings(ev.tts_settings);
        if (ev.game_bugs) setGameBugs(ev.game_bugs);
        if (ev.approvals) setApprovals(ev.approvals);
        // Daemon may have hot-reloaded mid-generation: trust its view, never a stale local spinner.
        setSelfEvolveStatus(ev.self_evolve_status ?? null);
        if (ev.comms_threads) setCommsThreads(ev.comms_threads);
        if (ev.pacing) {
          setPacing(ev.pacing);
          setPacingCheckin(ev.pacing.pending_checkin);
        }
        break;
      case 'TELEMETRY':
        setTelemetry(ev.data);
        if (ev.data.pacing) {
          setPacing(ev.data.pacing);
          // Walking away resets the session daemon-side; drop a check-in card that no longer applies.
          if (!ev.data.pacing.pending_checkin) setPacingCheckin(null);
        }
        if (ev.data.personality_mode) setPersonalityMode(ev.data.personality_mode);
        if (ev.data.tts_settings) setTtsSettings(ev.data.tts_settings);
        break;
      case 'AGENT_STATE':
        setAgents(ev.agents);
        break;
      case 'CLIENTS_STATE':
        setClients(ev.clients);
        break;
      case 'BROWSER_TAB_MUTATED':
        setLatestBrowserTab(ev);
        break;
      case 'MEMORY_COMMITTED':
        setMemories((m) => [ev.record, ...m.filter((r) => r.id !== ev.record.id)].slice(0, MAX_MEMORIES));
        break;
      case 'MEMORY_RESULTS':
        setSearchResults(ev);
        break;
      case 'ORCHESTRATOR_THINKING':
        setPending((p) => [...p, ev.request_id]);
        break;
      case 'ORCHESTRATOR_RESPONSE':
        setPending((p) => p.filter((id) => id !== ev.request_id));
        setResponses((r) => [ev, ...r].slice(0, 30));
        break;
      case 'GAME_BUG_REPORT_RECEIVED':
        setGameBugs((prev) => [ev.bug, ...prev.filter((b) => b.id !== ev.bug.id)]);
        break;
      case 'PROACTIVE_INSIGHT':
        setProactiveInsights((prev) => [ev, ...prev.filter((p) => p.id !== ev.id)].slice(0, 5));
        break;
      case 'SCREEN_WORKFLOW_PROGRESS':
        setWorkflowProgress(ev);
        break;
      case 'SCREEN_WORKFLOW_COMPLETE':
        setWorkflowProgress(null);
        break;
      case 'PERSONALITY_UPDATED':
        setPersonalityMode(ev.personality);
        break;
      case 'TTS_SETTINGS_UPDATED':
        setTtsSettings(ev.settings);
        break;
      case 'AUDIO_CUE':
        if (ev.cue === 'listen_start') cosmicAudio.playListenStart();
        else if (ev.cue === 'command_detected') cosmicAudio.playCommandDetected();
        else if (ev.cue === 'task_finished') cosmicAudio.playTaskFinished();
        else if (ev.cue === 'proactive_alert') cosmicAudio.playProactiveInsight();
        else if (ev.cue === 'celestial_chime') cosmicAudio.playCelestialChime();
        else if (ev.cue === 'approval_required') cosmicAudio.playApprovalPing();
        break;
      // ---------------------------------------------------------------- V2
      case 'APPROVAL_REQUIRED':
      case 'APPROVAL_RESOLVED':
        setApprovals((list) => upsertApproval(list, ev.item));
        break;
      case 'SELF_EVOLVE_STATUS':
        setSelfEvolveStatus(ev);
        break;
      case 'COMMS_MESSAGE':
        setCommsThreads((threads) => {
          const existing = threads.find((t) => t.key === ev.thread.key);
          const messages = existing
            ? [...existing.messages.filter((m) => m.id !== ev.message.id), ev.message].slice(-MAX_THREAD_MESSAGES)
            : [ev.message];
          const updated: CommsThread = { ...(existing ?? {}), ...ev.thread, messages } as CommsThread;
          return [updated, ...threads.filter((t) => t.key !== ev.thread.key)].sort((a, b) => b.last_ts - a.last_ts);
        });
        break;
      case 'COMMS_ROUTING': {
        const flags = new Map(ev.threads.map((t) => [t.key, t]));
        setCommsThreads((threads) =>
          threads.map((t) => {
            const f = flags.get(t.key);
            return f ? { ...t, vip: f.vip, muted: f.muted, unread: f.unread } : t;
          }),
        );
        break;
      }
      case 'COMMS_DRAFT':
        setCommsDrafts((d) => ({ ...d, [ev.thread_key]: ev }));
        break;
      case 'COMMS_REPLY_RESULT':
        setCommsReplyResult(ev);
        break;
      case 'PACING_CHECKIN':
        setPacing(ev.pacing);
        setPacingCheckin(ev.checkin);
        break;
      case 'PACING_STATE':
        setPacing(ev.pacing);
        setPacingCheckin(ev.pacing.pending_checkin);
        break;
      case 'TRAINING_EXPORTED':
        setTrainingExport(ev.result);
        break;
      case 'AUDIO_PLAY': {
        try {
          if (!audioCtxRef.current || audioCtxRef.current.state === 'closed') {
            const ctx = new (window.AudioContext || (window as any).webkitAudioContext)();
            const analyser = ctx.createAnalyser();
            analyser.fftSize = 256;
            analyser.smoothingTimeConstant = 0.8;
            analyser.connect(ctx.destination);
            audioCtxRef.current = ctx;
            analyserRef.current = analyser;
            setAudioAnalyser(analyser);
          }
          const ctx = audioCtxRef.current;
          const analyser = analyserRef.current;
          if (ctx.state === 'suspended') {
            void ctx.resume();
          }

          // Decode base64 audio
          const binary = atob(ev.audio_base64);
          const bytes = new Uint8Array(binary.length);
          for (let i = 0; i < binary.length; i++) {
            bytes[i] = binary.charCodeAt(i);
          }

          ctx.decodeAudioData(
            bytes.buffer.slice(0),
            (buffer) => {
              const source = ctx.createBufferSource();
              source.buffer = buffer;
              const gain = ctx.createGain();
              gain.gain.value = voiceGainRef.current;
              source.connect(gain);
              if (analyser) {
                gain.connect(analyser);
              } else {
                gain.connect(ctx.destination);
              }
              setIsAudioPlaying(true);
              source.onended = () => {
                setIsAudioPlaying(false);
              };
              source.start(0);
            },
            (err) => {
              console.error('Failed to decode Kokoro audio buffer', err);
            }
          );
        } catch (err) {
          console.error('Error handling AUDIO_PLAY event', err);
        }
        break;
      }
      case 'AUDIO_WAVEFORM_DATA':
        setWaveformBroadcast({
          amplitude: ev.amplitude,
          fft: ev.fft,
          is_playing: ev.is_playing,
        });
        setIsAudioPlaying(ev.is_playing);
        break;
    }
  }, []);

  useEffect(() => {
    let disposed = false;
    let retry = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const connect = async () => {
      if (disposed) return;
      // Guest demo (GitHub Pages): an in-browser stand-in daemon with sample data. Otherwise the daemon wants its
      // per-start key on the handshake (review B1); after a failed try, re-read it (the daemon may have restarted).
      const target = DEMO ? url : await withToken(url, retry > 0);
      if (disposed) return;
      const ws = DEMO ? (new DemoSocket() as unknown as WebSocket) : new WebSocket(target);
      wsRef.current = ws;
      ws.onopen = () => {
        retry = 0;
        setConnected(true);
        ws.send(JSON.stringify({ event: 'CLIENT_HELLO', client: 'desktop' } satisfies ClientMessage));
      };
      ws.onmessage = (msg) => {
        try {
          handle(JSON.parse(msg.data as string) as DaemonEvent);
        } catch (err) {
          console.error('Bad daemon message', err);
        }
      };
      ws.onclose = () => {
        setConnected(false);
        setPending([]);
        if (!disposed) timer = setTimeout(() => void connect(), Math.min(10_000, 500 * 2 ** retry++));
      };
      ws.onerror = () => ws.close();
    };

    void connect();
    return () => {
      disposed = true;
      clearTimeout(timer);
      wsRef.current?.close();
    };
  }, [url, handle]);

  const send = useCallback((msg: ClientMessage): boolean => {
    const ws = wsRef.current;
    if (ws?.readyState !== WebSocket.OPEN) return false;
    ws.send(JSON.stringify(msg));
    return true;
  }, []);

  const setPersonality = useCallback((personality: PersonalityMode) => {
    send({ event: 'SET_PERSONALITY', personality });
    setPersonalityMode(personality);
  }, [send]);

  const setTtsVoice = useCallback((voice_id?: string, rate?: number, volume?: number) => {
    send({ event: 'SET_TTS_VOICE', voice_id, rate, volume });
  }, [send]);

  const executeScreenAction = useCallback((action: Record<string, unknown>) => {
    send({ event: 'EXECUTE_SCREEN_ACTION', action });
  }, [send]);

  const executeScreenWorkflow = useCallback((steps: Array<Record<string, unknown>>) => {
    send({ event: 'EXECUTE_SCREEN_WORKFLOW', steps });
  }, [send]);

  const sendTelegram = useCallback((text: string, include_screen: boolean = false) => {
    send({ event: 'SEND_TELEGRAM', text, include_screen });
  }, [send]);

  const sendDiscordBugResponse = useCallback((bug_id: string, response: string) => {
    send({ event: 'DISCORD_SEND_RESPONSE', bug_id, response });
  }, [send]);

  const dismissInsight = useCallback((id: string) => {
    setProactiveInsights((prev) => prev.filter((p) => p.id !== id));
  }, []);

  const speak = useCallback((text: string, voice: string = 'af_nicole', speed: number = 0.92) => {
    send({ event: 'SPEAK', text, voice, speed });
  }, [send]);

  const saveSkill = useCallback((name: string, code: string, triggers: string[]) => {
    send({ event: 'SAVE_SKILL', name, code, triggers });
  }, [send]);

  // ---- V2 actions
  const decideApproval = useCallback((id: string, decision: 'approve' | 'edit' | 'reject', text?: string) => {
    send({ event: 'APPROVAL_DECISION', id, decision, text });
  }, [send]);

  const decidePatch = useCallback((id: string, action: 'apply' | 'reject' | 'revert') => {
    send({ event: 'PATCH_DECISION', id, action });
  }, [send]);

  const requestSelfEvolve = useCallback((request: string) => {
    send({ event: 'SELF_EVOLVE_REQUEST', request });
  }, [send]);

  const commsReply = useCallback((thread_key: string, text: string, draft_id?: string | null) => {
    setCommsReplyResult(null);
    send({ event: 'COMMS_REPLY', thread_key, text, draft_id });
    setCommsDrafts((d) => {
      const next = { ...d };
      delete next[thread_key];
      return next;
    });
  }, [send]);

  const requestCommsDraft = useCallback((thread_key: string) => {
    send({ event: 'COMMS_DRAFT_REQUEST', thread_key });
  }, [send]);

  const markCommsRead = useCallback((thread_key: string) => {
    send({ event: 'COMMS_MARK_READ', thread_key });
    setCommsThreads((threads) => threads.map((t) => (t.key === thread_key ? { ...t, unread: 0 } : t)));
  }, [send]);

  const ackPacing = useCallback((action: 'start_break' | 'snooze', routine?: RoutineKind) => {
    send({ event: 'PACING_ACK', action, routine });
    setPacingCheckin(null);
  }, [send]);

  const consolidateMemories = useCallback(() => {
    send({ event: 'CONSOLIDATE_REQUEST' });
  }, [send]);

  // ---- Feature Bible
  const bibleSend: BibleSend = useCallback(
    (event, payload = {}) => send({ ...payload, event } as ClientMessage),
    [send],
  );
  const biblePatch = useCallback((patch: Partial<BibleState>) => bibleDispatch({ event: '__LOCAL', patch }), []);
  const bibleAddAsset = useCallback(
    (asset: BibleState['assets'][number]) => bibleDispatch({ event: '__ASSET_ADDED', asset }),
    [],
  );

  return {
    connected,
    feed,
    telemetry,
    agents,
    clients,
    memories,
    responses,
    pending,
    searchResults,
    latestBrowserTab,
    proactiveInsights,
    gameBugs,
    personalityMode,
    ttsVoices,
    ttsSettings,
    workflowProgress,
    audioAnalyser,
    isAudioPlaying,
    waveformBroadcast,
    send,
    speak,
    saveSkill,
    setPersonality,
    setTtsVoice,
    executeScreenAction,
    executeScreenWorkflow,
    sendTelegram,
    sendDiscordBugResponse,
    dismissInsight,
    // V2
    approvals,
    selfEvolveStatus,
    commsThreads,
    commsDrafts,
    commsReplyResult,
    pacing,
    pacingCheckin,
    trainingExport,
    decideApproval,
    decidePatch,
    requestSelfEvolve,
    commsReply,
    requestCommsDraft,
    markCommsRead,
    ackPacing,
    consolidateMemories,
    // Feature Bible
    bible,
    bibleSend,
    biblePatch,
    bibleAddAsset,
  };
}

export type OrionSocket = ReturnType<typeof useOrionSocket>;
