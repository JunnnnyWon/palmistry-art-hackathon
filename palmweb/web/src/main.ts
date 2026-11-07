// 상태 머신 (W5 운영 흐름)
//   attract   대기 화면: 빈 3D 손이 천천히 돌고 안내. 프레임은 계속 보내 손을 감지한다
//   tracking  손이 보이면 카메라 화면 + 안내·게이지. 4초 동안 손이 안 보이면 attract
//   capturing 손금을 읽는 중
//   result    3D 손 위 손금 + 해석 자막 + 단계(방향키·노브)
//   leaving   90초 동안 입력이 없으면 뿌리가 걷히고 손금이 사라진 뒤 attract
//
// 주소 옵션
//   ?src=/samples/hand70.jpg   웹캠 대신 사진으로 점검
//   ?cam=macbook               이름에 이 글자가 들어간 카메라 우선
//   ?view=2d                   결과를 3D 손 대신 평면으로
//   ?room=exhibit              같은 방의 화면·노브가 단계를 공유 (전시장). 없으면 이 화면만의 개인 방
//   ?track=server              손 추적을 서버에서 (예전 방식). 기본은 브라우저에서 추적하고 캡처 순간 사진만 보냄
// 키: ← → 해석 단계 · R 다시 · Space 바로 캡처 · C 카메라 바꾸기 · 2 평면/3D 전환 · D 디버그 · F 전체화면
// "여기서 멈춤" (오른쪽 위 버튼 또는 결과 화면에서 Space): 관객이 불쾌감을 느낀 단계에서 누른다 → 닉네임 입력 → 제출(POST /api/submit)
//   → 서버가 대시보드(/dashboard)에 바로 띄운다 → 몇 초 뒤 다음 관객을 위해 촬영 화면으로 돌아간다.
// 단계는 서버가 하나로 들고 있다. 방향키·노브(라즈베리파이 /knob) 어느 쪽에서 바꿔도 서버가 type=step 으로 알린다.
import { TrackConnection } from "./connection";
import { CaptureView } from "./capture";
import { StillnessGate, palmFacing } from "./gate";
import type { InterpMsg, ServerMsg, TrackMsg } from "./protocol";
import { HandScene } from "./hand3d";
import { mountDevPanel } from "./devpanel";
import { Result2DView } from "./result2d";
import { CameraSource, ImageSource, cameraErrorMessage, type FrameSource } from "./source";
import { Subtitle } from "./subtitle";
import { BrowserTracker } from "./tracker";
import "./style.css";

type State = "attract" | "tracking" | "capturing" | "result" | "leaving";

const IDLE_AFTER_MS = 4000;        // 촬영 화면에서 손이 이만큼 안 보이면 대기 화면
const INACTIVE_MS = 90_000;        // 결과 화면에서 이만큼 입력이 없으면 처음으로 (명세서 4.11)
let lastHandAt = performance.now();
let lastActivityAt = performance.now();
const touch = () => { lastActivityAt = performance.now(); };

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
const params = new URLSearchParams(location.search);
// /dev: 저장된 손금을 바로 띄우고 카메라·서버 없이 색·재질을 시험한다
const DEV = params.get("dev") === "1";
const capture = new CaptureView($("camera-canvas"));
const result = new Result2DView($("result-canvas"), $("result-info"));
const hand = new HandScene($("hand-canvas"));
hand.onInteract = touch;   // 손을 돌려 보는 것도 활동이다 (90초 무입력 타이머)
let view2d = params.get("view") === "2d";
const room = (params.get("room") ?? "").replace(/[^A-Za-z0-9_-]/g, "").slice(0, 40);
const conn = new TrackConnection(room);
let trackMode: "client" | "server" = params.get("track") === "server" ? "server" : "client";
let tracker: BrowserTracker | null = null;
const gate = new StillnessGate();
const CAPTURE_FRAMES = 5;
const ring: HTMLCanvasElement[] = [];      // 손바닥이 정면인 최근 프레임 (캡처 때 서버로 보낼 사진)
let roomName = room;
let state: State = "tracking";
let showDebug = false;
let lastServer = "";

