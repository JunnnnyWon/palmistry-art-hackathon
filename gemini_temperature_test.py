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
