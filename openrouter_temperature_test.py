"""OpenRouter temperature 테스트 (표준 라이브러리만 사용)

gemini_temperature_test.py 와 같은 질문·지표로, OpenRouter의 여러 모델을 측정한다.
같은 폴더 .env 의 OPENROUTER_API_KEY 를 읽는다.

측정:
  python3 openrouter_temperature_test.py --model qwen/qwen3.5-9b --provider DeepInfra \\
    --temps 0.1 0.3 0.5 0.7 0.9 1.1 1.3 1.5 1.7 1.9 --runs 10 \\
    --system "한 문장으로 정의" --prompt "죽음에 대해 어떻게 생각해?" \\
    --top-p 1 --top-k 0 --min-p 0 --no-reasoning --summary --out results/qwen_B.json

분석 (여러 결과 파일을 표로):
  python3 openrouter_temperature_test.py --analyze results/*.json --report 결과.md
"""
import argparse
import itertools
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import Counter

URL = "https://openrouter.ai/api/v1/chat/completions"

_env = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(_env):
    with open(_env, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

# 한글·숫자·문장부호 이외의 '다른 언어 문자' (라틴 문자는 외래어 표기로 정상일 수 있어 제외)
FOREIGN = re.compile(r"[一-鿿぀-ヿ฀-๿Ѐ-ӿ؀-ۿऀ-ॿ]")


def call(body, retries=5):
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        sys.exit("OPENROUTER_API_KEY 를 .env 에 넣으세요.")
    data = json.dumps(body).encode()
    for attempt in range(retries + 1):
        req = urllib.request.Request(URL, data=data, headers={
            "Authorization": f"Bearer {key}", "Content-Type": "application/json",
            "X-Title": "temperature-experiment"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            msg = e.read().decode(errors="replace")[:300]
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(2 * (attempt + 1))
                continue
            return {"_error": e.code, "_body": msg}
        except Exception as e:  # 시간 초과·네트워크
            if attempt < retries:
                time.sleep(2 * (attempt + 1))
                continue
            return {"_error": "network", "_body": str(e)}


def generate(a, t):
    msgs = ([{"role": "system", "content": a.system}] if a.system else []) + [{"role": "user", "content": a.prompt}]
    body = {"model": a.model, "messages": msgs, "temperature": t, "max_tokens": a.max_tokens,
            "usage": {"include": True}}
    for k, v in (("top_p", a.top_p), ("top_k", a.top_k), ("min_p", a.min_p), ("seed", a.seed)):
        if v is not None:
            body[k] = v
    if a.no_reasoning:
        body["reasoning"] = {"enabled": False}
    prov = {"require_parameters": True}
    if a.provider:
        prov.update(order=[a.provider], allow_fallbacks=False)
    body["provider"] = prov
    r = call(body)
    if "_error" in r:
        return {"text": None, "error": f"{r['_error']} {r['_body']}"}
    ch = r["choices"][0]
    return {"text": (ch["message"].get("content") or "").strip(), "error": None,
            "finish": ch.get("finish_reason"), "provider": r.get("provider"),
            "cost": (r.get("usage") or {}).get("cost")}


def norm(s):
    return re.sub(r"\s+", " ", s).strip()


def jaccard_dist(texts, unit):
    sets = []
    for t in texts:
        if unit == "word":
            sets.append(set(t.split()))
        else:   # 글자 2-gram (공백 제거) — "만드는/만들어주는" 같은 어미 차이를 단어 통째로 다르게 보지 않는다
            s = re.sub(r"\s", "", t)
            sets.append({s[i:i + 2] for i in range(len(s) - 1)})
    ds = [1 - len(x & y) / max(len(x | y), 1) for x, y in itertools.combinations(sets, 2)]
    return sum(ds) / len(ds) if ds else 0.0


def stats(texts, modal=None):
    texts = [norm(t) for t in texts if t]
    c = Counter(texts)
    return {
        "n": len(texts),
        "unique": len(c),
        "non_modal": sum(v for k, v in c.items() if k != modal) if modal else None,
        "word_dist": jaccard_dist(texts, "word"),
        "char_dist": jaccard_dist(texts, "char"),
        "foreign": sum(bool(FOREIGN.search(t)) for t in texts),
        "avg_len": sum(len(t) for t in texts) / max(len(texts), 1),
    }


def measure(a):
    from concurrent.futures import ThreadPoolExecutor
    print(f"모델: {a.model} | 업체: {a.provider or '자동'} | top_p={a.top_p} top_k={a.top_k} "
          f"min_p={a.min_p} reasoning={'off' if a.no_reasoning else '기본'}")
    print(f"시스템: {a.system}\n프롬프트: {a.prompt}\n")
    results = []
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        for t in a.temps:
            outs = list(ex.map(lambda _: generate(a, t), range(a.runs)))
            texts = [o["text"] for o in outs if o["text"]]
            s = stats(texts)
            errs = [o["error"] for o in outs if o["error"]]
            results.append({"temperature": t, "outputs": outs, **s, "errors": len(errs)})
            if a.summary:
                print(f"T={t:<4} 서로다른답 {s['unique']:>2}/{len(texts)}  단어거리 {s['word_dist']:.2f}  "
                      f"글자거리 {s['char_dist']:.2f}  외국문자 {s['foreign']}  에러 {len(errs)}"
                      + (f"  ({errs[0][:120]})" if errs else ""), flush=True)
            else:
                print(f"--- T={t}")
                for o in outs:
                    print("  -", o["text"] if o["text"] else f"[에러] {o['error']}")
    cost = sum((o.get("cost") or 0) for r in results for o in r["outputs"])
    provs = Counter(o.get("provider") for r in results for o in r["outputs"] if o.get("provider"))
    print(f"\n비용 ${cost:.4f} | 업체 {dict(provs)}")
    if a.out:
        os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump({"args": vars(a), "results": results, "cost": cost, "providers": dict(provs)},
                      f, ensure_ascii=False, indent=2)


def analyze(files, report):
    """조건별 요약 표와 temperature별 상세 표를 만든다 (Gemini 실험 문서와 같은 형식)."""
    lines = []
    summary = ["| 실행 | 가장 많은 문장 비율 | 서로 다른 문장 (전체 중) | 다른 답 낮은/높은 온도 | 단어거리 낮은→높은 | 글자거리 낮은→높은 | 외국문자 섞임 낮은/높은 | 에러 |",
               "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    detail = {}
    for f in files:
        d = json.load(open(f, encoding="utf-8"))
        name = os.path.splitext(os.path.basename(f))[0]
        res = d["results"]
        alltexts = [norm(o["text"]) for r in res for o in r["outputs"] if o["text"]]
        modal, mc = Counter(alltexts).most_common(1)[0] if alltexts else (None, 0)
        lo = [r for r in res if r["temperature"] < 1.0]
        hi = [r for r in res if r["temperature"] >= 1.0]

        def agg(rs, key):
            return sum(r[key] for r in rs) / max(len(rs), 1)

        def nonmodal(rs):
            return sum(norm(o["text"]) != modal for r in rs for o in r["outputs"] if o["text"])

        def foreign(rs):
            return sum(r["foreign"] for r in rs)
        errs = sum(r["errors"] for r in res)
        summary.append(f"| {name} | {mc / max(len(alltexts), 1):.0%} | {len(set(alltexts))} / {len(alltexts)} | "
                       f"{nonmodal(lo)} / {nonmodal(hi)} | {agg(lo, 'word_dist'):.2f} → {agg(hi, 'word_dist'):.2f} | "
                       f"{agg(lo, 'char_dist'):.2f} → {agg(hi, 'char_dist'):.2f} | {foreign(lo)} / {foreign(hi)} | {errs} |")
        for r in res:
            detail.setdefault(r["temperature"], {})[name] = r
        lines.append((name, modal, mc, len(alltexts), d.get("cost", 0)))
    names = [os.path.splitext(os.path.basename(f))[0] for f in files]
    out = ["## 조건별 요약", "", "낮은 온도 = 0.1~0.9, 높은 온도 = 1.1~1.9. 거리는 각 온도 안에서 답 두 개씩 짝지은 평균.", ""] + summary
    out += ["", "## temperature별 상세", "", "각 칸: `서로 다른 답 수 / 단어거리 / 글자거리`", "",
            "| T | " + " | ".join(names) + " |", "|" + " --- |" * (len(names) + 1)]
    for t in sorted(detail):
        cells = []
        for n in names:
            r = detail[t].get(n)
            cells.append(f"{r['unique']} / {r['word_dist']:.2f} / {r['char_dist']:.2f}" if r else "-")
        out.append(f"| {t} | " + " | ".join(cells) + " |")
    out += ["", "## 가장 많이 나온 문장", ""]
    for name, modal, mc, n, cost in lines:
        out.append(f"- **{name}** ({mc}/{n}회, 비용 ${cost:.4f}): {modal}")
    text = "\n".join(out) + "\n"
    if report:
        with open(report, "w", encoding="utf-8") as f:
            f.write(text)
    print(text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen/qwen3.5-9b")
    ap.add_argument("--provider", default=None, help="업체 고정 (예: DeepInfra). 미지정 시 OpenRouter가 고름")
    ap.add_argument("--temps", type=float, nargs="+", default=[0.1, 1.0, 1.9])
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--system", default=None)
    ap.add_argument("--prompt", default="죽음에 대해 어떻게 생각해?")
    ap.add_argument("--max-tokens", type=int, default=400)
    ap.add_argument("--top-p", type=float, default=None)
    ap.add_argument("--top-k", type=int, default=None, help="0이면 끔")
    ap.add_argument("--min-p", type=float, default=None)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--no-reasoning", action="store_true", help="생각 단계 끄기 (지원 모델만)")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--out", default=None)
    ap.add_argument("--analyze", nargs="+", default=None, help="결과 JSON 파일들을 표로 분석")
    ap.add_argument("--report", default=None, help="분석 표를 저장할 .md")
    a = ap.parse_args()
    if a.analyze:
        analyze(a.analyze, a.report)
    else:
        measure(a)


if __name__ == "__main__":
    main()
