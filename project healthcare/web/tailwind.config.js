/** @type {import('tailwindcss').Config} */
export default {
  darkMode: "media",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        surface: "var(--surface-1)",
        plane: "var(--plane)",
        ink: {
          primary: "var(--text-primary)",
          secondary: "var(--text-secondary)",
          muted: "var(--text-muted)",
        },
        line: {
          grid: "var(--grid)",
          baseline: "var(--baseline)",
          border: "var(--border)",
        },
        status: {
          good: "var(--status-good)",
          warning: "var(--status-warning)",
          serious: "var(--status-serious)",
          critical: "var(--status-critical)",
        },
        series: {
          1: "var(--series-1)",
          2: "var(--series-2)",
          3: "var(--series-3)",
          4: "var(--series-4)",
          5: "var(--series-5)",
          6: "var(--series-6)",
        },
        role: {
          base: "var(--role-base)",
          accent: "var(--role-accent)",
        },
      },
      fontFamily: {
        sans: ["system-ui", "-apple-system", "Segoe UI", "sans-serif"],
      },
    },
  },
  plugins: [],
};
