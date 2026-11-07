"""손금 해석 10단계 생성 (OpenRouter, Qwen3.5-9B).

캡처 직후 10단계를 한꺼번에 병렬 생성한다. 방향키를 누를 때는 호출하지 않는다 (명세서 4.6절).
샘플링 설정은 temperature_실험_OpenRouter.md 결과에 따른다:
min_p 0.1이면 1.9까지도 문장이 유지되고, min_p 0이면 1.3부터 여러 나라 말이 섞인 글로 무너진다.
"""
from __future__ import annotations

import asyncio
import os
import re
import time
from dataclasses import dataclass, asdict
from pathlib import Path

import httpx

URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = os.environ.get("PALMWEB_MODEL", "qwen/qwen3.5-9b")

# 업체 선택은 단계별로 다르다.
# 안정 구간(1~6단계)은 업체가 달라도 결과가 비슷하므로 OpenRouter가 가장 빠른 업체를 고르게 한다.
# 무너지는 구간(7단계~)은 업체마다 거동이 다르다. 2026-11-06 비교 결과 (8~10단계 각 3회):
#   Together       DeepInfra와 같은 단계별 붕괴, 응답 약 1초 → 우선
#   DeepInfra      단계별 붕괴, 응답 3~10초, 요청 거절(429)이 잦음 → 대체
#   PrimeIntellect 온도 1.9, min_p 0에서도 멀쩡함 → 붕괴 연출이 사라지므로 쓰지 않는다
COLLAPSE_PROVIDERS = os.environ.get("PALMWEB_COLLAPSE_PROVIDERS", "Together,DeepInfra").split(",")
FALLBACK_MAX_STEP = int(os.environ.get("PALMWEB_FALLBACK_MAX_STEP", "6"))
CONCURRENCY = 5       # 해석 한 번(10단계) 안의 동시 요청 수. 10개를 한꺼번에 보내면 429가 잦다
# 서버 전체 동시 요청 상한. 관객 여럿이 동시에 캡처해도 OpenRouter로 가는 요청이 이 수를 넘지 않는다.
# (예전 상한 6은 DeepInfra가 요청을 자주 거절하던 때 값. 7~10단계를 Together 우선으로 바꾼 뒤 20으로 16명 동시 시험 통과)
GLOBAL_CONCURRENCY = int(os.environ.get("PALMWEB_LLM_CONCURRENCY", "20"))
MAX_TOKENS = 220      # 해석 2~3문장에 충분한 길이
MAX_TOKENS_COLLAPSE = 150   # 9단계부터는 무너진 글이 끝없이 이어지므로 짧게 끊어 빨리 받는다
TIMEOUT_S = 40

# 단계 → (temperature, min_p). 7~9단계 min_p는 2026-11-06 후보 실험으로 골랐다 (명세서 9절 W3).
#   1~6  안정적인 해석, 표현만 달라짐
#   7    1.3 / 0.03   대체로 멀쩡하나 외국 글자가 가끔 섞임
#   8    1.5 / 0.01   문법은 한국어인데 뜻이 어긋남 ("가죽조명처럼 활력 있고")
#   9    1.7 / 0.005  낱말이 깨지기 시작
#   10   1.9 / 0      여러 나라 말과 기호로 완전히 무너짐
STEPS: list[tuple[float, float]] = [
    (0.1, 0.1), (0.3, 0.1), (0.5, 0.1), (0.7, 0.1), (0.9, 0.1),
    (1.1, 0.1), (1.3, 0.03), (1.5, 0.01), (1.7, 0.005), (1.9, 0.0),
]

SYSTEM_PROMPT = """너는 손금을 읽어 주는 사람이다. 사용자가 손금의 생김새를 묘사하면, 아래 규칙으로 해석해 2~3문장으로 말한다.
- 감정선: 길고 진하면 감정이 깊고 관계를 오래 지킨다. 짧거나 곧으면 감정 표현이 절제되어 있다.
- 두뇌선: 길면 생각이 깊고 신중하다. 곧으면 현실적이고, 휘면 상상력이 풍부하다.
- 생명선: 길고 크게 휘면 활력과 회복력이 크다. 짧다고 수명이 짧다는 뜻은 아니며, 변화가 잦은 삶을 뜻한다.
- 운명선: 뚜렷하면 한 길을 오래 걷고, 희미하면 스스로 길을 바꿔 간다.
- 감정선과 두뇌선 사이가 넓으면 마음이 열려 있고, 좁으면 생각과 감정이 가깝게 붙어 있다.
- 잔금이 많으면 생각이 많고 예민하다.
말투: 단정하고 따뜻한 존댓말. 손금 이름을 문장에 넣는다. 제목, 목록, 면책 문구, 질문은 쓰지 않는다."""

_FOREIGN = re.compile(r"[一-鿿぀-ヿ฀-๿Ѐ-ӿ؀-ۿऀ-ॿ֐-׿]")
_HANGUL = re.compile(r"[가-힣]")
_LETTER = re.compile(r"\w")


