// 촬영 화면: 입력 프레임을 화면에 맞춰 그리고, 서버가 준 손 랜드마크를 겹쳐 그린다.
// 서버로는 응답을 받은 뒤에 다음 프레임을 보낸다 (프레임이 쌓이지 않게).
import { HAND_CONNECTIONS, type TrackMsg } from "./protocol";
import type { FrameSource } from "./source";

const SEND_MAX_SIDE = 1280;   // 손금이 보이려면 해상도가 높아야 한다
const JPEG_QUALITY = 0.9;

export class CaptureView {
  private ctx: CanvasRenderingContext2D;
  private sendCanvas = document.createElement("canvas");
  private sendCtx = this.sendCanvas.getContext("2d")!;
  private last: TrackMsg | null = null;
  private waiting = false;
  private lastSent = 0;
  rtt = 0;
  fps = 0;

  constructor(private canvas: HTMLCanvasElement, public source: FrameSource | null = null) {
    this.ctx = canvas.getContext("2d")!;
  }

  setSource(s: FrameSource | null) { this.source?.stop(); this.source = s; this.last = null; this.waiting = false; }
  onTrack(m: TrackMsg) {
    this.last = m;
    this.waiting = false;
    const now = performance.now();
    this.rtt = now - this.lastSent;
    this.fps = 0.9 * this.fps + 0.1 * (1000 / Math.max(this.rtt, 1));
  }
  clearHand() { this.last = null; }

  /** 현재 프레임을 (웹캠이면 거울 반전해) 그린 캔버스. 브라우저 손 추적에 쓴다. */
  grab(): HTMLCanvasElement | null {
    const s = this.source;
    if (!s || !s.width) return null;
    const k = Math.min(1, SEND_MAX_SIDE / Math.max(s.width, s.height));
    const w = Math.round(s.width * k), h = Math.round(s.height * k);
    if (this.sendCanvas.width !== w) { this.sendCanvas.width = w; this.sendCanvas.height = h; }
    s.draw(this.sendCtx, w, h);
    return this.sendCanvas;
  }

  /** 브라우저 추적 결과로 손 표시를 갱신한다 (서버 왕복 시간은 재지 않는다). */
  showHand(m: TrackMsg) { this.last = m; }

  /** 서버로 보낼 다음 프레임 (응답 대기 중이면 null). */
  async nextFrame(): Promise<Blob | null> {
    const s = this.source;
    if (!s || this.waiting || !s.width) return null;
    const k = Math.min(1, SEND_MAX_SIDE / Math.max(s.width, s.height));
    const w = Math.round(s.width * k), h = Math.round(s.height * k);
    if (this.sendCanvas.width !== w) { this.sendCanvas.width = w; this.sendCanvas.height = h; }
    s.draw(this.sendCtx, w, h);
    this.waiting = true;
    this.lastSent = performance.now();
    const blob = await new Promise<Blob | null>((r) => this.sendCanvas.toBlob(r, "image/jpeg", JPEG_QUALITY));
    if (!blob) this.waiting = false;
    return blob;
  }

  /** 응답이 오래 안 오면(연결 끊김 등) 다시 보낼 수 있게 푼다. */
  unstick(timeoutMs = 3000) { if (this.waiting && performance.now() - this.lastSent > timeoutMs) this.waiting = false; }

  render() {
    const c = this.canvas, dpr = devicePixelRatio || 1;
    const W = Math.round(c.clientWidth * dpr), H = Math.round(c.clientHeight * dpr);
    if (c.width !== W || c.height !== H) { c.width = W; c.height = H; }
    const ctx = this.ctx;
    ctx.fillStyle = "#0f0e0d";
    ctx.fillRect(0, 0, W, H);
    const s = this.source;
    if (!s || !s.width) return;
    // 화면에 꽉 차게(넘치는 부분은 잘림)
    const k = Math.max(W / s.width, H / s.height);
    const dw = s.width * k, dh = s.height * k, dx = (W - dw) / 2, dy = (H - dh) / 2;
    ctx.save();
    ctx.translate(dx, dy);
    s.draw(ctx, dw, dh);
    const hand = this.last?.hand;
    if (hand) {
      const p = hand.landmarks.map(([x, y]) => [x * dw, y * dh]);
      ctx.lineWidth = 2.5 * dpr;
      ctx.strokeStyle = hand.palm_facing ? "rgba(140,230,120,0.9)" : "rgba(255,120,90,0.9)";
      ctx.beginPath();
      for (const [a, b] of HAND_CONNECTIONS) { ctx.moveTo(p[a][0], p[a][1]); ctx.lineTo(p[b][0], p[b][1]); }
      ctx.stroke();
      ctx.fillStyle = "#fff";
      for (const [x, y] of p) { ctx.beginPath(); ctx.arc(x, y, 3.5 * dpr, 0, Math.PI * 2); ctx.fill(); }
    }
    ctx.restore();
  }
}
