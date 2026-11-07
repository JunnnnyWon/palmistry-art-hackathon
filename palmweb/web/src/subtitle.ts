// 손 아래 자막. 단계가 바뀌면 이전 글이 흐려지고 새 글이 타이핑되듯 나타난다.
// 무너진 글은 글자 자체로만 보여 준다. 흔들림·깜빡임 연출은 2026-11-07에 뺐다 (foreignRatio 는 받기만 하고 쓰지 않는다).
//
// 줄 높이 고정: 9~10단계 글에는 태국어·아랍어·데바나가리·CJK·이모지가 섞여 글자마다 다른 폰트로 대체된다.
// 대체 폰트는 올림·내림 폭이 달라 그 글자가 든 줄만 높아지고, 타이핑 중 줄 높이가 계속 바뀌어 자막 전체가 들썩였다.
// 그래서 한글·라틴·숫자·문장부호가 아닌 글자(자소 단위)는 높이가 줄 높이로 고정된 inline-block 으로 감싼다.
// 글자 단위라 CJK 처럼 아무 데서나 줄이 바뀔 수 있고, 결합 문자(태국어 성조 등)는 자소 분할로 본 글자와 함께 묶인다.

const TYPE_CPS = 40;          // 초당 글자 수
const FADE_MS = 300;

const SAFE = /^[\p{sc=Hangul}\p{sc=Latin}\p{Nd}\p{P}\s]+$/u;   // 기본 폰트가 직접 그리는 글자
const PICTO = /\p{Extended_Pictographic}/u;                      // 이모지는 Common 스크립트라 따로 거른다
// 띄어쓰기 없이 아무 데서나 줄이 바뀌는 문자: 글자마다 따로 감싼다. 그 밖의 외국 문자(키릴·아랍·데바나가리 등)는 단어째 감싼다
const NOSPACE = /[\p{sc=Han}\p{sc=Hiragana}\p{sc=Katakana}\p{sc=Thai}\p{sc=Lao}\p{sc=Khmer}\p{sc=Myanmar}]/u;
const segmenter = typeof Intl !== "undefined" && "Segmenter" in Intl ? new Intl.Segmenter("ko", { granularity: "grapheme" }) : null;

/** 글을 자소(grapheme) 배열로. 타이핑도 자소 단위로 진행해 서로게이트 쌍·결합 문자가 반쪽만 보이지 않게 한다. */
export function graphemes(text: string): string[] {
  if (segmenter) return Array.from(segmenter.segment(text), (s) => s.segment);
  return Array.from(text);
}

/** 자소들을 DOM 으로: 안전한 글자는 텍스트 노드, 그 밖의 글자는 높이가 고정된 span 으로 (단어째, CJK·태국어는 글자째). */
export function renderGraphemes(target: HTMLElement, gs: string[]) {
  const frag = document.createDocumentFragment();
  let text = "", foreign = "";
  const flushText = () => { if (text) { frag.appendChild(document.createTextNode(text)); text = ""; } };
  const flushForeign = () => {
    if (!foreign) return;
    const span = document.createElement("span");
    span.className = "g";
    span.textContent = foreign;
    frag.appendChild(span);
    foreign = "";
  };
  for (const g of gs) {
    if (SAFE.test(g) && !PICTO.test(g)) { flushForeign(); text += g; continue; }
    flushText();
    if (NOSPACE.test(g) || PICTO.test(g)) { flushForeign(); foreign = g; flushForeign(); }
    else foreign += g;   // 띄어쓰기(안전 글자)를 만날 때까지 한 단어로
  }
  flushText(); flushForeign();
  target.replaceChildren(frag);
}

export class Subtitle {
  private inner: HTMLElement;
  private view: HTMLElement;
  private text = "";
  private gs: string[] = [];
  private shown = 0;
  private start = 0;
  private token = 0;

  constructor(private box: HTMLElement) {
    this.inner = box.querySelector(".subtitle-inner") as HTMLElement;
    this.view = box.querySelector(".subtitle-view") as HTMLElement;
  }

  /** 새 글로 바꾼다. 같은 글이면 아무것도 하지 않는다. */
  show(text: string, _foreignRatio = 0) {
    if (text === this.text && !this.box.classList.contains("pending")) return;
    const my = ++this.token;
    this.box.classList.add("fading");
    setTimeout(() => {
      if (my !== this.token) return;
      this.text = text;
      this.gs = graphemes(text.replace(/\s+/g, " "));   // 탭·줄바꿈이 섞인 무너진 글도 한 줄 흐름으로
      this.shown = 0;
      this.start = performance.now();
      this.inner.replaceChildren();
      this.inner.style.transform = "translateY(0)";
      this.box.classList.remove("fading", "pending");
    }, this.text ? FADE_MS : 0);
  }

  /** 아직 이 단계 글이 도착하지 않았을 때. */
  pending(message = "손금을 읽고 있습니다") {
    this.token++;
    this.text = "";
    this.gs = [];
    this.inner.textContent = message;
    this.inner.style.transform = "translateY(0)";
    this.box.classList.remove("fading");
    this.box.classList.add("pending");
  }

  clear() {
    this.token++;
    this.text = "";
    this.gs = [];
    this.inner.replaceChildren();
    this.box.classList.remove("fading", "pending");
  }

  /** 매 프레임: 타이핑 진행, 3줄을 넘으면 줄 단위로 위로 흘려 올린다. */
  tick(now = performance.now()) {
    if (!this.text || this.shown >= this.gs.length) return;
    const n = Math.min(this.gs.length, Math.floor(((now - this.start) / 1000) * TYPE_CPS));
    if (n === this.shown) return;
    this.shown = n;
    renderGraphemes(this.inner, this.gs.slice(0, n));
    // 넘친 만큼 줄 단위로 올려서 윗줄이 반만 걸치지 않게
    const lh = parseFloat(getComputedStyle(this.inner).lineHeight) || 1;
    const overflow = this.inner.scrollHeight - this.view.clientHeight;
    const shift = overflow > 1 ? Math.ceil(overflow / lh - 0.05) * lh : 0;
    this.inner.style.transform = `translateY(${-shift}px)`;
  }
}
