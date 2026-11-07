"""구글 슬라이드 라이브: 대시보드를 몇 초마다 스크린샷으로 찍어 발표 자료의 한 쪽(기본 6쪽)을 꽉 채운 이미지로 갈아 끼운다.

구글 슬라이드에는 웹페이지를 넣을 수 없어서, 발표 모드가 문서 변경을 따라 그리는 점을 이용한다 (3~5초 지연, 소리·클릭 없음).

    uv run --group live python tools/slides_live.py --list              # 그 쪽의 요소 목록 (어느 이미지가 바뀔지 확인)
    uv run --group live python tools/slides_live.py --once              # 한 번만 찍어 교체 (시험)
    uv run --group live python tools/slides_live.py                     # 계속 (Ctrl-C 로 끝). 서버에선 setsid nohup … &

    환경: SLIDES_DECK (발표 자료 ID, 기본은 7조 전시 발표), SLIDES_PAGE (쪽 번호 1부터, 기본 6), SLIDES_INTERVAL (확인 사이 쉬는 초, 기본 0.25),
          SLIDES_SIZE (스크린샷 크기, 기본 1600x900), SLIDES_QUALITY (JPEG 품질, 기본 80),
          SLIDES_FORCE (화면이 안 바뀌어도 이 초마다 한 번은 보냄, 기본 30).
    부하: 스크린샷은 매번 찍지만(서버 CPU 0.5%), 구글 API 는 화면이 바뀌었을 때만 부른다(작은 회색 그림의 평균 차이 < 1 이면 같은 화면).
          대시보드는 새 기록이 올 때만 바뀌므로 실제 호출은 분당 몇 번이고, 쓰기 한도(계정당 분당 60회)에 한참 못 미친다.
          SLIDES_SA (서비스 계정 키 JSON, 기본 <저장소>/.google-sa.json), SLIDES_PUBLIC (스크린샷 공개 주소, 기본 https://hackathon.junnnny.kr),
          SLIDES_DASH (찍을 대시보드 주소, 기본 http://localhost:8000/dashboard?shot=1)

준비 (한 번): 구글 클라우드 프로젝트 → Google Slides API 사용 설정 → 서비스 계정 만들기 → 키(JSON) 내려받아 SLIDES_SA 자리에 두기
             → 발표 자료를 그 서비스 계정 이메일(…@….iam.gserviceaccount.com)과 "편집자"로 공유.
흐름: Playwright(헤드리스 크롬)로 대시보드를 1600×900(SLIDES_SIZE)으로 찍어 web/public/live/dash-<시각>.jpg 에 쓰고(서버가 /live/ 로 서빙),
      처음에 그 쪽에 쪽 크기(가로·세로 전체)만 한 이미지를 하나 만든 뒤(이미 라이브 이미지가 있으면 그것을 쓴다), Slides API replaceImage 로
      그 이미지를 새 주소로 바꾼다(요소 ID·크기는 교체 뒤에도 유지). 구글이 주소에서 그림을 가져가므로 공개 주소여야 한다. 파일은 60초가 지나면 지운다.
"""
import argparse, asyncio, os, sys, time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DECK = os.environ.get("SLIDES_DECK") or "1b_ZfQEHd-aXecGFkSGa-GBDbVExUDER5ySI7PLI7ogY"
PAGE = int(os.environ.get("SLIDES_PAGE") or 6)
INTERVAL = max(0.2, float(os.environ.get("SLIDES_INTERVAL") or 0.25))  # 화면 확인 간격 (스크린샷 0.1초)
SIZE = tuple(int(v) for v in (os.environ.get("SLIDES_SIZE") or "1600x900").split("x"))   # 스크린샷 크기. 작을수록 구글이 빨리 받아간다 (1920×1080 2.1초, 1600×900 1.6초, 1280×720 1.4초)
QUALITY = int(os.environ.get("SLIDES_QUALITY") or 80)
SA = Path(os.environ.get("SLIDES_SA") or (ROOT.parent / ".google-sa.json"))
PUBLIC = (os.environ.get("SLIDES_PUBLIC") or "https://hackathon.junnnny.kr").rstrip("/")
DASH = os.environ.get("SLIDES_DASH") or "http://localhost:8000/dashboard?shot=1"
LIVE = ROOT / "web" / "public" / "live"
KEEP_S = 60
FORCE_S = float(os.environ.get("SLIDES_FORCE") or 30)    # 화면이 안 바뀌어도 이만큼 지나면 한 번은 보낸다 (놓친 교체 복구용)
DIFF_MIN = 1.0                                           # 작은 회색 그림의 평균 차이가 이보다 작으면 "같은 화면" (0~255)


