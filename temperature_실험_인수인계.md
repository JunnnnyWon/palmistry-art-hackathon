# LLM temperature 실험 인수인계

- 작성일: 2026-11-06
- 목적: 같은 질문을 받았을 때 temperature에 따라 AI 답변이 얼마나 달라지는지 측정
- 모델: Google `gemini-3.5-flash-lite` (Google AI Studio API, 무료 등급)
- 이 문서만 있으면 실험을 재현할 수 있도록 스크립트 전문을 맨 아래에 넣었습니다.

---

## 1. 한눈에 보기

- 기본 설정에서는 **temperature를 바꿔도 답이 거의 바뀌지 않습니다.** 조건과 관계없이 100회 중 85~93회가 같은 문장이었습니다.
  > 죽음은 삶을 더욱 소중하고 의미 있게 만드는 유한한 완성입니다.
- 샘플링 제한(topP, topK)을 풀었을 때만 **높은 온도에서 다른 답이 약 3배** 많아졌습니다(낮은 온도 3회 → 높은 온도 10회).
- 원인은 세 가지가 겹쳐서입니다.
  1. 사람 말투에 맞춰 튜닝된 모델이라, 확률이 처음부터 1등 답 하나에 크게 쏠려 있습니다.
  2. API의 기본값 topP 0.95와 topK 64가 확률 낮은 단어를 미리 잘라냅니다.
  3. "한 문장으로 정의" 같은 프롬프트 제약이 고를 수 있는 답의 폭을 좁힙니다.

---

## 2. 배경: 왜 Gemini로 했나

처음에는 Claude 모델로 하려고 했지만 최신 Claude 모델에서는 temperature를 조절할 수 없습니다(2026-10 기준, Anthropic 문서 기준이며 실제 호출로 확인하지는 않았습니다).

| Claude 모델 | temperature |
|---|---|
| Fable 5.1 / 5, Opus 5.5 / 5 / 4.8 / 4.7, Sonnet 5 | 보내면 400 에러 |
| Sonnet 5.5, Haiku 5.5 | 기본값만 허용 |
| Opus 4.6, Sonnet 4.6, Haiku 4.5 이하 | 가능. 단, API 버전 `2026-10-21`부터는 모든 모델에서 400 |

그래서 temperature를 0~2 범위로 조절할 수 있는 Gemini를 대신 사용했습니다.

---

## 3. 환경 준비

1. Google AI Studio에서 API 키를 발급받습니다.
2. 스크립트와 같은 폴더에 `.env` 파일을 만들고 키를 넣습니다. **`.env`는 git에 올리지 마세요.**
   ```
   GEMINI_API_KEY=발급받은_키
   ```
3. Python 3.9 이상이면 됩니다. 표준 라이브러리만 사용하므로 따로 설치할 패키지가 없습니다.
4. 이 문서 맨 아래 **부록 A**의 코드를 `gemini_temperature_test.py`로 저장합니다.

### 사용법

```bash
python3 gemini_temperature_test.py --list
```
사용 가능한 모델과 기본/최대 temperature를 출력합니다.

```bash
python3 gemini_temperature_test.py \
  --temps 0.1 0.3 0.5 0.7 0.9 1.1 1.3 1.5 1.7 1.9 --runs 10 \
  --max-tokens 1024 --summary --rpm 14 \
  --system "한 문장으로 정의" --prompt "죽음에 대해 어떻게 생각해?" \
  --top-p 1 --top-k 1000 --out result.json
```

| 옵션 | 의미 | 기본값 |
|---|---|---|
| `--model` | 모델 이름 | `gemini-3.5-flash-lite` |
| `--temps` | 시험할 temperature 목록 | `0 1 2` |
| `--runs` | temperature마다 반복 횟수 | 3 |
| `--prompt` / `--system` | 질문 / 시스템 프롬프트 | 빵집 이름 질문 / 없음 |
| `--top-p` / `--top-k` | 샘플링 제한값 | 모델 기본값(0.95 / 64) |
| `--thinking-level` | `minimal` / `low` / `high` | 모델 기본값 |
| `--seed` | 시드 고정 | 지정 안 함 |
| `--max-tokens` | 최대 출력 토큰 | 256 |
| `--rpm` | 분당 최대 요청 수 | 14 |
| `--workers` | 동시 요청 수 | 4 |
| `--summary` | 답 전문 대신 요약 표만 출력 | 끔 |
| `--out` | 결과를 JSON으로 저장 | 없음 |

