/**
 * Guest preview: the daemon's HTTP routes, answered inside the browser with SAMPLE data (GitHub Pages build).
 *
 * Every window in the menu has its routes here, so each one opens and shows something believable: the Test
 * Center (the real checklist, copied from the daemon by snapshot_daemon.py), Projects and Mission Control
 * (missions that move along by themselves), the Phone panel (a sample pairing QR that only opens the website),
 * the privacy dashboard, Guide Mode, Skills, Video Scout, Self-changes and the camera light.
 *
 * Nothing leaves the browser: no request goes anywhere, the camera and microphone are never opened, and every
 * "Do it for me" only animates to a plausible result. State lasts for this visit only.
 */
import type { Mission, MissionDetail, MissionState } from '../lib/missions';
import type { Project } from '../lib/projects';
import SAMPLE from './sampleDaemon.json';

// ---------------------------------------------------------------- helpers
const now = () => Date.now() / 1000;
const T0 = now();
let seq = 0;
const nid = (p: string) => `${p}-${(++seq).toString(36)}${Math.random().toString(36).slice(2, 6)}`;
type Json = Record<string, unknown>;
export type DemoEmit = (ev: Json) => void;

/** Every route prefix the preview answers (the smoke test checks each menu window is covered). */
export const DEMO_ROUTES = [
  '/tests', '/missions', '/projects', '/phones', '/privacy', '/self', '/presence', '/hotmic', '/guide',
  '/browser-skills', '/desktop-skills', '/scout', '/health/status', '/freeze', '/access', '/linux/windows',
  '/hardware/report', '/telegram/pair', '/extension/pair', '/compute', '/agents/swarm', '/vault/drop', '/assets/upload', '/games',
  '/voice', // [voice2]
  '/focus', // [focus]
  '/promises', '/core/signals', // [command] the console's Today and Computer panels
] as const;

// ---------------------------------------------------------------- command console panels [command]
const promiseDone = new Set<string>();
function demoPromises() {
  const t = now();
  return [
    { id: 'pr-demo', task: 'Send Maya the demo track', to: 'tg:maya', platform: 'telegram', due: t + 3 * 3600 },
    { id: 'pr-leo', task: 'Switch the reset timer to server time', to: 'dc:openwow-dev', platform: 'discord', due: t + 5 * 3600 },
    { id: 'pr-sam', task: 'Look over Sam\'s level sketches', to: 'dc:openwow-dev', platform: 'discord', due: null },
  ].filter((p) => !promiseDone.has(p.id)).map((p) => ({ ...p, status: 'open', said: '', sent_at: T0 - 3600 }));
}
/** A gently moving sample CPU load, so the meter looks alive. */
const demoCpu = () => Math.round((0.22 + 0.1 * Math.sin(now() / 9) + 0.04 * Math.sin(now() / 2.3)) * 100) / 100;

// ---------------------------------------------------------------- Focus dashboard [focus]
const focusPins = new Set<string>();
const focusHidden = new Set<string>();
function focusView() {
  const t = now();
  const cards = [
    { id: 'now:mission:star', kind: 'now', title: 'Star catcher: add a high-score table', detail: 'Claude: Checking the work (build, tests, review). $0.42 of $2.00 spent.',
      why: 'This is what Orion is working on right now.', score: 70, open: { window: 'missions' }, actions: ['open'],
      meta: { progress: 0.75, step: 'Checking the work', waiting_on: 'Your OK on the result, once the checks pass.' } },
    { id: 'msg:tg:maya', kind: 'message', title: 'Maya on Telegram', detail: 'perfect. can you send the demo track before then?',
      why: 'You usually reply to Maya within minutes.', score: 66, open: { window: 'messages', thread: 'tg:maya' }, actions: ['open', 'draft'],
      meta: { sender: 'Maya', app_label: 'Telegram', unread: 1, thread: 'tg:maya', ts: t - 240 } },
    { id: 'approval:ap2', kind: 'approval', title: '2 things are waiting for your OK', detail: 'Top: Devlog: raid lockouts',
      why: "Orion won't do these until you say yes.", score: 64, open: { window: 'queue' }, actions: ['open'], meta: { count: 2, risk: 'medium' } },
    { id: 'msg:dc:openwow-dev', kind: 'message', title: 'Leo on Discord', detail: 'one thing — the reset timer shows UTC instead of server time',
      why: 'You open Leo\'s messages most days.', score: 48, open: { window: 'messages', thread: 'dc:openwow-dev' }, actions: ['open', 'draft'],
      meta: { sender: 'Leo', app_label: 'Discord', unread: 2, thread: 'dc:openwow-dev', ts: t - 900 } },
    { id: 'next:promise:demo', kind: 'next_up', title: 'Send Maya the demo track', detail: 'Due in 3 hours (you told Maya).',
      why: "You said you'd do this.", score: 40, open: { window: 'life' }, actions: ['open'], meta: { due: t + 3 * 3600 } },
  ]
    .filter((c) => focusPins.has(c.id) || !focusHidden.has(c.id))
    .map((c) => (focusPins.has(c.id) ? { ...c, pinned: true, score: c.score + 100, why: 'You pinned this.' } : { ...c, pinned: false }))
    .sort((a, b) => b.score - a.score)
    .slice(0, 5);
  return { cards, all_clear: cards.length === 0, line: cards.length ? '' : 'All clear. Nothing needs you right now.', generated_at: t };
}
const FOCUS_DRAFTS: Record<string, { draft: string; thread: string }> = {
  'msg:tg:maya': { draft: "yep! sending it tonight, it's sounding good", thread: 'tg:maya' },
  'msg:dc:openwow-dev': { draft: 'good catch, I\'ll switch it to server time today', thread: 'dc:openwow-dev' },
};

// ---------------------------------------------------------------- Orion's voice [voice2]
const voiceActive = { engine: 'kokoro', voice: 'af_nicole', speed: 0.92, warmth: 0, delivery: '', chosen: true };
function voiceSnap() {
  const kokoro = [['af_nicole', "Nicole and Heart (Orion's usual voice)"], ['af_heart', 'Heart'], ['af_bella', 'Bella'], ['af_sky', 'Sky'], ['am_michael', 'Michael'], ['am_adam', 'Adam']]
    .map(([id, name]) => ({ id, name }));
  return {
    active: voiceActive, chosen: voiceActive.chosen,
    engines: [
      { id: 'kokoro', name: 'Kokoro (built in)', available: true, voices: kokoro, speed: true, warmth: true, delivery: false, design: false, about: 'Clear and quick on any computer. Apache-2.0.' },
      { id: 'voicebox', name: 'Voicebox', available: false, voices: [], speed: true, warmth: true, delivery: true, design: false, about: 'More natural voices, voices from a description, and delivery styles. Best with a graphics card. MIT.', gpu: null },
      { id: 'piper', name: 'Piper (tiny)', available: false, voices: [], speed: true, warmth: true, delivery: false, design: false, about: 'Very small and fast. Each voice has its own licence.' },
    ],
    voicebox: { reachable: false, url: '', gpu: null, error: '', how: "Voicebox isn't running. Start it, or install it (OrionOS: Settings > Orion's voice). Orion uses its built-in voice until then." },
    note: '', consent: 'Only use your own voice or a voice you have permission to use.', sample: "Morning. I'm Orion. This is how I'll sound when we talk.",
  };
}

