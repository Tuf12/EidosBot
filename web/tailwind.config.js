/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        dracula: {
          bg: "#1e1f29",
          card: "#282a36",
          border: "#44475a",
          text: "#f8f8f2",
          dim: "#6272a4",
          green: "#50fa7b",
          yellow: "#f1fa8c",
          orange: "#ffb86c",
          purple: "#bd93f9",
          red: "#ff5555",
          cyan: "#8be9fd",
          pink: "#ff79c6",
        },
      },
    },
  },
  plugins: [],
}
