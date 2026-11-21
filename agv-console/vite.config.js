import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Dev mode: `npm run dev` proxies API + camera to the Django server on :8000.
// Production build output (dist/) is served directly by Django via WhiteNoise.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8000',
      '/cam': 'http://127.0.0.1:8000',
    },
  },
  build: {
    outDir: 'dist',
  },
})
