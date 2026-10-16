import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    host: '127.0.0.1',
    proxy: {
      // 开发时把 /api 转发到后端，避免跨域配置烦恼
      '/api': {
        target: 'http://127.0.0.1:8200',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    // ⚠️ 必须关闭「清空输出目录」：本机沙箱的 safe-delete 会拦截 vite 在
    //    emptyDir 阶段对 dist/assets 的 rmSync（回收站不可用 → 操作被中止），
    //    导致整个 build 失败 —— 而 tsc 类型检查其实已经通过。
    //    产物文件名带内容 hash，旧文件残留不影响运行，可定期手工清理。
    emptyOutDir: false,
  },
})
