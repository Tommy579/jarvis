import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
    setupFiles: ["./src/tests/setup.ts"],
    // Une origine reelle est necessaire pour que jsdom expose localStorage/sessionStorage
    // (sans URL, l'origine est opaque et le stockage vaut undefined).
    environmentOptions: {
      jsdom: {
        url: "http://127.0.0.1:8765/",
      },
    },
  },
});
