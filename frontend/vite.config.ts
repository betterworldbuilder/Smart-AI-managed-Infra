import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// In development the UI talks to the backend through this proxy, so the browser
// only ever needs one origin. In the container build, nginx does the same job.
const backend = process.env.VITE_BACKEND_URL ?? 'http://localhost:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 3000,
    proxy: {
      '/api': { target: backend, changeOrigin: true, ws: true },
    },
  },
  preview: { host: '0.0.0.0', port: 3000 },
  build: { outDir: 'dist', sourcemap: false },
})
