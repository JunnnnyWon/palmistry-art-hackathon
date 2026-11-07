"""여러 관객 동시 진행 시험.

    uv run python -m server.multi_test --visitors 6 --url ws://localhost:8000

관객마다 개인 방으로 접속해 캡처 사진 묶음(브라우저 방식)을 동시에 보내고, 해석 10단계를 받은 뒤
자기 단계를 바꾼다. 확인하는 것: 모두 캡처·해석되는가, 시간, 그리고 다른 관객의 단계 변경이 새어 들어오지 않는가.
"""
import argparse
import asyncio
import glob
import json
import random
import struct
import time
from pathlib import Path

import cv2
import websockets

ROOT = Path(__file__).resolve().parents[1]


def pack(jpegs: list[bytes]) -> bytes:
    return b"PALM" + bytes([len(jpegs)]) + b"".join(struct.pack(">I", len(j)) + j for j in jpegs)


def load_images():
    files = sorted(glob.glob(str(ROOT.parent / "palmlines" / "samples" / "*.jpg")))
    files = [f for f in files if "IMG_0367" not in f and "hand70" not in f]   # 손이 화면 밖·경계인 사진 제외
    out = []
    for f in files:
        img = cv2.imread(f)
        s = 1280 / max(img.shape[:2])
        img = cv2.resize(img, None, fx=s, fy=s) if s < 1 else img
        out.append((Path(f).name, cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 90])[1].tobytes()))
    return out


async def visitor(i: int, base: str, name: str, jpg: bytes, start: asyncio.Event):
    r = {"visitor": i, "image": name}
    async with websockets.connect(f"{base}/ws/track", max_size=16 * 2**20) as ws:
        hello = json.loads(await ws.recv())
        r["room"] = hello.get("room")
        await start.wait()
        t0 = time.time()
        await ws.send(pack([jpg] * 5))
        session = None
        my_steps, foreign = [], 0
        sent_step = None
        while True:
            m = json.loads(await asyncio.wait_for(ws.recv(), 120))
            if m["type"] == "capture":
                session = m["session"]
                r["capture_s"] = round(time.time() - t0, 2)
                r["lines"] = sum(1 for l in m["palm"]["lines"] if l["kind"] != "minor")
            elif m["type"] == "interp" and m["step"] == 1 and "step1_s" not in r:
                r["step1_s"] = round(time.time() - t0, 2)
                sent_step = random.randint(2, 10)
                await ws.send(json.dumps({"type": "set_step", "step": sent_step}))
            elif m["type"] == "step":
                if m.get("room") != r["room"] or (m.get("session") and session and m["session"] != session):
                    foreign += 1
                if m.get("source") == "keyboard":
                    my_steps.append(m["step"])
            elif m["type"] == "interp_done":
                r["all_s"] = round(time.time() - t0, 2)
                r["failed"] = m["failed"]
                break
            elif m["type"] in ("error", "interp_error"):
                r["error"] = m["message"]
                if m["type"] == "error":
                    break
        r["step_ok"] = sent_step in my_steps if sent_step else None
        r["foreign_msgs"] = foreign
    return r

async def main(n: int, base: str):
    imgs = load_images()
    start = asyncio.Event()
    tasks = [asyncio.create_task(visitor(i, base, *imgs[i % len(imgs)], start)) for i in range(n)]
    await asyncio.sleep(1.0)
    t0 = time.time()
    start.set()
    res = await asyncio.gather(*tasks, return_exceptions=True)
    for r in res:
        print(r)
    ok = [r for r in res if isinstance(r, dict) and "all_s" in r]
    print(f"\n관객 {n}명 동시: 완료 {len(ok)}/{n}, 전체 {time.time() - t0:.1f}s")
    if ok:
        cap = sorted(r["capture_s"] for r in ok)
        s1 = sorted(r["step1_s"] for r in ok if "step1_s" in r)
        print(f"캡처 {cap[0]}~{cap[-1]}s · 1단계 {s1[0] if s1 else '-'}~{s1[-1] if s1 else '-'}s · "
              f"서로 다른 방 {len({r['room'] for r in ok})}개 · 다른 방 메시지 {sum(r['foreign_msgs'] for r in ok)}건 · "
              f"단계 반영 {sum(1 for r in ok if r['step_ok'])}/{len(ok)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--visitors", type=int, default=6)
    ap.add_argument("--url", default="ws://localhost:8000")
    a = ap.parse_args()
    asyncio.run(main(a.visitors, a.url))
