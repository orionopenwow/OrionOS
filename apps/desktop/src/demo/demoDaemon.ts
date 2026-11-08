/**
 * Guest demo: an in-browser stand-in for the Orion daemon, used by the GitHub Pages build (VITE_DEMO=1).
 *
 * It speaks the same WebSocket protocol as the real brain, with sample data: a few chats, approvals,
 * projects, a coding-agent pet that works on a loop, XP, Sun & Moon. Simple commands ("log water",
 * "idea: …", "rule: …", "focus mode", "take a break", "wind down") work for real and are saved in this
 * browser only. Nothing leaves the visitor's machine; nothing is sent anywhere.
 */
import type {
  AgentActivity,
  ApprovalItem,
  CircadianState,
  CommsMessage,
  CommsThread,
  GeoLocation,
  GitRepoStatus,
  IdeaRecord,
  MediaState,
  MemoryRecord,
  PacingState,
  RoutineSuggestion,
  TelemetrySnapshot,
  UserRule,
  Workspace,
  XPAward,
  XPState,
} from '@orion/shared-types';
import { demoRoute } from './demoApi';

export const DEMO = import.meta.env.MODE === 'demo' || import.meta.env.VITE_DEMO === '1';

// ---------------------------------------------------------------- tiny persistence (per visitor, optional)
const KEY = 'orion-demo-v1';
interface Saved {
  water: { day: string; today: number };
  ideas: IdeaRecord[];
  rules: UserRule[];
  xp: number;
  skin: string;
  recent: XPAward[];
  location: GeoLocation | null;
}
function today() {
  return new Date().toISOString().slice(0, 10);
}
function load(): Saved {
  const fresh: Saved = { water: { day: today(), today: 3 }, ideas: [], rules: [], xp: 640, skin: 'default', recent: [], location: null };
  try {
    const raw = window.localStorage.getItem(KEY);
    if (!raw) return fresh;
    const s = { ...fresh, ...(JSON.parse(raw) as Partial<Saved>) };
    if (s.water.day !== today()) s.water = { day: today(), today: 0 };
    return s;
  } catch {
    return fresh;
  }
}
function save(s: Saved) {
  try {
    window.localStorage.setItem(KEY, JSON.stringify(s));
  } catch {
    /* storage blocked (private mode) — the demo still works for this visit */
  }
}

// ---------------------------------------------------------------- sample data
const now = () => Date.now() / 1000;
const T0 = now();
const uid = () => Math.random().toString(36).slice(2, 10);

const WORKSPACES: Workspace[] = [
  { id: 'orion', name: 'Orion', path: '~/code/orion', exists: true, start_cmd: 'npm run dev', ports: [1420, 9120], tags: ['ai'], scripts: ['dev', 'test'] },
  { id: 'openwow', name: 'OpenWoW', path: '~/code/openwow', exists: true, start_cmd: 'make run', ports: [8085], tags: ['game'], scripts: ['build'] },
  { id: 'synth-lab', name: 'Synth Lab', path: '~/code/synth-lab', exists: true, start_cmd: 'npm start', ports: [5173], tags: ['audio'], scripts: ['start'] },
  { id: 'portfolio', name: 'Portfolio', path: '~/code/portfolio', exists: true, start_cmd: 'npm run dev', ports: [3000], tags: ['web'], scripts: ['dev'] },
  { id: 'notes', name: 'Field Notes', path: '~/notes', exists: true, start_cmd: '', ports: [], tags: ['writing'], scripts: [] },
];
const RADAR: GitRepoStatus[] = [
  { id: 'orion', name: 'Orion', path: '~/code/orion', branch: 'main', dirty_files: 3, last_commit_at: T0 - 1800,
    recent: [{ hash: 'a1b2c3d', subject: 'Sanctuary mode + Sun & Moon shift', time: T0 - 1800, author: 'you' }] },
  { id: 'openwow', name: 'OpenWoW', path: '~/code/openwow', branch: 'feature/raids', dirty_files: 0, last_commit_at: T0 - 86400 * 2,
    recent: [{ hash: 'd4e5f6a', subject: 'Raid lockout timers', time: T0 - 86400 * 2, author: 'you' }] },
  { id: 'synth-lab', name: 'Synth Lab', path: '~/code/synth-lab', branch: 'main', dirty_files: 1, last_commit_at: T0 - 86400 * 6,
    recent: [{ hash: '0f1e2d3', subject: 'Bass rise envelope', time: T0 - 86400 * 6, author: 'you' }] },
  { id: 'portfolio', name: 'Portfolio', path: '~/code/portfolio', branch: 'main', dirty_files: 0, last_commit_at: T0 - 86400 * 21,
    recent: [{ hash: '9a8b7c6', subject: 'New case study', time: T0 - 86400 * 21, author: 'you' }] },
];