출력 지표:
- **서로다른답**: 반복한 답 중 글자가 다른 답의 개수
- **단어거리**: 답 두 개씩 짝지었을 때 띄어쓰기 단위 단어가 얼마나 안 겹치는지의 평균(Jaccard 거리). 0이면 모두 같고, 1이면 겹치는 단어가 없습니다.

---

## 4. 알아둘 제약 (직접 호출해서 확인한 것)

| 항목 | 결과 |
|---|---|
| 무료 등급 호출 한도 | 모델당 **분당 15회**. 넘으면 429 에러가 납니다. 스크립트가 `--rpm 14`로 제한하고, 429가 나면 기다렸다가 다시 시도합니다. 일일 한도는 확인하지 못했습니다. |
| 모델 기본값 | `gemini-3.5-flash-lite`: temperature 1, 최대 2, **topP 0.95, topK 64, thinking 켜짐** |
| `topK: 1000` | 에러 없이 받아들여졌지만, 서버에서 실제로 그대로 적용되는지는 확인하지 못했습니다. |
| `thinkingBudget: 0` | 400 에러. thinking을 완전히 끌 수 없습니다. |
| `thinkingLevel: "minimal"` | 사용할 수 있습니다. |
| `responseLogprobs` | "이 모델에서는 지원하지 않는다"는 400 에러. 단어별 확률은 볼 수 없습니다. |
| `gemini-2.5-flash-lite` 등 | 모델 목록에는 나오지만 신규 사용자는 404 에러가 납니다. |
| `gemini-3.8-flash` | 응답이 60초를 넘겨 시간 초과가 났습니다. |

---

## 5. 실험 경과

### 5-1. 예비 실험 (참고용, 표본이 작음)

| 실험 | 조건 | 결과 |
|---|---|---|
| 빵집 이름 짓기 | T = 0 / 1 / 2, 각 3회 | T=0에서도 3번 모두 다른 이름이 나왔습니다. temperature 0이 같은 답을 보장하지 않습니다. |
| "죽음에 대해 어떻게 생각해?" (시스템 프롬프트 없음) | T = 0.1~1.9 (0.2 간격), 각 1회 | 모든 온도에서 구성이 같았습니다: 도입 → 4개 관점(유한성, 자연의 순환, 두려움, 종교) → "어떻게 살 것인가" 결론 → 되묻기. 높은 온도에서는 오타나 잘못 붙인 단어("모멘토 모리", "어떻게 살 것인가(Well-dying)")가 조금 늘었습니다. |
| 같은 질문 + 시스템 프롬프트 "한 문장으로 정의" | T = 0.1~1.9, 각 3회 | 30회 중 26회가 완전히 같은 문장이었습니다. |

> 참고: 예비 실험과 본 실험 모두 seed는 지정하지 않았습니다.

### 5-2. 본 실험: 샘플링 조건 A / B / C 비교

**공통 조건**
- 시스템 프롬프트: `한 문장으로 정의`
- 질문: `죽음에 대해 어떻게 생각해?`
- temperature: 0.1, 0.3, 0.5, 0.7, 0.9, 1.1, 1.3, 1.5, 1.7, 1.9 (10단계)
- 반복: 단계마다 10회, 조건마다 100회, 총 300회
- maxOutputTokens 1024, seed 지정 안 함, 에러 0회

| 조건 | topP | topK | thinking |
|---|---|---|---|
| A (기본값) | 0.95 | 64 | 기본 |
| B (샘플링 제한 해제) | 1 | 1000 | 기본 |
| C (B에 thinking 최소화) | 1 | 1000 | minimal |

#### 조건별 요약

| 조건 | 가장 많이 나온 문장 비율 | 서로 다른 문장 수 (100회 중) | 다른 답: 낮은 온도(0.1~0.9) / 높은 온도(1.1~1.9) |
|---|---|---|---|
| A | 85% | 11 | 8회 / 7회 |
| B | 87% | 10 | **3회 / 10회** |
| C | **93%** | **5** | 3회 / 4회 |

"다른 답"은 가장 많이 나온 문장과 글자가 하나라도 다른 답을 뜻합니다.

#### temperature별 상세

각 칸은 `서로 다른 답 수 / 가장 많이 나온 문장과 다른 답 수 / 단어거리`입니다(10회 기준).

