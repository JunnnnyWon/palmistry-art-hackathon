"""예술 캡차 합성 그림 사전 생성: 고정 9장(manifest.round)에서 고를 수 있는 모든 조합(2^9-1 = 511가지)을 하나의 그림으로 합쳐 둔다.

    uv run python tools/gen_captcha_merge.py                 # 아직 없는 조합 전부 (web/public/captcha/merge/<mask>.jpg)
    uv run python tools/gen_captcha_merge.py 101000100 000000001   # 특정 조합만 다시
    MERGE_LIMIT=3 uv run python tools/gen_captcha_merge.py   # 앞 3개만 (품질 확인용)
    MERGE_MODEL=black-forest-labs/flux-3-image-klein MERGE_WORKERS=4 uv run python tools/gen_captcha_merge.py

mask: manifest.round 순서대로 고른 칸 1, 아닌 칸 0 (9자리). 화면(web/src/captcha.ts)이 같은 규칙으로 /captcha/merge/<mask>.jpg 를 연다.
OpenRouter 이미지 엔드포인트(/api/v1/images)에 고른 장들을 참조 이미지(input_references)로 넣는다. 키는 저장소 최상위 .env 의 OPENROUTER_API_KEY.
기본 모델 bytedance-seed/seedream-5-0-flash (한 장 약 0.018달러, 511장 ≈ 9달러). 공급자가 내용 검열로 거부하면 FALLBACK 모델로 한 번 더 시도한다.
결과는 1024px jpg 로 줄여 저장하고, merge/_log.jsonl 에 mask·모델·비용·걸린 시간을 남긴다.
"""
import base64, json, os, sys, time, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
CAPTCHA = ROOT / "web" / "public" / "captcha"
OUT = CAPTCHA / "merge"
MODEL = os.environ.get("MERGE_MODEL") or "bytedance-seed/seedream-5-0-flash"
FALLBACK = os.environ.get("MERGE_FALLBACK") or "black-forest-labs/flux-3-image-klein"
WORKERS = int(os.environ.get("MERGE_WORKERS") or 5)
LIMIT = int(os.environ.get("MERGE_LIMIT") or 0)
# 피·혈관 같은 단어는 검열에 걸리기 쉬워 쓰지 않는다
PROMPT = ("Merge every reference image into one single hybrid hand, as if their features were bred together in a GAN latent space: "
          "an uncanny amalgam in which the shapes, colors, materials and textures of all references dissolve into one another, "
          "so that no single reference stays recognizable on its own. One hand-like form, centered and filling the frame, "
          "on a pure black background. Painterly oil-on-canvas rendering with visible brushstrokes and impasto. Square format, no text.")


def api_key() -> str:
    env = ROOT.parent / ".env"
    for l in env.read_text().splitlines():
        if l.startswith("OPENROUTER_API_KEY"):
            return l.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit(f"OPENROUTER_API_KEY 가 {env} 에 없습니다")


KEY = api_key()
MANIFEST = json.loads((CAPTCHA / "manifest.json").read_text())
ROUND: list[str] = MANIFEST["round"]
assert len(ROUND) == 9, ROUND

_ref_cache: dict[str, str] = {}


def ref_data_url(file: str) -> str:
    """참조 이미지는 512px jpg 로 줄여 보낸다 (요청 크기 절약, 품질엔 충분)."""
    if file not in _ref_cache:
        im = cv2.imread(str(CAPTCHA / file))
        im = cv2.resize(im, (512, 512), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", im, [cv2.IMWRITE_JPEG_QUALITY, 85])
        _ref_cache[file] = "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode()
    return _ref_cache[file]


def call(model: str, files: list[str]):
    body = {"model": model, "prompt": PROMPT, "aspect_ratio": "1:1", "resolution": "1K", "n": 1,
            "input_references": [{"type": "image_url", "image_url": {"url": ref_data_url(f)}} for f in files]}
    req = urllib.request.Request("https://openrouter.ai/api/v1/images", data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
    r = json.load(urllib.request.urlopen(req, timeout=180))
    data = r.get("data") or []
    if not data:
        raise RuntimeError(f"빈 응답: {str(r)[:200]}")
    return base64.b64decode(data[0]["b64_json"]), r.get("usage", {}).get("cost")


def generate(mask: str):
    files = [f for f, b in zip(ROUND, mask) if b == "1"]
    out = OUT / f"{mask}.jpg"
    t0 = time.time()
    last = None
    for attempt, model in enumerate([MODEL, MODEL, FALLBACK, FALLBACK]):
        try:
            raw, cost = call(model, files)
            im = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
            if im is None:
                raise RuntimeError("이미지 디코드 실패")
            if max(im.shape[:2]) > 1024:
                im = cv2.resize(im, (1024, 1024), interpolation=cv2.INTER_AREA)
            cv2.imwrite(str(out), im, [cv2.IMWRITE_JPEG_QUALITY, 88])
            rec = {"mask": mask, "files": files, "model": model, "cost": cost, "s": round(time.time() - t0, 1), "at": time.time()}
            with (OUT / "_log.jsonl").open("a") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            return rec
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code} {e.read().decode()[:160]}"
        except Exception as e:  # noqa: BLE001
            last = repr(e)[:160]
        time.sleep(2 + attempt * 3)
    rec = {"mask": mask, "files": files, "error": last, "s": round(time.time() - t0, 1), "at": time.time()}
    with (OUT / "_log.jsonl").open("a") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    args = [a for a in sys.argv[1:] if len(a) == 9 and set(a) <= {"0", "1"} and "1" in a]
    masks = args or [format(n, "09b") for n in range(1, 512) if not (OUT / f"{format(n, '09b')}.jpg").exists()]
    if LIMIT:
        masks = masks[:LIMIT]
    print(f"{len(masks)}장 생성 · 모델 {MODEL} (대체 {FALLBACK}) · 동시 {WORKERS}", flush=True)
    total = 0.0
    fails = []
    with ThreadPoolExecutor(WORKERS) as ex:
        for i, rec in enumerate(ex.map(generate, masks), 1):
            if "error" in rec:
                fails.append(rec["mask"])
                print(f"[{i}/{len(masks)}] {rec['mask']} 실패: {rec['error']}", flush=True)
            else:
                total += rec.get("cost") or 0
                print(f"[{i}/{len(masks)}] {rec['mask']} {rec['model'].split('/')[-1]} {rec['s']}s ${rec.get('cost') or 0:.3f} (누적 ${total:.2f})", flush=True)
    print(f"끝. 성공 {len(masks) - len(fails)} · 실패 {len(fails)} · 비용 ${total:.2f}")
    if fails:
        print("실패한 조합 (다시 실행하면 이것만 생성):", " ".join(fails))


if __name__ == "__main__":
    main()
