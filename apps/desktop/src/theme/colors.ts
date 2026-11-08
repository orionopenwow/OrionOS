import type { MemorySource } from '@orion/shared-types';

/**
 * Art Update brand palette (plan §6). Ember is the only warm colour: for things that need you (stuck, urgent).
 * Mirrored as CSS custom properties (--orion-void ... --orion-ember) in index.css and as Tailwind colours.
 */
export const brand = {
  void: '#05070E',
  event: '#0B0F1E',
  edge: '#262D50',
  accretion: '#7C5CFF',
  photon: '#00E5FF',
  starlight: '#D8DEFF',
  ember: '#FFC66B',
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
  void: '#05070E',
  cosmos: '#0B0D17',
  glass: 'rgba(19, 23, 40, 0.65)',
  glassBorder: 'rgba(255, 255, 255, 0.08)',
  cyan: '#00E5FF',
  purple: '#9D8EC7',
  violet: brand.accretion,
  starlight: '#FFFFFF',
  dust: '#8F90A6',
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
  purple: '0 0 28px rgba(157, 142, 199, 0.55)',
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
  idle: '#7B5CFF',
  focus: '#00E5FF',
  warming: '#B05CFF',
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
