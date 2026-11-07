import { defineConfig } from "vite";

// 개발 중에는 Vite(5173)가 화면을, Python 서버(8000)가 API·WebSocket을 맡는다.
// 전시 때는 `npm run build` 결과(dist)를 Python 서버가 직접 서빙한다.
export default defineConfig({
  server: {
    port: 5173,
    proxy: {
      "/ws": { target: "ws://localhost:8000", ws: true },
      "/api": "http://localhost:8000",
      "/samples": "http://localhost:8000",
      "/testset": "http://localhost:8000",
      "/models/hand_landmarker.task": "http://localhost:8000",
    },
  },
  // Three.js가 커서 기본 경고(500kB)를 넘는다. 같은 랩탑에서 불러오므로 문제 없음
  build: {
    outDir: "dist", emptyOutDir: true, chunkSizeWarningLimit: 1500,
    // 세 페이지: 체험 화면(index.html), 대시보드(dashboard.html → /dashboard), 캡차(captcha.html → /captcha)
    rollupOptions: { input: { main: "index.html", dashboard: "dashboard.html", captcha: "captcha.html" } },
  },
});