// 해석 단계: 캡처마다 10단계가 서버에서 도착하는 대로 쌓인다. 방향키로 넘긴다.
const STEP_COUNT = 10;
const subtitle = new Subtitle($("subtitle"));
let session = "";
let nickname = "";   // 이 관객의 이름 (감정 + 이름). 서버가 손금을 읽기 시작할 때 붙여 준다. 입력받지 않는다
let step = 1;
let interpError = "";
let description = "";   // 손금 묘사 (interp_start)
const interps = new Map<number, InterpMsg>();
let knobs = 0;

function renderSteps() {
  const el = $("steps");
  if (el.childElementCount !== STEP_COUNT) el.replaceChildren(...Array.from({ length: STEP_COUNT }, () => document.createElement("span")));
  [...el.children].forEach((c, i) => {
    c.classList.toggle("current", i + 1 === step);
    c.classList.toggle("ready", interps.has(i + 1));
  });
}

function showStep() {
  if (DEV) { renderSteps(); return; }
  const m = interps.get(step);
  if (m) subtitle.show(m.text, m.foreign_ratio);
  else subtitle.pending(interpError || "손금을 읽고 있습니다");
  renderSteps();
}

// 단계별 머문 시간 (ms). 제출에 함께 보낸다: 멈추기 전 가장 오래 머문 단계가 진짜 역치일 수 있다
const dwell: Record<number, number> = {};
let stepEnteredAt = performance.now();
function accrueDwell() {
  const now = performance.now();
  dwell[step] = (dwell[step] ?? 0) + (now - stepEnteredAt);
  stepEnteredAt = now;
}

function setStep(k: number) {
  const next = Math.max(1, Math.min(STEP_COUNT, k));
  if (next === step) return;
  accrueDwell();
  step = next;
  showStep();
  hand.setStage(step);   // 뿌리 확산 (W4)
}

/** 방향키: 화면은 바로 바꾸고 서버에 알린다 (서버가 노브에도 알린다). */
let lastKeyStepAt = 0;   // 방향키로 직접 바꾼 시각
function requestStep(k: number) {
  const next = Math.max(1, Math.min(STEP_COUNT, k));
  lastKeyStepAt = performance.now();
  setStep(next);
  if (DEV) return;
  conn.sendCommand("set_step", { step: next });
}

function resetInterp(newSession = "") {
  session = newSession;
  step = 1;
  for (const k of Object.keys(dwell)) delete dwell[Number(k)];
  stepEnteredAt = performance.now();
  description = "";
  interpError = "";
  interps.clear();
  subtitle.clear();
  renderSteps();
}

function setState(s: State) {
  state = s;
  $("app").dataset.mode = s;
  const handVisible = s === "result" || s === "attract" || s === "leaving";
  $("capture-view").hidden = handVisible;
  $("result-view").hidden = !handVisible;
  $("capturing").hidden = s !== "capturing";
  hand.attract = s === "attract";
  // 촬영·대기로 돌아갈 때는 이전 관객의 손금과 뿌리를 지운다 (R 키로 돌아온 경우 대기 화면에 남아 보이던 문제)
  if (s === "tracking" || s === "attract") { hand.clearLines(); hand.resetView(); }
  if (s === "tracking") { setGuide("손바닥을 카메라에 보여주세요", 0); capture.clearHand(); lastHandAt = performance.now(); }
  if (s === "result") touch();
  $("name-tag").textContent = nickname;
  $("name-tag").hidden = s !== "result" || !nickname || DEV;
}

/** 90초 무입력: 뿌리를 거두고 손금을 지운 뒤 대기 화면으로. */
async function leave() {
  if (state !== "result" || submitting) return;
  setState("leaving");
  subtitle.clear();
  await hand.fadeOut(3000);
  if ((state as State) !== "leaving") return;   // 기다리는 동안 R 등으로 이미 바뀌었으면 그대로 둔다
  conn.sendCommand("reset");
  result.clear();
  resetInterp();
  setState("attract");
}

function applyView() {
  $("hand-canvas").hidden = view2d;
  $("result-canvas").hidden = !view2d;
}

function setGuide(msg: string, progress: number) {
  $("guide-message").textContent = msg;
  $("gauge-fill").style.width = `${Math.round(progress * 100)}%`;
}

