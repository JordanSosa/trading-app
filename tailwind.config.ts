import type { Config } from 'tailwindcss';

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        ember: {
          50: '#fff4eb',
          100: '#ffe4cc',
          200: '#ffca99',
          300: '#ffad66',
          400: '#ff8f33',
          500: '#ff7100',
          600: '#cc5a00',
          700: '#994300',
          800: '#662c00',
          900: '#331600'
        }
      }
    }
  },
  plugins: []
} satisfies Config;
