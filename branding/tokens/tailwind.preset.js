/** AgentShip Tailwind preset. Usage: presets: [require('./tokens/tailwind.preset.js')] */
module.exports = {
  theme: {
    extend: {
      colors: {
        ink: '#0D1317',
        panel: '#141C22',
        harbor: '#1B262D',
        line: { DEFAULT: '#243039', strong: '#2A3944' },
        outline: '#34454F',
        fog: '#E8EEF0',
        body: '#C9D5DB',
        muted: '#9FB0B9',
        paper: '#F3F5F4',
        accent: { DEFAULT: '#A685EE', hover: '#C0AAF5', onlight: '#6A47C2' },
        codekey: '#5CCFC0',
      },
      fontFamily: {
        display: ["'Bricolage Grotesque'", 'system-ui', 'sans-serif'],
        sans: ["'Instrument Sans'", 'system-ui', 'sans-serif'],
        mono: ["'JetBrains Mono'", 'ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      borderRadius: { chip: '6px', card: '12px', panel: '14px' },
    },
  },
};
