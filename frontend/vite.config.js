import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 开发期代理：前端 5173 的 /api 转发到后端 8100，避免跨域
// 可用 VITE_BACKEND_TARGET 覆盖（例如后端换端口时无需改代码）
const backendTarget = process.env.VITE_BACKEND_TARGET || 'http://127.0.0.1:8100'

export default defineConfig({
  plugins: [vue()],
  server: {
    host: '127.0.0.1', // Windows 下 vite 默认绑 IPv6 localhost，显式绑 IPv4 便于代理与本地验收
    port: 5173,
    proxy: {
      '/api': {
        target: backendTarget,
        changeOrigin: true,
      },
    },
  },
})
