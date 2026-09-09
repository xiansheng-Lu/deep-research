import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import AutoImport from 'unplugin-auto-import/vite'
import Components from 'unplugin-vue-components/vite'
import { fileURLToPath, URL } from 'node:url'

// 依据 [前端详细设计 §3.2] 装配 dev 代理与构建分包
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const apiBase = env.VITE_API_BASE ?? 'http://localhost:8000'
  const wsBase = apiBase.replace(/^http/, 'ws')
  const mockEnabled = env.VITE_MOCK === 'gateway'

  return {
    plugins: [
      vue(),
      AutoImport({
        imports: ['vue', 'vue-router', 'pinia'],
        dts: 'src/types/auto-imports.d.ts',
        eslintrc: { enabled: true }
      }),
      Components({
        dts: 'src/types/components.d.ts',
        dirs: ['src/components']
      })
    ],
    resolve: {
      alias: {
        '@': fileURLToPath(new URL('./src', import.meta.url))
      }
    },
    server: {
      host: '0.0.0.0',
      port: 5173,
      strictPort: false,
      proxy: mockEnabled
        ? {
            '/api': { target: apiBase, changeOrigin: true },
            '/ws': { target: wsBase, ws: true, changeOrigin: true },
            '/sse': { target: apiBase, changeOrigin: true }
          }
        : undefined
    },
    build: {
      target: 'es2020',
      sourcemap: mode !== 'production',
      cssCodeSplit: true,
      rollupOptions: {
        output: {
          manualChunks: {
            vue: ['vue', 'vue-router', 'pinia']
          }
        }
      },
      chunkSizeWarningLimit: 600
    },
    optimizeDeps: {
      include: ['vue', 'vue-router', 'pinia', '@floating-ui/dom']
    }
  }
})
