// 예술 캡차 (/captcha): "매력을 느끼는 '손'을 선택하세요".
//   web/public/captcha/manifest.json 의 round(고정 9장: 손 5, 손이 아닌 것 4)을 자리만 섞어 보여준다. 선택 수 제한 없음.
//   확인을 누르면 고른 장들을 하나로 합친 그림이 격자 자리에 뜬다. 그림은 미리 만들어 둔 것(/captcha/merge/<mask>.jpg, tools/gen_captcha_merge.py)이라 바로 나온다.
//   mask = round 순서대로 고른 칸 1, 아닌 칸 0 (9자리, 511가지). 칸을 누를 때마다 그 조합의 그림을 미리 받아 둔다.
//   선택은 POST /api/captcha 로 기록된다 (대시보드 재료).
//   회차를 시작하면 POST /api/captcha/live {start} 로 이름(감정 + 이름)을 받아 머리띠에 보이고, 칸을 누를 때마다 {pick, 고른 수},
//   확인 뒤 {done} 을 보내 대시보드가 "고르는 중" 을 실시간으로 안다.
import "./captcha.css";

interface Item { file: string; group: "hand" | "other"; label: string; prompt: string; }
interface Manifest { items: Item[]; round: string[]; merge?: { dir?: string }; }

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
const card = $("card"), grid = $("grid"), verify = $("verify") as HTMLButtonElement, next = $("next") as HTMLButtonElement;
const merge = $("merge"), mergeImg = $("merge-img") as HTMLImageElement, mergeCap = $("merge-cap");
let pool: Item[] = [];        // manifest.round 순서 (mask 자릿수 기준)
let round: Item[] = [];       // 이번 회차 화면 순서 (자리만 섞은 것)
let mergeDir = "/captcha/merge";
let startedAt = 0;
let nickname = "";
const client = Math.random().toString(36).slice(2, 10);   // 이 화면을 구분하는 표지 (대시보드가 여러 캡차 화면을 구분)
const prefetched = new Map<string, HTMLImageElement>();

async function live(state: "start" | "pick" | "done", chosen = 0) {
  try {
    const r = await fetch("/api/captcha/live", { method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ client, state, nickname, chosen }) });
    if (state === "start") {
      const j = (await r.json()) as { nickname?: string };
      nickname = j.nickname ?? "";
      $("who").textContent = nickname;
    }
  } catch (e) { console.warn("[captcha] live 실패", e); }
}

const shuffle = <T,>(a: T[]) => { for (let i = a.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [a[i], a[j]] = [a[j], a[i]]; } return a; };

/** 화면에 고른 칸 → manifest.round 순서의 9자리 mask */
function currentMask(): string {
  const tiles = Array.from(grid.children) as HTMLElement[];
  const on = new Set(round.filter((_, i) => tiles[i]?.classList.contains("on")).map((it) => it.file));
  return pool.map((it) => (on.has(it.file) ? "1" : "0")).join("");
}
const mergeUrl = (mask: string) => `${mergeDir}/${mask}.jpg`;

function prefetch(mask: string) {
  if (!mask.includes("1") || prefetched.has(mask)) return;
  const im = new Image();
  im.src = mergeUrl(mask);
  prefetched.set(mask, im);
}

function render() {
  card.dataset.phase = "pick";
  grid.hidden = false; merge.hidden = true; mergeImg.removeAttribute("src"); merge.classList.remove("shown");
  grid.replaceChildren(...round.map((it, i) => {
    const b = document.createElement("button");
    b.type = "button"; b.className = "tile" + (it.group === "hand" ? " is-hand" : ""); b.dataset.i = String(i);
    b.setAttribute("aria-pressed", "false"); b.setAttribute("aria-label", `이미지 ${i + 1}`);
    b.innerHTML = `<img src="/captcha/${it.file}" alt="" draggable="false"><span class="check">✓</span>`;
    b.addEventListener("click", () => {
      if (card.dataset.phase !== "pick") return;
      b.classList.toggle("on");
      b.setAttribute("aria-pressed", String(b.classList.contains("on")));
      const n = grid.querySelectorAll(".tile.on").length;
      verify.disabled = !n;
      prefetch(currentMask());
      void live("pick", n);
    });
    return b;
  }));
  verify.disabled = true; verify.hidden = false; next.hidden = true;
  $("verdict").hidden = true;
  $("sub").textContent = "마음이 가는 칸을 모두 누른 뒤 확인을 누르세요. 몇 개든 좋습니다";
  startedAt = performance.now();
}

