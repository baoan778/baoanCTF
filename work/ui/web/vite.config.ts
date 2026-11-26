import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 构建产物直接落到 server.mjs 读取的目录（DIST = work/ui/web/dist）
// 代理只在 npm run dev 时用；生产由 server.mjs 同源提供，不需要 proxy。
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
    chunkSizeWarningLimit: 1200,
  },
  server: {
    port: 5173,
    proxy: { "/api": { target: "http://127.0.0.1:30142", changeOrigin: true } },
  },
});