def thumb(path: Path):
    """비교용 작은 회색 그림 (64×36)."""
    im = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    return None if im is None else cv2.resize(im, (64, 36), interpolation=cv2.INTER_AREA).astype(np.float32)


def slides_service():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    if not SA.exists():
        sys.exit(f"서비스 계정 키가 없습니다: {SA}\n(구글 클라우드 → 서비스 계정 → 키 JSON 을 내려받아 이 자리에 두고, 발표 자료를 그 계정과 편집자로 공유)")
    creds = service_account.Credentials.from_service_account_file(str(SA), scopes=["https://www.googleapis.com/auth/presentations"])
    return build("slides", "v1", credentials=creds, cache_discovery=False)


def page_elements(svc):
    """(쪽 ID, 요소 목록, 쪽 크기 EMU)"""
    pres = svc.presentations().get(presentationId=DECK).execute()
    slides = pres.get("slides", [])
    if not 1 <= PAGE <= len(slides):
        sys.exit(f"{PAGE}쪽이 없습니다 (총 {len(slides)}쪽)")
    slide = slides[PAGE - 1]
    ps = pres.get("pageSize", {})
    page_w = ps.get("width", {}).get("magnitude", 9144000); page_h = ps.get("height", {}).get("magnitude", 5143500)
    out = []
    for el in slide.get("pageElements", []):
        size = el.get("size", {}); w = size.get("width", {}).get("magnitude", 0); h = size.get("height", {}).get("magnitude", 0)
        kind = "image" if "image" in el else "shape" if "shape" in el else "video" if "video" in el else "other"
        text = ""
        if kind == "shape":
            text = "".join(t.get("textRun", {}).get("content", "") for t in el["shape"].get("text", {}).get("textElements", [])).strip()[:40]
        out.append({"id": el["objectId"], "kind": kind, "w": w, "h": h, "text": text, "url": el.get("image", {}).get("contentUrl", "")[:60]})
    return slide["objectId"], out, (page_w, page_h)


def fill_image(svc, slide_id: str, elements, page_size, url: str) -> str:
    """쪽 전체 크기의 라이브 이미지(ID 가 live_ 로 시작)를 쓴다. 없으면 만든다. 다른 요소는 건드리지 않는다."""
    for e in elements:
        if e["kind"] == "image" and e["id"].startswith("live_"):
            return e["id"]
    page_w, page_h = page_size
    new_id = f"live_{int(time.time())}"
    req = {"createImage": {"objectId": new_id, "url": url, "elementProperties": {
        "pageObjectId": slide_id,
        "size": {"width": {"magnitude": page_w, "unit": "EMU"}, "height": {"magnitude": page_h, "unit": "EMU"}},
        "transform": {"scaleX": 1, "scaleY": 1, "translateX": 0, "translateY": 0, "unit": "EMU"}}}}
    svc.presentations().batchUpdate(presentationId=DECK, body={"requests": [req]}).execute()
    return new_id


def replace(svc, image_id: str, url: str, clock_id: str | None = None):
    reqs = [{"replaceImage": {"imageObjectId": image_id, "url": url, "imageReplaceMethod": "CENTER_INSIDE"}}]
    if clock_id:   # 진단용 시계 글 상자(ID 가 livetxt_ 로 시작)가 있으면 시각도 같이 바꾼다 → 발표 모드가 갱신되는지 눈으로 확인
        reqs += [{"deleteText": {"objectId": clock_id, "textRange": {"type": "ALL"}}},
                 {"insertText": {"objectId": clock_id, "text": time.strftime("%H:%M:%S")}}]
    svc.presentations().batchUpdate(presentationId=DECK, body={"requests": reqs}).execute()


