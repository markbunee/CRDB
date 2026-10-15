import { fileURLToPath, URL } from 'node:url'

import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import vueDevTools from 'vite-plugin-vue-devtools'

// 后端地址：默认 18000（本机 8000 被 ticketsys-api 容器占用）。
// start.sh 会 export VITE_BACKEND_URL，两处永远指向同一个端口，避免各写一个数字对不上。
const BACKEND_URL = process.env.VITE_BACKEND_URL || 'http://127.0.0.1:18000'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    vue(),
    vueDevTools(),
  ],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: BACKEND_URL,
        changeOrigin: true,
      },
    },
  },
})
