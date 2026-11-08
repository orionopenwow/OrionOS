/**
 * The HUD's one menu (the "simple menu" pass): six main items and a "More" drawer for everything else.
 *
 * Every item opens by id with one window event, so any window can listen without App.tsx knowing about it:
 *   window.dispatchEvent(new CustomEvent('orion:open', { detail: 'projects' }))
 * Listen with `onOrionOpen('projects', () => setOpen(true))` (returns the unsubscribe function).
 *
 * Reserved ids: "tests" (Test Center), "phone" (phone pairing and status), "projects" (Start a project with
 * Claude, the front door to Mission Control), "privacy" (privacy dashboard), "missions" (Mission Control).
 * Icons are lucide icon names; MainMenu maps them to components. This file has no imports so tests can run it
 * directly with `node --test`.
 */
export const ORION_OPEN = 'orion:open';

export type MenuGroup = 'main' | 'more';

export interface MenuItem {
  id: string;
  label: string;
  /** lucide-react icon name (MainMenu keeps the map). */
  icon: string;
  /** One short line in plain words. */
  description: string;
  group: MenuGroup;
  /** Extra words people might type to find it. */
  keywords?: string;
}

export const MENU: MenuItem[] = [
  // ---- main: at most six ----
  { id: 'chat', label: 'Chat', icon: 'MessageCircle', group: 'main', description: 'Talk to Orion.' },
  { id: 'projects', label: 'Projects', icon: 'FolderGit2', group: 'main', description: 'Start a project with Claude, or see the ones running.', keywords: 'game website mission code build' },
  { id: 'phone', label: 'Phone', icon: 'Smartphone', group: 'main', description: 'Pair your phone and see how it is doing.', keywords: 'mobile pair' },
  { id: 'skills', label: 'Skills', icon: 'Wand2', group: 'main', description: 'Things Orion learned by watching you, ready to run again.', keywords: 'record macro automate' },
  { id: 'tests', label: 'Test', icon: 'FlaskConical', group: 'main', description: 'Check that every part of Orion works.', keywords: 'test center health check' },
  { id: 'settings', label: 'Settings', icon: 'Settings2', group: 'main', description: 'Look, sound, voice, accessibility and more.' },
  // ---- more: everything else, by name ----
  { id: 'messages', label: 'Messages', icon: 'Inbox', group: 'more', description: 'Every chat from your phone and socials in one place.', keywords: 'comms inbox discord telegram' },
  { id: 'queue', label: 'Waiting for your OK', icon: 'ShieldCheck', group: 'more', description: 'Things Orion will only do after you say yes.', keywords: 'approvals queue' },
  { id: 'missions', label: 'Mission Control', icon: 'Rocket', group: 'more', description: 'Every agent job: its live log, changes and cost.', keywords: 'agents claude codex' },
  { id: 'agents', label: 'Agents', icon: 'Bot', group: 'more', description: 'Turn helper agents on or off. Coding agents also orbit the black hole.', keywords: 'swarm crew robots' },
  { id: 'scout', label: 'Video Scout', icon: 'Clapperboard', group: 'more', description: 'Paste video links; Orion watches them and pulls out what matters.', keywords: 'tiktok youtube reels' },
  { id: 'tools', label: 'Toolkit tools', icon: 'Wrench', group: 'more', description: 'Collision Guard, Proof Gate, package checks and the other tools.', keywords: 'toolkit' },
  { id: 'life', label: 'Life', icon: 'HeartPulse', group: 'more', description: 'Routines, breaks, sleep and the day ahead.', keywords: 'wellness routines' },
  { id: 'guide', label: 'Guide Mode', icon: 'Compass', group: 'more', description: 'Ask "show me how to..." and Orion points at each step.', keywords: 'help how' },
  { id: 'privacy', label: 'Privacy dashboard', icon: 'Eye', group: 'more', description: 'Everything Orion can see or hear, with one tap to turn it off.', keywords: 'camera mic' },
  { id: 'camera', label: 'Camera presence', icon: 'Camera', group: 'more', description: 'Turn the camera for presence and hand gestures on or off.', keywords: 'webcam gestures' },
  { id: 'selfchanges', label: 'Self-changes history', icon: 'History', group: 'more', description: 'Changes Orion made to itself, with Undo.', keywords: 'self program undo living' },
  { id: 'screen_tasks', label: 'Screen tasks', icon: 'MousePointer', group: 'more', description: 'Let Orion click and type through a task on screen, step by step.', keywords: 'automation' },
  { id: 'analytics', label: 'Computer health', icon: 'Activity', group: 'more', description: 'CPU, memory and what is running.', keywords: 'telemetry stats' },
  { id: 'chrome', label: 'Chrome reader', icon: 'Chrome', group: 'more', description: 'Read and pull text from the page open in Chrome.', keywords: 'browser scrape' },
  { id: 'discord', label: 'Discord community', icon: 'Layers', group: 'more', description: 'Your Discord server at a glance.' },
  { id: 'games', label: 'Games', icon: 'Gamepad2', group: 'more', description: 'Play the games you already have: Steam, Epic, Battle.net and your WoW 1.12 client.', keywords: 'play steam epic heroic lutris wow warcraft rocket league proton' }, // [games]
  { id: 'game_bugs', label: 'Game bug hub', icon: 'Gamepad2', group: 'more', description: 'Bug reports from players, sorted and ready to answer.', keywords: 'game bugs' },
  { id: 'personality', label: 'Personality and voice', icon: 'Mic', group: 'more', description: 'How Orion talks and sounds.', keywords: 'tts voice' },
  { id: 'widgets', label: 'Side widgets', icon: 'LayoutGrid', group: 'more', description: 'Show or hide music, messages, alerts and the agent pet on the side.', keywords: 'dock pet music' },
];