function msg(thread: Omit<CommsThread, 'messages' | 'unread' | 'last_ts'>, author: string, text: string, ago: number, out = false): CommsMessage {
  return {
    id: uid(), platform: thread.platform, thread_id: thread.thread_id, thread_key: thread.key, thread_name: thread.thread_name,
    route: thread.route, author, text, direction: out ? 'out' : 'in', timestamp: T0 - ago,
  };
}
function thread(key: string, platform: CommsThread['platform'], name: string, lines: Array<[string, string, number, boolean?]>, unread: number, vip = false): CommsThread {
  const base = { key, platform, thread_id: key, thread_name: name, route: (platform === 'telegram' ? 'telegram_user' : 'discord_bot') as CommsThread['route'] };
  const messages = lines.map(([a, t, ago, out]) => msg(base, a, t, ago, out));
  return { ...base, messages, unread, last_ts: messages[messages.length - 1].timestamp, vip };
}
const THREADS: CommsThread[] = [
  thread('tg:maya', 'telegram', 'Maya', [
    ['Maya', 'are we still on for the synth jam saturday?', 3600],
    ['you', 'yes!! bringing the new patch', 3500, true],
    ['Maya', 'perfect. can you send the demo track before then?', 240],
  ], 1, true),
  thread('dc:openwow-dev', 'discord', '#dev · OpenWoW', [
    ['Leo', 'raid lockouts look good on staging', 7200],
    ['Leo', 'one thing — the reset timer shows UTC instead of server time', 900],
  ], 2),
  thread('ms:sam', 'messenger', 'Sam', [
    ['Sam', 'saw your orion video, that black hole thing is wild', 5400],
    ['you', 'haha thanks, it eats files now', 5300, true],
    ['Sam', 'wait what. show me', 600],
  ], 1),
  thread('tg:jules', 'telegram', 'Jules', [ // [context] you wrote last: shows under "Waiting on others"
    ['Jules', 'cover art is coming along, want a sneak peek?', 9000],
    ['you', 'yes please! can you send the files by friday?', 7200, true],
  ], 0),
];
const APPROVALS: ApprovalItem[] = [
  { id: 'ap1', kind: 'discord_prompt', status: 'pending', created_at: T0 - 1200, platform: 'discord', category: 'question',
    channel: '#help', author: 'newcomer42', original: 'how do I build the server on linux?',
    draft: 'Hey! Run `make deps && make run` from the repo root — the README has a Linux section with the package list. Ping me if cmake complains.' },
  { id: 'ap2', kind: 'devlog', status: 'pending', created_at: T0 - 600, platform: 'discord', category: 'devlog', title: 'Devlog: raid lockouts',
    draft: 'This week: raid lockout timers, faster zone loading, and a fix for the vendor bug. Next up: dungeon finder.' },
];
const MEMORIES: MemoryRecord[] = [
  { id: 'm1', timestamp: T0 - 900, source: 'user', content: 'Prefers short replies, lowercase, a bit playful.' },
  { id: 'm2', timestamp: T0 - 4000, source: 'browser', content: 'Researching procedural audio in Web Audio for the soundscape feature.' },
  { id: 'm3', timestamp: T0 - 9000, source: 'consolidated', content: 'OpenWoW: raid lockouts shipped to staging, timer timezone bug open.' },
];
const ROUTINE: RoutineSuggestion = {
  id: 'demo-sanctuary', kind: 'yoga', duration_min: 5, title: 'Sanctuary: reset',
  detail: 'Stand up and roll your shoulders\nThree slow breaths — longer out than in\nDrink a glass of water\nLook out of a window for 20 seconds',
};
const PET_JOBS = ['Reading App.tsx', 'Running the unit tests', 'Editing singularity.css', 'Fixing a type error', 'Writing the release notes'];
const SONGS: MediaState[] = [
  { title: 'Night Drive', artist: 'Lumen Fields', album: 'Afterglow', app: 'Spotify', playing: true, position: 42, duration: 214, art: null },
  { title: 'Slow Morning', artist: 'Harbor Lane', album: 'Weekdays', app: 'Spotify', playing: true, position: 0, duration: 188, art: null },
];

// ---------------------------------------------------------------- XP (mirrors apps/daemon/xp.py)
const AWARDS: Record<string, number> = { focus_block: 50, recovery: 20, commit: 10, idea: 5, water: 2, approval: 3, devlog: 15 };
const SKINS = [
  { id: 'default', name: 'Cobalt Blue', level: 1 },
  { id: 'emerald', name: 'Emerald Green', level: 3 },
  { id: 'supernova', name: 'Gold', level: 5 },
  { id: 'bloodmoon', name: 'Deep Red', level: 8 },
  { id: 'runic', name: 'Indigo', level: 12 },
];
function levelFor(xp: number) {
  const level = Math.floor(Math.sqrt(Math.max(0, xp) / 100)) + 1;
  return { level, start: 100 * (level - 1) ** 2, next: 100 * level ** 2 };
}