function setStatus(text: string, bad = false) {
  const el = $("status");
  el.textContent = text;
  el.classList.toggle("bad", bad);
}

conn.onStatus = (open) => {
  if (DEV) return;
  setStatus(open ? `연결됨 · ${capture.source?.label ?? ""}` : "서버 연결 끊김 · 다시 연결하는 중", !open);
  if (open) { conn.sendCommand("reset"); conn.sendCommand("ping"); setState("tracking"); }
};

conn.onMessage = (m: ServerMsg) => {
  if (DEV) return;   // 테스트 화면은 서버 메시지를 따르지 않는다
  switch (m.type) {
    case "step":
      knobs = m.knobs;
      if (m.room) roomName = m.room;
      if (m.source === "knob") touch();
      // 방금 방향키로 바꿨으면 서버가 되돌려 보낸 자기 입력은 무시한다.
      // 키를 빠르게 연달아 누르면 늦게 도착한 이전 값이 화면을 뒤로 되돌리기 때문이다.
      if (m.source === "keyboard" && performance.now() - lastKeyStepAt < 700) break;
      // 지금 보고 있는 손금의 단계만 따른다 (캡처 직후 서버가 1단계로 되돌린다)
      if (state === "result" && (!m.session || m.session === session)) setStep(m.step);
      break;
    case "track":
      capture.onTrack(m);
      handleTrack(m);
      break;
    case "capturing":
      nickname = (m as { nickname?: string }).nickname ?? nickname;
      setState("capturing");
      break;
    case "capture":
      nickname = m.nickname ?? nickname;
      resetInterp(m.session);
      result.show(m.palm, m);
      setState("result");
      showStep();
      // 투영·확산 계산 중에 노브가 돌아갔을 수 있으니 끝나면 현재 단계를 다시 맞춘다
      void hand.setPalm(m.palm).then((r) => { hand.setStage(step); console.info("[3d]", r); });
      console.info("[capture]", m.session, m.ms + "ms", m.palm.lines.map((l) => l.kind));
      break;
    case "error":
      if (state === "capturing") setState("tracking");
      gate.reset();
      setGuide(m.message, 0);
      break;
    case "interp_start":
      if (m.session === session) { description = m.description; console.info("[interp] 묘사:", m.description); }
      break;
    case "interp":
      if (m.session !== session) break;
      interps.set(m.step, m);
      if (m.step === step) showStep(); else renderSteps();
      break;
    case "interp_error":
      if (m.session !== session) break;
      interpError = m.message;
      if (!interps.has(step)) showStep();
      break;
    case "interp_done":
      if (m.session === session) console.info(`[interp] ${m.seconds}s 실패=${m.failed.join(",") || "없음"}`);
      break;
    case "pong":
      lastServer = `U-Net ${m.unet} · ${m.method}`;
      break;
    case "reset_ok":
      break;
  }
};

/** 손 추적 결과(브라우저든 서버든)에 따라 대기·촬영 화면과 안내를 바꾼다. */
function handleTrack(m: TrackMsg) {
  if (m.hand) lastHandAt = performance.now();
  if (state === "attract" && m.hand) setState("tracking");
  else if (state === "tracking" && !m.hand && performance.now() - lastHandAt > IDLE_AFTER_MS) setState("attract");
  if (state === "tracking") setGuide(m.message || (m.hand ? "" : "손바닥을 카메라에 보여주세요"), m.progress);
}

function restart() {
  conn.sendCommand("reset");
  result.clear();
  resetInterp();
  gate.reset();
  ring.length = 0;
  setState("tracking");
}

/** 최근 정면 프레임을 보관한다 (캔버스는 돌려 쓴다). */
function keepFrame(c: HTMLCanvasElement) {
  const slot = ring.length >= CAPTURE_FRAMES ? ring.shift()! : document.createElement("canvas");
  if (slot.width !== c.width || slot.height !== c.height) { slot.width = c.width; slot.height = c.height; }
  slot.getContext("2d")!.drawImage(c, 0, 0);
  ring.push(slot);
}

