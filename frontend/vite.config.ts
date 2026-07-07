import { defineConfig, type Plugin } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'
import Components from 'unplugin-vue-components/vite'
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers'

const frontendRoot = fileURLToPath(new URL('.', import.meta.url))

function excludePublicDev(): Plugin {
  return {
    name: 'exclude-public-dev',
    generateBundle(_options, bundle) {
      for (const key of Object.keys(bundle)) {
        if (key.startsWith('_dev/')) {
          delete bundle[key]
        }
      }
    },
  }
}

export default defineConfig({
  root: frontendRoot,
  plugins: [
    vue(),
    Components({
      resolvers: [ElementPlusResolver({ importStyle: false })]
    }),
    excludePublicDev(),
  ],

  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8888',
        changeOrigin: true,
        ws: true
      }
    }
  },

  build: {
    outDir: 'dist',
    emptyOutDir: true,
    target: 'es2020',

    rollupOptions: {
      output: {
        manualChunks: {
          'vue-vendor': ['vue'],
          'three': ['three'],
          'xterm': ['@xterm/xterm', '@xterm/addon-fit']
        }
      }
    },

    modulePreload: {
      polyfill: true,
      resolveDependencies(_filename, deps) {
        return deps.filter(dep => !/monaco|three|xterm|\.worker/i.test(dep))
      }
    },

    minify: 'terser',
    terserOptions: {
      compress: {
        ecma: 2020,
        drop_console: true,
        drop_debugger: true,
        pure_funcs: ['console.log'],
        passes: 2
      }
    },

    chunkSizeWarningLimit: 800,
    cssCodeSplit: true,

    commonjsOptions: {
      include: [/node_modules/],
      transformMixedEsModules: true
    }
  },

  optimizeDeps: {
    include: [
      'vue',
      'element-plus',
      '@element-plus/icons-vue',
      '@vueuse/core'
    ],
    exclude: ['monaco-editor']
  }
})
