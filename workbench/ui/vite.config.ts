import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { rmSync } from 'node:fs';
import { resolve } from 'node:path';

const webOutDir = resolve(__dirname, '../web');
const OUTPUT_FILES = {
  entry: 'app.js',
  styles: 'styles.css',
  chunksDir: 'chunks',
  assetsDir: 'assets',
};
const BUILD_OUTPUT_ENTRIES = [OUTPUT_FILES.entry, OUTPUT_FILES.styles, 'index.html', OUTPUT_FILES.chunksDir, OUTPUT_FILES.assetsDir];

function cleanWebBuildOutput() {
  return {
    name: 'clean-web-build-output',
    apply: 'build',
    buildStart() {
      BUILD_OUTPUT_ENTRIES.forEach((entry) => {
        rmSync(resolve(webOutDir, entry), { force: true, recursive: true });
      });
    },
  };
}

export default defineConfig({
  base: '/web/',
  plugins: [react(), cleanWebBuildOutput()],
  build: {
    outDir: webOutDir,
    emptyOutDir: false,
    rollupOptions: {
      output: {
        entryFileNames: OUTPUT_FILES.entry,
        chunkFileNames: `${OUTPUT_FILES.chunksDir}/[name]-[hash].js`,
        assetFileNames: (assetInfo) => {
          if (assetInfo.name?.endsWith('.css')) {
            return OUTPUT_FILES.styles;
          }
          return `${OUTPUT_FILES.assetsDir}/[name]-[hash][extname]`;
        },
      },
    },
  },
  server: {
    host: '127.0.0.1',
    proxy: {
      '/api': 'http://127.0.0.1:8080',
    },
  },
});
