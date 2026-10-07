/** Design tokens from the InfoPoint design spec: calm, light, one accent colour. */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        page: "#FAFAF7",
        surface: "#FFFFFF",
        line: "#E7E5E0",
        ink: "#1F2328",
        muted: "#6B6F76",
        accent: { DEFAULT: "#0F6E6E", hover: "#0B5757", soft: "#E8F1F0" },
        danger: "#9B3B2F",
      },
      fontFamily: {
        sans: ['"IBM Plex Sans"', "system-ui", "sans-serif"],
      },
      fontSize: { base: ["15.5px", "1.65"] },
      boxShadow: { card: "0 1px 2px rgba(31,35,40,0.04)" },
    },
  },
  plugins: [],
};
