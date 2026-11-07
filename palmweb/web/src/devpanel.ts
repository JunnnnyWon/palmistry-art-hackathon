// /dev 테스트 패널: 핏줄·손금 색을 hex로 고르고, 거칠기·환경광·핏줄 세기를 슬라이더로 바꿔 본다.
import type { HandScene, Look } from "./hand3d";

export function mountDevPanel(hand: HandScene, onStep: (k: number) => void) {
  const el = document.createElement("div");
  el.id = "devpanel";
  const colors = hand.getColors();
  const look = hand.getLook();
  const colorRow = (label: string, key: keyof ReturnType<HandScene["getColors"]>, hint: string) => `
    <div class="crow"><div class="clabel">${label}<small>${hint}</small></div>
      <input type="color" data-color="${key}" value="${colors[key]}" title="클릭해서 색 고르기">
      <input type="text" data-hex="${key}" value="${colors[key]}" maxlength="7" spellcheck="false" placeholder="#rrggbb"></div>`;
  const slider = (label: string, key: keyof Look, min: number, max: number, step: number) => `
    <label class="row"><span>${label}</span><input type="range" data-look="${key}" min="${min}" max="${max}" step="${step}" value="${look[key]}"><output data-out="${key}">${look[key]}</output></label>`;
  el.innerHTML = `
    <h3>테스트 패널 <small>/dev</small></h3>
    <div class="group"><b>색 — 네모를 클릭해 고르거나 hex를 입력하면 바로 바뀝니다</b>
      ${colorRow("핏줄", "veinDeep", "기본 색 (대부분의 핏줄)")}${colorRow("핏줄 끝·박동", "veinBright", "퍼지는 맨 앞과 박동 파동")}${colorRow("손금", "palmLine", "손 위의 손금 선")}</div>
    <div class="group"><b>피부</b>${slider("거칠기 배율", "roughness", 0.3, 2.5, 0.05)}${slider("환경광", "envMap", 0, 1, 0.02)}${slider("키 조명", "keyLight", 0, 2, 0.05)}</div>
    <div class="group"><b>핏줄</b>${slider("붉은 빛", "veinStrength", 0, 2, 0.05)}${slider("어둡게", "veinDarken", 0, 1, 0.02)}</div>
    <div class="group"><b>텍스처</b><div class="tex">${Object.entries(look.textures).map(([k, v]) => `${k} ${v}`).join(" · ")}</div></div>
    <div class="group"><b>단계</b><div class="stepbtns">${Array.from({ length: 10 }, (_, i) => `<button data-step="${i + 1}">${i + 1}</button>`).join("")}</div></div>
    <button id="dev-copy">현재 값 복사</button><pre id="dev-json"></pre>`;
  document.body.appendChild(el);
  const dump = () => {
    const j = { colors: hand.getColors(), look: hand.getLook() };
    (el.querySelector("#dev-json") as HTMLElement).textContent = JSON.stringify(j, null, 1);
    return JSON.stringify(j, null, 1);
  };
  el.querySelectorAll<HTMLInputElement>("input[data-color]").forEach((inp) => inp.addEventListener("input", () => {
    const key = inp.dataset.color!;
    hand.setColors({ [key]: inp.value });
    (el.querySelector(`input[data-hex="${key}"]`) as HTMLInputElement).value = inp.value;
    dump();
  }));
  el.querySelectorAll<HTMLInputElement>("input[data-hex]").forEach((inp) => ["input", "change"].forEach((ev) => inp.addEventListener(ev, () => {
    const key = inp.dataset.hex!;
    const v = inp.value.trim().replace(/^([0-9a-fA-F]{6})$/, "#$1");
    if (!/^#[0-9a-fA-F]{6}$/.test(v)) return;
    inp.value = v.toLowerCase();
    hand.setColors({ [key]: v });
    (el.querySelector(`input[data-color="${key}"]`) as HTMLInputElement).value = v.toLowerCase();
    dump();
  })));
  el.querySelectorAll<HTMLInputElement>("input[data-look]").forEach((inp) => inp.addEventListener("input", () => {
    const key = inp.dataset.look as keyof Look;
    hand.setLook({ [key]: Number(inp.value) });
    (el.querySelector(`output[data-out="${key}"]`) as HTMLElement).textContent = inp.value;
    dump();
  }));
  el.querySelectorAll<HTMLButtonElement>("button[data-step]").forEach((b) => b.addEventListener("click", () => onStep(Number(b.dataset.step))));
  el.querySelector("#dev-copy")!.addEventListener("click", () => { void navigator.clipboard?.writeText(dump()); });
  dump();
  return el;
}