// ---------------------------------------------------------------- Sun & Moon (NOAA approximation, as in sun.py)
const rad = (d: number) => (d * Math.PI) / 180;
const deg = (r: number) => (r * 180) / Math.PI;
function solarElevation(lat: number, lon: number, when: Date) {
  const jc = (when.getTime() / 86400000 + 2440587.5 - 2451545) / 36525;
  const l0 = (280.46646 + jc * (36000.76983 + jc * 0.0003032)) % 360;
  const m = 357.52911 + jc * (35999.05029 - 0.0001537 * jc);
  const e = 0.016708634 - jc * (0.000042037 + 0.0000001267 * jc);
  const c = Math.sin(rad(m)) * (1.914602 - jc * (0.004817 + 0.000014 * jc)) + Math.sin(rad(2 * m)) * (0.019993 - 0.000101 * jc) + Math.sin(rad(3 * m)) * 0.000289;
  const omega = 125.04 - 1934.136 * jc;
  const app = l0 + c - 0.00569 - 0.00478 * Math.sin(rad(omega));
  const obliq = 23 + (26 + (21.448 - jc * (46.815 + jc * (0.00059 - jc * 0.001813))) / 60) / 60 + 0.00256 * Math.cos(rad(omega));
  const decl = deg(Math.asin(Math.sin(rad(obliq)) * Math.sin(rad(app))));
  const y = Math.tan(rad(obliq / 2)) ** 2;
  const eq = 4 * deg(y * Math.sin(2 * rad(l0)) - 2 * e * Math.sin(rad(m)) + 4 * e * y * Math.sin(rad(m)) * Math.cos(2 * rad(l0))
    - 0.5 * y * y * Math.sin(4 * rad(l0)) - 1.25 * e * e * Math.sin(2 * rad(m)));
  const minutes = when.getUTCHours() * 60 + when.getUTCMinutes() + when.getUTCSeconds() / 60;
  const ts = (((minutes + eq + 4 * lon) % 1440) + 1440) % 1440;
  const ha = ts / 4 < 0 ? ts / 4 + 180 : ts / 4 - 180;
  const zen = deg(Math.acos(Math.sin(rad(lat)) * Math.sin(rad(decl)) + Math.cos(rad(lat)) * Math.cos(rad(decl)) * Math.cos(rad(ha))));
  return 90 - zen;
}
function crossing(lat: number, lon: number, start: Date, rising: boolean) {
  let prev = solarElevation(lat, lon, start);
  for (let i = 1; i <= 540; i++) {
    const t = new Date(start.getTime() + i * 4 * 60000);
    const cur = solarElevation(lat, lon, t);
    if ((rising && prev < -0.833 && cur >= -0.833) || (!rising && prev >= -0.833 && cur < -0.833)) return t;
    prev = cur;
  }
  return null;
}
const hhmm = (d: Date | null) => (d ? d.toTimeString().slice(0, 5) : null);

// ---------------------------------------------------------------- the fake daemon
type Listener = ((ev: { data: string }) => void) | null;

export class DemoSocket {
  static instance: DemoSocket | null = null;
  readyState = 0;
  onopen: (() => void) | null = null;
  onmessage: Listener = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;

  private s = load();
  private timers: number[] = [];
  private threads = THREADS.map((t) => ({ ...t, messages: [...t.messages] }));
  private approvals = APPROVALS.map((a) => ({ ...a }));
  private pacing: PacingState = { phase: 'focus', session_minutes: 38, limit_minutes: 90, progress: 38 / 90, checkins: 0, breaks_taken: 1, pending_checkin: null };
  private manualPhase: CircadianState['phase'] | null = null;
  private agent: AgentActivity = { state: 'idle', status: '', project: 'Orion', since: now(), agents: 0, watching: ['Claude', 'Codex', 'Cursor'] };
  private media: MediaState = { ...SONGS[0] };
  private activeVenture: string | null = null;
  private tick = 0;

  constructor() {
    DemoSocket.instance = this;
    window.setTimeout(() => {
      this.readyState = 1;
      this.onopen?.();
    }, 350);
  }

  close() {
    if (this.readyState === 3) return;
    this.readyState = 3;
    this.timers.forEach((t) => window.clearInterval(t));
    this.timers = [];
    if (DemoSocket.instance === this) DemoSocket.instance = null;
    this.onclose?.();
  }

  // ---- outgoing (daemon -> UI)
  emit(ev: Record<string, unknown>) {
    if (this.readyState !== 1) return;
    const data = JSON.stringify({ timestamp: now(), ...ev });
    window.setTimeout(() => this.onmessage?.({ data }), 0);
  }
  private after(ms: number, ev: Record<string, unknown>) {
    window.setTimeout(() => this.emit(ev), ms);
  }

