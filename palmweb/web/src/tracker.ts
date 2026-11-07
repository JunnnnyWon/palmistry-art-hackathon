// 브라우저 손 추적 (MediaPipe Hand Landmarker 웹 버전).
// 서버로 영상을 계속 보내지 않고 브라우저가 직접 손을 찾는다. 캡처 순간의 사진만 서버로 간다.
// wasm 파일과 모델은 우리 서버에서 받는다 (/mediapipe-wasm, /models/hand_landmarker.task).
import { FilesetResolver, HandLandmarker } from "@mediapipe/tasks-vision";
import type { Point } from "./protocol";

export interface Observation {
  landmarks: Point[];      // 픽셀 좌표 21점 (입력 캔버스 기준)
  handedness: string;      // "Left" | "Right"
  score: number;
  width: number;
  height: number;
}

export class BrowserTracker {
  private constructor(private lm: HandLandmarker, readonly delegate: string) {}
  private lastTs = -1;

  static async create(): Promise<BrowserTracker> {
    const fileset = await FilesetResolver.forVisionTasks("/mediapipe-wasm");
    const make = (delegate: "GPU" | "CPU") => HandLandmarker.createFromOptions(fileset, {
      baseOptions: { modelAssetPath: "/models/hand_landmarker.task", delegate },
      runningMode: "VIDEO",
      numHands: 1,
      minHandDetectionConfidence: 0.5,
      minHandPresenceConfidence: 0.5,
      minTrackingConfidence: 0.5,
    });
    try {
      return new BrowserTracker(await make("GPU"), "GPU");
    } catch {
      return new BrowserTracker(await make("CPU"), "CPU");   // WebGL이 안 되는 기기
    }
  }

  detect(canvas: HTMLCanvasElement, nowMs: number): Observation | null {
    const ts = Math.max(Math.round(nowMs), this.lastTs + 1);   // 단조 증가
    this.lastTs = ts;
    const r = this.lm.detectForVideo(canvas, ts);
    if (!r.landmarks?.length) return null;
    const w = canvas.width, h = canvas.height;
    const cat = r.handedness?.[0]?.[0] ?? r.handednesses?.[0]?.[0];
    return {
      landmarks: r.landmarks[0].map((p) => [p.x * w, p.y * h] as Point),
      handedness: cat?.categoryName ?? "Right",
      score: cat?.score ?? 0,
      width: w,
      height: h,
    };
  }

  close() { this.lm.close(); }
}
