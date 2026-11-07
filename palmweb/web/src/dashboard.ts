// 대시보드 (/dashboard): 손금 "여기서 멈춤" 기록과 캡차 응답을 그림 위주로 한 화면에 보여 준다 (페이지 스크롤 없음).
//   헤더   손금·캡차 건수, 소리, 초기화 (진행 중 표시는 두지 않는다)
//   왼쪽   손금: 고른 기록의 손 스냅샷 + 이름 + 멈춘 단계. 아래는 최근 기록 띠(그림 + 단계)
//   오른쪽 캡차: 고른 응답의 합친 그림 + 이름. 아래는 최근 그림 띠
//   두 패널은 같은 틀: 같은 크기의 정사각형 검은 틀(스냅샷은 그 안에 맞춤), 같은 자리의 이름, 같은 규격의 정사각형 띠.
//   글은 이름과 단계뿐. 띠는 화면에 들어가는 만큼만 보이고, 카드를 누르면 고정(Esc 해제), 아니면 새 기록이 오는 대로 바뀐다.
//   새 기록이 오면 "띠링" (Web Audio 합성). 브라우저는 첫 클릭 전까지 소리를 막으므로 🔔 버튼이 "클릭해서 켜기"로 깜빡인다.
//   이름은 입력받지 않고 서버가 붙인 "감정 + 이름" (행복한 원준, 신난 승우 …)
// 서버: /ws/dashboard → init(items, captcha) · submission · captcha · live · captcha_live · reset. 끊기면 다시 붙어 init 을 다시 받는다.
import "./dashboard.css";

interface Submission {
  id: string; at: number; session: string; room: string; step: number; steps: number; temperature: number; min_p: number;
  nickname: string; text: string; text_step1?: string; foreign_ratio: number; description: string; dwell_ms: Record<string, number> | null; image: string | null;
}
interface Captcha {
  id: string; at: number; ms: number | null; nickname: string; mask: string; chosen: string[]; chosen_files: string[];
  chosen_hand: number; chosen_other: number; image: string | null;
}
type Msg =
  | { type: "init"; items: Submission[]; captcha?: Captcha[] }
  | { type: "submission"; item: Submission }
  | { type: "captcha"; item: Captcha }
  | { type: "live"; room: string; step: number; phase: string; displays: number; nickname?: string }
  | { type: "captcha_live"; client: string; state: "start" | "pick" | "done"; nickname: string; chosen: number; at: number }
  | { type: "reset"; archived: number }
  | { type: "pong" };

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
if (new URLSearchParams(location.search).get("shot") === "1") document.body.classList.add("shot");   // 스크린샷용 (tools/slides_live.py): 버튼·연결 표시 숨김
let items: Submission[] = [];
let caps: Captcha[] = [];
let selected: string | null = null;
let selectedCap: string | null = null;
let pinned = false, pinnedCap = false;   // 카드를 직접 골랐으면 새 기록이 와도 상세를 바꾸지 않는다 (Esc 로 해제)

/** 합친 그림이 생기기 전에 그 주소를 연 적이 있으면 브라우저가 404 를 기억한다. 한 번은 주소를 바꿔 다시 받아 본다. */
function retryOnce(img: HTMLImageElement) {
  img.onerror = () => {
    if (img.dataset.retried || !img.src) return;
    img.dataset.retried = "1";
    img.src = img.src.split("?")[0] + "?r=" + Date.now();
  };
}

/** 띠에 들어가지 않는 카드는 숨긴다 (페이지·띠 모두 스크롤 없음). */
function clipStrip(ol: HTMLElement) {
  const right = ol.getBoundingClientRect().right + 1;
  let hidden = false;
  for (const li of Array.from(ol.children) as HTMLElement[]) {
    li.hidden = false;
    if (hidden || li.getBoundingClientRect().right > right) { li.hidden = true; hidden = true; }
  }
}

// ── 손금 ──────────────────────────────────────────
function select(id: string, pin: boolean) { selected = id; pinned = pin; renderList(); renderDetail(); }

function renderList(freshId?: string) {
  const ol = $("cards");
  ol.replaceChildren(...items.map((s) => {
    const li = document.createElement("li");
    li.className = "card" + (s.id === selected ? " selected" : "") + (s.id === freshId ? " fresh" : "");
    li.innerHTML = `${s.image ? `<img class="thumb fit" src="${s.image}" alt="">` : `<div class="thumb none"></div>`}<small>${s.step}</small>`;
    li.title = `${s.nickname} · ${s.step}단계`;
    li.addEventListener("click", () => select(s.id, true));
    return li;
  }));
  clipStrip(ol);
  $("empty").hidden = items.length > 0;
  $("count").textContent = String(items.length);
}

