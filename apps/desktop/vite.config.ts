import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { daemonTokenForDevPage } from './vite-daemon-token.mjs';

// Tauri expects a fixed port; fail if unavailable.
export default defineConfig({
  // daemonTokenForDevPage: `npm run dev:web` hands the daemon's key to its own page only (review B1).
  plugins: [react(), daemonTokenForDevPage(1420)],
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    watch: { ignored: ['**/src-tauri/**'] },
  },
  envPrefix: ['VITE_', 'TAURI_ENV_'],
  build: {
    target: 'es2021',
    sourcemap: false,
  },
});
