"""전시 리허설용 부하 시험: 관객이 계속 오는 상황을 정해진 시간 동안 흉내 낸다.

    uv run python -m server.soak_test --minutes 30 --url ws://localhost:8012

한 사이클 = 손 사진을 프레임처럼 보내 자동 캡처 → 해석 10단계 수신 → 그동안 노브가 단계를 돌림
→ 잠시 머문 뒤 다시 찍기(reset). 전시 화면 연결과 노브 연결은 실제처럼 계속 유지한다.

기록: 캡처 지연, 1단계·10단계 도착 시간, 실패 단계, 노브-화면 단계 어긋남, 서버 메모리(RSS).
결과는 logs/soak_<시각>.json 과 화면 요약.
"""
import argparse
import asyncio
import glob
import json
import random
import statistics
import subprocess
import time
from pathlib import Path

import cv2
import websockets

ROOT = Path(__file__).resolve().parents[1]


def frames():
    files = sorted(glob.glob(str(ROOT.parent / "palmlines" / "samples" / "*.jpg")))
    files += sorted(glob.glob(str(ROOT.parent / "palmlines" / "tests" / "testset" / "*" / "frame_0.png")))
    out = []
    for f in files:
        img = cv2.imread(f)
        s = 1280 / max(img.shape[:2])
        img = cv2.resize(img, None, fx=s, fy=s) if s < 1 else img
        out.append((Path(f).name, cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 90])[1].tobytes()))
    return out


def server_rss_mb(port: int) -> float | None:
    try:
        pid = subprocess.run(["lsof", "-t", f"-iTCP:{port}", "-sTCP:LISTEN"], capture_output=True, text=True).stdout.split()[0]
        rss = subprocess.run(["ps", "-o", "rss=", "-p", pid], capture_output=True, text=True).stdout.strip()
        return round(int(rss) / 1024, 1)
    except Exception:
        return None


async def main(minutes: float, base: str):
    port = int(base.rsplit(":", 1)[1])
    imgs = frames()
    deadline = time.time() + minutes * 60
    cycles, errors = [], []
    display_steps: list[int] = []
    async with websockets.connect(f"{base}/ws/track", max_size=16 * 2**20) as disp, \
               websockets.connect(f"{base}/ws/control") as knob:
        knob_log: list[dict] = []

        async def knob_reader():
            async for raw in knob:
                knob_log.append(json.loads(raw))
        knob_task = asyncio.create_task(knob_reader())
        n = 0
        while time.time() < deadline:
            n += 1
            name, data = imgs[n % len(imgs)]
            c = {"cycle": n, "image": name, "t": round(time.time())}
            t0 = time.time()
            session, interp_first, interp_done, failed = None, None, None, None
            mismatches = 0
            last_knob_target = None
            # 1) 캡처될 때까지 프레임 보내기 (최대 20초)
            while session is None and time.time() - t0 < 20:
                await disp.send(data)
                while True:
                    m = json.loads(await asyncio.wait_for(disp.recv(), 30))
                    if m["type"] == "track":
                        break
                    if m["type"] == "capture":
                        session = m["session"]
                        c["capture_s"] = round(time.time() - t0, 2)
                        c["lines"] = [l["kind"] for l in m["palm"]["lines"] if l["kind"] != "minor"]
                        break
                    if m["type"] == "error":
                        errors.append({"cycle": n, "error": m["message"]})
                await asyncio.sleep(1 / 15)
            if session is None:
                c["result"] = "캡처 안 됨"
                cycles.append(c)
                await disp.send(json.dumps({"type": "reset"}))
                continue
            t_cap = time.time()
            # 2) 해석 수신 + 노브 조작 (최대 90초)
            next_knob = time.time() + 2
            while interp_done is None and time.time() - t_cap < 90:
                if time.time() > next_knob:
                    target = random.randint(1, 10)
                    await knob.send(json.dumps({"type": "set_step", "step": target}))
                    last_knob_target = target
                    next_knob = time.time() + random.uniform(1.5, 3.5)
                try:
                    m = json.loads(await asyncio.wait_for(disp.recv(), 0.5))
                except asyncio.TimeoutError:
                    continue
                if m["type"] == "interp" and m["step"] == 1 and interp_first is None:
                    interp_first = round(time.time() - t_cap, 2)
                elif m["type"] == "interp_done":
                    interp_done = round(time.time() - t_cap, 2)
                    failed = m["failed"]
                elif m["type"] == "step" and m.get("source") == "knob":
                    display_steps.append(m["step"])
                    if last_knob_target is not None and m["step"] != last_knob_target:
                        mismatches += 1
                elif m["type"] in ("error", "interp_error"):
                    errors.append({"cycle": n, "error": m.get("message")})
            c.update(step1_s=interp_first, all_s=interp_done, failed=failed, knob_mismatch=mismatches,
                     rss_mb=server_rss_mb(port), result="ok" if interp_done is not None else "해석 시간 초과")
            cycles.append(c)
            print(f"[{n:3d}] {name:28s} 캡처 {c.get('capture_s')}s · 1단계 {interp_first}s · 전체 {interp_done}s · "
                  f"실패 {failed} · 노브 어긋남 {mismatches} · RSS {c['rss_mb']}MB", flush=True)
            # 3) 관객이 잠시 머문 뒤 다시 찍기
            await asyncio.sleep(random.uniform(3, 8))
            await disp.send(json.dumps({"type": "reset"}))
            await asyncio.sleep(1)
        knob_task.cancel()

    ok = [c for c in cycles if c.get("result") == "ok"]
    rss = [c["rss_mb"] for c in cycles if c.get("rss_mb")]
    summary = {
        "minutes": minutes, "cycles": len(cycles), "ok": len(ok),
        "capture_s_median": statistics.median([c["capture_s"] for c in ok]) if ok else None,
        "step1_s_median": statistics.median([c["step1_s"] for c in ok if c["step1_s"] is not None]) if ok else None,
        "step1_s_max": max([c["step1_s"] for c in ok if c["step1_s"] is not None], default=None),
        "all_s_median": statistics.median([c["all_s"] for c in ok]) if ok else None,
        "all_s_max": max([c["all_s"] for c in ok], default=None),
        "cycles_with_failed_steps": sum(1 for c in ok if c["failed"]),
        "knob_mismatches": sum(c.get("knob_mismatch", 0) for c in cycles),
        "rss_mb_first_last_max": [rss[0], rss[-1], max(rss)] if rss else None,
        "errors": errors[:20], "error_count": len(errors),
    }
    out = ROOT / "logs" / f"soak_{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"summary": summary, "cycles": cycles}, ensure_ascii=False, indent=1))
    print("\n요약", json.dumps(summary, ensure_ascii=False, indent=1))
    print("저장:", out)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=30)
    ap.add_argument("--url", default="ws://localhost:8012")
    a = ap.parse_args()
    asyncio.run(main(a.minutes, a.url))