| T | A | B | C |
|---|---|---|---|
| 0.1 | 4 / 3 / 0.23 | 3 / 2 / 0.16 | 1 / 0 / 0.00 |
| 0.3 | 2 / 1 / 0.15 | 1 / 0 / 0.00 | 2 / 1 / 0.04 |
| 0.5 | 2 / 1 / 0.11 | 1 / 0 / 0.00 | 2 / 1 / 0.11 |
| 0.7 | 2 / 1 / 0.10 | 1 / 0 / 0.00 | 2 / 1 / 0.11 |
| 0.9 | 3 / 2 / 0.14 | 2 / 1 / 0.12 | 1 / 0 / 0.00 |
| 1.1 | 3 / 2 / 0.15 | 3 / 2 / 0.19 | 2 / 1 / 0.11 |
| 1.3 | 1 / 0 / 0.00 | 3 / 2 / 0.15 | 2 / 1 / 0.11 |
| 1.5 | 1 / 0 / 0.00 | 2 / 1 / 0.11 | 1 / 0 / 0.00 |
| 1.7 | 3 / 3 / 0.24 | 3 / 2 / 0.19 | 2 / 1 / 0.08 |
| 1.9 | 3 / 2 / 0.18 | 3 / 3 / **0.28** | 2 / 1 / 0.11 |

#### 실제로 나온 문장 (빈도순 일부)

**A (기본값)**
- 85회: 죽음은 삶을 더욱 소중하고 의미 있게 만드는 유한한 완성입니다.
- 5회: 죽음은 삶을 더욱 소중하고 의미 있게 만드는 유한한 마침표이자, 모든 생명에게 공평하게 주어지는 자연스러운 과정이라고 생각합니다.
- 2회: 죽음은 삶을 더욱 소중하고 의미 있게 만들어주는 유한한 완성입니다.
- 1회: 죽음은 모든 생명에게 피할 수 없는 자연스러운 과정이자, 삶의 유한함을 통해 현재의 순간을 더욱 값지고 의미 있게 만드는 삶의 완성입니다.
- 그 밖에 1회씩 나온 변형 7개

**B (topP 1, topK 1000)**
- 87회: 죽음은 삶을 더욱 소중하고 의미 있게 만드는 유한한 완성입니다.
- 3회: …유한한 완성의 순간입니다.
- 3회: …유한한 마침표이자, 모든 생명에게 공평하게 주어지는 자연스러운 과정이라고 생각합니다.
- 1회 (T=1.3): …유한한 아름다움이자, 모든 생명이 **거el치는** 자연스러운 완성이라고 생각합니다. ← 깨진 출력
- 그 밖에 1회씩 나온 변형 6개

**C (B에 thinking minimal)**
- 93회: 죽음은 삶을 더욱 소중하고 의미 있게 만드는 유한한 완성입니다.
- 4회: …유한한 마침표이자, 모든 생명에게 공평하게 주어지는 자연스러운 과정이라고 생각합니다.
- 1회: …만들어주는 유한한 완성입니다.
- 1회: …유한한 완성이자, 자연의 섭리라고 생각합니다.
- 1회: …유한한 완성이자, 모든 생명에게 공평하게 주어지는 자연스러운 순환 과정입니다.

---

## 6. 해석

1. **기본 설정에서는 temperature 효과가 보이지 않습니다.** A에서 다른 답은 낮은 온도 8회, 높은 온도 7회로 비슷했습니다. topP와 topK가 후보 단어를 먼저 잘라내서, 온도를 올려도 고를 수 있는 단어가 늘지 않는 것으로 보입니다.
2. **샘플링 제한을 풀면 temperature 효과가 약하게 나타납니다.** B에서는 높은 온도의 다른 답이 낮은 온도의 약 3배였고, 단어거리도 T=1.9에서 가장 컸습니다. T=1.3에서 나온 "거el치는" 같은 깨진 출력은 확률이 아주 낮은 단어까지 후보로 열리면서 높은 온도에서 뽑힌 것으로 보입니다. 교과서에서 말하는 temperature 효과가 이 조건에서 처음 관찰됐습니다.
3. **thinking을 줄이면 오히려 더 획일적입니다.** C는 93%가 같은 문장이었고 서로 다른 문장도 5개뿐이었습니다. "thinking이 답을 한 방향으로 모은다"는 처음의 가설과는 반대입니다. 생각하는 과정이 오히려 답을 조금씩 흔드는 역할을 했을 수 있습니다(가설).
4. **달라진 답도 뼈대는 같습니다.** 거의 모든 답이 "죽음은 삶을 더욱 소중하고 의미 있게 만드는 유한한…"으로 시작하고, 뒷부분의 명사만 바뀌었습니다. 튜닝된 모델의 확률 쏠림을 시스템 프롬프트가 더 굳힌 것으로 보입니다.