function newRound() { round = shuffle(pool.slice()); nickname = ""; $("who").textContent = ""; render(); void live("start"); }

function showMerged(mask: string) {
  const url = mergeUrl(mask);
  merge.hidden = false; grid.hidden = true;
  mergeCap.textContent = "";   // 고른 손 이름은 보이지 않는다 (기록·대시보드에만)
  const done = () => { merge.classList.add("shown"); };
  mergeImg.onload = done;
  mergeImg.onerror = () => {
    // 아직 만들어지지 않은 조합: 고른 칸을 그대로 보여 준다
    merge.hidden = true; grid.hidden = false;
    $("sub").textContent = "이 조합의 그림은 아직 준비되지 않았습니다";
  };
  const pre = prefetched.get(mask);
  mergeImg.src = pre && pre.complete && pre.naturalWidth ? pre.src : url;
  if (mergeImg.complete && mergeImg.naturalWidth) done();
}

async function reveal() {
  if (card.dataset.phase !== "pick") return;
  const tiles = Array.from(grid.children) as HTMLElement[];
  const chosenFlags = tiles.map((t) => t.classList.contains("on"));
  const chosen = round.filter((_, i) => chosenFlags[i]);
  if (!chosen.length) return;
  const mask = currentMask();
  card.dataset.phase = "reveal";
  verify.hidden = true; next.hidden = false; next.focus();
  $("verdict").hidden = true;   // "N개를 합쳤습니다 …" 문구는 쓰지 않는다
  $("sub").textContent = "정답은 없습니다. 당신이 고른 손들이 한 손이 되었습니다";
  showMerged(mask);
  try {
    await fetch("/api/captcha", { method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ ms: Math.round(performance.now() - startedAt), mask, merge: `${mask}.jpg`, nickname,
        tiles: round.map((it, i) => ({ file: it.file, group: it.group, label: it.label, chosen: chosenFlags[i] })) }) });
  } catch (e) { console.warn("[captcha] 기록 실패", e); }
  void live("done", chosen.length);
}

verify.addEventListener("click", () => { void reveal(); });
next.addEventListener("click", newRound);
$("refresh").addEventListener("click", newRound);
window.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && card.dataset.phase === "pick" && !verify.disabled) void reveal();
  else if ((e.key === "Enter" || e.key === " ") && card.dataset.phase === "reveal" && document.activeElement === document.body) newRound();
  else if (e.key.toLowerCase() === "r") newRound();
  else if (e.key.toLowerCase() === "f") { if (document.fullscreenElement) void document.exitFullscreen(); else void document.documentElement.requestFullscreen(); }
});

(async () => {
  const m = (await fetch("/captcha/manifest.json").then((r) => r.json())) as Manifest;
  const byFile = new Map(m.items.map((it) => [it.file, it]));
  pool = (m.round ?? []).map((f) => byFile.get(f)).filter((it): it is Item => !!it);
  if (pool.length !== 9) console.warn("[captcha] manifest.round 가 9장이 아닙니다", pool.length);
  if (m.merge?.dir) mergeDir = `/captcha/${m.merge.dir}`;
  (window as unknown as { __captcha: unknown }).__captcha = { getRound: () => round, getMask: currentMask, newRound, reveal };
  newRound();
})();
