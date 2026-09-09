import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

export default defineConfig({
  plugins: [vue()],
  base: '/admin/',
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    port: 4173,
    proxy: {
      '/v1': { target: 'http://127.0.0.1:18080', changeOrigin: true },
      '/healthz': { target: 'http://127.0.0.1:18080', changeOrigin: true },
    },
  },
})
