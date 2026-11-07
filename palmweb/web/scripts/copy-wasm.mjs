// MediaPipe 손 추적 WebAssembly 파일을 public/ 으로 복사한다 (빌드·개발 서버 시작 전에 자동 실행).
// 외부 CDN 없이 우리 서버에서 받게 하려는 것이다. public/mediapipe-wasm 은 git에 넣지 않는다.
import { cpSync, existsSync, mkdirSync } from "node:fs";
const src = "node_modules/@mediapipe/tasks-vision/wasm";
const dst = "public/mediapipe-wasm";
if (!existsSync(src)) { console.error("먼저 npm install"); process.exit(1); }
mkdirSync(dst, { recursive: true });
cpSync(src, dst, { recursive: true });
console.log("MediaPipe wasm →", dst);