/** 캡처: 브라우저 추적이면 보관한 사진 묶음을, 서버 추적이면 캡처 명령을 보낸다. */
async function captureNow(reason: "auto" | "manual") {
  if (trackMode === "server") { conn.sendCommand("capture_now"); return; }
  if (!ring.length) { setGuide("손바닥이 보일 때 눌러주세요", 0); return; }
  const frames = [...ring];
  ring.length = 0;
  gate.reset();
  setState("capturing");
  const blobs = await Promise.all(frames.map((c) => new Promise<Blob | null>((r) => c.toBlob(r, "image/jpeg", 0.9))));
  const parts = await Promise.all(blobs.filter((b): b is Blob => !!b).map((b) => b.arrayBuffer()));
  // b"PALM" + 사진 수 + [4바이트 길이(빅엔디언) + JPEG] × n  (server/main.py 의 _unpack_capture)
  const total = 5 + parts.reduce((n, p) => n + 4 + p.byteLength, 0);
  const out = new Uint8Array(total);
  out.set([0x50, 0x41, 0x4c, 0x4d, parts.length], 0);
  let i = 5;
  for (const p of parts) {
    new DataView(out.buffer).setUint32(i, p.byteLength);
    out.set(new Uint8Array(p), i + 4);
    i += 4 + p.byteLength;
  }
  conn.sendBinary(out.buffer);
  console.info(`[capture] ${reason} 사진 ${parts.length}장 ${(total / 1024).toFixed(0)}KB 전송`);
}

async function openSource(deviceId?: string): Promise<FrameSource | null> {
  const src = params.get("src");
  try {
    if (src) return await ImageSource.load(src);
    return await CameraSource.open(params.get("cam") ?? undefined, deviceId);
  } catch (e) {
    setGuide(cameraErrorMessage(e), 0);
    return null;
  }
}

async function cycleCamera() {
  if (!(capture.source instanceof CameraSource)) return;
  const cams = await CameraSource.listCameras();
  const i = cams.findIndex((c) => c.deviceId === (capture.source as CameraSource).deviceId);
  const next = cams[(i + 1) % cams.length];
  capture.setSource(null);
  capture.setSource(await openSource(next.deviceId));
  setStatus(`연결됨 · ${capture.source?.label ?? ""}`);
}

window.addEventListener("keydown", (e) => {
  if (e.repeat && !e.key.startsWith("Arrow")) return;
  touch();
  // 닉네임을 입력하는 동안은 단축키(R, Space 등)를 받지 않는다. Esc 는 모달 닫기
  if (submitting) { if (e.key === "Escape") closeSubmit(); return; }
  const k = e.key.toLowerCase();
  if (k === "r") { if (DEV) location.reload(); else restart(); }
  else if (e.key === "ArrowRight" && state === "result") { e.preventDefault(); requestStep(step + 1); }
  else if (e.key === "ArrowLeft" && state === "result") { e.preventDefault(); requestStep(step - 1); }
  else if (e.code === "Space") {
    e.preventDefault();
    if (state === "result") openSubmit();                                        // 결과 화면: "여기서 멈춤"
    else if (state === "tracking" || state === "attract") captureNow("manual");   // 촬영 화면: 바로 캡처
  }
  else if (k === "c") void cycleCamera();
  else if (k === "2") { view2d = !view2d; applyView(); }
  else if (k === "d") { showDebug = !showDebug; $("debug").hidden = !showDebug; }
  else if (k === "f") { if (document.fullscreenElement) void document.exitFullscreen(); else void document.documentElement.requestFullscreen(); }
});

// 손 추적 주기: 초당 15번. 캡처 조건이 '15번 연속 정지'라서 관객이 약 1초 멈추면 캡처된다.
const SEND_INTERVAL_MS = 1000 / 15;
let detectMs = 0;

/** 브라우저 추적: 프레임마다 손을 찾고 캡처 조건을 판정한다. 서버로는 캡처 순간 사진만 간다. */
function clientStep(now: number) {
  const c = capture.grab();
  if (!c || !tracker) return;
  const t0 = performance.now();
  const o = tracker.detect(c, now);
  detectMs = 0.9 * detectMs + 0.1 * (performance.now() - t0);
  const [ready, message, progress] = gate.update(o);
  const facing = !!o && palmFacing(o);
  if (facing) keepFrame(c); else ring.length = 0;
  const m: TrackMsg = {
    type: "track", message, progress, ready, size: [c.width, c.height],
    hand: o ? { landmarks: o.landmarks.map(([x, y]) => [x / c.width, y / c.height]), handedness: o.handedness, palm_facing: facing } : null,
  };
  capture.showHand(m);
  handleTrack(m);
  if (ready && state === "tracking" && conn.open) void captureNow("auto");
}

