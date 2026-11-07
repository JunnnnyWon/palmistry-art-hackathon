// 프레임 입력: 웹캠, 또는 점검용 사진(?src=/samples/hand70.jpg).
// 웹캠은 거울처럼 좌우 반전해 보여주고, 서버에도 반전된 프레임을 보낸다 (기존 앱과 같은 규약).

const CAMERA_KEY = "palmweb.cameraId";
// 맥에 함께 잡히는 iPhone 연속성 카메라·데스크뷰·가상 카메라는 기본 선택에서 뒤로 미룬다
const DEPRIORITIZE = /iphone|desk view|데스크뷰|vcam|virtual|obs/i;

export interface FrameSource {
  readonly width: number;
  readonly height: number;
  readonly mirrored: boolean;
  readonly label: string;
  draw(ctx: CanvasRenderingContext2D, w: number, h: number): void;
  stop(): void;
}

export class ImageSource implements FrameSource {
  mirrored = false;
  private constructor(private img: HTMLImageElement, public label: string) {}
  get width() { return this.img.naturalWidth; }
  get height() { return this.img.naturalHeight; }
  static async load(url: string): Promise<ImageSource> {
    const img = new Image();
    // decode()는 창이 숨겨져 있으면 끝나지 않을 수 있어 load 이벤트를 기다린다
    await new Promise<void>((resolve, reject) => {
      img.onload = () => resolve();
      img.onerror = () => reject(new Error(`사진을 불러오지 못했습니다: ${url}`));
      img.src = url;
    });
    const name = url.startsWith("data:") ? "점검용 이미지" : url.length > 60 ? `…${url.slice(-57)}` : url;
    return new ImageSource(img, `사진 ${name}`);
  }
  draw(ctx: CanvasRenderingContext2D, w: number, h: number) { ctx.drawImage(this.img, 0, 0, w, h); }
  stop() {}
}

export class CameraSource implements FrameSource {
  mirrored = true;
  private constructor(private video: HTMLVideoElement, private stream: MediaStream, public label: string,
                      public deviceId: string) {}
  get width() { return this.video.videoWidth; }
  get height() { return this.video.videoHeight; }

  static async listCameras(): Promise<MediaDeviceInfo[]> {
    const all = await navigator.mediaDevices.enumerateDevices();
    return all.filter((d) => d.kind === "videoinput");
  }

  /** 저장된 카메라 → 이름에 hint가 들어간 카메라 → 내장 카메라 순으로 고른다. */
  static async open(hint?: string, deviceId?: string): Promise<CameraSource> {
    // 권한을 먼저 받아야 장치 이름이 보인다
    const probe = await navigator.mediaDevices.getUserMedia({ video: true });
    probe.getTracks().forEach((t) => t.stop());
    const cams = await CameraSource.listCameras();
    if (!cams.length) throw new DOMException("카메라 없음", "NotFoundError");
    const saved = deviceId ?? localStorage.getItem(CAMERA_KEY) ?? undefined;
    const pick =
      cams.find((c) => c.deviceId === saved) ??
      (hint ? cams.find((c) => c.label.toLowerCase().includes(hint.toLowerCase())) : undefined) ??
      cams.find((c) => !DEPRIORITIZE.test(c.label)) ??
      cams[0];
    const stream = await navigator.mediaDevices.getUserMedia({
      video: { deviceId: { exact: pick.deviceId }, width: { ideal: 1280 }, height: { ideal: 720 } },
      audio: false,
    });
    const video = document.createElement("video");
    video.muted = true;
    video.playsInline = true;
    video.srcObject = stream;
    await video.play();
    try { localStorage.setItem(CAMERA_KEY, pick.deviceId); } catch { /* 저장 불가 환경 */ }
    return new CameraSource(video, stream, pick.label || "카메라", pick.deviceId);
  }

  /** 카메라 트랙이 끝났는가 (케이블 분리, 다른 앱이 가져감 등). */
  get ended() { return this.stream.getVideoTracks().every((t) => t.readyState === "ended"); }

  draw(ctx: CanvasRenderingContext2D, w: number, h: number) {
    ctx.save();
    ctx.translate(w, 0);
    ctx.scale(-1, 1);
    ctx.drawImage(this.video, 0, 0, w, h);
    ctx.restore();
  }

  stop() { this.stream.getTracks().forEach((t) => t.stop()); }
}

export function cameraErrorMessage(e: unknown): string {
  const name = e instanceof DOMException ? e.name : "";
  if (name === "NotAllowedError") return "카메라 권한을 허용해주세요 (주소창 왼쪽 아이콘)";
  if (name === "NotFoundError") return "카메라를 찾을 수 없습니다";
  if (name === "NotReadableError") return "다른 앱이 카메라를 쓰고 있습니다";
  return `카메라를 열 수 없습니다: ${e instanceof Error ? e.message : String(e)}`;
}
