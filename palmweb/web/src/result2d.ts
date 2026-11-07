// W1 결과 화면: 서버가 준 손금(PalmLineSet)을 손 윤곽 위에 2D로 그린다. W2에서 3D 손으로 바뀐다.
import { LINE_COLORS, LINE_NAMES, type PalmLineSet, type Point } from "./protocol";

const DRAW_MS = 2000;       // 선 하나가 그려지는 시간
const MINOR_DELAY = 500;    // 잔선은 조금 늦게

/** 손 전체(손가락 끝~손목, 윤곽 포함)가 캔버스에 들어오도록 맞추는 변환. palmlines.shape.view_transform 과 같은 방식. */
function fitTransform(palm: PalmLineSet, margin = 0.08) {
  const lm = palm.landmarks;
  const pw = Math.hypot(lm[5][0] - lm[17][0], lm[5][1] - lm[17][1]);
  const pts: Point[] = [...lm, ...(palm.outline ?? [])];
  let lx = Infinity, ly = Infinity, hx = -Infinity, hy = -Infinity;
  for (const [x, y] of pts) { lx = Math.min(lx, x); ly = Math.min(ly, y); hx = Math.max(hx, x); hy = Math.max(hy, y); }
  lx -= pw * 0.1; ly -= pw * 0.1; hx += pw * 0.1; hy = Math.max(hy, lm[0][1] + pw * 0.3);
  const span = Math.max(hx - lx, hy - ly);
  const scale = (1 - 2 * margin) / span;
  return { scale, ox: 0.5 - ((lx + hx) / 2) * scale, oy: 0.5 - ((ly + hy) / 2) * scale };
}

function polyLength(p: Point[]) {
  let L = 0;
  for (let i = 1; i < p.length; i++) L += Math.hypot(p[i][0] - p[i - 1][0], p[i][1] - p[i - 1][1]);
  return L;
}

export class Result2DView {
  private ctx: CanvasRenderingContext2D;
  private palm: PalmLineSet | null = null;
  private t0 = 0;

  constructor(private canvas: HTMLCanvasElement, private info: HTMLElement) {
    this.ctx = canvas.getContext("2d")!;
  }

  show(palm: PalmLineSet, meta: { ms: number; method: string; outline: string; session: string }) {
    this.palm = palm;
    this.t0 = performance.now();
    const major = palm.lines.filter((l) => l.kind !== "minor");
    const minorCount = palm.lines.length - major.length;
    const lm = palm.landmarks;
    const palmH = Math.hypot(lm[0][0] - lm[9][0], lm[0][1] - lm[9][1]);
    const items = major
      .map((l) => `<li><span class="dot" style="background:${LINE_COLORS[l.kind]}"></span>${LINE_NAMES[l.kind]}
        <span class="meta">길이 ${(polyLength(l.points) / palmH).toFixed(2)} · 진하기 ${l.strength.toFixed(2)}</span></li>`)
      .join("");
    this.info.innerHTML = `<h2>찾은 손금</h2><ul>${items || "<li>주요 선을 찾지 못했습니다</li>"}
      <li><span class="dot" style="background:${LINE_COLORS.minor}"></span>잔선 ${minorCount}개</li></ul>
      <div class="meta">${palm.handedness === "Left" ? "왼손" : "오른손"} · ${meta.method} · 윤곽 ${meta.outline} · ${meta.ms}ms<br>${meta.session}</div>`;
  }

  clear() { this.palm = null; }

  render() {
    const c = this.canvas, dpr = devicePixelRatio || 1;
    const W = Math.round(c.clientWidth * dpr), H = Math.round(c.clientHeight * dpr);
    if (c.width !== W || c.height !== H) { c.width = W; c.height = H; }
    const ctx = this.ctx;
    ctx.fillStyle = "#0f0e0d";
    ctx.fillRect(0, 0, W, H);
    const palm = this.palm;
    if (!palm) return;
    const S = Math.min(W, H) * 0.92;
    const x0 = (W - S) / 2 - (W > H * 1.3 ? W * 0.08 : 0), y0 = (H - S) / 2;
    const { scale, ox, oy } = fitTransform(palm);
    const tx = (p: Point): [number, number] => [x0 + (p[0] * scale + ox) * S, y0 + (p[1] * scale + oy) * S];
    const path = (pts: Point[], closed = false) => {
      ctx.beginPath();
      pts.forEach((p, i) => { const [x, y] = tx(p); if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y); });
      if (closed) ctx.closePath();
    };
    const t = performance.now() - this.t0;

    if (palm.outline) {
      ctx.globalAlpha = Math.min(t / 600, 1);
      ctx.lineWidth = 2 * dpr;
      ctx.strokeStyle = "#e6e1da";
      ctx.lineJoin = "round";
      path(palm.outline, true);
      ctx.stroke();
      ctx.globalAlpha = 1;
    }
    // 주요 선 → 잔선 순으로, 시작점부터 끝점까지 그려 나간다
    const lines = [...palm.lines].sort((a, b) => Number(a.kind !== "minor") - Number(b.kind !== "minor"));
    for (const l of lines) {
      const delay = l.kind === "minor" ? MINOR_DELAY : 0;
      const k = Math.max(0, Math.min((t - 300 - delay) / DRAW_MS, 1));
      if (k <= 0) continue;
      const pts = l.points.map(tx);
      let total = 0;
      for (let i = 1; i < pts.length; i++) total += Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]);
      const ease = 1 - (1 - k) ** 3;
      ctx.setLineDash([total * ease, total]);
      ctx.lineWidth = (l.kind === "minor" ? 1.6 : 4) * dpr * (S / 900);
      ctx.lineCap = "round";
      ctx.strokeStyle = LINE_COLORS[l.kind];
      path(l.points);
      ctx.stroke();
      ctx.setLineDash([]);
    }
  }
}