function renderDetail() {
  const s = items.find((x) => x.id === selected);
  const img = $("d-img") as HTMLImageElement;
  fitFrame();
  if (!s) { $("d-nick").textContent = "—"; $("d-step").textContent = "—"; img.removeAttribute("src"); return; }
  setNick($("d-nick"), s.nickname);
  $("d-step").textContent = String(s.step);
  if (s.image) img.src = s.image; else img.removeAttribute("src");
}

// ── 캡차 ──────────────────────────────────────────
function selectCap(id: string, pin: boolean) { selectedCap = id; pinnedCap = pin; renderCapList(); renderCapDetail(); }

function renderCapList(freshId?: string) {
  const ol = $("cap-cards");
  ol.replaceChildren(...caps.map((c) => {
    const li = document.createElement("li");
    li.className = "card" + (c.id === selectedCap ? " selected" : "") + (c.id === freshId ? " fresh" : "");
    li.innerHTML = c.image ? `<img class="thumb" src="${c.image}" alt="">` : `<div class="thumb none"></div>`;
    li.title = (c.nickname ? c.nickname + " · " : "") + c.chosen.join(" · ");
    const im = li.querySelector("img"); if (im) retryOnce(im);
    li.addEventListener("click", () => selectCap(c.id, true));
    return li;
  }));
  clipStrip(ol);
  $("cap-empty").hidden = caps.length > 0;
  $("count-cap").textContent = String(caps.length);
}

/** 이름은 "감정 / 이름" 두 줄로 (칸이 좁아도 같은 모양). */
function setNick(el: HTMLElement, name: string) {
  const parts = (name || "익명").split(" ");
  el.replaceChildren(...(parts.length > 1 ? [parts[0], parts.slice(1).join(" ")] : parts).map((t) => { const sp = document.createElement("span"); sp.textContent = t; return sp; }));
}

/** 그림 틀: 자리 높이와 (폭 − 이름 칸) 중 작은 쪽의 정사각형. 두 패널에 같은 값을 준다
 *  (CSS 의 % max-height 는 auto 행 안에서 풀리지 않아 JS 로 정한다). */
function fitFrame() {
  const det = $("c-detail");
  const nameCol = Math.max(190, innerWidth * 0.1);   // 이름 칸 폭 (감정 / 이름 두 줄, 단계 숫자)
  const side = Math.floor(Math.min(det.clientHeight, det.clientWidth - nameCol));
  if (side <= 0) return;
  for (const id of ["d-frame", "c-frame"]) { const f = $(id); f.style.width = side + "px"; f.style.height = side + "px"; }
}
new ResizeObserver(fitFrame).observe($("c-detail"));

function renderCapDetail() {
  fitFrame();
  const c = caps.find((x) => x.id === selectedCap);
  const img = $("c-img") as HTMLImageElement;
  const fig = $("c-frame");
  if (c) setNick($("c-nick"), c.nickname); else $("c-nick").textContent = "—";
  if (!c) { img.removeAttribute("src"); fig.classList.remove("none"); return; }
  if (c.image) { delete img.dataset.retried; retryOnce(img); img.src = c.image; fig.classList.remove("none"); }
  else { img.removeAttribute("src"); fig.classList.add("none"); }   // 합친 그림이 아직 없는 조합
}

// ── 알림음 ────────────────────────────────────────
let audio: AudioContext | null = null;
let soundOn = localStorage.getItem("dash-sound") !== "off";

function soundState() {
  const b = $("sound") as HTMLButtonElement;
  const blocked = soundOn && (!audio || audio.state !== "running");
  b.setAttribute("aria-pressed", String(soundOn));
  b.classList.toggle("blocked", blocked);
  b.textContent = !soundOn ? "🔕 소리 꺼짐" : blocked ? "🔔 클릭해서 소리 켜기" : "🔔 소리";
}

/** 첫 사용자 동작에서 오디오를 연다 (자동재생 정책). */
async function unlockAudio() {
  if (!audio) audio = new AudioContext();
  if (audio.state !== "running") { try { await audio.resume(); } catch { /* 사용자 동작 없이 불리면 거부될 수 있다 */ } }
  soundState();
}

