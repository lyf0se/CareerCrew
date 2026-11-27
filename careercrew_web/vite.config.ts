import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(import.meta.dirname, './src'),
    },
  },
  server: {
    port: 5276,
    strictPort: true, // 端口被占用时报错，不静默顺延到其他端口
    proxy: {
      '/api': {
        target: 'http://localhost:8100',
        changeOrigin: true,
      },
    },
  },
})