def prune():
    now = time.time()
    for f in LIVE.glob("dash-*.jpg"):
        if now - f.stat().st_mtime > KEEP_S:
            try: f.unlink()
            except OSError: pass


async def run(once: bool):
    from playwright.async_api import async_playwright
    LIVE.mkdir(parents=True, exist_ok=True)
    svc = slides_service()
    slide_id, els, page_size = page_elements(svc)
    async with async_playwright() as p:
        b = await p.chromium.launch(args=["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        pg = await b.new_page(viewport={"width": SIZE[0], "height": SIZE[1]})
        await pg.goto(DASH)
        await pg.wait_for_timeout(2500)
        # 처음: 쪽 전체 크기의 라이브 이미지를 확보하고 첫 장을 넣는다
        name = f"dash-{int(time.time() * 1000)}.jpg"
        await pg.screenshot(path=str(LIVE / name), type="jpeg", quality=QUALITY)
        image_id = fill_image(svc, slide_id, els, page_size, f"{PUBLIC}/live/{name}")
        clock_id = next((e["id"] for e in els if e["id"].startswith("livetxt_")), None)
        replace(svc, image_id, f"{PUBLIC}/live/{name}", clock_id)
        print(f"{PAGE}쪽({slide_id}) 쪽 전체 이미지 {image_id} · {INTERVAL}초마다 확인, 화면이 바뀌었을 때만 교체 (최소 {FORCE_S:.0f}초에 한 번)"
              + (f" (시계 {clock_id})" if clock_id else ""), flush=True)
        fails = 0
        last = thumb(LIVE / name); last_sent = time.time(); sent = skipped = 0
        while not once:
            await asyncio.sleep(INTERVAL)
            t0 = time.time()
            name = f"dash-{int(t0 * 1000)}.jpg"
            try:
                await pg.screenshot(path=str(LIVE / name), type="jpeg", quality=QUALITY)
                cur = thumb(LIVE / name)
                diff = float(np.abs(cur - last).mean()) if cur is not None and last is not None else 999
                if diff < DIFF_MIN and time.time() - last_sent < FORCE_S:
                    skipped += 1
                    (LIVE / name).unlink(missing_ok=True)   # 같은 화면: API 를 부르지 않는다
                    continue
                replace(svc, image_id, f"{PUBLIC}/live/{name}", clock_id)
                last, last_sent = cur, time.time(); sent += 1; fails = 0
                print(f"{time.strftime('%H:%M:%S')} 교체 {name} 차이 {diff:.1f} ({time.time() - t0:.1f}s) · 보냄 {sent} / 건너뜀 {skipped}", flush=True)
            except Exception as e:  # noqa: BLE001
                fails += 1
                print(f"{time.strftime('%H:%M:%S')} 실패 {fails}: {str(e)[:200]}", flush=True)
                if "429" in str(e) or "quota" in str(e).lower():
                    await asyncio.sleep(10)   # 한도에 걸리면 잠시 쉰다
                if "imageObjectId" in str(e) or "not found" in str(e).lower():
                    slide_id, els, page_size = page_elements(svc)   # 요소가 지워졌으면 다시 만든다
                    image_id = fill_image(svc, slide_id, els, page_size, f"{PUBLIC}/live/{name}")
                if fails >= 3:
                    try: await pg.reload(); await pg.wait_for_timeout(2500)
                    except Exception: pass
            prune()
        await b.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="그 쪽의 요소 목록만")
    ap.add_argument("--once", action="store_true", help="한 번만 찍어 넣고 끝")
    a = ap.parse_args()
    if a.list:
        svc = slides_service()
        sid, els, (pw, ph) = page_elements(svc)
        print(f"{PAGE}쪽 {sid} (쪽 크기 {pw:.0f}×{ph:.0f} EMU)")
        for e in els:
            print(f"  {e['kind']:6s} {e['id']:24s} {e['w']:9.0f}×{e['h']:<9.0f} {e['text'] or e['url']}")
        print("라이브 이미지:", [e["id"] for e in els if e["kind"] == "image" and e["id"].startswith("live_")] or "없음 (시작하면 쪽 전체 이미지를 만든다)")
        return
    asyncio.run(run(a.once))


if __name__ == "__main__":
    main()
