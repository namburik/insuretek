import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  const BACKEND = env.BACKEND_URL || 'http://127.0.0.1:6001';

  return {
    plugins: [react()],
    server: {
      port: 6001,
      proxy: {
        '/intake': { target: BACKEND, changeOrigin: true },
        '/api':    { target: BACKEND, changeOrigin: true },
        '/auth':   { target: BACKEND, changeOrigin: true },
      },
    },
    build: {
      outDir: 'dist',
      emptyOutDir: true,
    },
  };
});
