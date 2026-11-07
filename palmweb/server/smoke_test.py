"""서버 점검: 샘플 사진을 프레임처럼 반복해 보내 추적 → 자동 캡처 → 손금 → 해석 10단계를 확인한다.

    uv run python -m server.smoke_test ../palmlines/samples/hand70.jpg [ws://localhost:8000/ws/track]
"""
import asyncio
import json
import sys
import time

import cv2
import websockets


async def wait_interp(ws, t_cap: float) -> int:
    """캡처 뒤 해석 메시지를 interp_done 까지 받는다."""
    while True:
        m = json.loads(await asyncio.wait_for(ws.recv(), timeout=90))
        dt = time.time() - t_cap
        if m["type"] == "interp_start":
            print(f"+{dt:4.1f}s 묘사: {m['description'][:90]}...")
        elif m["type"] == "interp":
            tag = f" (빌림←{m['borrowed_from']})" if m.get("borrowed_from") else ""
            print(f"+{dt:4.1f}s [{m['step']:2d}] T={m['temperature']} min_p={m['min_p']} 외국{m['foreign_ratio']:.2f}{tag} {m['text'][:60]}")
        elif m["type"] == "interp_error":
            print("INTERP ERROR", m["message"])
        elif m["type"] == "interp_done":
            print(f"+{dt:4.1f}s 완료 실패단계={m['failed']}")
            return 0 if len(m["failed"]) < 10 else 1


async def main(img_path: str, url: str):
    img = cv2.imread(img_path)
    s = 1280 / max(img.shape[:2])
    img = cv2.resize(img, None, fx=s, fy=s) if s < 1 else img
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 90])
    data = buf.tobytes()
    async with websockets.connect(url, max_size=8 * 2**20) as ws:
        t0 = time.time()
        n = 0
        while time.time() - t0 < 30:
            await ws.send(data)
            n += 1
            while True:
                m = json.loads(await ws.recv())
                if m["type"] == "track":
                    if n % 5 == 1:
                        print(f"frame {n:3d} hand={'O' if m['hand'] else 'X'} progress={m['progress']:.2f} {m['message']}")
                    break
                if m["type"] == "capturing":
                    print("capturing...")
                    continue
                if m["type"] == "capture":
                    kinds = [(l["kind"], len(l["points"])) for l in m["palm"]["lines"]]
                    print(f"CAPTURE {m['session']} {m['ms']}ms frames={m['frames']} outline={m['outline']} lines={kinds}")
                    return await wait_interp(ws, time.time())
                if m["type"] == "error":
                    print("ERROR", m["message"])
                    return 1
        print("시간 초과: 캡처되지 않음")
        return 1


if __name__ == "__main__":
    url = sys.argv[2] if len(sys.argv) > 2 else "ws://localhost:8000/ws/track"
    sys.exit(asyncio.run(main(sys.argv[1], url)))
