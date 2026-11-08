/**
 * Matte emoji for cards: Fluent Emoji, Flat style (Microsoft, MIT licence; files in public/emoji with the licence).
 * Picked from the card's own words first (a game task gets a game controller), then from the kind of card.
 */
export const EMOJI = [
  'alarm-clock', 'artist-palette', 'bar-chart', 'bell', 'books', 'brain', 'briefcase', 'bug', 'calendar',
  'card-file-box', 'check-mark-button', 'crescent-moon', 'envelope', 'file-folder', 'fire-extinguisher', 'gear',
  'globe-with-meridians', 'hammer-and-wrench', 'hot-beverage', 'hourglass-not-done', 'house', 'joystick', 'laptop',
  'light-bulb', 'link', 'locked', 'magnifying-glass-tilted-left', 'memo', 'microphone', 'mobile-phone', 'movie-camera',
  'musical-note', 'package', 'party-popper', 'pushpin', 'puzzle-piece', 'robot', 'rocket', 'shield', 'sparkles',
  'speech-balloon', 'spiral-calendar', 'star', 'sun', 'test-tube', 'trophy', 'video-game', 'warning',
] as const;
export type EmojiName = (typeof EMOJI)[number];

/** Word patterns, checked in order: the first that matches the card's text wins. */
const WORDS: [RegExp, EmojiName][] = [
  [/\b(bug|crash|error|broken|fail(ing|ed)?)\b/i, 'bug'],
  [/\b(test|tests|check|checks)\b/i, 'test-tube'],
  [/\b(star|high[- ]?score|score|leaderboard)\b/i, 'star'],
  [/\b(game|games|raid|wow|warcraft|level|loot|player|quest)\b/i, 'video-game'],
  [/\b(website|site|web|page|landing|domain)\b/i, 'globe-with-meridians'],
  [/\b(music|song|track|beat|synth|audio|patch)\b/i, 'musical-note'],
  [/\b(video|clip|reel|shorts?|film)\b/i, 'movie-camera'],
  [/\b(design|logo|art|colour|color|style)\b/i, 'artist-palette'],
  [/\b(note|notes|doc|docs|write|draft|devlog|blog)\b/i, 'memo'],
  [/\b(email|mail|inbox)\b/i, 'envelope'],
  [/\b(phone|mobile|app)\b/i, 'mobile-phone'],
  [/\b(voice|speak|mic)\b/i, 'microphone'],
  [/\b(file|files|folder|tidy|clean)\b/i, 'file-folder'],
  [/\b(stats|report|chart|numbers|analytics)\b/i, 'bar-chart'],
  [/\b(idea|ideas|plan|brainstorm)\b/i, 'light-bulb'],
  [/\b(release|launch|ship|deploy)\b/i, 'rocket'],
  [/\b(security|secret|password|privacy|safe)\b/i, 'locked'],
  [/\b(meeting|call|calendar|tomorrow|today)\b/i, 'spiral-calendar'],
  [/\b(learn|read|study|book)\b/i, 'books'],
  [/\b(fix|repair|update|build|refactor)\b/i, 'hammer-and-wrench'],
];

const BY_KIND: Record<string, EmojiName> = {
  now: 'hammer-and-wrench',
  message: 'speech-balloon',
  approval: 'shield',
  needs_you: 'warning',
  next_up: 'alarm-clock',
};

export function pickEmoji(kind: string, title: string, detail = ''): EmojiName {
  // Messages and approvals keep their kind's picture: the sender's words shouldn't turn a chat into a game card.
  if (kind === 'message' || kind === 'approval') return BY_KIND[kind];
  // The title says what the task is; the detail only helps when the title gives nothing away.
  for (const text of [title, detail]) for (const [re, name] of WORDS) if (re.test(text)) return name;
  return BY_KIND[kind] ?? 'sparkles';
}

export function emojiUrl(name: EmojiName): string {
  const base = (import.meta as { env?: { BASE_URL?: string } }).env?.BASE_URL ?? '/';
  return `${base}emoji/${name}.svg`;
}