async function trackLoop() {
  let last = 0;
  for (;;) {
    const now = performance.now();
    if ((state === "tracking" || state === "attract") && conn.open && now - last >= SEND_INTERVAL_MS) {
      last = now;
      if (trackMode === "client") {
        clientStep(now);
      } else {
        capture.unstick();
        const blob = await capture.nextFrame();
        if (blob) conn.sendFrame(blob);
      }
    }
    await new Promise((r) => setTimeout(r, 8));
  }
}

function stepDebug() {
  const m = interps.get(step);
  if (!m) return `\nstep ${step} 대기 중 (${interps.size}/10 도착)`;
  return `\nstep ${step}  T ${m.temperature}  min_p ${m.min_p}  외국 ${m.foreign_ratio.toFixed(2)}  한글 ${m.hangul_ratio.toFixed(2)}` +
    `\n${m.provider ?? "-"} ${m.ms}ms ${m.finish ?? ""}${m.borrowed_from ? `  (${m.borrowed_from}단계 글 빌림)` : ""}  ${interps.size}/10 도착`;
}

function frame() {
  if (state === "result" || state === "leaving") { if (view2d) result.render(); else hand.render(); subtitle.tick(); }
  else if (state === "attract") hand.render();
  else capture.render();
  if (!DEV && state === "result" && performance.now() - lastActivityAt > INACTIVE_MS) void leave();
  if (showDebug) {
    $("debug").textContent =
      `state ${state}  무입력 ${((performance.now() - lastActivityAt) / 1000).toFixed(0)}s  방 ${roomName || "-"}\n` +
      (trackMode === "client" ? `추적 브라우저(${tracker?.delegate ?? "-"}) ${detectMs.toFixed(1)}ms\n` : `추적 서버 fps ${capture.fps.toFixed(1)}  rtt ${capture.rtt.toFixed(0)}ms\n`) +
      `source ${capture.source?.label ?? "-"} ${capture.source?.width ?? 0}x${capture.source?.height ?? 0}\n${lastServer}  노브 ${knobs}개` +
      (state === "result" ? stepDebug() : "");
  }
  requestAnimationFrame(frame);
}

(window as unknown as { __palm: unknown }).__palm = {
  hand, result, interps, subtitle, getStep: () => step, getState: () => state, leave,
  getTrackMode: () => trackMode, getRoom: () => roomName, getDetectMs: () => detectMs,
  ageActivity: (ms: number) => { lastActivityAt -= ms; },   // 점검용: 무입력 시간 앞당기기
  useImage: async (url: string) => { capture.setSource(await ImageSource.load(url)); },   // 점검용: 입력 사진 바꾸기
};  // 디버그용

// 카메라가 끊기면(케이블·권한·다른 앱) 2초마다 다시 연다
setInterval(async () => {
  const s = capture.source;
  if (s instanceof CameraSource && s.ended) {
    setStatus("카메라 연결 끊김 · 다시 여는 중", true);
    capture.setSource(null);
    capture.setSource(await openSource());
    if (capture.source) setStatus(`연결됨 · ${capture.source.label}`);
  }
}, 2000);

/** /dev: 저장된 손금(public/dev/palm.json)을 바로 띄운다. 서버·카메라·해석은 쓰지 않는다. */
async function devStart() {
  $("app").classList.add("dev");
  const palm = await fetch("/dev/palm.json").then((r) => r.json());
  resetInterp("dev");
  setState("result");
  renderSteps();
  const r = await hand.setPalm(palm);
  console.info("[3d]", r);
  mountDevPanel(hand, (k) => requestStep(k));
  setStatus("테스트 /dev · 저장된 손금");
  requestAnimationFrame(frame);
}

