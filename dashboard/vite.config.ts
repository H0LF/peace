import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Dev server proxies the API so the session cookie stays same-origin.
const api = process.env.PEACE_API ?? 'http://127.0.0.1:8421'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { proxy: { '/api': api, '/auth': api } },
})
