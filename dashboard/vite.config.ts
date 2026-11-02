import { cpSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig, type Plugin } from 'vite'

/** Holo Hands serves MediaPipe's wasm itself (offline, no CDN): copied from the pinned package into public/holo/wasm. */
const holoWasm: Plugin = {
  name: 'holo-wasm',
  buildStart: () =>
    cpSync(fileURLToPath(new URL('node_modules/@mediapipe/tasks-vision/wasm', import.meta.url)), fileURLToPath(new URL('public/holo/wasm', import.meta.url)), { recursive: true }),
}

// Dev server proxies the API so the session cookie stays same-origin.
const api = process.env.PEACE_API ?? 'http://127.0.0.1:8421'

export default defineConfig({
  plugins: [holoWasm, react(), tailwindcss()],
  server: { proxy: { '/api': api, '/auth': api } },
})