def _load_key() -> str | None:
    key = os.environ.get("OPENROUTER_API_KEY")
    if key:
        return key
    env = Path(__file__).resolve().parents[2] / ".env"   # 저장소 최상위 .env
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("OPENROUTER_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'") or None
    return None


@dataclass
class StepResult:
    step: int                 # 1~10
    temperature: float
    min_p: float
    text: str
    foreign_ratio: float      # 글자 중 외국 문자(한자·가나·키릴 등) 비율
    hangul_ratio: float       # 글자 중 한글 비율
    finish: str | None
    provider: str | None
    ms: int
    error: str | None = None
    borrowed_from: int | None = None   # 실패해서 다른 단계 글을 빌렸으면 그 단계

    def to_dict(self):
        return asdict(self)


def _ratios(text: str) -> tuple[float, float]:
    letters = len(_LETTER.findall(text)) or 1
    return round(len(_FOREIGN.findall(text)) / letters, 3), round(len(_HANGUL.findall(text)) / letters, 3)


def _clean(text: str) -> str:
    """자막용 정리: 마크다운 기호와 줄바꿈을 걷어낸다. 무너진 글은 그대로 둔다."""
    t = re.sub(r"[*#>`_]+", "", text or "")
    t = re.sub(r"\s*\n+\s*", " ", t)
    return t.strip()


class Interpreter:
    def __init__(self):
        self.key = _load_key()
        self.client = httpx.AsyncClient(timeout=TIMEOUT_S, limits=httpx.Limits(max_connections=GLOBAL_CONCURRENCY + 5))
        self.global_sem = asyncio.Semaphore(GLOBAL_CONCURRENCY)

    @property
    def available(self) -> bool:
        return bool(self.key)

    async def _call(self, description: str, temperature: float, min_p: float, fast: bool = False) -> dict:
        # fast: 아무 업체나 가장 빠른 곳 (min_p 등 파라미터는 지원해야 함). 아니면 붕괴가 확인된 업체만.
        provider = ({"sort": "latency", "require_parameters": True} if fast else
                    {"order": COLLAPSE_PROVIDERS, "only": COLLAPSE_PROVIDERS, "allow_fallbacks": True,
                     "require_parameters": True})
        body = {
            "model": MODEL,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": description}],
            "temperature": temperature, "top_p": 1, "top_k": 0, "min_p": min_p,
            "max_tokens": MAX_TOKENS if temperature < 1.7 else MAX_TOKENS_COLLAPSE, "reasoning": {"enabled": False},
            "provider": provider,
        }
        for wait in (1, 2, 4, 8, 0):   # 429(요청이 너무 많음)는 간격을 늘려 가며 다시
            r = await self.client.post(URL, json=body, headers={
                "Authorization": f"Bearer {self.key}", "X-Title": "palmweb"})
            if r.status_code != 429 or not wait:
                break
            await asyncio.sleep(wait)
        r.raise_for_status()
        return r.json()

    async def step(self, description: str, step: int) -> StepResult:
        temperature, min_p = STEPS[step - 1]
        t0 = time.perf_counter()
        last_err = None
        for attempt in range(2):   # 실패하면 같은 조건으로 한 번 더
            try:
                d = await self._call(description, temperature, min_p, fast=step <= FALLBACK_MAX_STEP)
                ch = d["choices"][0]
                text = _clean(ch["message"].get("content") or "")
                fr, hr = _ratios(text)
                return StepResult(step, temperature, min_p, text, fr, hr, ch.get("finish_reason"),
                                  d.get("provider"), round((time.perf_counter() - t0) * 1000))
            except Exception as e:
                last_err = f"{type(e).__name__}: {e}"[:300]
                await asyncio.sleep(0.5)
        return StepResult(step, temperature, min_p, "", 0, 0, None, None,
                          round((time.perf_counter() - t0) * 1000), error=last_err)

    async def run_all(self, description: str, on_result=None) -> list[StepResult]:
        """10단계를 병렬로 생성한다. 1단계를 가장 먼저 요청하고, 도착하는 대로 on_result를 부른다."""
        results: dict[int, StepResult] = {}
        sem = asyncio.Semaphore(CONCURRENCY)

        async def one(k: int):
            await asyncio.sleep(0.02 * (k - 1))   # 요청 순서: 1단계 먼저 (세마포어 대기 줄도 이 순서)
            async with sem, self.global_sem:
                res = await self.step(description, k)
            results[k] = res
            if on_result and not res.error:
                await on_result(res)

        await asyncio.gather(*(one(k) for k in range(1, len(STEPS) + 1)))
        # 실패한 단계는 가장 가까운 성공 단계의 글을 빌린다
        ok = [k for k, r in results.items() if not r.error]
        for k, r in sorted(results.items()):
            if r.error and ok:
                src = min(ok, key=lambda j: (abs(j - k), j))
                b = results[src]
                r.text, r.foreign_ratio, r.hangul_ratio, r.borrowed_from = b.text, b.foreign_ratio, b.hangul_ratio, src
                if on_result:
                    await on_result(r)
        return [results[k] for k in sorted(results)]

    async def close(self):
        await self.client.aclose()