// ---------------------------------------------------------------- Test Center
type TStatus = 'todo' | 'running' | 'pass' | 'fail' | 'skip';
interface TResult { status: TStatus; detail?: string; note?: string; at?: number; armed?: boolean; ask?: string | null }
const tests = SAMPLE.tests;
const tResults: Record<string, TResult> = {};
let tRunningUntil = 0;

/** What the sample PC "sees" for each automatic check (plain words, all sample data). */
const AUTO_NOTES: Record<string, string> = {
  daemon_up: "Orion's brain answered in 12 ms.",
  key_works: 'The per-start key works.',
  hud_connected: 'This window is connected.',
  health_light: 'All green.',
  parts_ok: 'Every part started.',
  mic_found: 'Found a microphone (sample).',
  mic_off_default: 'The hot mic is off until you turn it on.',
  phone_paired: 'No phone paired yet: open Phone to pair one.',
  phone_reaches: 'Waiting for a paired phone.',
  inbox_draft: 'Drafted a reply in your style. Nothing was sent.',
  agents_found: 'Claude Code and Codex are installed (sample).',
  dry_mission: 'A stand-in agent made a change on a test branch and Orion reviewed it.',
  os_wifi: 'Connected to Wi-Fi (sample).',
};
const AUTO_FAIL = new Set(['phone_paired', 'phone_reaches']); // honest: no phone yet in the preview

/** What "Do it for me" shows for manual items, and whether the window ticks it off by itself. */
const DO_RESULT: Record<string, { note: string; pass?: boolean; open?: string }> = {
  glass_windows: { note: 'Opened a glass window for you. Do the windows look clear and soft?', open: 'projects' },
  tts_speaks: { note: 'Orion said "Hello, I can talk." Did you hear it?' },
  approve_card: { note: 'Sent a do-nothing test card to your phone. Approve it there with your fingerprint.' },
  real_mission: { note: 'Preview: a real mission costs a few cents, so here it only opens Mission Control.', open: 'missions' },
  start_project: { note: 'Opened Projects with a sample idea.', open: 'projects', pass: true },
  record_skill: { note: 'Recorded a practice skill on the practice page.', pass: true, open: 'skills' },
  replay_skill: { note: 'Replayed the practice skill: 3 of 3 steps worked.', pass: true },
  undo_last: { note: 'Undid the last change. Everything is back as it was.', pass: true },
  privacy_off: { note: 'Turned everything off. Nothing is watching or listening.', pass: true, open: 'privacy' },
  ghost_preview: { note: 'Built a practice change: see the ghost preview in the corner.', pass: true },
  os_sleep_lock: { note: 'Close the lid or press the power key, then wake it. Did it lock and come back?' },
};

function tPublic(it: (typeof tests.items)[number]) {
  const r = tResults[it.id] ?? { status: 'todo' as TStatus };
  const status = r.status;
  let next = it.next;
  if (status === 'running') next = 'Wait a moment while Orion checks this.';
  else if (status === 'pass' || status === 'skip') next = 'Nothing more to do here.';
  else if (status === 'fail') next = it.kind === 'auto' ? 'Tap Run to try again.' : "Try it again, then tap Done or It didn't work.";
  if (r.ask && status === 'todo') next = r.ask;
  const doLabel = status === 'pass' || status === 'skip' || status === 'running' ? null : it.do;
  return {
    ...it, status, detail: r.detail ?? it.detail, next, note: r.note ?? '', at: r.at ?? null, do: doLabel,
    ask: status === 'todo' ? r.ask ?? null : null, armed: !!r.armed && status === 'todo',
  };
}
function tView() {
  if (tRunningUntil && now() >= tRunningUntil) {
    tRunningUntil = 0;
    for (const it of tests.items) {
      if (tResults[it.id]?.status !== 'running') continue;
      tResults[it.id] = {
        status: AUTO_FAIL.has(it.id) ? 'fail' : 'pass', at: now(),
        note: AUTO_NOTES[it.id] ?? 'Works (sample).',
      };
    }
  }
  const groups = tests.groups.map((g) => ({ ...g, items: tests.items.filter((i) => i.group === g.id).map(tPublic) }))
    .filter((g) => g.items.length);
  const flat = groups.flatMap((g) => g.items);
  const summary = { pass: 0, fail: 0, todo: 0, skip: 0 };
  for (const x of flat) summary[x.status === 'running' ? 'todo' : x.status] += 1;
  const todo = flat.filter((x) => x.status === 'todo');
  const nxt = todo.find((x) => x.kind === 'auto' && !x.at) ?? todo.find((x) => x.do) ?? todo[0] ?? null;
  return { groups, next: nxt, summary, running: tRunningUntil > 0, platform: 'orionos', all_done: !!flat.length && !summary.todo && !summary.fail };
}
function tRunAuto(fresh: boolean) {
  for (const it of tests.items) {
    if (it.kind !== 'auto' || it.id === 'core_renders') continue;
    const st = tResults[it.id]?.status;
    if (!fresh && st === 'pass') continue;
    tResults[it.id] = { status: 'running' };
  }
  tRunningUntil = now() + 2.2;
  return tView();
}

// ---------------------------------------------------------------- missions (they move along by themselves)
interface SimMission extends Mission {
  script: string[];
  log: string[];
  step_s: number;
  diff_text: string;
}
const DIFF = (file: string, add: string[]) =>
  `diff --git a/${file} b/${file}\n--- a/${file}\n+++ b/${file}\n@@ -12,6 +12,${6 + add.length} @@\n${add.map((l) => `+${l}`).join('\n')}\n`;