---

## 7. 한계

- temperature마다 10회라 표본이 작고, 통계 검정은 하지 않았습니다. A/B/C 사이의 차이는 경향으로만 봐야 합니다.
- 모델 하나(`gemini-3.5-flash-lite`), 질문 하나만 테스트했습니다.
- `topK: 1000`이 서버에서 실제로 적용됐는지 확인할 수 없었습니다.
- logprobs를 지원하지 않아 확률 분포를 직접 관찰하지 못했습니다.
- 단어거리는 띄어쓰기 기준이라 한국어 형태소 차이("만드는" ↔ "만들어주는")를 따로 구분하지 못합니다.

---

## 8. 다음 단계 제안

1. **시스템 프롬프트 없이** 같은 A/B 조건으로 실험합니다. 형식 제약을 빼면 temperature 효과가 더 커지는지 확인할 수 있습니다.
2. **반복 횟수를 30회 이상**으로 늘리고, 조건 사이의 차이를 통계로 검정합니다.
3. **seed 고정 실험**: seed를 하나로 고정하고 temperature만 바꿔 효과를 분리합니다.
4. **의미 수준 지표**: 임베딩 거리나 형태소 단위 distinct-n으로 다양성을 다시 측정합니다.
5. **원시 모델과 비교**: Gemma나 Llama의 base 모델을 로컬이나 Hugging Face에서 돌려 튜닝된 모델과 나란히 비교합니다. 목록에 있는 `gemma-4-*-it`도 튜닝된 모델(it)입니다.
6. **logprobs를 지원하는 모델**을 찾아 단어별 확률 분포를 직접 기록합니다.

---

## 부록 A. 스크립트 전문 (`gemini_temperature_test.py`)