  // ---- derived state
  private xpState(): XPState {
    const { level, start, next } = levelFor(this.s.xp);
    const todayXp = this.s.recent.filter((r) => new Date(r.at * 1000).toISOString().slice(0, 10) === today()).reduce((a, r) => a + r.xp, 0);
    return {
      xp: this.s.xp, level, level_start: start, next_level: next, today: todayXp + 85, skin: this.s.skin,
      skins: SKINS.map((k) => ({ ...k, unlocked: k.level <= level })), recent: [...this.s.recent].reverse().slice(0, 8),
    };
  }
  private circadian(): CircadianState {
    const c: CircadianState = { phase: this.manualPhase ?? 'normal', bedtime: '23:30', wind_down_starts: '22:30' };
    const loc = this.s.location;
    if (loc) {
      const n = new Date();
      const elev = solarElevation(loc.lat, loc.lon, n);
      const sunset = crossing(loc.lat, loc.lon, new Date(n.getTime() - 14 * 3600000), false);
      c.sun = { elevation: Math.round(elev * 10) / 10, is_day: elev > -0.833, sunset: hhmm(sunset), sunrise: hhmm(crossing(loc.lat, loc.lon, n, true)) };
      if (!this.manualPhase && !c.sun.is_day) {
        c.phase = 'wind_down';
        c.trigger = 'sunset';
      }
    }
    return c;
  }
  private momentum() {
    const pending = this.approvals.filter((a) => a.status === 'pending').length;
    const score = Math.min(1, 0.35 * (this.pacing.session_minutes / 180) + 0.25 * 0.6 + 0.15 * Math.min(1, this.s.water.today / 8) + (pending <= 2 ? 0.1 : 0.04) + 0.15);
    const debris = pending
      ? [{ id: 'approvals', label: `${pending} item${pending === 1 ? '' : 's'} waiting in the Queue`, tab: 'queue', count: Math.max(1, pending) }]
      : [];
    return { score: Math.round(score * 1000) / 1000, swarm: Math.round(score * 12), debris };
  }
  private telemetry(): TelemetrySnapshot {
    const unread = this.threads.reduce((a, t) => a + t.unread, 0);
    return {
      uptime_s: Math.round(now() - T0) + 5400, capture_running: false, ocr_available: false, frames_total: 0, frames_skipped: 0,
      frames_processed: 0, skip_ratio: 0, est_tokens_saved: 0, last_phash_diff: null, llm_calls_local: 12, llm_calls_cloud: 3,
      llm_tokens_in: 18200, llm_tokens_out: 4100, llm_failures: 0, memory_commits: 42, memory_rows: 1280, embedding_backend: 'demo',
      approvals_pending: this.approvals.filter((a) => a.status === 'pending').length, comms_threads: this.threads.length, comms_unread: unread,
      pacing: this.pacing,
      preload: { dir: '~/.orion/preload', rules: this.s.rules.length, workspaces: WORKSPACES.length, grimoire: 6, vips: 1, routines: 4 },
      hydration: { today: this.s.water.today, goal: 8 }, circadian: this.circadian(), recording_macro: false, producer: null,
      active_station: null, runs_active: 0, momentum: this.momentum(),
    };
  }

  private hello() {
    this.emit({
      event: 'HELLO', version: 'demo', client_id: 'guest',
      agents: { coding_agent: true, routine_optimizer: true, discord_monitor: true, system_capturer: false, proactive_observer: true },
      telemetry: this.telemetry(), clients: [{ id: 'guest', kind: 'desktop', browser: null, connected_at: now() }],
      recent_memories: MEMORIES, approvals: this.approvals, comms_threads: this.threads, pacing: this.pacing,
      workspaces: WORKSPACES, stations: [], grimoire: [], runs: [], ideas: this.s.ideas, rules: this.s.rules,
      media: this.media, agent: this.agent, xp: this.xpState(), location: this.s.location, active_venture: this.activeVenture,
    });
    this.emit({ event: 'GIT_RADAR', repos: RADAR });
    this.after(2500, {
      event: 'LOG', level: 'info',
      message: 'Welcome to the Orion guest demo. Try typing "log water", "idea: …", "focus mode", "take a break" or "wind down".',
    });
  }

  private loop() {
    this.timers.push(window.setInterval(() => {
      this.tick++;
      // focus session creeps forward
      if (this.pacing.phase === 'focus') {
        this.pacing = { ...this.pacing, session_minutes: this.pacing.session_minutes + 1, progress: Math.min(1, (this.pacing.session_minutes + 1) / 90) };
      }
      // music position
      if (this.media.playing && this.media.duration) {
        const pos = (this.media.position ?? 0) + 5;
        if (pos >= this.media.duration) this.media = { ...SONGS[(SONGS.findIndex((s) => s.title === this.media.title) + 1) % SONGS.length] };
        else this.media = { ...this.media, position: pos };
        this.emit({ event: 'MEDIA_STATE', media: this.media });
      }
      this.emit({ event: 'TELEMETRY', data: this.telemetry() });
      this.emit(this.coreSignals());
      this.petCycle();
      if (this.tick === 9) this.incoming();
    }, 5000));
  }