function mission(p: Partial<SimMission> & { title: string; repo_name: string; state: MissionState }): SimMission {
  const started = p.started ?? T0 - 600;
  return {
    id: nid('m'), description: '', repo: `~/Orion Projects/${p.repo_name}`, agent: 'claude', agent_label: 'Claude Code',
    model: null, task: 'implement', why: 'Claude is best at this kind of job (routing table v3).', phase: '', paused: false,
    cost_usd: 0, cost_estimated: true, cost_note: 'Estimated from tokens used (sample).', budget_usd: 2, time_limit_min: 30,
    active_s: 0, created: started - 5, started, finished: null, branch: `orion/mission-${p.repo_name.toLowerCase().replace(/\W+/g, '-')}`,
    summary: '', source: 'hud', race_group: null, swarm_id: null, changed_files: [], review: null, checks: null, merge: null,
    script: [], log: [], step_s: 2.4, diff_text: '', ...p,
  } as SimMission;
}
const SCRIPT_GAME = [
  'Reading the starter: index.html, game.js, style.css', 'Plan: a player ship, falling stars to catch, a score and lives',
  'Writing game.js: the game loop and the player', 'Writing game.js: stars, collisions and the score',
  'Adding a win screen at 50 points and a lose screen at 0 lives', 'Running the tests: node --test', '4 tests passed',
  'Writing the README: how to play', 'Done. Asking for a second review',
];
const SCRIPT_FIX = [
  'Reading the bug report', 'Found it: the reset timer used UTC instead of the server time', 'Writing a test that fails first',
  'Fixing the timer', 'Running the tests', 'All 38 tests passed', 'Done. Asking for a second review',
];
const missions: SimMission[] = [
  mission({
    title: 'Star catcher: add a high-score table', repo_name: 'Star Catcher', state: 'running', started: T0 - 40,
    script: ['Reading game.js and the README', 'Plan: keep the top 5 scores in this browser', 'Writing scores.js',
      'Showing the table on the lose screen', 'Running the tests: node --test', '6 tests passed', 'Done. Asking for a second review'],
    log: ['Started in a fresh branch, separate from your work'], step_s: 11,
    diff_text: DIFF('scores.js', ['export function saveScore(score) {', '  const top = loadScores().concat(score).sort((a, b) => b - a).slice(0, 5);', "  localStorage.setItem('scores', JSON.stringify(top));", '}']),
  }),
  mission({
    title: 'Guild site: add a dark mode switch', repo_name: 'Guild Site', state: 'running', agent: 'codex', agent_label: 'Codex', started: T0 - 70, step_s: 14,
    script: ['Reading styles.css and the header', 'Plan: a switch in the header that remembers your choice', 'Adding dark colours to styles.css',
      'Writing the switch in header.js', 'Running the tests: npm test', '22 tests passed', 'Done. Asking for a second review'],
    log: ['Started in a fresh branch, separate from your work'],
    diff_text: DIFF('header.js', ["const toggle = document.querySelector('#theme');", "toggle.onclick = () => document.body.classList.toggle('dark');"]),
  }),
  mission({
    title: 'Fix the raid reset timer', repo_name: 'Guild Site', state: 'review', agent: 'codex', agent_label: 'Codex',
    started: T0 - 1500, finished: T0 - 300, active_s: 1200, cost_usd: 0.42, summary: 'The reset timer now shows server time. Added a test.',
    changed_files: ['src/timers.ts', 'tests/timers.test.ts'], log: SCRIPT_FIX,
    checks: { verdict: 'green', reason: 'Build passed, 38 tests passed.' },
    review: { verdict: 'approve', reviewer: 'Claude', notes: 'Small and correct. The new test covers the bug.' },
    diff_text: DIFF('src/timers.ts', ['export const resetAt = (raid: Raid) =>', '  zonedTime(raid.reset, SERVER_TIMEZONE);']),
  }),
  mission({
    title: 'Website: add a contact form', repo_name: 'Portfolio', state: 'needs_you', started: T0 - 900, active_s: 700,
    cost_usd: 0.31, summary: 'Claude asks: should the form email you, or save messages to a file?', phase: 'question',
    log: ['Reading index.html', 'Added the form fields', 'Question for you: email or a file?'],
  }),
  mission({
    title: 'Tidy the notes folder', repo_name: 'Field Notes', state: 'queued', agent: 'gemini', agent_label: 'Gemini CLI', started: null as unknown as number,
  }),
  mission({
    title: 'Pixel garden: first playable level', repo_name: 'Pixel Garden', state: 'done', started: T0 - 86400, finished: T0 - 84000,
    active_s: 2100, cost_usd: 0.88, merge: 'merged', summary: 'One level, arrow keys to move, plant all seeds to win.',
    changed_files: ['game.js', 'level1.js', 'README.md'], log: SCRIPT_GAME,
    checks: { verdict: 'green', reason: 'Build passed, 4 tests passed.' }, review: { verdict: 'approve', reviewer: 'Codex' },
  }),
  mission({
    title: 'Python tool: rename photos by date', repo_name: 'Photo Renamer', state: 'done', started: T0 - 172800, finished: T0 - 171000,
    active_s: 1300, cost_usd: 0.27, merge: 'merged', summary: 'tool.py renames photos to their date taken. Dry run by default.',
    changed_files: ['tool.py', 'test_tool.py'], checks: { verdict: 'green', reason: '5 tests passed.' },
  }),
];
let emit: DemoEmit = () => undefined;

function advanceMissions() {
  const t = now();
  for (const m of missions) {
    if (m.state === 'queued' && !missions.some((x) => x.state === 'running' && x.agent === m.agent) && m.started && t > m.started) {
      m.state = 'running';
    }
    if (m.state !== 'running' || m.paused) continue;
    const due = Math.min(m.script.length, Math.floor((t - (m.started ?? t)) / m.step_s));
    const done = m.log.length - (m.log[0]?.startsWith('Started') ? 1 : 0);
    for (let i = done; i < due; i++) m.log.push(m.script[i]);
    m.active_s = Math.round(t - (m.started ?? t));
    m.cost_usd = Math.round(Math.min(m.budget_usd * 0.6, 0.02 + m.active_s * 0.004) * 100) / 100;
    m.phase = m.log[m.log.length - 1] ?? '';
    if (due >= m.script.length && m.script.length) {
      m.state = 'review';
      m.finished = t;
      m.summary = m.summary || 'Finished. Build and tests passed; a second agent reviewed it.';
      m.checks = { verdict: 'green', reason: 'Build passed, tests passed.' };
      m.review = { verdict: 'approve', reviewer: m.agent === 'claude' ? 'Codex' : 'Claude', notes: 'Looks right and is small enough to merge.' };
      if (!m.changed_files.length) m.changed_files = m.diff_text ? [m.diff_text.split(' b/')[1].split('\n')[0]] : ['game.js', 'README.md'];
      emit({ event: 'AUDIO_CUE', cue: 'task_finished' });
    }
  }
}
const pub = (m: SimMission): Mission => {
  const { script: _s, log: _l, step_s: _t, diff_text: _d, ...rest } = m;
  void _s; void _l; void _t; void _d;
  return rest;
};
const detail = (m: SimMission): MissionDetail => ({
  ...pub(m), diffstat: m.changed_files.map((f) => ` ${f} | ${4 + f.length % 9} ++++`).join('\n'), diff: m.diff_text, log_tail: m.log.slice(-40),
});

// ---------------------------------------------------------------- projects
const projects: Project[] = [
  { name: 'Star Catcher', path: '~/Orion Projects/Star Catcher', starter: 'web-game', idea: 'Catch falling stars with a little ship. Three lives.', created: T0 - 3600, missions: [missions[0].id], exists: true, playable: true, latest: null },
  { name: 'Pixel Garden', path: '~/Orion Projects/Pixel Garden', starter: 'web-game', idea: 'Plant every seed on the level to win.', created: T0 - 90000, missions: [missions[5].id], exists: true, playable: true, latest: null },
  { name: 'Portfolio', path: '~/Orion Projects/Portfolio', starter: 'website', idea: 'A one-page site with my art and a contact form.', created: T0 - 200000, missions: [missions[3].id], exists: true, playable: true, latest: null },
  { name: 'Photo Renamer', path: '~/Orion Projects/Photo Renamer', starter: 'python-tool', idea: 'Rename photos to the date they were taken.', created: T0 - 260000, missions: [missions[6].id], exists: true, playable: false, latest: null },
];
const latestOf = (p: Project) => {
  const m = missions.find((x) => x.id === p.missions[p.missions.length - 1]);
  return { ...p, latest: m ? pub(m) : null };
};
function startProjectMission(p: Project, text: string) {
  const m = mission({
    title: text.length > 60 ? `${text.slice(0, 57)}…` : text, repo_name: p.name, state: 'running', started: now(),
    description: text, script: p.starter === 'python-tool' ? SCRIPT_FIX.map((l) => l.replace('bug report', 'idea')) : SCRIPT_GAME,
    log: ['Started in a fresh branch, separate from your work'],
    diff_text: DIFF(p.starter === 'python-tool' ? 'tool.py' : 'game.js', ['// written by Claude in the preview', 'function update(dt) {', '  player.move(dt);', '}']),
  });
  missions.unshift(m);
  p.missions.push(m.id);
  return m;
}

