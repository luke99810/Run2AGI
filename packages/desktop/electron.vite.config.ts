import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig, externalizeDepsPlugin } from "electron-vite";

const packageRoot = dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  main: {
    plugins: [externalizeDepsPlugin()],
    resolve: {
      alias: {
        "@desktop": packageRoot,
      },
    },
    build: {
      rollupOptions: {
        input: resolve(packageRoot, "shell/main/index.ts"),
      },
    },
  },
  preload: {
    plugins: [externalizeDepsPlugin()],
    resolve: {
      alias: {
        "@desktop": packageRoot,
      },
    },
    build: {
      rollupOptions: {
        input: resolve(packageRoot, "shell/preload/index.ts"),
        output: {
          entryFileNames: "[name].cjs",
          format: "cjs",
        },
      },
    },
  },
  renderer: {
    root: resolve(packageRoot, "renderer"),
    plugins: [react()],
    build: {
      rollupOptions: {
        input: resolve(packageRoot, "renderer/index.html"),
      },
    },
    resolve: {
      alias: {
        "@desktop": packageRoot,
      },
    },
  },
});
