import react from '@vitejs/plugin-react'
import path from 'node:path'
import { defineConfig } from 'vite'

// DSH 前端基座位于仓库根 dsh-frontend/（详见其 README-使用说明.md，路径 A：直接消费已编译产物）
const DSH = path.resolve(__dirname, '../dsh-frontend')

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@dsh/primitives': `${DSH}/packages/client/ui-primitives/lib/index.js`,
      '@dsh/ui-theme': `${DSH}/packages/client/ui-theme`,
      // ui-primitives 产物的 workspace 依赖：symlink 在跨目录解析下不稳定，显式别名兜底
      '@deepseek-ai/dsh-client-ui-primitives': `${DSH}/packages/client/ui-primitives/lib/index.js`,
      '@deepseek-ai/dsh-client-store': `${DSH}/packages/client/store/lib/index.js`,
      '@deepseek-ai/dsh-util-code-language': `${DSH}/packages/util/code-language/lib/index.js`,
      '@deepseek-ai/dsh-util-workspace-path': `${DSH}/packages/util/workspace-path/lib/index.js`,
    },
  },
  server: {
    port: 5180,
    proxy: { '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true } },
    fs: { allow: [path.resolve(__dirname), DSH] },
  },
  build: { outDir: 'dist', chunkSizeWarningLimit: 4096 },
})
