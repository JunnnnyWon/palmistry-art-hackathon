// /ws/track 연결. 끊기면 자동으로 다시 붙는다.
import type { ServerMsg } from "./protocol";

export class TrackConnection {
  private ws: WebSocket | null = null;
  private retry = 0;
  onMessage: (m: ServerMsg) => void = () => {};
  onStatus: (open: boolean) => void = () => {};

  private url: string;

  /** room: 같은 방의 화면·노브가 단계를 공유한다. 비우면 서버가 이 연결만의 개인 방을 만든다. */
  constructor(room = "") {
    const q = room ? `?room=${encodeURIComponent(room)}` : "";
    this.url = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/track${q}`;
    this.connect();
  }

  get open() { return this.ws?.readyState === WebSocket.OPEN; }

  private connect() {
    const ws = new WebSocket(this.url);
    ws.binaryType = "arraybuffer";
    ws.onopen = () => { this.retry = 0; this.onStatus(true); };
    ws.onmessage = (e) => this.onMessage(JSON.parse(e.data as string) as ServerMsg);
    ws.onclose = () => {
      this.onStatus(false);
      const wait = Math.min(1000 * 2 ** this.retry++, 8000);
      setTimeout(() => this.connect(), wait);
    };
    this.ws = ws;
  }

  sendFrame(blob: Blob) { if (this.open) this.ws!.send(blob); }
  sendBinary(data: ArrayBuffer) { if (this.open) this.ws!.send(data); }
  sendCommand(type: string, extra: Record<string, unknown> = {}) {
    if (this.open) this.ws!.send(JSON.stringify({ type, ...extra }));
  }
}
