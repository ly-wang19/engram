import type { Config } from 'tailwindcss'

// Design tokens mirror the Engram brand (the landing page): deep-navy canvas,
// cyan→violet gradient accent. Kept in one place so the whole console is on-brand.
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        ink: {
          DEFAULT: '#070b14',
          900: '#070b14',
          800: '#0b1220',
          700: '#0f1830',
          600: '#16213e',
        },
        line: 'rgba(255,255,255,0.10)',
        brand: {
          cyan: '#22d3ee',
          violet: '#a78bfa',
          mint: '#34d399',
          rose: '#fb7185',
          amber: '#fbbf24',
        },
        ghost: '#8a97b8',
      },
      fontFamily: {
        sans: ['Inter', 'PingFang SC', 'system-ui', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      boxShadow: {
        glow: '0 0 0 1px rgba(34,211,238,0.25), 0 8px 40px -12px rgba(34,211,238,0.35)',
        card: '0 1px 0 rgba(255,255,255,0.04) inset, 0 12px 40px -24px rgba(0,0,0,0.8)',
      },
      backgroundImage: {
        'brand-gradient': 'linear-gradient(90deg, #22d3ee, #a78bfa)',
        'brand-radial': 'radial-gradient(1200px 600px at 50% -10%, rgba(34,211,238,0.12), transparent 60%)',
      },
      keyframes: {
        'fade-up': {
          '0%': { opacity: '0', transform: 'translateY(8px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
      },
      animation: {
        'fade-up': 'fade-up 0.35s ease both',
      },
    },
  },
  plugins: [],
} satisfies Config
