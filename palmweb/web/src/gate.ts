// 캡처 판정: palmlines/palmlines/hand.py 의 HandObservation·StillnessGate 를 그대로 옮긴 것.
// 손바닥이 정면이고, 너무 멀거나 가깝지 않고, 손 전체가 화면 안에 있고, 15번 연속 거의 움직이지 않으면 캡처.
// 브라우저는 초당 15번 판정하므로 약 1초 멈추면 캡처된다.
import type { Point } from "./protocol";
import type { Observation } from "./tracker";

const WRIST = 0, THUMB_MCP = 2, MIDDLE_MCP = 9;

export function thumbOnLeft(lm: Point[]) {
  const ax = lm[MIDDLE_MCP][0] - lm[WRIST][0], ay = lm[MIDDLE_MCP][1] - lm[WRIST][1];
  const tx = lm[THUMB_MCP][0] - lm[WRIST][0], ty = lm[THUMB_MCP][1] - lm[WRIST][1];
  return ax * ty - ay * tx < 0;
}

/** 손바닥이 카메라를 향하는가. 라벨 'Right' ↔ 엄지 오른쪽 (서버와 같은 규약, 샘플 사진으로 확인). */
export function palmFacing(o: Observation) { return (o.handedness === "Right") !== thumbOnLeft(o.landmarks); }

export function palmSize(o: Observation) {
  const [a, b] = [o.landmarks[WRIST], o.landmarks[MIDDLE_MCP]];
  return Math.hypot(a[0] - b[0], a[1] - b[1]);
}

export function fullyVisible(o: Observation, margin = 0.03) {
  const size = palmSize(o), pad = 0.12 * size;
  let lx = Infinity, ly = Infinity, hx = -Infinity, hy = -Infinity;
  for (const [x, y] of o.landmarks) { lx = Math.min(lx, x); ly = Math.min(ly, y); hx = Math.max(hx, x); hy = Math.max(hy, y); }
  lx -= pad; ly -= pad; hx += pad; hy += pad;
  hy = Math.max(hy, o.landmarks[WRIST][1] + 0.25 * size);
  const { width: w, height: h } = o;
  return lx >= margin * w && ly >= margin * h && hx <= (1 - margin) * w && hy <= (1 - margin) * h + 0.25 * size;
}

export class StillnessGate {
  private hist: Point[][] = [];
  constructor(private window = 15, private motionThresh = 0.012, private minSize = 0.22, private maxSize = 0.5) {}
  reset() { this.hist = []; }

  /** [열림, 안내 문구, 진행도 0~1] */
  update(o: Observation | null): [boolean, string, number] {
    if (!o) { this.reset(); return [false, "손바닥을 카메라에 보여주세요", 0]; }
    if (!palmFacing(o)) { this.reset(); return [false, "손바닥 쪽을 보여주세요", 0]; }
    const size = palmSize(o);
    if (size < this.minSize * o.height) { this.reset(); return [false, "손을 조금 더 가까이", 0]; }
    if (size > this.maxSize * o.height) { this.reset(); return [false, "손을 조금 더 멀리", 0]; }
    if (!fullyVisible(o)) { this.reset(); return [false, "손 전체가 화면 안에 들어오게 해주세요", 0]; }
    this.hist.push(o.landmarks);
    if (this.hist.length > this.window) this.hist.shift();
    if (this.hist.length < 2) return [false, "그대로 멈춰주세요", 0];
    let sum = 0, n = 0;
    for (let i = 1; i < this.hist.length; i++) {
      for (let j = 0; j < 21; j++) {
        sum += Math.hypot(this.hist[i][j][0] - this.hist[i - 1][j][0], this.hist[i][j][1] - this.hist[i - 1][j][1]);
        n++;
      }
    }
    if (sum / n / size > this.motionThresh) {   // 움직이면 처음부터
      this.hist = [this.hist[this.hist.length - 1]];
      return [false, "그대로 멈춰주세요", 0];
    }
    const progress = this.hist.length / this.window;
    return [progress >= 1, "그대로 멈춰주세요", Math.min(progress, 1)];
  }
}