// ---------------------------------------------------------------- phone pairing
interface DemoPhone { device_id: string; name: string; fingerprint: string; words: string; last_seen: number | null; online: boolean; battery: number | null; charging?: boolean; network?: string }
const phones: DemoPhone[] = [];
let pairOpenedAt = 0;
let pairConfirmed = false;
const SAMPLE_PHONE = { device_id: 'demo-phone', name: 'Sample phone', fingerprint: 'demo-fp', words: 'river · lantern · maple · orbit · quiet' };
const LAN_OK = { ok: true, words: 'Your phone can reach this PC on the Wi-Fi.', fix: null, fix_words: '' };

// ---------------------------------------------------------------- privacy
const sensorOn: Record<string, boolean> = { screen: true, proactive: true, hotmic: false, wake: false, voice_typing: true, camera: false, recorder: false, inbox_mirror: true };
function privacySnap() {
  const sensors = SAMPLE.privacy.map((s) => ({ ...s, on: !!sensorOn[s.id], available: true }));
  const on = sensors.filter((s) => s.on);
  const words = on.length ? `${on.length} thing${on.length === 1 ? ' is' : 's are'} on: ${on.map((s) => s.label).join(', ')}.` : 'Nothing is watching or listening.';
  return { sensors, on: on.length, words };
}

// ---------------------------------------------------------------- guide mode
interface Guide { title: string; steps: Array<{ text: string; find?: string; selector?: string }>; index: number }
let guide: Guide | null = null;
function guideFor(task: string): Guide {
  const low = task.toLowerCase();
  if (/phone|pair/.test(low)) {
    return { title: 'Pair my phone', index: 0, steps: [
      { text: 'Open the console: the round Orion button (bottom left unless you moved it).', find: 'Orion button', selector: '[data-orion-button]' },
      { text: 'Choose Phone.', find: 'Phone' },
      { text: 'On your phone, open the camera and point it at the QR code.' },
      { text: 'Check the five words match on both screens, then press Confirm.' },
    ] };
  }
  return { title: task.replace(/^show me how to\s*/i, '') || 'Connect to Wi-Fi', index: 0, steps: [
    { text: 'Click the network icon at the top right of the screen.', find: 'network icon' },
    { text: 'Pick your Wi-Fi name from the list.' },
    { text: 'Type the Wi-Fi password and press Connect.' },
  ] };
}
function guideState() {
  if (!guide) return { active: false };
  const st = guide.steps[guide.index];
  let target: { x: number; y: number; w: number; h: number } | null = null;
  if (st.selector) {
    const el = document.querySelector(st.selector);
    const r = el?.getBoundingClientRect();
    const s = window.devicePixelRatio || 1;
    if (r && r.width) target = { x: r.left * s, y: r.top * s, w: r.width * s, h: r.height * s };
  }
  return { active: true, id: 'demo-guide', title: guide.title, index: guide.index, total: guide.steps.length, text: st.text,
    find: st.find, auto: false, target, source: target ? 'screen' : null, size: null };
}

// ---------------------------------------------------------------- skills
interface Skill { id: string; name: string; version: number; trusted: boolean; steps: number; risky_steps: number; voice_triggers: string[]; card: string[]; variables: Array<{ name: string; label: string; kind: string; example: string }>; surface?: string }
const skills: Skill[] = [
  { id: 'sk1', name: 'Weekly invoice', version: 2, trusted: true, steps: 6, risky_steps: 1, voice_triggers: ['send the weekly invoice'], surface: 'browser',
    card: ['1. Open the invoices page', '2. Click "New invoice"', '3. Type the client name', '4. Type the hours', '5. Click "Preview"', '6. Click "Send" (asks you first)'],
    variables: [{ name: 'client', label: 'Client', kind: 'text', example: 'Northwind' }, { name: 'hours', label: 'Hours', kind: 'number', example: '12' }] },
  { id: 'sk2', name: 'Export the sprite sheet', version: 1, trusted: true, steps: 4, risky_steps: 0, voice_triggers: ['export sprites'], surface: 'desktop',
    card: ['1. Open the art app', '2. File > Export', '3. Choose PNG sprite sheet', '4. Save to the game folder'], variables: [] },
];
const drafts: Json[] = [];
let recording: { surface: string; events: number; since: number; app?: string } | null = null;
const runs: Array<{ run_id: string; name: string; status: string; message: string; index: number; total: number; surface?: string; steps: Array<{ description: string; risky: boolean }>; started: number; all: boolean }> = [];
const suggestions = [{ id: 'sg1', title: 'You rename screenshots the same way every day', body: 'Orion noticed the same 3 steps 4 times this week. Want it as a skill?', steps: ['Open the Screenshots folder', 'Rename the newest file to the date', 'Move it to Art/Refs'] }];
function skillsState() {
  const t = now();
  for (const r of runs) {
    if (r.status === 'running' || (r.all && r.status === 'waiting_step')) {
      r.index = Math.min(r.total, Math.floor((t - r.started) / 1.2));
      const step = r.steps[r.index];
      if (r.index >= r.total) { r.status = 'done'; r.message = 'Done. Every step worked.'; }
      else if (step?.risky) { r.status = 'waiting_approval'; r.message = `"${step.description}" waits for your OK.`; }
      else r.message = `Doing: ${step?.description ?? ''}`;
    }
  }
  if (recording) recording.events = Math.min(9, Math.floor((t - recording.since) / 1.5));
  return { skills, drafts, recording: recording ? { ...recording, title: 'Practice page', url: 'about:practice' } : null,
    runs: runs.filter((r) => r.status !== 'done' || t - r.started < 20), suggestions };
}

