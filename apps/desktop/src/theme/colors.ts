import type { MemorySource } from '@orion/shared-types';

/**
 * Brand palette. Look pass (owner feedback): deep cobalt blue is the accent, surfaces are dark gunmetal
 * (blue-grey steel) with a faint brushed grain; purple only where it means something (a brand, WoW XP).
 * Ember is the only warm colour: for things that need you (stuck, urgent).
 * Mirrored as CSS custom properties (--orion-void ... --orion-ember, --orion-cobalt, --orion-gunmetal) in
 * index.css and as Tailwind colours. The old name `accretion` now holds cobalt so existing code follows.
 */
export const brand = {
  void: '#06080C',
  event: '#151A21',
  edge: '#2F3744',
  accretion: '#2E6BFF',
  photon: '#00E5FF',
  starlight: '#E6ECF5',
  ember: '#FFC66B',
  /** Accent: buttons, focus rings, selected things. */
  cobalt: '#2E6BFF',
  cobaltDeep: '#1F4FD8',
  /** Accent text and icons on dark surfaces (6.5:1 on gunmetal). */
  cobaltLight: '#8DB0FF',
  /** Surfaces, darkest to lightest. */
  gunmetal: '#1A1F27',
  gunmetalRaised: '#222833',
  steel: '#2A313B',
} as const;

/**
 * Text on gunmetal, three tiers that really differ (hierarchy pass, docs/design/CONSOLE_DESIGN.md). WCAG AA or
 * better on #1A1F27: body 16.5:1, secondary 10.1:1, muted 6.7:1 (5.3:1 on #2A313B).
 */
export const text = {
  body: '#FFFFFF',
  secondary: '#C3CBD7',
  muted: '#9AA6B8',
} as const;

/** Brand type. Unbounded: wordmark, clock, headings. Figtree: reading text. Martian Mono: terminal, code, numbers. */
export const fonts = {
  display: "'Unbounded Variable', 'Unbounded', 'Nunito', system-ui, sans-serif",
  ui: "'Figtree Variable', 'Figtree', 'Nunito', 'Inter', system-ui, sans-serif",
  mono: "'Martian Mono Variable', 'Martian Mono', 'JetBrains Mono', Consolas, ui-monospace, monospace",
} as const;

/** Type roles and scale (px) used by the Art Update surfaces. */
export const type = {
  wordmark: { family: fonts.display, weight: 600, tracking: '0.04em' },
  clock: { family: fonts.display, weight: 500, tracking: '0.02em' },
  heading: { family: fonts.display, weight: 600, tracking: '0.01em' },
  body: { family: fonts.ui, weight: 500, tracking: '0.005em' },
  mono: { family: fonts.mono, weight: 400, tracking: '0' },
  size: { xs: 11, sm: 13, base: 15, lg: 18, xl: 24, display: 40 },
} as const;

/** Orion cosmic glassmorphism palette. */
export const colors = {
  void: '#06080C',
  cosmos: '#11151B',
  glass: 'rgba(26, 31, 39, 0.72)',
  glassBorder: 'rgba(255, 255, 255, 0.08)',
  cyan: '#00E5FF',
  purple: '#8A9AB5',
  violet: brand.accretion,
  starlight: '#FFFFFF',
  dust: '#A3AEC2',
  success: '#3DFFA8',
  warn: '#FFB86B',
  danger: '#FF5C8A',
  magenta: '#FF2A85',
  amber: '#FFB300',
  // Art Update additions (existing names above keep working)
  event: brand.event,
  edge: brand.edge,
  accretion: brand.accretion,
  photon: brand.photon,
  ember: brand.ember,
} as const;

/** Canonical design-bible token names. */
export const palette = {
  voidBackground: colors.void,
  glassOverlay: colors.glass,
  cyanGlow: colors.cyan,
  purpleGlow: colors.purple,
} as const;

export const glow = {
  cyan: '0 0 24px rgba(0, 229, 255, 0.45)',
  purple: '0 0 28px rgba(138, 154, 181, 0.55)',
} as const;

/** V2 Tactile Cosmic Emboss tokens (mirrored as CSS classes in index.css). */
export const emboss = {
  highlight: 'inset 0 1px 1px rgba(255, 255, 255, 0.18)',
  innerShadow: 'inset 0 -2px 3px rgba(0, 0, 0, 0.65)',
  depth: '0 6px 16px rgba(0, 0, 0, 0.75), 0 2px 4px rgba(0, 0, 0, 0.5)',
  container:
    'inset 0 1px 1px rgba(255, 255, 255, 0.18), inset 0 -2px 3px rgba(0, 0, 0, 0.65), 0 6px 16px rgba(0, 0, 0, 0.75), 0 2px 4px rgba(0, 0, 0, 0.5)',
  pressed: 'inset 0 2px 4px rgba(0, 0, 0, 0.7), inset 0 -1px 1px rgba(255, 255, 255, 0.1), 0 1px 2px rgba(0, 0, 0, 0.5)',
  headerText: '0 2px 4px rgba(0, 0, 0, 0.85), 0 1px 0 rgba(255, 255, 255, 0.12)',
  bodyText: '0 1px 3px rgba(0, 0, 0, 0.9)',
} as const;

/** Pacing / wellness arc colours (focus -> break due). */
export const pacingColors = {
  idle: '#2E6BFF',
  focus: '#00E5FF',
  warming: '#5B8CFF',
  break_due: '#FFB300',
  resting: '#3DFFA8',
} as const;

export const sourceColors: Record<MemorySource, string> = {
  ocr: colors.cyan,
  browser: colors.purple,
  voice: colors.warn,
  discord: '#7289DA',
  telegram: '#229ED9',
  messenger: '#A855F7',
  user: colors.starlight,
  consolidated: colors.success,
};
