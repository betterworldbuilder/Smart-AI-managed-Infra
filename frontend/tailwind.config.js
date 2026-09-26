/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  darkMode: ['class', '[data-theme="dark"]'],
  theme: {
    extend: {
      colors: {
        // Semantic roles only -- the actual values live as CSS custom
        // properties in index.css so light/dark swap in one place.
        surface: 'var(--surface-1)',
        plane: 'var(--plane)',
        raised: 'var(--surface-2)',
        ink: 'var(--text-primary)',
        'ink-secondary': 'var(--text-secondary)',
        muted: 'var(--text-muted)',
        hairline: 'var(--border)',
        grid: 'var(--gridline)',
        series1: 'var(--series-1)',
        series2: 'var(--series-2)',
        series3: 'var(--series-3)',
        good: 'var(--status-good)',
        warning: 'var(--status-warning)',
        serious: 'var(--status-serious)',
        critical: 'var(--status-critical)',
        accent: 'var(--accent)',
      },
      fontFamily: {
        sans: ['system-ui', '-apple-system', 'Segoe UI', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      borderRadius: { card: '10px' },
    },
  },
  plugins: [],
}