  /** The coding-agent pet: naps, works through a few steps, celebrates, naps again (≈ every 80 s). */
  private petCycle() {
    const phase = this.tick % 16;
    if (phase === 2) this.agent = { ...this.agent, state: 'working', agent: 'Claude', status: `Claude · ${PET_JOBS[0]}`, since: now(), agents: 1 };
    else if (phase > 2 && phase < 11 && this.agent.state === 'working') this.agent = { ...this.agent, status: `Claude · ${PET_JOBS[Math.floor((phase - 2) / 2) % PET_JOBS.length]}` };
    else if (phase === 11) this.agent = { ...this.agent, state: 'done', duration: now() - this.agent.since };
    else if (phase === 13) this.agent = { ...this.agent, state: 'idle', status: '', agents: 0 };
    else return;
    this.emit({ event: 'AGENT_STATUS', agent: this.agent });
    if (phase === 11) this.emit({ event: 'AUDIO_CUE', cue: 'task_finished' });
  }

  private incoming() {
    const t = this.threads.find((x) => x.key === 'dc:openwow-dev')!;
    const m = msg(t, 'Leo', 'also — can you look at the vendor bug when you get a sec?', 0);
    t.messages.push(m);
    t.unread += 1;
    t.last_ts = m.timestamp;
    this.emit({ event: 'COMMS_MESSAGE', thread: { ...t, messages: undefined }, message: m });
    this.emit({ event: 'CORE_EVENT', kind: 'comet', size: 'normal', count: 1 });
  }

  /** Art stage 6: what apps/daemon/core_signals.py would send (pending approvals orbit the core). */
  private coreSignals(): Record<string, unknown> {
    const pending = this.approvals.filter((a) => a.status === 'pending');
    return {
      event: 'CORE_SIGNALS', thinking: this.agent.state === 'working' ? 0.25 : 0, heat: 0, turbulence: 0, dim: 0,
      update_ready: 0, quiet: 0, paused: false, orbit_total: pending.length,
      orbit: pending.slice(0, 7).map((a) => ({ id: a.id, tone: a.kind === 'boardroom' ? 'boardroom' : 'approval' })),
    };
  }

  private award(kind: string, note = '') {
    const pts = AWARDS[kind] ?? 0;
    if (!pts) return;
    const before = levelFor(this.s.xp).level;
    this.s.xp += pts;
    const a: XPAward = { at: now(), kind, xp: pts, note };
    this.s.recent = [...this.s.recent, a].slice(-30);
    save(this.s);
    const after = levelFor(this.s.xp).level;
    this.emit({ event: 'XP', xp: this.xpState(), award: { ...a, level_up: after > before ? after : null } });
  }

  private reply(prompt: string, summary: string, level: 'info' | 'success' | 'warn' = 'info') {
    const request_id = uid();
    this.emit({ event: 'ORCHESTRATOR_THINKING', request_id, trigger: 'user' });
    this.after(500, {
      event: 'ORCHESTRATOR_RESPONSE', request_id, trigger: 'user', prompt,
      response: { intent: 'question', tier: 'offline', summary, model: 'guest demo', latency_ms: 12,
        actions: level === 'info' ? [] : [{ type: 'hud_notify', payload: { title: 'Orion', body: summary, level } }] },
    });
  }

