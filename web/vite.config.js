import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';

export default defineConfig({
  plugins: [svelte()],
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8384',
      '/ws': { target: 'ws://127.0.0.1:8384', ws: true },
      '/portal': 'http://127.0.0.1:8384',
    },
  },
});
