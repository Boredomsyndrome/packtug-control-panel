import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Fused site (2026-11-21): build lands in repo-root dist/agv/ and the hub
// serves it at '/agv/' (panel SPA owns '/'). Assets are emitted under
// /agv/ so WhiteNoise finds them. Dev mode proxies to the fused hub :8090.
export default defineConfig({
  plugins: [react()],
  base: '/agv/',
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8090',
      '/cam': 'http://127.0.0.1:8090',
    },
  },
  build: {
    outDir: '../dist/agv',
    emptyOutDir: true,
  },
})