  // ---- built-in commands that really work in the demo
  private command(text: string) {
    const t = text.trim();
    const low = t.toLowerCase();
    let m: RegExpMatchArray | null;
    if (/\b(log|drank|had)\b.*\bwater\b|^water$|\bglass of water\b/.test(low)) {
      this.water(1);
      return this.reply(t, `Logged a glass of water. That's ${this.s.water.today} of 8 today.`, 'success');
    }
    if ((m = t.match(/^(?:idea|log idea|note)[:\s-]+(.+)$/i))) {
      const idea: IdeaRecord = { id: uid(), at: now(), kind: 'idea', project: this.activeVenture ?? 'inbox', text: m[1].trim() };
      this.s.ideas = [...this.s.ideas, idea].slice(-60);
      save(this.s);
      this.emit({ event: 'IDEA_LOGGED', idea });
      this.award('idea', idea.text.slice(0, 40));
      return this.reply(t, `Saved to your ideas: "${idea.text}".`, 'success');
    }
    if ((m = t.match(/^(?:rule|new rule|always|never)[:\s-]+(.+)$/i))) {
      const text2 = /^(always|never)/i.test(t) ? t : m[1].trim();
      this.addRule(text2);
      return this.reply(t, `Rule added: "${text2}". Orion follows it from now on.`, 'success');
    }
    if (/\b(focus mode|start focus|lock in|deep work)\b/.test(low)) {
      this.pacing = { ...this.pacing, phase: 'focus', session_minutes: 0, progress: 0 };
      this.manualPhase = null;
      this.emit({ event: 'PACING_STATE', pacing: this.pacing });
      return this.reply(t, 'Focus mode on. I’ll hold distractions and nudge you for a break in 90 minutes.', 'success');
    }
    if (/\b(take a break|break time|sanctuary|stretch)\b/.test(low)) {
      this.pacing = { ...this.pacing, phase: 'break_due' };
      this.emit({ event: 'PACING_STATE', pacing: this.pacing });
      this.emit({ event: 'SANCTUARY_START', routine: ROUTINE });
      return this.reply(t, 'Sanctuary time — five minutes, then the next focus block unlocks.');
    }
    if (/\b(wind down|night mode|good ?night|sunset)\b/.test(low)) {
      this.manualPhase = 'wind_down';
      this.emit({ event: 'CIRCADIAN', circadian: this.circadian(), location: this.s.location });
      this.emit({ event: 'TELEMETRY', data: this.telemetry() });
      return this.reply(t, 'Winding down: the HUD warms up, my voice gets softer and work alerts wait until morning.');
    }
    if (/\b(wake up|morning|day mode|normal mode)\b/.test(low)) {
      this.manualPhase = 'normal';
      this.pacing = { ...this.pacing, phase: 'idle' };
      this.emit({ event: 'PACING_STATE', pacing: this.pacing });
      this.emit({ event: 'CIRCADIAN', circadian: this.circadian(), location: this.s.location });
      this.emit({ event: 'TELEMETRY', data: this.telemetry() });
      return this.reply(t, 'Good morning. Back to normal mode.');
    }
    if (/\b(play|pause|skip|next song|music)\b/.test(low)) {
      this.mediaControl(/skip|next/.test(low) ? 'next' : 'toggle');
      return this.reply(t, this.media.playing ? `Playing ${this.media.title}.` : 'Paused the music.');
    }
    if (/^(help|what can you do|commands)\??$/.test(low)) {
      return this.reply(t, 'In this demo you can say: "log water", "idea: …", "rule: …", "focus mode", "take a break", "wind down", "wake up", "pause music". Drop a file on the core to see it swallowed. The full app also reads your screen, chats, projects and voice — on your own PC.');
    }
    return this.reply(t, 'This is the guest demo, so there’s no AI brain behind me here. Try "log water", "idea: …", "focus mode", "take a break" or "wind down" — or type "help". Install Orion on your PC for the real thing.');
  }

  private water(delta: number) {
    this.s.water = { day: today(), today: Math.max(0, this.s.water.today + delta) };
    save(this.s);
    this.emit({ event: 'HYDRATION', hydration: { today: this.s.water.today, goal: 8 } });
    if (delta > 0) this.award('water');
  }
  private addRule(text: string) {
    this.s.rules = [...this.s.rules, { id: uid(), timestamp: now(), text, source: 'guest', tier: 1 }];
    save(this.s);
    this.emit({ event: 'RULES', rules: this.s.rules });
  }
  private mediaControl(action: string) {
    if (action === 'next') this.media = { ...SONGS[(SONGS.findIndex((s) => s.title === this.media.title) + 1) % SONGS.length] };
    else if (action === 'previous') this.media = { ...this.media, position: 0 };
    else this.media = { ...this.media, playing: action === 'play' ? true : action === 'pause' ? false : !this.media.playing };
    this.emit({ event: 'MEDIA_STATE', media: this.media });
  }

  /** Hooks the guided tour uses to show things happening while Orion talks about them. */
  tour(action: 'winddown' | 'wake' | 'pet' | 'water') {
    if (action === 'winddown' || action === 'wake') {
      this.manualPhase = action === 'winddown' ? 'wind_down' : null;
      this.emit({ event: 'CIRCADIAN', circadian: this.circadian(), location: this.s.location });
      this.emit({ event: 'TELEMETRY', data: this.telemetry() });
    } else if (action === 'pet') {
      this.agent = { ...this.agent, state: 'working', agent: 'Claude', status: 'Claude · Running the unit tests', since: now(), agents: 1 };
      this.emit({ event: 'AGENT_STATUS', agent: this.agent });
    } else if (action === 'water') {
      this.water(1);
    }
  }

  /** A document dropped into the black hole (see installDemoFetch). */
  briefing(name: string, text: string) {
    this.emit({ event: 'BRIEFING', name, text });
    this.award('idea', name);
  }

