import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'node:path'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, __dirname, '')
  const target = env.VITE_API_TARGET || 'http://127.0.0.1:8766'
  const token = env.VITE_INGEST_TOKEN || ''
  const appNodeModules = path.resolve(__dirname, './node_modules')
  return {
    envDir: __dirname,
    plugins: [react(), tailwindcss()],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, './src'),
        '@stroll-map': path.resolve(__dirname, '../../../packages/stroll-map/src'),
        'mapbox-gl': path.resolve(appNodeModules, 'mapbox-gl'),
        gcoord: path.resolve(appNodeModules, 'gcoord'),
      },
      dedupe: ['react', 'react-dom'],
    },
    optimizeDeps: {
      include: ['react', 'react-dom', 'react-dom/client', 'react/jsx-runtime'],
    },
    build: {
      rollupOptions: {
        output: {
          manualChunks: () => 'index',
        },
      },
    },
    server: {
      host: '0.0.0.0',
      port: 5173,
      strictPort: true,
      hmr: {
        host: env.VITE_DEV_HOST || undefined,
        port: 5173,
      },
      proxy: {
        '/api': {
          target,
          changeOrigin: true,
          configure: (proxy) => {
            proxy.on('proxyReq', (proxyReq) => {
              if (token) proxyReq.setHeader('X-Fiam-Token', token)
            })
          },
        },
        '/favilla': {
          target,
          changeOrigin: true,
          configure: (proxy) => {
            proxy.on('proxyReq', (proxyReq) => {
              if (token) proxyReq.setHeader('X-Fiam-Token', token)
            })
          },
        },
      },
    },
  }
})
