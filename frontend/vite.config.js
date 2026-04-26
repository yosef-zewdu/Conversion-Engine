import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
  ],
  server: {
    port: 5173,
    proxy: {
      '/health': 'http://localhost:8000',
      '/campaigns': 'http://localhost:8000',
      '/leads': 'http://localhost:8000',
      '/dev': 'http://localhost:8000',
      '/traces': 'http://localhost:8000',
      '/webhooks': 'http://localhost:8000',
    },
  },
})