  // ---- incoming (UI -> daemon)
  send(raw: string) {
    let m: Record<string, any>;
    try {
      m = JSON.parse(raw);
    } catch {
      return;
    }
    switch (m.event) {
      case 'CLIENT_HELLO':
        this.hello();
        this.loop();
        break;
      case 'PING':
        this.emit({ event: 'PONG' });
        break;
      case 'USER_PROMPT':
      case 'VOICE_TRANSCRIPT':
        this.command(String(m.text ?? ''));
        break;
      case 'HYDRATION_LOG':
        this.water(Number(m.delta ?? 1));
        break;
      case 'RULE_ADD':
        if (m.text) this.addRule(String(m.text));
        break;
      case 'RULE_DELETE':
        this.s.rules = this.s.rules.filter((r) => r.id !== m.id);
        save(this.s);
        this.emit({ event: 'RULES', rules: this.s.rules });
        break;
      case 'RULES_LIST':
        this.emit({ event: 'RULES', rules: this.s.rules });
        break;
      case 'IDEAS_LIST':
        this.emit({ event: 'IDEAS', ideas: this.s.ideas });
        break;
      case 'GIT_RADAR':
        this.emit({ event: 'GIT_RADAR', repos: RADAR });
        break;
      case 'VAULT_LIST':
        this.emit({ event: 'VAULT', dir: '~/.orion/vault', files: [{ name: 'orion-plan.md', bytes: 18200, indexed: true }] });
        break;
      case 'XP_SKIN': {
        const skin = SKINS.find((k) => k.id === m.skin);
        if (skin && skin.level <= levelFor(this.s.xp).level) {
          this.s.skin = skin.id;
          save(this.s);
          this.emit({ event: 'XP', xp: this.xpState() });
        }
        break;
      }
      case 'LOCATION_SET': {
        const lat = Number(m.lat), lon = Number(m.lon);
        if (Number.isFinite(lat) && Number.isFinite(lon)) {
          this.s.location = { lat, lon, label: String(m.label ?? '') };
          this.manualPhase = null;
          save(this.s);
        }
        this.emit({ event: 'CIRCADIAN', circadian: this.circadian(), location: this.s.location });
        this.emit({ event: 'TELEMETRY', data: this.telemetry() });
        break;
      }
      case 'PACING_ACK':
        if (m.action === 'start_break') {
          this.emit({ event: 'SANCTUARY_START', routine: ROUTINE });
        } else {
          this.pacing = { ...this.pacing, phase: 'focus', pending_checkin: null };
          this.emit({ event: 'PACING_STATE', pacing: this.pacing });
        }
        break;
      case 'SANCTUARY_DONE':
        this.pacing = { ...this.pacing, phase: 'focus', session_minutes: 0, progress: 0, breaks_taken: this.pacing.breaks_taken + 1 };
        this.emit({ event: 'PACING_STATE', pacing: this.pacing });
        if (m.completed) this.award('recovery', 'Sanctuary');
        break;
      case 'ACTIVE_VENTURE': {
        const ws = WORKSPACES.find((w) => w.id === m.id);
        if (ws) {
          this.activeVenture = ws.id;
          this.emit({
            event: 'PROJECT_CONTEXT', workspace: ws, git: RADAR.find((r) => r.id === ws.id) ?? null,
            memories: MEMORIES.filter((x) => x.content.toLowerCase().includes(ws.name.toLowerCase().split(' ')[0])),
          });
        }
        break;
      }
      case 'APPROVAL_DECISION': {
        const a = this.approvals.find((x) => x.id === m.id);
        if (a) {
          a.status = m.decision === 'reject' ? 'rejected' : 'sent';
          a.decision = m.decision === 'reject' ? 'rejected' : m.decision === 'edit' ? 'edited' : 'approved';
          a.final = m.text ?? a.draft;
          a.resolved_at = now();
          this.emit({ event: 'APPROVAL_RESOLVED', item: a });
          this.emit({ event: 'CORE_EVENT', kind: 'orbit', id: a.id, outcome: a.status === 'sent' ? 'approve' : 'reject' });
          this.emit(this.coreSignals());
          if (a.status === 'sent') this.award('approval');
          this.emit({ event: 'TELEMETRY', data: this.telemetry() });
        }
        break;
      }
      case 'COMMS_MARK_READ': {
        const t = this.threads.find((x) => x.key === m.thread_key);
        if (t) {
          t.unread = 0;
          this.emit({ event: 'COMMS_ROUTING', routing: {}, threads: this.threads });
        }
        break;
      }
      case 'COMMS_REPLY': {
        const t = this.threads.find((x) => x.key === m.thread_key);
        if (t && m.text) {
          const out = msg(t, 'you', String(m.text), 0, true);
          t.messages.push(out);
          t.last_ts = out.timestamp;
          this.emit({ event: 'COMMS_MESSAGE', thread: { ...t, messages: undefined }, message: out });
          this.emit({ event: 'COMMS_REPLY_RESULT', thread_key: t.key, ok: true, detail: 'Demo: shown here only — nothing was actually sent.' });
        }
        break;
      }
      case 'SUGGEST_REPLIES': {
        const t = this.threads.find((x) => x.key === m.thread_key);
        const last = t?.messages.filter((x) => x.direction === 'in').slice(-1)[0];
        if (t && last) {
          this.after(400, {
            event: 'REPLY_SUGGESTIONS', thread_key: t.key, for_id: last.id, model: 'demo',
            replies: ['haha yes, on it', 'give me 10 min and i’ll send it', 'can we do tomorrow instead?'],
          });
        }
        break;
      }
      case 'MEDIA_CONTROL':
        this.mediaControl(String(m.action ?? 'toggle'));
        break;
      default:
        if (typeof m.event === 'string' && !['RUNWAY_GET', 'GRIMOIRE_LIST', 'RUNS_LIST', 'PRELOAD_INFO', 'AUDIO_CUE', 'SPEAK'].includes(m.event)) {
          this.emit({ event: 'LOG', level: 'info', message: `“${m.event.toLowerCase().replace(/_/g, ' ')}” needs the desktop app — it’s not part of the guest demo.` });
        }
    }
  }
}

