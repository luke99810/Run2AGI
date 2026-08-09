import { resolve } from 'node:path'
import react from '@vitejs/plugin-react'
import { defineConfig, externalizeDepsPlugin } from 'electron-vite'

// electron-vite bundles this config before loading it, so import.meta.url points
// at a temporary bundle rather than this package on Windows. Its CLI guarantees
// that cwd is the configured package root for all package scripts.
const desktopRoot = process.cwd()

export default defineConfig({
  main: {
    plugins: [externalizeDepsPlugin()],
    build: {
      rollupOptions: {
        input: resolve(desktopRoot, 'shell/main/index.ts')
      }
    }
  },
  preload: {
    plugins: [externalizeDepsPlugin()],
    build: {
      rollupOptions: {
        input: resolve(desktopRoot, 'shell/preload/index.ts'),
        output: {
          format: 'cjs',
          entryFileNames: 'index.cjs'
        }
      }
    }
  },
  renderer: {
    root: resolve(desktopRoot, 'renderer'),
    plugins: [react()],
    build: {
      rollupOptions: {
        input: resolve(desktopRoot, 'renderer/index.html')
      }
    },
    resolve: {
      alias: {
        '@desktop': desktopRoot,
        '@codentum/contracts': resolve(desktopRoot, '../contracts/typescript/state.ts')
      }
    }
  }
})