/** "띠링": 짧은 높은 음 뒤에 더 높은 음이 길게 울린다. */
function chime() {
  if (!soundOn || !audio || audio.state !== "running") return;
  const t0 = audio.currentTime + 0.01;
  const master = audio.createGain(); master.gain.value = 0.5; master.connect(audio.destination);
  const note = (freq: number, at: number, dur: number, vol: number) => {
    const g = audio!.createGain();
    g.gain.setValueAtTime(0.0001, at); g.gain.exponentialRampToValueAtTime(vol, at + 0.008); g.gain.exponentialRampToValueAtTime(0.0001, at + dur);
    g.connect(master);
    for (const [mul, w, v] of [[1, "sine", 1], [2, "sine", 0.25], [3, "triangle", 0.06]] as [number, OscillatorType, number][]) {
      const o = audio!.createOscillator(); o.type = w; o.frequency.value = freq * mul;
      const og = audio!.createGain(); og.gain.value = v; o.connect(og); og.connect(g);
      o.start(at); o.stop(at + dur + 0.05);
    }
  };
  note(1318.5, t0, 0.35, 0.6);          // 띠 (E6)
  note(1975.5, t0 + 0.13, 1.1, 0.7);    // 링 (B6)
}

function renderAll(freshId?: string, freshCap?: string) { renderList(freshId); renderDetail(); renderCapList(freshCap); renderCapDetail(); }

function connect() {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/dashboard`);
  ws.onopen = () => { $("conn").className = "conn ok"; $("conn").title = "실시간 연결됨"; };
  ws.onclose = () => { $("conn").className = "conn bad"; $("conn").title = "끊김 · 다시 연결 중"; setTimeout(connect, 2000); };
  ws.onmessage = (e) => {
    const m = JSON.parse(e.data as string) as Msg;
    if (m.type === "init") {
      items = m.items; caps = m.captcha ?? [];
      if (!selected || !items.some((x) => x.id === selected)) { selected = items[0]?.id ?? null; pinned = false; }
      if (!selectedCap || !caps.some((x) => x.id === selectedCap)) { selectedCap = caps[0]?.id ?? null; pinnedCap = false; }
      renderAll();
    } else if (m.type === "submission") {
      items = [m.item, ...items.filter((x) => x.id !== m.item.id)];
      if (!pinned) selected = m.item.id;
      renderAll(m.item.id);
      chime();
    } else if (m.type === "captcha") {
      caps = [m.item, ...caps.filter((x) => x.id !== m.item.id)];
      if (!pinnedCap) selectedCap = m.item.id;
      renderAll(undefined, m.item.id);
      chime();
    } else if (m.type === "reset") {
      items = []; caps = []; selected = null; selectedCap = null; pinned = false; pinnedCap = false;
      renderAll();
    }
    // live · captcha_live(진행 중 상태)는 받기만 하고 보이지 않는다
  };
  setInterval(() => { if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "ping" })); }, 20000);
}

// 초기화: 두 단계 확인. 서버는 파일을 지우지 않고 archive-<시각> 폴더로 옮긴다 (손금 제출 + 캡차 응답)
$("reset").addEventListener("click", () => { $("reset-n").textContent = String(items.length + caps.length); $("reset").hidden = true; $("reset-confirm").hidden = false; $("reset-yes").focus(); });
$("reset-no").addEventListener("click", () => { $("reset-confirm").hidden = true; $("reset").hidden = false; });
$("reset-yes").addEventListener("click", async () => {
  ($("reset-yes") as HTMLButtonElement).disabled = true;
  try { await fetch("/api/submissions", { method: "DELETE" }); } catch (e) { console.warn("[reset]", e); }
  ($("reset-yes") as HTMLButtonElement).disabled = false;
  $("reset-confirm").hidden = true; $("reset").hidden = false;
});

// 소리: 버튼으로 켜고 끄기. 화면 아무 데나 첫 클릭·키 입력에서 오디오를 연다
$("sound").addEventListener("click", (e) => {
  e.stopPropagation();
  const wasBlocked = soundOn && (!audio || audio.state !== "running");
  if (wasBlocked) { void unlockAudio().then(chime); return; }   // 막혀 있던 상태면 켜기만 하고 미리 들려 준다
  soundOn = !soundOn; localStorage.setItem("dash-sound", soundOn ? "on" : "off");
  if (soundOn) void unlockAudio().then(chime); else soundState();
});
// 🔔 버튼 자체는 click 에서 처리한다 (pointerdown 에서 글자가 바뀌어 버튼이 줄어들면 click 이 버튼 밖에 떨어진다)
for (const ev of ["pointerdown", "keydown"]) window.addEventListener(ev, (e) => { if (soundOn && !(e.target as HTMLElement).closest?.("#sound")) void unlockAudio(); }, { passive: true });
soundState();

connect();
window.addEventListener("resize", () => renderAll());
window.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if (!$("reset-confirm").hidden) { $("reset-confirm").hidden = true; $("reset").hidden = false; return; }
  select(items[0]?.id ?? "", false); selectCap(caps[0]?.id ?? "", false);
});