/** Answers the HUD's HTTP calls (asset uploads, black-hole document drops) inside the browser. */
export function installDemoFetch() {
  const real = window.fetch.bind(window);
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
    if (url.includes('/__orion/daemon-token')) return new Response('', { status: 200 }); // no daemon key in the preview
    if (!url.startsWith('http://localhost:9120') && !url.startsWith('http://127.0.0.1:9120')) return real(input, init);
    const headers = new Headers(init?.headers);
    const name = decodeURIComponent(headers.get('X-Filename') ?? 'file');
    const body = init?.body instanceof Blob ? init.body : null;
    const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });
    if (url.endsWith('/vault/drop')) {
      let text = '';
      try {
        text = body ? (await body.slice(0, 4000).text()).replace(/\s+/g, ' ').trim() : '';
      } catch {
        /* binary */
      }
      const words = text.split(' ').filter(Boolean);
      const briefing = words.length
        ? `${name}: about ${Math.max(words.length, Math.round((body?.size ?? 0) / 6))} words. It opens with “${words.slice(0, 18).join(' ')}…”. In the full app Orion files it in your knowledge vault and briefs you on it aloud.`
        : `${name} was swallowed. In the full app Orion saves it to your knowledge vault, indexes it and briefs you on it aloud.`;
      window.setTimeout(() => DemoSocket.instance?.briefing(name, briefing), 900);
      return json({ name, briefing });
    }
    if (url.endsWith('/agents/swarm')) return json(demoSwarm());
    if (url.endsWith('/assets/upload')) {
      return json({
        asset: {
          id: uid(), name, path: `~/.orion/assets/${name}`, kind: body?.type.split('/')[0] || 'file', bytes: body?.size ?? 0, outputs: [],
          actions: [
            { id: 'remove_bg', label: 'Remove Background', enabled: false, reason: 'Desktop app only' },
            { id: 'compress', label: 'Compress for Web', enabled: false, reason: 'Desktop app only' },
          ],
        },
      });
    }
    const path = url.replace(/^http:\/\/(localhost|127\.0\.0\.1):9120/, '');
    let parsed: Record<string, unknown> = {};
    if (typeof init?.body === 'string') {
      try {
        parsed = JSON.parse(init.body) as Record<string, unknown>;
      } catch {
        /* not JSON */
      }
    }
    const answer = await demoRoute((init?.method ?? 'GET').toUpperCase(), path, parsed, (ev) => DemoSocket.instance?.emit(ev));
    if (answer) return answer;
    (window as unknown as { __demoMisses?: string[] }).__demoMisses ??= [];
    (window as unknown as { __demoMisses: string[] }).__demoMisses.push(`${init?.method ?? 'GET'} ${path}`);
    return json({ error: 'Not available in the guest demo' }, 404);
  };
}

/** Sample coding agents for the robot swarm around the core: one working, one stuck, one that keeps finishing. */
function demoSwarm() {
  const now = Date.now() / 1000;
  const cycle = now % 26; // works for 16 s, then sits "done" for 10 s
  const base = {
    events: [] as Array<{ at: number | null; text: string }>,
    files: [] as string[],
    controllable: false,
    run_id: null,
    paused: false,
    can_open: false,
    control_note: 'Guest demo: the robots are sample data.',
    started: now - 1800,
  };
  return {
    at: now,
    watching: ['claude', 'codex', 'gemini'],
    agents: [
      {
        ...base,
        id: 'demo-claude',
        tool: 'claude',
        project: 'openwow',
        name: 'Claude · openwow',
        cwd: '~/projects/openwow',
        state: 'working',
        status: 'Fixing the loot table',
        reason: '',
        last_activity: now - 4,
        events: [{ at: now - 40, text: 'Ran cargo test loot' }, { at: now - 4, text: 'Edited loot_table.rs' }],
        files: ['src/loot_table.rs'],
      },
      {
        ...base,
        id: 'demo-codex',
        tool: 'codex',
        project: 'orion-site',
        name: 'Codex · orion-site',
        cwd: '~/projects/orion-site',
        state: 'stuck',
        status: 'Stuck on a failing build',
        reason: 'Same error for 6 minutes',
        last_activity: now - 360,
      },
      {
        ...base,
        id: 'demo-gemini',
        tool: 'gemini',
        project: 'notes',
        name: 'Gemini · notes',
        cwd: '~/notes',
        state: cycle < 16 ? 'working' : 'done',
        status: cycle < 16 ? 'Tidying meeting notes' : 'Finished the summary',
        reason: '',
        last_activity: now - 2,
      },
    ],
  };
}
