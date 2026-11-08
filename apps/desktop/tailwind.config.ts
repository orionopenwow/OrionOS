import type { Config } from 'tailwindcss';
import { brand, colors, emboss, glow, text } from './src/theme/colors';

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
        // look pass: cobalt accent, gunmetal surfaces, readable text tiers
        cobalt: { DEFAULT: brand.cobalt, deep: brand.cobaltDeep, light: brand.cobaltLight },
        gunmetal: { DEFAULT: brand.gunmetal, raised: brand.gunmetalRaised },
        steel: brand.steel,
        ink: { DEFAULT: text.body, 2: text.secondary, 3: text.muted },
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
      borderRadius: { sm: '2px', DEFAULT: '3px', md: '4px', lg: '4px', xl: '5px', '2xl': '6px', '3xl': '8px' },
      fontFamily: {
        display: ['Arial', 'Arimo', '"Liberation Sans"', 'Helvetica', 'sans-serif'],
        sans: ['Arial', 'Arimo', '"Liberation Sans"', 'Helvetica', 'sans-serif'],
        mono: ['"Martian Mono Variable"', '"Martian Mono"', '"JetBrains Mono"', 'Consolas', 'ui-monospace', 'monospace'],
        rounded: ['Arial', 'Arimo', '"Liberation Sans"', 'Helvetica', 'sans-serif'],
      },
    },
  },
  plugins: [],
} satisfies Config;