// ---------------------------------------------------------------- video scout
const scoutVideos: Json[] = [
  scoutCard('Top 10 addons for the new season', 'youtube', 'Raid Ready', 182000, 412000, 'worth checking',
    ['Details! and a new nameplate addon lead the list', 'Two addons are brand new this week', 'A weak aura pack is called out as outdated'],
    [{ name: 'Details!', what: 'Damage meter', link: 'https://www.curseforge.com/' }, { name: 'Plater', what: 'Nameplates', link: 'https://www.curseforge.com/' }]),
  scoutCard('I built a game in a weekend with AI agents', 'tiktok', 'devlog daily', 54000, 1200000, 'maybe',
    ['Uses two coding agents side by side', 'Most of the time went into testing', 'Claims a full game in 48 hours (not shown)'], []),
];
function scoutCard(title: string, platform: string, uploader: string, followers: number, views: number, verdict: string, bullets: string[], addons: Array<{ name: string; what: string; link: string }>): Json {
  return {
    id: nid('v'), url: platform === 'youtube' ? 'https://www.youtube.com/' : 'https://www.tiktok.com/', platform, title, uploader, followers,
    upload_ts: T0 - 86400 * 2, status: 'done', error: null, notes: [], transcript_source: 'captions',
    stats: { views, likes: Math.round(views * 0.06), comments: Math.round(views * 0.004), reposts: Math.round(views * 0.002) },
    virality: { views_per_day: Math.round(views / 2), engagement: 0.066, vs_creator: 3.1, velocity: Math.round(views / 9), label: 'taking off' },
    summary: { bullets, addons, projects: [], links: [], claims: verdict === 'maybe' ? ['A full game in 48 hours'] : [], verdict, verdict_reason: verdict === 'maybe' ? 'Fun, but the main claim is not shown.' : 'Two useful finds.' },
    top_mentions: addons.map((a, i) => ({ name: a.name, kind: 'addon', count: 4 - i, first_t: 30 + i * 40 })),
  };
}
const scoutBoard = {
  days: 7, videos: 12,
  addons: [
    { name: 'Details!', score: 9.1, videos: 6, mentions: 14, trend: 'steady', best_video: { title: 'Top 10 addons for the new season', url: 'https://www.youtube.com/' } },
    { name: 'Plater', score: 7.4, videos: 4, mentions: 9, trend: 'rising', best_video: null },
    { name: 'BigWigs', score: 5.2, videos: 3, mentions: 5, trend: 'new', best_video: null },
  ],
  projects: [], keywords: [{ name: 'season 2', score: 6, videos: 5, mentions: 8, trend: 'rising', best_video: null }], new_on_github: [],
};

// ---------------------------------------------------------------- self-changes (Living Orion)
interface Change { id: string; request: string; summary: string; reason: string; kind: string; status: string; mode: string; files: Json[]; checks: Json[]; screens: Json; touches_ui: boolean; touches_daemon: boolean; preview_dir?: string; decision?: string; diff?: string }
const changes: Change[] = [
  { id: 'c1', request: 'make the clock bigger', summary: 'The clock on the desktop is 20% bigger.', reason: 'You asked for it.', kind: 'ui_code', status: 'applied', mode: 'layer',
    files: [{ path: 'apps/desktop/src/components/shell/TitleBar.tsx', action: 'modify', additions: 2, deletions: 2 }], checks: [], screens: {}, touches_ui: true, touches_daemon: false, decision: 'approved' },
];
const swaps: Json[] = [{ id: 's1', change_id: 'c1', summary: 'The clock on the desktop is 20% bigger.', status: 'kept', at: T0 - 7200, touches_ui: true, touches_daemon: false }];
function practiceChange() {
  changes.unshift({
    id: nid('c'), request: 'practice change: a softer glow on the menu', summary: 'The menu gets a softer blue glow.', reason: 'A practice change from the Test Center.',
    kind: 'ui_code', status: 'ready', mode: 'layer', touches_ui: true, touches_daemon: false, screens: { before: true, after: true },
    files: [{ path: 'apps/desktop/src/components/console/CommandConsole.tsx', action: 'modify', additions: 3, deletions: 1 }],
    checks: [{ name: 'types', label: 'Type check', ok: true, skipped: false, detail: 'No errors' }, { name: 'tests', label: 'Tests', ok: true, skipped: false, detail: '5 of 5 passed' }],
    diff: "-  boxShadow: '0 0 24px #2E6BFF'\n+  boxShadow: '0 0 40px rgba(46,107,255,0.45)'\n",
  });
}

// ---------------------------------------------------------------- the router
const ok = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });
const say = (message: string, extra: Json = {}) => ok({ ok: true, message, ...extra });
let presenceOn = false;

