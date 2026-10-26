import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

function vendorChunk(id: string): string | undefined {
  if (id.includes('commonjsHelpers')) return 'vendor-react'
  if (!id.includes('node_modules')) return undefined

  const normalized = id.split('\\').join('/')

  if (
    normalized.includes('/node_modules/react/') ||
    normalized.includes('/node_modules/react-dom/') ||
    normalized.includes('/node_modules/react-router') ||
    normalized.includes('/node_modules/scheduler/') ||
    normalized.includes('/node_modules/jwt-decode/')
  ) {
    return 'vendor-react'
  }

  if (
    normalized.includes('/node_modules/@mui/icons-material/')
  ) {
    return 'vendor-mui-icons'
  }

  if (
    normalized.includes('/node_modules/@mui/') ||
    normalized.includes('/node_modules/@emotion/')
  ) {
    return 'vendor-mui'
  }

  if (
    normalized.includes('/node_modules/i18next') ||
    normalized.includes('/node_modules/react-i18next/')
  ) {
    return 'vendor-i18n'
  }

  if (
    normalized.includes('/node_modules/react-hook-form/') ||
    normalized.includes('/node_modules/@hookform/') ||
    normalized.includes('/node_modules/zod/')
  ) {
    return 'vendor-forms'
  }

  if (
    normalized.includes('/node_modules/axios/') ||
    normalized.includes('/node_modules/notistack/') ||
    normalized.includes('/node_modules/zustand/')
  ) {
    return 'vendor-core'
  }

  return 'vendor'
}

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        manualChunks: vendorChunk,
      },
    },
  },
})
