import { readFileSync } from 'node:fs'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const appVersion = readFileSync(new URL('../VERSION', import.meta.url), 'utf-8').trim()

export default defineConfig({
  plugins: [react()],
  define: {
    'import.meta.env.VITE_APP_VERSION': JSON.stringify(appVersion),
  },
  build: {
    rolldownOptions: {
      output: {
        codeSplitting: true,
      },
    },
  },
})