export async function demoRoute(method: string, path: string, body: Json, emitEvent: DemoEmit): Promise<Response | null> {
  emit = emitEvent;
  const [p, q = ''] = path.split('?');
  const query = new URLSearchParams(q);
  const POST = method === 'POST';
  let m: RegExpMatchArray | null;

  // ---- Focus dashboard [focus] (sample data; drafts are never sent)
  if (p === '/focus' && !POST) return ok(focusView());
  // ---- the console's side panels [command] (sample data)
  if (p === '/promises' && !POST) return ok({ promises: demoPromises(), week: '', brief: '' });
  if ((m = p.match(/^\/promises\/([\w-]+)\/(done|dismiss|reopen)$/))) {
    if (m[2] === 'reopen') promiseDone.delete(m[1]); else promiseDone.add(m[1]);
    return ok({ promise: { id: m[1], status: m[2] === 'reopen' ? 'open' : m[2] === 'done' ? 'done' : 'dismissed' } });
  }
  if (p === '/core/signals') return ok({ cpu: demoCpu(), battery: null, thinking: 0, heat: 0, quiet: 0 });
  if (p === '/focus/feedback') {
    const id = String(body.id ?? '');
    const action = String(body.action ?? '');
    if (action === 'pin') focusPins.add(id);
    else if (action === 'unpin') focusPins.delete(id);
    else if (action === 'dismiss') { focusHidden.add(id); focusPins.delete(id); }
    return ok({ ok: true });
  }
  if (p === '/focus/draft') {
    const d = FOCUS_DRAFTS[String(body.id ?? '')];
    return d ? ok(d) : ok({ error: "That message isn't on the dashboard any more." }, 400);
  }

  // ---- Test Center
  if (p === '/tests' && !POST) return ok(tView());
  if (p === '/tests/open-request') return ok({ open: new URLSearchParams(window.location.search).get('open') === 'tests', run: false });
  if (p === '/tests/run-auto') return ok(tRunAuto(body.fresh !== false));
  if (p === '/tests/reset') { for (const k of Object.keys(tResults)) delete tResults[k]; return ok(tView()); }
  if (p === '/tests/report') {
    const v = tView();
    return ok({ markdown: `# Orion test report (preview, sample data)\n\nWorks: ${v.summary.pass} · Didn't work: ${v.summary.fail} · To do: ${v.summary.todo} · Skipped: ${v.summary.skip}\n` });
  }
  if ((m = p.match(/^\/tests\/([\w-]+)\/result$/))) {
    const st = String(body.status ?? 'pass') as TStatus;
    tResults[m[1]] = { status: st, at: now(), note: typeof body.note === 'string' ? body.note : st === 'pass' ? 'You said it works.' : '' };
    return ok({ ok: true });
  }
  if ((m = p.match(/^\/tests\/([\w-]+)\/run$/))) {
    const id = m[1];
    const it = tests.items.find((x) => x.id === id);
    if (!it) return ok({ error: 'No such check.' }, 404);
    if (it.kind === 'auto') {
      tResults[id] = { status: 'running' };
      window.setTimeout(() => { tResults[id] = { status: AUTO_FAIL.has(id) ? 'fail' : 'pass', at: now(), note: AUTO_NOTES[id] ?? 'Works (sample).' }; }, 1400);
      return ok({ ok: true });
    }
    const r = DO_RESULT[id] ?? { note: 'Preview: on your PC Orion does this part for you.' };
    tResults[id] = { status: 'todo', armed: true, detail: r.note };
    window.setTimeout(() => {
      if (r.pass) tResults[id] = { status: 'pass', at: now(), note: r.note };
      else tResults[id] = { status: 'todo', detail: r.note, ask: r.note.endsWith('?') ? r.note : null };
      if (id === 'ghost_preview') practiceChange();
      if (r.open) {
        emit({ event: 'HUD_OPEN', what: r.open, ...(id === 'start_project' ? { prefill: { name: 'Comet Dodge', starter: 'web-game', idea: 'Dodge comets with the arrow keys. Survive 60 seconds to win.' } } : {}) });
      }
    }, 1600);
    return ok({ ok: true });
  }

  // ---- Mission Control
  advanceMissions();
  if (p === '/missions' && !POST) {
    return ok({
      missions: missions.map(pub),
      agents: [
        { key: 'claude', label: 'Claude Code', installed: true, mode: 'headless', note: '' },
        { key: 'codex', label: 'Codex', installed: true, mode: 'headless', note: '' },
        { key: 'gemini', label: 'Gemini CLI', installed: true, mode: 'headless', note: '' },
      ],
      templates: SAMPLE.templates, routing: { version: 3, source: 'built-in', tasks: ['implement', 'bugfix', 'website', 'game'] },
    });
  }
  if (p === '/missions' && POST) {
    const proj = { name: 'Sandbox', starter: 'web-game' } as Project;
    const mm = startProjectMission({ ...proj, missions: [] } as Project, String(body.title ?? 'New mission'));
    mm.repo_name = String(body.repo ?? 'Sandbox').split(/[\\/]/).pop() || 'Sandbox';
    if (body.agent) { mm.agent = String(body.agent); mm.agent_label = mm.agent === 'codex' ? 'Codex' : mm.agent === 'gemini' ? 'Gemini CLI' : 'Claude Code'; }
    return ok(pub(mm));
  }
  if ((m = p.match(/^\/missions\/([\w-]+)\/log$/))) {
    const mm = missions.find((x) => x.id === m![1]);
    if (!mm) return ok({ error: 'No such mission.' }, 404);
    const since = Number(query.get('since') ?? 0);
    return ok({ lines: mm.log.slice(since), next: mm.log.length, state: mm.state });
  }
  if ((m = p.match(/^\/missions\/([\w-]+)\/(pause|resume|stop|approve_review|reject)$/))) {
    const mm = missions.find((x) => x.id === m![1]);
    if (!mm) return ok({ error: 'No such mission.' }, 404);
    const op = m[2];
    let message = '';
    if (op === 'pause') { mm.paused = true; message = 'Paused.'; }
    if (op === 'resume') { mm.paused = false; message = 'Carrying on.'; }
    if (op === 'stop') { mm.state = 'stopped'; mm.finished = now(); message = 'Stopped. Nothing was merged.'; }
    if (op === 'reject') { mm.state = 'stopped'; mm.finished = now(); message = 'Thrown away. Your project is unchanged.'; }
    if (op === 'approve_review') {
      mm.state = 'done'; mm.merge = 'merged'; mm.finished = now();
      message = 'Merged. (On your PC, your phone asks for your fingerprint before anything is merged.)';
    }
    return ok({ ok: true, message, mission: pub(mm) });
  }
  if ((m = p.match(/^\/missions\/([\w-]+)$/))) {
    const mm = missions.find((x) => x.id === m![1]);
    return mm ? ok(detail(mm)) : ok({ error: 'No such mission.' }, 404);
  }

  // ---- Projects
  if (p === '/projects' && !POST) return ok({ root: '~/Orion Projects', starters: SAMPLE.starters, projects: projects.map(latestOf) });
  if (p === '/projects' && POST) {
    const name = String(body.name ?? 'New project').trim() || 'New project';
    if (projects.some((x) => x.name.toLowerCase() === name.toLowerCase())) return ok({ error: 'There is already a project with that name.' }, 409);
    const proj: Project = { name, path: `~/Orion Projects/${name}`, starter: (body.starter as Project['starter']) ?? 'web-game', idea: String(body.idea ?? ''), created: now(), missions: [], exists: true, playable: false, latest: null };
    projects.unshift(proj);
    const mm = body.agent ? startProjectMission(proj, proj.idea || `Build ${name}`) : null;
    window.setTimeout(() => { proj.playable = proj.starter !== 'python-tool'; }, 8000);
    return ok({ project: latestOf(proj), mission: mm ? pub(mm) : null, message: mm ? 'Made the folder and started Claude on it. Watch it work below.' : 'Made the folder.' });
  }
  if ((m = p.match(/^\/projects\/([^/]+)\/(change|open|play)$/))) {
    const proj = projects.find((x) => x.name === decodeURIComponent(m![1]));
    if (!proj) return ok({ error: 'No such project.' }, 404);
    if (m[2] === 'change') {
      const mm = startProjectMission(proj, String(body.text ?? 'A change'));
      return ok({ project: latestOf(proj), mission: pub(mm), message: 'Claude is on it.' });
    }
    if (m[2] === 'open') return say(`Preview: on your PC this opens ${proj.path} in the file manager.`);
    return ok({ url: '', message: 'Preview: on your PC this opens the game in your browser.' });
  }

  // ---- Phone
  if (p === '/phones' && !POST) return ok({ phones, pc: { name: 'Orion PC' } });
  if (p === '/phones/app-share' && !POST) return ok({ open: false, apk: false, url: '', qr_svg: '', until: null });
  if (p === '/phones/app-share') return ok({ open: true, apk: false, url: '', qr_svg: '', until: now() + 900 });
  if (p === '/phones/pair/open') {
    pairOpenedAt = now();
    pairConfirmed = false;
    return ok({ code: '4821-0937', url: SAMPLE.qr_text, qr_svg: SAMPLE.qr_svg, expires: now() + 600, address: 'http://192.168.1.20:9121', how: 'Your phone and this PC are on the same Wi-Fi.', lan: LAN_OK });
  }
  if (p === '/phones/pair/status') {
    const pending = !pairConfirmed && pairOpenedAt && now() - pairOpenedAt > 7 ? [SAMPLE_PHONE] : [];
    return ok({ open: !pairConfirmed, expires: pairOpenedAt + 600, pending, phones, lan: LAN_OK });
  }
  if (p === '/phones/pair/close') return say('Closed.');
  if ((m = p.match(/^\/phones\/([\w-]+)\/confirm$/))) {
    pairConfirmed = true;
    if (!phones.length) phones.push({ ...SAMPLE_PHONE, last_seen: now(), online: true, battery: 82, charging: false, network: 'Wi-Fi' });
    tResults.phone_paired = { status: 'pass', at: now(), note: 'Sample phone paired.' };
    return ok({ ok: true, words: 'Paired. Your phone can now see this PC.' });
  }
  if ((m = p.match(/^\/phones\/([\w-]+)\/unpair$/))) { phones.splice(0); return ok({ ok: true, words: 'Unpaired.' }); }
  if (p === '/phones/test-card') return ok({ ok: true, words: 'Sent a do-nothing test card. (Preview: no phone is really paired.)' });
  if (p === '/phones/open-access') return ok({ ok: true, words: 'Preview: on Windows this asks once for admin permission.' });

  // ---- Games (sample library: nothing is installed or launched in the preview)
  if (p === '/games' && !POST) return ok({
    ok: true,
    windows_drive: { present: true, open: true, bitlocker: false },
    launchers: {
      steam: { name: 'Steam', for: 'Steam games (Proton)', installed: true },
      heroic: { name: 'Heroic', for: 'Epic games', installed: false },
      battlenet: { name: 'Battle.net (Lutris)', for: 'World of Warcraft', installed: false },
    },
    windows: [
      { id: 'epic-rocket-league', name: 'Rocket League', store: 'Epic', where: 'windows', reuse: 'Copy it here, then Import Game in Heroic.', note: 'Runs through Proton; an update can break anti-cheat for a while.' },
      { id: 'bnet-wow', name: 'World of Warcraft', store: 'Battle.net', where: 'windows', reuse: 'Copy it here, then Locate the game in Battle.net.' },
    ],
    here: [{ id: 'steam-sample', name: 'Sample Steam game', store: 'Steam', where: 'here' }],
    general_note: 'Preview: sample games. On OrionOS this lists the games on your PC.',
  });
  if (p === '/games/do') return ok({ ok: true, message: 'Preview: on OrionOS this sets it up or starts the game.' });

  // ---- Orion's voice [voice2] (nothing is spoken in the preview; Voicebox shows as not installed)
  if (p === '/voice/engines') return ok(voiceSnap());
  if (p === '/voice/set') {
    for (const k of ['engine', 'voice', 'speed', 'warmth', 'delivery', 'chosen'] as const) if (body[k] !== undefined) (voiceActive as Record<string, unknown>)[k] = body[k];
    return ok({ ok: true, active: voiceActive, message: 'Saved. (Preview: on your PC Orion uses this voice from now on.)' });
  }
  if (p === '/voice/preview') return ok({ ok: true, engine: 'kokoro', note: 'Preview: on your PC Orion says the sample out loud.', duration_s: 2.4 });
  if (p === '/voice/design') return ok({ ok: false, message: 'Designing a voice needs Voicebox running on your PC.' });
  if (p === '/voice/hear') return ok({ ok: true, text: 'how do I sound' });

  // ---- Privacy
  if (p === '/privacy/dashboard') return ok(privacySnap());
  if (p === '/privacy/off') {
    const id = String(body.id ?? '');
    sensorOn[id] = false;
    const label = SAMPLE.privacy.find((s) => s.id === id)?.label ?? id;
    return ok({ ok: true, id, message: `${label} is off.`, ...privacySnap() });
  }
  if (p === '/privacy/off-all') { for (const k of Object.keys(sensorOn)) sensorOn[k] = false; presenceOn = false; return ok({ ok: true, message: 'Everything is off.', ...privacySnap() }); }

  // ---- Guide Mode
  if (p === '/guide' && !POST) return ok(guideState());
  if (p === '/guide/start') { guide = guideFor(String(body.task ?? '')); sensorOn.guide = true; return ok(guideState()); }
  if (p === '/guide/next' || p === '/guide/back') {
    if (!guide) return ok({ active: false });
    guide.index += p.endsWith('next') ? 1 : -1;
    if (guide.index < 0) guide.index = 0;
    if (guide.index >= guide.steps.length) { const t = guide.title; guide = null; sensorOn.guide = false; return ok({ active: false, finished: t }); }
    return ok(guideState());
  }
  if (p === '/guide/stop') { guide = null; sensorOn.guide = false; return ok({ active: false }); }

  // ---- Skills
  if (p === '/browser-skills' && !POST) return ok(skillsState());
  if (p === '/desktop-skills' && !POST) return ok({ backend: 'preview', identity: true, input: 'preview', can_record: true, ocr: true, settings: { notice_patterns: true, nightly_review: false, review_hour: 21 } });
  if ((m = p.match(/^\/(browser|desktop)-skills\/record\/start$/))) { recording = { surface: m[1] === 'desktop' ? 'desktop' : 'browser', events: 0, since: now(), app: m[1] === 'desktop' ? 'the desktop' : undefined }; return say('Recording. Do the task once, then press Stop.'); }
  if ((m = p.match(/^\/(browser|desktop)-skills\/record\/stop$/))) {
    const surface = recording?.surface ?? 'browser';
    recording = null;
    drafts.push({ draft_id: nid('d'), name: 'Rename the newest screenshot', surface, skipped_private: 1, variables: [{ name: 'folder', label: 'Folder', kind: 'text', example: 'Art/Refs' }],
      card: ['1. Open the Screenshots folder', '2. Click the newest file', '3. Press F2 and type the date', '4. Drag it to Art/Refs'] });
    return say('Stopped. Check the new skill card.');
  }
  if ((m = p.match(/^\/browser-skills\/drafts\/([\w-]+)\/(save|discard)$/))) {
    const i = drafts.findIndex((d) => d.draft_id === m![1]);
    const d = drafts.splice(i, 1)[0] as { name: string; card: string[]; variables: Skill['variables']; surface: string } | undefined;
    if (d && m[2] === 'save') skills.unshift({ id: nid('sk'), name: String(body.name || d.name), version: 1, trusted: true, steps: d.card.length, risky_steps: 0, voice_triggers: (body.voice_triggers as string[]) ?? [], card: d.card, variables: d.variables, surface: d.surface });
    return say(m[2] === 'save' ? 'Saved.' : 'Thrown away.');
  }
  if ((m = p.match(/^\/browser-skills\/([\w-]+)\/run$/))) {
    const s = skills.find((x) => x.id === m![1]);
    if (!s) return ok({ error: 'No such skill.' }, 404);
    runs.unshift({ run_id: nid('r'), name: s.name, status: 'preview', message: 'Preview: the steps are outlined. Choose Step by step or Run all.', index: 0, total: s.card.length, surface: s.surface,
      steps: s.card.map((c) => ({ description: c.replace(/^\d+\.\s*/, '').replace(/ \(asks you first\)$/, ''), risky: /asks you first/.test(c) })), started: now(), all: false });
    return say('Ready to run.');
  }
  if ((m = p.match(/^\/browser-skills\/runs\/([\w-]+)\/control$/))) {
    const r = runs.find((x) => x.run_id === m![1]);
    if (r) {
      const a = String(body.action ?? '');
      if (a === 'stop') { r.status = 'done'; r.message = 'Stopped.'; }
      else if (a === 'next') { r.index += 1; r.status = r.index >= r.total ? 'done' : 'waiting_step'; r.message = r.status === 'done' ? 'Done.' : `Next: ${r.steps[r.index].description}`; }
      else if (a === 'start') { r.status = 'waiting_step'; r.message = `Next: ${r.steps[0].description}`; }
      else { r.status = 'running'; r.all = true; r.started = now() - r.index * 1.2; }
    }
    return say('OK');
  }
  if ((m = p.match(/^\/browser-skills\/([\w-]+)\/delete$/))) { const i = skills.findIndex((x) => x.id === m![1]); if (i >= 0) skills.splice(i, 1); return say('Deleted.'); }
  if ((m = p.match(/^\/browser-skills\/suggestions\/([\w-]+)\/dismiss$/))) { suggestions.splice(0); return say('OK'); }
  if ((m = p.match(/^\/browser-skills\/([\w-]+)\/export$/))) {
    const s = skills.find((x) => x.id === m![1]);
    return new Response(`# ${s?.name ?? 'skill'} (exported from the Orion preview)\n${(s?.card ?? []).map((c) => `# ${c}`).join('\n')}\n`, { status: 200 });
  }
  if (p === '/browser-skills/import') return ok({ draft_id: null, message: 'Preview: importing works in the full app.' });
  if (p === '/desktop-skills/settings') return say('Saved.');

  // ---- Video Scout
  if (p === '/scout/videos') return ok({ videos: scoutVideos, status: { fetcher: true, voice: true, ocr: true, queued: 0, working: [] } });
  if (p === '/scout/leaderboard') {
    if (query.get('format') === 'md') return new Response('# Top addons this week (sample)\n\n1. Details!\n2. Plater\n3. BigWigs\n');
    if (query.get('format') === 'csv') return new Response('name,score\nDetails!,9.1\nPlater,7.4\nBigWigs,5.2\n');
    return ok(scoutBoard);
  }
  if (p === '/scout/leaderboard/ideas') return ok({ saved: 'ideas/addons-this-week.md (preview)' });
  if (p === '/scout' && POST) {
    const urls = String(body.urls ?? body.text ?? '').split(/\s+/).filter((u) => /^https?:\/\//.test(u));
    for (const u of urls.slice(0, 3)) {
      const card = scoutCard('New video (sample)', /tiktok/.test(u) ? 'tiktok' : 'youtube', 'a creator', 30000, 90000, 'maybe',
        ['Preview: the full app watches the video, reads its captions and pulls out what matters.'], []);
      card.url = u;
      card.status = 'working';
      scoutVideos.unshift(card);
      window.setTimeout(() => { card.status = 'done'; }, 4000);
    }
    return say(urls.length ? `Watching ${urls.length} video${urls.length === 1 ? '' : 's'}.` : 'Paste a video link first.');
  }

  // ---- Self-changes and the ghost preview
  if (p === '/self/changes') return ok({ mode: 'layer', changes, probation: null, swaps });
  if ((m = p.match(/^\/self\/changes\/([\w-]+)\/screens\/(before|after)$/))) {
    const img = await fetch(m[2] === 'before' ? './black-hole-core.png' : './central-vortex.png').catch(() => null);
    return img && img.ok ? new Response(await img.blob(), { status: 200, headers: { 'Content-Type': 'image/png' } }) : ok({}, 404);
  }
  if ((m = p.match(/^\/self\/changes\/([\w-]+)\/(approve|reject|ask-signature|upstream|preview)$/))) {
    const c = changes.find((x) => x.id === m![1]);
    if (!c) return ok({ message: 'No such change.' }, 404);
    if (m[2] === 'preview') return ok({ url: null });
    if (m[2] === 'approve') { c.status = 'applied'; c.decision = 'approved'; swaps.unshift({ id: nid('s'), change_id: c.id, summary: c.summary, status: 'kept', at: now(), touches_ui: true, touches_daemon: false }); }
    if (m[2] === 'reject') { c.status = 'rejected'; c.decision = 'rejected'; }
    return ok({ ok: true, message: m[2] === 'ask-signature' ? 'Preview: your phone would ask for your fingerprint.' : m[2] === 'upstream' ? 'Preview: a branch would be made ready. Nothing is sent.' : 'Done.' });
  }
  if ((m = p.match(/^\/self\/changes\/([\w-]+)$/))) {
    const c = changes.find((x) => x.id === m![1]);
    return c ? ok({ change: c }) : ok({ message: 'No such change.' }, 404);
  }
  if (p === '/self/undo') {
    const s = swaps.find((x) => x.status === 'kept');
    if (s) s.status = 'rolled_back';
    return ok({ message: s ? `Put back: ${String(s.summary)}` : 'Nothing to undo.' });
  }
  if (p === '/self/probation/ui-ok') return ok({ ok: true });

  // ---- Camera presence (the camera is never opened in the preview: this only shows the light)
  if (p === '/presence') {
    if (POST) {
      const a = String(body.action ?? 'toggle');
      presenceOn = a === 'on' ? true : a === 'off' ? false : !presenceOn;
      sensorOn.camera = presenceOn;
    }
    return ok({ on: presenceOn, running: presenceOn, paused: '', error: '', missing: [], camera_switch: presenceOn ? 'on' : 'off', present: presenceOn ? true : null,
      looking_away: false, screen_time_today_min: 214, last_gesture: null, calibrating: false, config: { zone: [0.2, 0.2, 0.6, 0.6] } });
  }
  if (p === '/presence/calibration' || p === '/presence/calibrate') return ok({ on: presenceOn, zone: [0.2, 0.2, 0.6, 0.6], hand: null, armed: false, calibrating: false, samples: 0, message: 'Preview: the camera is not used here.' });
  if (p === '/presence/config' || p === '/presence/current-card') return ok({ ok: true });

  // ---- small status endpoints the desktop polls
  if (p === '/hotmic') {
    if (POST) sensorOn.hotmic = body.action === 'on';
    return ok({ listening: false, wake: { on: false, active: false }, last: null });
  }
  if (p === '/health/status') {
    return ok({ status: 'ok', words: 'Everything is working.', turbulence: 0, checks: [
      { id: 'brain', label: "Orion's brain", status: 'ok', reason: 'Answering.' },
      { id: 'disk', label: 'Disk space', status: 'ok', reason: '212.4 GB free on / (46%).' },
      { id: 'memory', label: 'Memory', status: 'ok', reason: '9.8 GB of 16.0 GB free (61%).' }, // [command]
      { id: 'phone', label: 'Phone link', status: phones.length ? 'ok' : 'warn', reason: phones.length ? 'Paired.' : 'No phone paired yet.', fix_words: phones.length ? undefined : 'Open Phone from the Orion button.' },
    ] });
  }
  if (p === '/freeze' && !POST) return ok({ freeze: { frozen: false } });
  if (p.startsWith('/freeze')) return ok({ ok: true, freeze: { frozen: false }, message: 'Preview: freezing stops every agent at once on your PC.' });
  if (p === '/access') return ok({ contrast: false, text_scale: 1, hud_zoom: 1 });
  if (p === '/linux/windows') return ok({ available: false, windows: [] });
  if (p === '/hardware/report') return ok({ available: false, try_mode: false });
  if (p === '/telegram/pair') return POST ? ok({ code: '482913', expires_in: 600 }) : ok({ paired: false, code: null });
  if (p === '/extension/pair/start') return ok({ code: '7316', expires_in: 120 });
  if (p === '/compute') {
    const models = [
      { model: 'claude-sonnet', provider: 'anthropic', local: false, calls: 41, tokens_in: 182000, tokens_out: 26000, cost: 0.94, price_in: 3, price_out: 15, price_known: true },
      { model: 'local small model', provider: 'ollama', local: true, calls: 212, tokens_in: 96000, tokens_out: 18000, cost: 0, price_in: 0, price_out: 0, price_known: true },
    ];
    const period = { cost: 0.94, tokens_in: 278000, tokens_out: 44000, calls: 253, models };
    const day = (n: number) => new Date(Date.now() - n * 86400000).toISOString().slice(0, 10);
    return ok({ today: { ...period, date: day(0) }, session: { ...period, since: T0 - 5400 }, history: [6, 5, 4, 3, 2, 1, 0].map((n, i) => ({ date: day(n), cost: [0.4, 1.2, 0.7, 0.3, 1.6, 0.8, 0.94][i] })),
      tips: ['Most calls ran on the local model, for free.'], note: 'Sample numbers.' });
  }
  return null;
}