export const MAX_MAIN = 6;

export const mainItems = (items: MenuItem[] = MENU) => items.filter((i) => i.group === 'main');
export const moreItems = (items: MenuItem[] = MENU) => items.filter((i) => i.group === 'more');

/** Items matching what was typed (label, description, keywords), main items first. Empty query: everything. */
export function filterMenu(query: string, items: MenuItem[] = MENU): MenuItem[] {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return items;
  return items.filter((i) => {
    const hay = `${i.label} ${i.description} ${i.keywords ?? ''} ${i.id}`.toLowerCase();
    return words.every((w) => hay.includes(w));
  });
}

/** Open a menu item (or any window that listens for its id). */
export function openOrion(id: string): void {
  window.dispatchEvent(new CustomEvent(ORION_OPEN, { detail: id }));
}

/** Call `fn` when one of `ids` is opened. Returns the unsubscribe function (use it as a useEffect cleanup). */
export function onOrionOpen(ids: string | string[], fn: (id: string) => void): () => void {
  const want = Array.isArray(ids) ? ids : [ids];
  const listener = (e: Event) => {
    const id = (e as CustomEvent<unknown>).detail;
    if (typeof id === 'string' && want.includes(id)) fn(id);
  };
  window.addEventListener(ORION_OPEN, listener);
  return () => window.removeEventListener(ORION_OPEN, listener);
}

/** Super+Space or Alt+Space opens the menu (Ctrl+Space stays "show or hide the chat"). */
export function isMenuShortcut(e: { code: string; key?: string; altKey: boolean; metaKey: boolean; ctrlKey: boolean; shiftKey?: boolean }): boolean {
  return (e.code === 'Space' || e.key === ' ') && (e.altKey || e.metaKey) && !e.ctrlKey && !e.shiftKey;
}

/** Windows can put a small count next to their menu item (for example skills that need a look). */
export const ORION_BADGE = 'orion:badge';

export function setMenuBadge(id: string, count: number): void {
  window.dispatchEvent(new CustomEvent(ORION_BADGE, { detail: { id, count } }));
}