```python
"""Gemini temperature 테스트 (Google AI Studio API 키 사용, 표준 라이브러리만 사용)

사용법:
  export GEMINI_API_KEY="AI Studio에서 받은 키"
  python3 gemini_temperature_test.py --list                 # 사용 가능한 모델 목록
  python3 gemini_temperature_test.py --model gemini-3.5-flash-lite
  python3 gemini_temperature_test.py --model gemini-3.5-flash-lite --temps 0 1 2 --runs 3
"""
import argparse
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request

BASE = "https://generativelanguage.googleapis.com/v1beta"

# 같은 폴더의 .env 파일(GEMINI_API_KEY=...)이 있으면 읽어온다
_env = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(_env):
    with open(_env, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_RPM = 0  # 0이면 제한 없음
_lock = threading.Lock()
_last = [0.0]


def _throttle():
    if _RPM <= 0:
        return
    with _lock:
        wait = _last[0] + 60.0 / _RPM - time.time()
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()


def request(method, path, body=None, retries=6):
    for attempt in range(retries + 1):
        _throttle()
        res = _request_once(method, path, body)
        if res.get("_error") != 429 or attempt == retries:
            return res
        m = re.search(r"retry in ([0-9.]+)s", res.get("_body", ""))
        time.sleep(float(m.group(1)) + 1 if m else 15)
    return res


def _request_once(method, path, body=None):
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        sys.exit("GEMINI_API_KEY 환경변수를 먼저 설정하세요.")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{BASE}/{path}",
        data=data,
        method=method,
        headers={"x-goog-api-key": key, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        return {"_error": e.code, "_body": e.read().decode(errors="replace")}
    except (TimeoutError, urllib.error.URLError) as e:
        return {"_error": "timeout/network", "_body": str(e)}


def list_models():
    res = request("GET", "models?pageSize=200")
    if "_error" in res:
        sys.exit(f"HTTP {res['_error']}: {res['_body']}")
    for m in res.get("models", []):
        if "generateContent" in m.get("supportedGenerationMethods", []):
            print(f"{m['name'].removeprefix('models/'):45} "
                  f"temp={m.get('temperature', '-')} maxTemp={m.get('maxTemperature', '-')}")


def generate(model, prompt, temperature, max_tokens=256, system=None,
             top_p=None, top_k=None, thinking_level=None, seed=None):
    gc = {"temperature": temperature, "maxOutputTokens": max_tokens}
    if top_p is not None:
        gc["topP"] = top_p
    if top_k is not None:
        gc["topK"] = top_k
    if seed is not None:
        gc["seed"] = seed
    if thinking_level:
        gc["thinkingConfig"] = {"thinkingLevel": thinking_level}
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": gc}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    res = request("POST", f"models/{model}:generateContent", body)
    if "_error" in res:
        return f"[HTTP {res['_error']}] {res['_body'][:300]}"
    try:
        parts = res["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
    except (KeyError, IndexError):
        return f"[응답 파싱 실패] {json.dumps(res, ensure_ascii=False)[:300]}"


def diversity(outs):
    """서로 다른 답 개수, 답 사이 평균 단어 Jaccard 거리(0=모두 같음, 1=겹치는 단어 없음)"""
    sets = [set(o.split()) for o in outs]
    dists = []
    for i in range(len(sets)):
        for j in range(i + 1, len(sets)):
            u = sets[i] | sets[j]
            dists.append(1 - len(sets[i] & sets[j]) / len(u) if u else 0.0)
    return len(set(outs)), (sum(dists) / len(dists) if dists else 0.0)


def main():
    from concurrent.futures import ThreadPoolExecutor

    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="모델 목록과 기본/최대 temperature 출력")
    ap.add_argument("--model", default="gemini-3.5-flash-lite")
    ap.add_argument("--temps", type=float, nargs="+", default=[0.0, 1.0, 2.0])
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--system", default=None, help="시스템 프롬프트")
    ap.add_argument("--max-tokens", type=int, default=256)
    ap.add_argument("--top-p", type=float, default=None, help="미지정 시 모델 기본값(0.95)")
    ap.add_argument("--top-k", type=int, default=None, help="미지정 시 모델 기본값(64)")
    ap.add_argument("--thinking-level", default=None, help="minimal / low / high 등")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--workers", type=int, default=4, help="동시 요청 수")
    ap.add_argument("--rpm", type=int, default=14, help="분당 최대 요청 수 (무료 등급은 15)")
    ap.add_argument("--summary", action="store_true", help="답 전문 대신 요약 표만 출력")
    ap.add_argument("--out", default=None, help="결과를 JSON 파일로 저장")
    ap.add_argument("--prompt", default="새로 연 동네 빵집 이름을 하나만 지어줘. 이름만 답해.")
    a = ap.parse_args()

    if a.list:
        list_models()
        return
    global _RPM
    _RPM = a.rpm

    print(f"모델: {a.model} | topP={a.top_p} topK={a.top_k} thinking={a.thinking_level} seed={a.seed}")
    print(f"시스템: {a.system}\n프롬프트: {a.prompt}\n")
    results = []
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        for t in a.temps:
            outs = list(ex.map(lambda _: generate(a.model, a.prompt, t, a.max_tokens, a.system,
                                                  a.top_p, a.top_k, a.thinking_level, a.seed),
                               range(a.runs)))
            uniq, dist = diversity(outs)
            errs = sum(o.startswith("[") for o in outs)
            avg_len = sum(len(o) for o in outs) / len(outs)
            results.append({"temperature": t, "unique": uniq, "runs": a.runs, "jaccard": dist,
                            "avg_len": avg_len, "errors": errs, "outputs": outs})
            if a.summary:
                print(f"T={t:<4} 서로다른답 {uniq:>2}/{a.runs}  단어거리 {dist:.2f}  "
                      f"평균길이 {avg_len:5.0f}자  에러 {errs}", flush=True)
            else:
                print(f"--- temperature={t}  (서로 다른 답 {uniq}/{a.runs}개, 단어거리 {dist:.2f})")
                for i, o in enumerate(outs, 1):
                    print(f"  {i}. {o}")
                print(flush=True)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump({"args": vars(a), "results": results}, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
```

## 부록 B. 본 실험 재현 명령

```bash
COMMON=(--temps 0.1 0.3 0.5 0.7 0.9 1.1 1.3 1.5 1.7 1.9 --runs 10 --max-tokens 1024 --summary --rpm 14 --system "한 문장으로 정의" --prompt "죽음에 대해 어떻게 생각해?")
python3 gemini_temperature_test.py "${COMMON[@]}" --out exp_A.json
python3 gemini_temperature_test.py "${COMMON[@]}" --top-p 1 --top-k 1000 --out exp_B.json
python3 gemini_temperature_test.py "${COMMON[@]}" --top-p 1 --top-k 1000 --thinking-level minimal --out exp_C.json
```

분당 14회로 제한하면 조건 하나(100회)에 약 7~8분, 세 조건 합쳐 약 22분 걸립니다.
