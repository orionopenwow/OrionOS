import type { Config } from 'tailwindcss';
import { brand, colors, emboss, glow } from './src/theme/colors';

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        void: colors.void,
        cosmos: colors.cosmos,
        glass: colors.glass,
        pulse: colors.cyan,
        nebula: colors.purple,
        violet: colors.violet,
        starlight: colors.starlight,
        dust: colors.dust,
        success: colors.success,
        warn: colors.warn,
        danger: colors.danger,
        warmamber: colors.amber,
        event: brand.event,
        edge: brand.edge,
        accretion: brand.accretion,
        photon: brand.photon,
        ember: brand.ember,
        'starlight-soft': brand.starlight,
      },
      borderColor: { glass: colors.glassBorder },
      boxShadow: {
        'glow-cyan': glow.cyan,
        'glow-purple': glow.purple,
        emboss: emboss.container,
        'emboss-pressed': emboss.pressed,
      },
      letterSpacing: { hud: '0.02em' },
      backdropBlur: { glass: '16px' },
      fontFamily: {
        display: ['"Unbounded Variable"', 'Unbounded', 'Nunito', '"Arial Rounded MT Bold"', 'system-ui', 'sans-serif'],
        sans: ['"Figtree Variable"', 'Figtree', 'Nunito', '"Arial Rounded MT Bold"', 'Inter', 'system-ui', 'sans-serif'],
        mono: ['"Martian Mono Variable"', '"Martian Mono"', '"JetBrains Mono"', 'Consolas', 'ui-monospace', 'monospace'],
        rounded: ['Nunito', '"Arial Rounded MT Bold"', 'Inter', 'system-ui', 'sans-serif'],
      },
    },
  },
  plugins: [],
} satisfies Config;
