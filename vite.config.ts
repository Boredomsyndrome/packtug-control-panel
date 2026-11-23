import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    host: true,
    proxy: {
      // v3.7 fused hub: ONE origin serves panel + AGV bridge + robot API.
      // (Standalone dev against the old two-process layout: point at :8020.)
      '/api': 'http://127.0.0.1:8090',
    },
  },
})