(async () => {
  applyView();
  if (DEV) { await devStart(); return; }
  setState("tracking");
  setGuide("카메라를 준비하고 있습니다", 0);
  capture.setSource(await openSource());
  if (capture.source) setStatus(`${conn.open ? "연결됨" : "서버 연결 중"} · ${capture.source.label}`);
  if (capture.source) setGuide("손바닥을 카메라에 보여주세요", 0);
  requestAnimationFrame(frame);
  // 브라우저 손 추적을 준비한다. 안 되면(오래된 기기 등) 서버 추적으로 돌아간다
  if (trackMode === "client") {
    try {
      tracker = await BrowserTracker.create();
      console.info("[track] 브라우저 추적", tracker.delegate);
    } catch (e) {
      trackMode = "server";
      console.warn("[track] 브라우저 추적을 쓸 수 없어 서버 추적으로 전환:", e);
    }
  }
  void trackLoop();
})();

// ---------------------------------------------------------------- "여기서 멈춤" → 이름 확인(서버가 붙인 감정 + 이름) → 제출 → 다음 관객
const THANKS_MS = 4500;   // 제출 뒤 이만큼 보여 주고 처음(촬영)으로
let submitting = false;   // 모달이 열려 있는 동안 (무입력 타이머·단축키 멈춤)
let thanksTimer = 0;

function openSubmit() {
  if (state !== "result" || DEV) return;
  touch();
  submitting = true;
  const m = interps.get(step);
  $("submit-step").textContent = String(step);
  $("submit-text").textContent = m?.text ?? "";
  $("submit-status").hidden = true;
  ($("submit-ok") as HTMLButtonElement).disabled = false;
  $("submit-name").textContent = nickname || "익명";
  $("submit-modal").hidden = false;
  setTimeout(() => $("submit-ok").focus(), 50);
}

function closeSubmit() {
  submitting = false;
  $("submit-modal").hidden = true;
  touch();
}

async function sendSubmit() {
  if (!submitting) return;
  const ok = $("submit-ok") as HTMLButtonElement;
  const status = $("submit-status");
  ok.disabled = true;
  status.hidden = true;
  accrueDwell();
  const m = interps.get(step);
  const nick = nickname || "익명";
  let snapshot = "";
  try { snapshot = view2d ? "" : hand.snapshot(); } catch (e) { console.warn("[submit] 스냅샷 실패", e); }
  const body = {
    session, room: roomName, step, nickname: nick, text: m?.text ?? "", temperature: m?.temperature, foreign_ratio: m?.foreign_ratio ?? 0,
    text_step1: interps.get(1)?.text ?? "",   // 대시보드에는 멈춘 단계의 글이 아니라 1단계(온전한) 글을 띄운다
    description, dwell_ms: Object.fromEntries(Object.entries(dwell).map(([k, v]) => [k, Math.round(v)])), snapshot,
  };
  try {
    const r = await fetch("/api/submit", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const j = await r.json();
    console.info("[submit]", j.id, nick, step + "단계");
  } catch (e) {
    ok.disabled = false;
    status.textContent = "제출하지 못했습니다. 다시 눌러 주세요 (" + String(e) + ")";
    status.hidden = false;
    return;
  }
  $("submit-modal").hidden = true;
  ($("thanks").querySelector(".thanks-nick") as HTMLElement).textContent = nick;
  $("thanks").hidden = false;
  clearTimeout(thanksTimer);
  thanksTimer = window.setTimeout(() => {
    $("thanks").hidden = true;
    submitting = false;
    restart();   // 다음 관객: 촬영 화면으로. 새로 고침해도 같은 상태(촬영)로 시작한다
  }, THANKS_MS);
}

$("stop-btn").addEventListener("click", openSubmit);
$("submit-cancel").addEventListener("click", closeSubmit);
$("submit-form").addEventListener("submit", (e) => { e.preventDefault(); void sendSubmit(); });
$("submit-modal").addEventListener("click", (e) => { if (e.target === e.currentTarget) closeSubmit(); });
$("thanks").addEventListener("click", () => { clearTimeout(thanksTimer); $("thanks").hidden = true; submitting = false; restart(); });

// 화면의 단계 조작 패널(−/슬라이더/+/다시 찍기)과 아래 힌트 문구는 2026-11-07에 뺐다. 단계는 방향키·노브, 다시 찍기는 R 키.
