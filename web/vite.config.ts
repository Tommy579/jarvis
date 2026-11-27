import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    // Pas de manualChunks pour three/@react-three : les forcer dans un chunk nomme
    // faisait importer ce chunk par l'entree, donc precharger ~850 Ko au demarrage.
    // Laisse a Rollup, il reste partage entre VoiceSphere et VisualArtifactWindow,
    // tous deux charges a la demande.
    rollupOptions: {},
  },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8765",
      "/ws": {
        target: "ws://127.0.0.1:8765",
        ws: true
      }
    }
  }
});