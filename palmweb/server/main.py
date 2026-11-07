"""손금 웹 체험 서버.

    cd palmweb && uv run uvicorn server.main:app --port 8000

여러 관객이 각자 진행한다. 단계 상태는 "방(room)" 단위로 나뉜다.
- 전시장: 전시 화면은 /?room=exhibit 으로 열고, 노브(라즈베리파이)는 /ws/control 로 붙는다 (room 없으면 exhibit).
- 일반 관객: room 없이 접속하면 연결마다 개인 방이 생긴다. 다른 관객과 단계가 섞이지 않는다.

- GET  /api/health      서버·모델 상태, 방·접속 수
- WS   /ws/track        전시 화면. 두 가지 입력을 받는다.
                        1) 캡처 묶음 (브라우저가 손을 직접 추적하고, 캡처 순간의 사진 몇 장만 보냄)
                           바이너리 = b"PALM" + 사진 수(1바이트) + [길이(4바이트 빅엔디언) + JPEG] × 사진 수
                        2) 프레임 하나씩 (예전 방식: 서버가 손을 추적. 점검 스크립트용)
                        → 손금(PalmLineSet JSON) → 해석 10단계를 도착하는 대로 보낸다 (interp_start / interp / interp_done)
- WS   /ws/control      노브: 해석 단계 1~10 조절. 같은 방의 노브와 전시 화면 모두에 알린다 (type: step)
- GET  /knob            노브 화면 (palmweb/knob/index.html). 방을 지정하지 않으면 exhibit
- GET  /r{번호}         전시 화면 → /?room=r{번호}&controls=0 으로 보낸다 (예: /r1)
- GET  /n{번호}         그 전시의 노브 화면 (예: /n1, 아이패드 등). 노브 페이지가 r{번호} 방에 붙는다
- GET  /samples/*       점검용 손바닥 사진 (palmlines/samples)
- GET  /testset/*       로컬 웹캠 테스트 사진 (palmlines/tests/testset, 있을 때만)
- POST /api/submit      체험 화면의 "여기서 멈춤" 제출: 멈춘 단계·그 순간의 글·닉네임·손 스냅샷 → sessions/submissions 에 저장,
                        대시보드(/ws/dashboard)에 바로 알린다
- GET  /api/submissions 제출 목록 (최신순). /api/submissions/{id}/image 는 그 순간의 손 스냅샷
- DELETE /api/submissions 대시보드 초기화: 기록을 지우지 않고 sessions/submissions/archive-<시각>/ (캡차는 sessions/captcha/archive-<시각>/) 으로 옮긴 뒤 대시보드에 reset 을 알린다
- WS   /ws/dashboard    대시보드: 접속하면 init(지금까지 제출 items + 캡차 captcha), 이후 submission(새 제출)·captcha(새 캡차 응답)·live(체험 화면의 현재 단계)·reset
- GET  /dashboard       병원 차트 형식의 실시간 대시보드 화면
- GET  /captcha         예술 캡차 화면 ("매력을 느끼는 '손'을 선택하세요", web/public/captcha 의 manifest.round 고정 9장)
- GET  /captcha/merge/  고른 조합을 합친 그림 (web/public/captcha/merge/<mask>.jpg, tools/gen_captcha_merge.py 로 사전 생성)
- POST /api/captcha     캡차 응답 기록 → sessions/captcha/<id>.json
- POST /api/captcha/live 캡차 화면의 지금 상태(start: 이름 받기 / pick: 고른 수 / done) → 대시보드 captcha_live
- GET  /live/           대시보드 스크린샷 (web/public/live, tools/slides_live.py 가 몇 초마다 찍어 구글 슬라이드 2쪽에 갈아 끼운다)
- GET  /                빌드된 웹 화면 (web/dist)
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import struct
import threading
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

import cv2
import numpy as np
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect

from .names import random_name
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from palmlines.hand import HandTracker, StillnessGate
from palmlines.model import to_json
from palmlines.pipeline import extract_lines, get_unet

from .llm import STEPS, Interpreter
from .palmistry import compute_features, describe

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT.parent / "palmlines" / "samples"
TESTSET = ROOT.parent / "palmlines" / "tests" / "testset"   # 로컬 웹캠 테스트 사진 (저장소에는 없음)
DIST = ROOT / "web" / "dist"
KNOB = ROOT / "knob" / "index.html"
HAND_MODEL = ROOT.parent / "palmlines" / "assets" / "hand_landmarker.task"
SESSIONS = ROOT / "sessions"
SUBMISSIONS = SESSIONS / "submissions"     # "여기서 멈춤" 제출 (항상 저장한다. 대시보드의 재료)
SAVE_SESSIONS = os.environ.get("PALMWEB_SAVE_SESSIONS", "1") != "0"
METHOD = os.environ.get("PALMWEB_METHOD", "merged")
CAPTURE_FRAMES = 5      # 정지 구간 최근 프레임 몇 장을 합쳐 손금을 뽑을지
EXHIBIT_ROOM = "exhibit"
MAX_CAPTURE_BYTES = 12 * 2**20

# 손금 추출 워커: U-Net(GPU) + GrabCut·릿지 필터(CPU). 관객이 여럿이면 동시에 처리한다.
CPU = os.cpu_count() or 4
WORKERS = int(os.environ.get("PALMWEB_WORKERS", str(max(1, min(4, CPU // 4)))))

log = logging.getLogger("palmweb")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

EXTRACT_POOL = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="extract")
STATE = {"unet": "loading", "method": METHOD, "workers": WORKERS}
INTERP: Interpreter | None = None
_local = threading.local()      # 워커 스레드마다 하나씩 쓰는 손 검출기 (MediaPipe는 스레드 사이에 나눠 쓰지 않는다)


def _image_tracker() -> HandTracker:
    t = getattr(_local, "tracker", None)
    if t is None:
        t = _local.tracker = HandTracker(model_path=HAND_MODEL, mode="image")
    return t


@asynccontextmanager
async def lifespan(app: FastAPI):
    loop = asyncio.get_running_loop()

    def warm():
        t0 = time.perf_counter()
        try:
            import torch
            # 워커끼리 CPU를 나눠 쓰도록 torch 스레드 수를 줄인다
            torch.set_num_threads(max(1, CPU // max(WORKERS, 1)))
            if METHOD != "classical":
                net = get_unet()
                STATE["device"] = str(net.device)
            STATE["unet"] = "ready"
            log.info("U-Net 준비 완료 %.1fs (워커 %d)", time.perf_counter() - t0, WORKERS)
        except Exception as e:  # torch·가중치 없음 → 고전 CV로
            STATE.update(unet=f"unavailable: {e}", method="classical")
            log.warning("U-Net을 쓸 수 없어 고전 CV로 전환: %s", e)

    loop.run_in_executor(EXTRACT_POOL, warm)
    global INTERP
    INTERP = Interpreter()
    STATE["llm"] = "ready" if INTERP.available else "OPENROUTER_API_KEY 없음"
    yield
    await INTERP.close()


app = FastAPI(lifespan=lifespan)


# ---------------------------------------------------------------- 방(room) 단위 단계 상태

_ROOM_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


class ControlHub:
    """한 방의 해석 단계 상태. 같은 방의 노브와 전시 화면이 단계를 바꾸고, 바뀌면 모두에게 알린다.

    phase: waiting(손금 촬영 전) → capturing → result(체험 중). 체험 중에만 단계를 바꿀 수 있다.
    """

    def __init__(self, name: str):
        self.name = name
        self.step = 1
        self.phase = "waiting"
        self.session: str | None = None
        self.nickname = ""                      # 이 관객의 이름 (감정 + 이름, 손금을 읽기 시작할 때 붙인다)
        self.ready: set[int] = set()            # 해석 글이 도착한 단계
        self.track: set = set()                 # 전시 화면 연결의 send 함수
        self.knobs: set = set()                 # 노브 연결의 send 함수

    @property
    def empty(self) -> bool:
        return not self.track and not self.knobs

    def snapshot(self, source: str = "") -> dict:
        t, m = STEPS[self.step - 1]
        return {"type": "step", "room": self.name, "step": self.step, "steps": len(STEPS), "phase": self.phase,
                "session": self.session, "nickname": self.nickname, "temperature": t, "min_p": m, "ready": sorted(self.ready),
                "source": source, "displays": len(self.track), "knobs": len(self.knobs)}

    async def broadcast(self, source: str = ""):
        msg = self.snapshot(source)
        for send in list(self.track | self.knobs):
            try:
                await send(msg)
            except Exception:   # 끊긴 연결은 각자의 finally에서 정리된다
                pass
        # 대시보드에도 "지금 체험 중인 화면의 단계"를 알린다 (노브 화면 자체는 제외)
        if self.track:
            await dash_broadcast({"type": "live", **{k: v for k, v in msg.items() if k != "type"}})

    async def set_step(self, k: int, source: str) -> bool:
        if self.phase != "result":
            return False
        k = max(1, min(len(STEPS), int(k)))
        if k != self.step:
            self.step = k
            await self.broadcast(source)
        return True

    async def set_phase(self, phase: str, session: str | None = None):
        self.phase = phase
        if phase in ("waiting", "result"):
            self.step = 1
        if session is not None or phase == "waiting":
            self.session = session
            self.ready = set()
        if phase == "waiting":
            self.nickname = ""
        await self.broadcast("server")

    async def mark_ready(self, session: str, step: int):
        if session == self.session and step not in self.ready:
            self.ready.add(step)
            await self.broadcast("server")


ROOMS: dict[str, ControlHub] = {}


def room_name(requested: str | None, default: str | None) -> str:
    """요청한 방 이름(검증) → 없으면 기본값 → 기본값도 없으면 이 연결만의 개인 방."""
    if requested and _ROOM_RE.match(requested):
        return requested
    return default or f"solo-{uuid.uuid4().hex[:10]}"


def join(name: str) -> ControlHub:
    hub = ROOMS.get(name)
    if hub is None:
        hub = ROOMS[name] = ControlHub(name)
    return hub


def leave_if_empty(hub: ControlHub):
    if hub.empty and ROOMS.get(hub.name) is hub:
        del ROOMS[hub.name]


@app.get("/api/health")
def health(room: str | None = None):
    out = {"ok": True, **STATE, "save_sessions": SAVE_SESSIONS, "rooms": len(ROOMS),
           "displays": sum(len(h.track) for h in ROOMS.values()), "knobs": sum(len(h.knobs) for h in ROOMS.values())}
    hub = ROOMS.get(room or EXHIBIT_ROOM)
    if hub:
        out.update(room=hub.name, step=hub.step, phase=hub.phase,
                   room_displays=len(hub.track), room_knobs=len(hub.knobs))
    return out


@app.get("/knob")
def knob_page():
    return FileResponse(KNOB, media_type="text/html")


# 전시 번호별 짝: /r1 화면 ↔ /n1 노브(아이패드 등), /r2 ↔ /n2 ...
# 그냥 / 로 들어온 관객은 지금처럼 각자 개인 방이고 노브를 쓰지 않는다.
@app.get("/dev")
def dev_page():
    """테스트 화면: 저장된 손금을 바로 띄우고 색·재질 패널을 연다 (카메라·해석 없음)."""
    return RedirectResponse("/?dev=1", status_code=302)


@app.get("/r{num:int}")
def exhibit_display(num: int, request: Request):
    """전시 화면: r{번호} 방에 붙고 화면 단계 버튼을 숨긴다. 붙어 온 다른 옵션(?src= 등)은 이어 준다."""
    extra = [(k, v) for k, v in request.query_params.multi_items() if k not in ("room", "controls")]
    query = urlencode([("room", f"r{num}"), ("controls", "0"), *extra])
    return RedirectResponse(f"/?{query}", status_code=302)


@app.get("/n{num:int}")
def exhibit_knob(num: int):
    """노브 화면 (주소 유지). 노브 페이지가 주소의 번호를 보고 r{번호} 방에 붙는다."""
    return FileResponse(KNOB, media_type="text/html")


@app.websocket("/ws/control")
async def ws_control(ws: WebSocket, room: str | None = None):
    """노브: {"type": "set_step", "step": k} 또는 {"type": "delta", "delta": ±n}. 서버는 type=step 상태를 보낸다.
    room이 없으면 전시장(exhibit) 방을 조종한다 (기존 노브 앱 호환)."""
    await ws.accept()
    hub = join(room_name(room, EXHIBIT_ROOM))
    lock = asyncio.Lock()

    async def send(obj):
        async with lock:
            await ws.send_text(json.dumps(obj, ensure_ascii=False))

    hub.knobs.add(send)
    who = ws.client.host if ws.client else "?"
    log.info("노브 연결 %s 방=%s (노브 %d개)", who, hub.name, len(hub.knobs))
    await hub.broadcast("knob-join")
    try:
        while True:
            cmd = json.loads(await ws.receive_text())
            kind = cmd.get("type")
            if kind == "set_step":
                ok = await hub.set_step(cmd.get("step", hub.step), "knob")
            elif kind == "delta":
                ok = await hub.set_step(hub.step + int(cmd.get("delta", 0)), "knob")
            elif kind == "ping":
                ok = True
            else:
                continue
            if not ok:   # 체험 중이 아니면 바뀌지 않았음을 알려 노브 화면을 되돌린다
                await send(hub.snapshot("rejected"))
            elif kind == "ping":
                await send(hub.snapshot("pong"))
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        hub.knobs.discard(send)
        log.info("노브 연결 끊김 %s 방=%s", who, hub.name)
        await hub.broadcast("knob-leave")
        leave_if_empty(hub)


# ---------------------------------------------------------------- "여기서 멈춤" 제출과 대시보드

DASH: set = set()               # 대시보드 연결의 send 함수
MAX_SNAPSHOT_BYTES = 3 * 2**20
NICK_MAX = 20


async def dash_broadcast(msg: dict):
    for send in list(DASH):
        try:
            await send(msg)
        except Exception:
            pass


def _submission_public(d: dict) -> dict:
    """저장 파일 → 대시보드에 보낼 형태 (스냅샷은 주소로)."""
    out = {k: v for k, v in d.items() if k != "snapshot"}
    out["image"] = f"/api/submissions/{d['id']}/image" if (SUBMISSIONS / f"{d['id']}.jpg").exists() else None
    return out


def _load_submissions(limit: int) -> list[dict]:
    if not SUBMISSIONS.exists():
        return []
    files = sorted(SUBMISSIONS.glob("*.json"), reverse=True)[:limit]
    out = []
    for f in files:
        try:
            out.append(_submission_public(json.loads(f.read_text())))
        except Exception:
            log.warning("제출 파일을 읽지 못함 %s", f.name)
    out.sort(key=lambda d: d.get("at", 0), reverse=True)   # 같은 초에 들어온 기록도 제출 순서대로
    return out


def _save_submission(d: dict, image: bytes | None):
    SUBMISSIONS.mkdir(parents=True, exist_ok=True)
    (SUBMISSIONS / f"{d['id']}.json").write_text(json.dumps(d, ensure_ascii=False))
    if image:
        (SUBMISSIONS / f"{d['id']}.jpg").write_bytes(image)


def _decode_data_url(s: str) -> tuple[bytes, str] | None:
    m = re.match(r"^data:image/(png|jpeg);base64,(.+)$", s or "", re.S)
    if not m:
        return None
    try:
        raw = base64.b64decode(m.group(2), validate=False)
    except Exception:
        return None
    return raw, m.group(1)


@app.post("/api/submit")
async def submit(request: Request):
    """체험 화면: {"session", "step", "nickname", "text", "temperature", "foreign_ratio", "room", "snapshot": "data:image/jpeg;base64,..."}"""
    body = await request.json()
    try:
        step = max(1, min(len(STEPS), int(body.get("step", 1))))
    except (TypeError, ValueError):
        raise HTTPException(400, "step")
    nick = re.sub(r"\s+", " ", str(body.get("nickname", "")))[:NICK_MAX].strip() or "익명"
    text = str(body.get("text", ""))[:2000]
    text1 = str(body.get("text_step1", ""))[:2000]   # 1단계 글 (대시보드 표시용)
    snap = body.get("snapshot") or ""
    if len(snap) > MAX_SNAPSHOT_BYTES * 4 // 3 + 64:
        raise HTTPException(413, "snapshot")
    image = None
    dec = _decode_data_url(snap) if snap else None
    if dec:
        raw, kind = dec
        if kind == "png":   # 저장은 jpg 로 통일
            img = _decode(raw)
            ok, enc = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 88]) if img is not None else (False, None)
            raw = enc.tobytes() if ok else None
        image = raw
    sid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    t, m = STEPS[step - 1]
    d = {
        "id": sid, "at": time.time(), "session": str(body.get("session", ""))[:64], "room": str(body.get("room", ""))[:40],
        "step": step, "steps": len(STEPS), "temperature": t, "min_p": m, "nickname": nick, "text": text, "text_step1": text1,
        "foreign_ratio": float(body.get("foreign_ratio") or 0), "description": str(body.get("description", ""))[:1000],
        "dwell_ms": body.get("dwell_ms") if isinstance(body.get("dwell_ms"), dict) else None,   # 단계별 머문 시간
    }
    await asyncio.get_running_loop().run_in_executor(None, _save_submission, d, image)
    pub = _submission_public(d)
    log.info("제출 %s %s %d단계 (%s)", sid, nick, step, d["room"] or "-")
    await dash_broadcast({"type": "submission", "item": pub})
    return {"ok": True, **pub}


@app.get("/api/submissions")
def submissions(limit: int = 500):
    return {"items": _load_submissions(max(1, min(limit, 2000)))}


@app.delete("/api/submissions")
async def reset_submissions():
    """대시보드 초기화. 파일은 archive-<시각> 폴더로 옮겨 둔다 (나중에 40명분 집계에 쓸 수 있게)."""
    def move():
        n = 0
        stamp = time.strftime("archive-%Y%m%d-%H%M%S")
        for folder in (SUBMISSIONS, CAPTCHA_LOG):   # 손금 제출과 캡차 응답 모두
            if not folder.exists():
                continue
            files = [f for f in folder.iterdir() if f.is_file() and f.suffix in (".json", ".jpg")]
            if not files:
                continue
            dest = folder / stamp
            dest.mkdir(parents=True, exist_ok=True)
            for f in files:
                f.rename(dest / f.name)
            n += len([f for f in files if f.suffix == ".json"])
        return n
    n = await asyncio.get_running_loop().run_in_executor(None, move)
    log.info("대시보드 초기화: %d건 보관", n)
    await dash_broadcast({"type": "reset", "archived": n})
    return {"ok": True, "archived": n}


@app.get("/api/submissions/{sid}/image")
def submission_image(sid: str):
    if not re.match(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$", sid):
        raise HTTPException(404)
    p = SUBMISSIONS / f"{sid}.jpg"
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type="image/jpeg")


@app.get("/dashboard")
def dashboard_page():
    p = DIST / "dashboard.html"
    if not p.exists():
        raise HTTPException(404, "대시보드 화면이 빌드되지 않았습니다 (cd web && npm run build)")
    return FileResponse(p, media_type="text/html")


CAPTCHA_LOG = SESSIONS / "captcha"
CAPTCHA_MERGE = ROOT / "web" / "public" / "captcha" / "merge"   # 사전 생성한 합성 그림 (tools/gen_captcha_merge.py)
CAPTCHA_MERGE.mkdir(parents=True, exist_ok=True)


def _captcha_public(d: dict) -> dict:
    """캡차 저장 파일 → 대시보드에 보낼 형태 (고른 칸 이름, 합친 그림 주소)."""
    mask = d.get("mask") or ""
    chosen = [t for t in d.get("tiles", []) if t.get("chosen")]
    return {"id": d["id"], "at": d.get("at", 0), "ms": d.get("ms"), "nickname": d.get("nickname", ""), "mask": mask,
            "chosen": [t.get("label", "") for t in chosen], "chosen_files": [t.get("file", "") for t in chosen],
            "chosen_hand": d.get("chosen_hand", 0), "chosen_other": d.get("chosen_other", 0),
            "image": f"/captcha/merge/{mask}.jpg" if mask and (CAPTCHA_MERGE / f"{mask}.jpg").exists() else None}


def _load_captcha(limit: int) -> list[dict]:
    if not CAPTCHA_LOG.exists():
        return []
    out = []
    for f in sorted(CAPTCHA_LOG.glob("*.json"), reverse=True)[:limit]:
        try:
            out.append(_captcha_public(json.loads(f.read_text())))
        except Exception:
            log.warning("캡차 파일을 읽지 못함 %s", f.name)
    out.sort(key=lambda d: d.get("at", 0), reverse=True)
    return out


@app.get("/captcha")
def captcha_page():
    p = DIST / "captcha.html"
    if not p.exists():
        raise HTTPException(404, "캡차 화면이 빌드되지 않았습니다 (cd web && npm run build)")
    return FileResponse(p, media_type="text/html")


@app.post("/api/captcha")
async def captcha_answer(request: Request):
    """{"tiles": [{"file", "group", "label", "chosen": bool} × 9], "mask"?: "101000100", "nickname"?: str, "ms": int}"""
    body = await request.json()
    tiles = body.get("tiles")
    if not isinstance(tiles, list) or len(tiles) != 9:
        raise HTTPException(400, "tiles")
    sid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    mask = str(body.get("mask", ""))
    d = {"id": sid, "at": time.time(), "nickname": str(body.get("nickname", ""))[:NICK_MAX], "ms": body.get("ms"),
         "mask": mask if re.fullmatch(r"[01]{9}", mask) else "",
         "tiles": [{"file": str(t.get("file", ""))[:40], "group": str(t.get("group", ""))[:10], "label": str(t.get("label", ""))[:40],
                    "chosen": bool(t.get("chosen"))} for t in tiles]}
    d["chosen_hand"] = sum(1 for t in d["tiles"] if t["chosen"] and t["group"] == "hand")
    d["chosen_other"] = sum(1 for t in d["tiles"] if t["chosen"] and t["group"] == "other")

    def save():
        CAPTCHA_LOG.mkdir(parents=True, exist_ok=True)
        (CAPTCHA_LOG / f"{sid}.json").write_text(json.dumps(d, ensure_ascii=False))
    await asyncio.get_running_loop().run_in_executor(None, save)
    log.info("캡차 %s 선택 %s: 손 %d · 손아님 %d", sid, d["mask"], d["chosen_hand"], d["chosen_other"])
    await dash_broadcast({"type": "captcha", "item": _captcha_public(d)})
    return {"ok": True, "id": sid}


CAPTCHA_LIVE: dict[str, dict] = {}   # 캡차 화면별 지금 상태 (대시보드가 새로 붙을 때 보내 준다)


@app.post("/api/captcha/live")
async def captcha_live(request: Request):
    """캡차 화면의 지금 상태 → 대시보드. {"client": str, "state": "start"|"pick"|"done", "nickname"?: str, "chosen"?: int}
    start 이면 이름(감정 + 이름)을 새로 붙여 돌려준다."""
    body = await request.json()
    state = str(body.get("state", ""))
    if state not in ("start", "pick", "done"):
        raise HTTPException(400, "state")
    nick = random_name() if state == "start" else str(body.get("nickname", ""))[:NICK_MAX]
    try:
        chosen = max(0, min(9, int(body.get("chosen") or 0)))
    except (TypeError, ValueError):
        chosen = 0
    msg = {"type": "captcha_live", "client": str(body.get("client", ""))[:40], "state": state,
           "nickname": nick, "chosen": chosen, "at": time.time()}
    if state == "done":
        CAPTCHA_LIVE.pop(msg["client"], None)
    else:
        CAPTCHA_LIVE[msg["client"]] = msg
    for k, v in list(CAPTCHA_LIVE.items()):   # 오래된 화면은 잊는다
        if time.time() - v["at"] > 300:
            CAPTCHA_LIVE.pop(k, None)
    await dash_broadcast(msg)
    return {"ok": True, "nickname": nick}


@app.websocket("/ws/dashboard")
async def ws_dashboard(ws: WebSocket):
    await ws.accept()
    lock = asyncio.Lock()

    async def send(obj):
        async with lock:
            await ws.send_text(json.dumps(obj, ensure_ascii=False))

    DASH.add(send)
    try:
        items = await asyncio.get_running_loop().run_in_executor(None, _load_submissions, 500)
        captcha = await asyncio.get_running_loop().run_in_executor(None, _load_captcha, 500)
        await send({"type": "init", "items": items, "captcha": captcha})
        for hub in ROOMS.values():
            if hub.track:
                await send({"type": "live", **{k: v for k, v in hub.snapshot("dash-join").items() if k != "type"}})
        for v in CAPTCHA_LIVE.values():
            if time.time() - v["at"] <= 120:
                await send(v)
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            if msg.get("text") and json.loads(msg["text"]).get("type") == "ping":
                await send({"type": "pong"})
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        DASH.discard(send)


# ---------------------------------------------------------------- 손금 추출

def _decode(data: bytes) -> np.ndarray | None:
    return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)


def _unpack_capture(data: bytes) -> list[bytes]:
    """b"PALM" + 사진 수 + [4바이트 길이 + JPEG]×n"""
    n = data[4]
    out, i = [], 5
    for _ in range(n):
        (L,) = struct.unpack(">I", data[i:i + 4])
        out.append(data[i + 4:i + 4 + L])
        i += 4 + L
    return out


def _extract_from_jpegs(jpegs: list[bytes]):
    """워커 스레드: 사진들을 풀고 손을 다시 검출한 뒤(정면화에 필요) 손금을 뽑는다."""
    tracker = _image_tracker()
    frames = []
    for j in jpegs:
        img = _decode(j)
        if img is None:
            continue
        obs = tracker.detect(img)
        if obs is not None and obs.palm_facing:
            frames.append((img, obs))
    if not frames:
        raise ValueError("사진에서 손바닥을 찾지 못했습니다")
    return frames, *extract_lines(frames, method=STATE["method"], keep_roi=False)


def _extract_from_frames(frames):
    return frames, *extract_lines(frames, method=STATE["method"], keep_roi=False)


def _save_session(sid: str, palm: dict, meta: dict):
    if not SAVE_SESSIONS:
        return
    SESSIONS.mkdir(exist_ok=True)
    (SESSIONS / f"{sid}.json").write_text(json.dumps({"id": sid, **meta, "palm": palm}, ensure_ascii=False))


def _save_interpretation(sid: str, interp: dict):
    path = SESSIONS / f"{sid}.json"
    if not SAVE_SESSIONS or not path.exists():
        return
    d = json.loads(path.read_text())
    d["interpretation"] = interp
    path.write_text(json.dumps(d, ensure_ascii=False))


class TrackSession:
    """연결 하나의 상태. 서버 추적 방식일 때만 MediaPipe 추적기를 만든다 (스레드 하나에서만 쓴다)."""

    def __init__(self):
        self.pool: ThreadPoolExecutor | None = None
        self.tracker = None
        self.gate = StillnessGate()
        self.recent: deque = deque(maxlen=CAPTURE_FRAMES)
        self.capturing = False
        self.done = False
        self.t0 = time.monotonic()
        self.interp_task: asyncio.Task | None = None

    def cancel_interp(self):
        if self.interp_task and not self.interp_task.done():
            self.interp_task.cancel()
        self.interp_task = None

    def track(self, frame: np.ndarray) -> dict:
        if self.tracker is None:
            self.tracker = HandTracker(model_path=HAND_MODEL, mode="video")
        ts = int((time.monotonic() - self.t0) * 1000)
        obs = self.tracker.detect(frame, ts)
        h, w = frame.shape[:2]
        if self.done or self.capturing:
            ready, msg, prog = False, "", 0.0
        else:
            ready, msg, prog = self.gate.update(obs)
            if obs is not None and obs.palm_facing:
                self.recent.append((frame, obs))
            else:
                self.recent.clear()
        out = {"type": "track", "message": msg, "progress": round(prog, 3), "ready": bool(ready),
               "size": [w, h], "hand": None}
        if obs is not None:
            out["hand"] = {
                "landmarks": (obs.landmarks_px / np.array([w, h], np.float32)).round(4).tolist(),
                "handedness": obs.handedness,
                "palm_facing": bool(obs.palm_facing),
            }
        return out

    def close(self):
        self.cancel_interp()
        if self.pool is not None:
            if self.tracker is not None:
                self.pool.submit(self.tracker.close)
            self.pool.shutdown(wait=False)


@app.websocket("/ws/track")
async def ws_track(ws: WebSocket, room: str | None = None):
    await ws.accept()
    loop = asyncio.get_running_loop()
    sess = TrackSession()
    hub = join(room_name(room, None))
    send_lock = asyncio.Lock()

    async def send(obj):
        async with send_lock:
            await ws.send_text(json.dumps(obj, ensure_ascii=False))

    hub.track.add(send)
    log.info("화면 연결 방=%s (방 %d개)", hub.name, len(ROOMS))
    await send(hub.snapshot("hello"))

    async def interpret(sid: str, palm: dict):
        """손금 특징 → 묘사 → 10단계 해석. 단계가 도착하는 대로 보낸다."""
        feats = compute_features(palm)
        desc = describe(feats)
        await send({"type": "interp_start", "session": sid, "description": desc, "features": feats.to_dict(),
                    "steps": [{"step": i + 1, "temperature": t, "min_p": m} for i, (t, m) in enumerate(STEPS)]})
        if INTERP is None or not INTERP.available:
            await send({"type": "interp_error", "session": sid, "message": "해석을 불러오지 못했습니다 (API 키 없음)"})
            return
        t0 = time.perf_counter()

        async def on_result(r):
            await send({"type": "interp", "session": sid, **r.to_dict()})
            await hub.mark_ready(sid, r.step)

        results = await INTERP.run_all(desc, on_result)
        failed = [r.step for r in results if r.error]
        log.info("해석 %s %.1fs 실패단계=%s", sid, time.perf_counter() - t0, failed or "없음")
        if len(failed) == len(results):
            await send({"type": "interp_error", "session": sid, "message": "해석을 불러오지 못했습니다"})
        await send({"type": "interp_done", "session": sid, "failed": failed,
                    "seconds": round(time.perf_counter() - t0, 1)})
        await loop.run_in_executor(None, _save_interpretation, sid, {
            "description": desc, "features": feats.to_dict(), "steps": [r.to_dict() for r in results]})

    async def capture(job, reason: str, n_frames: int):
        """job: 워커에서 돌릴 함수 (사진 묶음 또는 서버 추적 프레임에서 손금 추출)."""
        sess.capturing = True
        hub.nickname = random_name()   # 이 관객의 이름. 읽는 동안부터 대시보드에 뜬다
        await send({"type": "capturing", "reason": reason, "nickname": hub.nickname})
        await hub.set_phase("capturing")
        sid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        t0 = time.perf_counter()
        try:
            frames, ls, dbg = await loop.run_in_executor(EXTRACT_POOL, job)
            palm = to_json(ls)
            meta = {"method": STATE["method"], "ms": round((time.perf_counter() - t0) * 1000),
                    "frames": len(frames), "outline": dbg.get("shape_source"), "reason": reason}
            await loop.run_in_executor(None, _save_session, sid, palm, meta)
            sess.done = True
            kinds = [l["kind"] for l in palm["lines"] if l["kind"] != "minor"]
            log.info("캡처 %s 방=%s %s %dms 사진 %d/%d 주요선=%s", sid, hub.name, reason, meta["ms"],
                     len(frames), n_frames, kinds)
            await send({"type": "capture", "session": sid, "nickname": hub.nickname, "palm": palm, **meta})
            await hub.set_phase("result", sid)
            sess.cancel_interp()
            sess.interp_task = asyncio.create_task(interpret(sid, palm))
        except Exception as e:
            log.warning("손금 추출 실패 방=%s: %s", hub.name, e)
            sess.gate.reset()
            sess.recent.clear()
            await send({"type": "error", "message": f"손금을 읽지 못했습니다: {e}"})
            await hub.set_phase("waiting")
        finally:
            sess.capturing = False

    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            data = msg.get("bytes")
            if data is not None:
                if data[:4] == b"PALM":
                    # 브라우저가 손을 추적하고 캡처 순간의 사진만 보낸 경우
                    if sess.capturing or len(data) > MAX_CAPTURE_BYTES:
                        await send({"type": "error", "message": "잠시 후 다시 시도해 주세요"})
                        continue
                    try:
                        jpegs = _unpack_capture(data)
                    except Exception:
                        await send({"type": "error", "message": "사진 묶음을 읽지 못했습니다"})
                        continue
                    asyncio.create_task(capture(lambda j=jpegs: _extract_from_jpegs(j), "auto", len(jpegs)))
                    continue
                # 예전 방식: 프레임 하나씩 보내고 서버가 추적한다
                frame = await loop.run_in_executor(None, _decode, data)
                if frame is None:
                    await send({"type": "error", "message": "프레임을 읽지 못했습니다"})
                    continue
                if sess.pool is None:
                    sess.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="track")
                out = await loop.run_in_executor(sess.pool, sess.track, frame)
                await send(out)
                if out["ready"] and not sess.capturing and not sess.done:
                    frames = list(sess.recent)
                    asyncio.create_task(capture(lambda f=frames: _extract_from_frames(f), "auto", len(frames)))
            elif msg.get("text") is not None:
                cmd = json.loads(msg["text"])
                kind = cmd.get("type")
                if kind == "reset":
                    sess.cancel_interp()
                    sess.done = False
                    sess.gate.reset()
                    sess.recent.clear()
                    await send({"type": "reset_ok"})
                    await hub.set_phase("waiting")
                elif kind == "set_step":   # 화면 방향키·버튼
                    if not await hub.set_step(cmd.get("step", hub.step), "keyboard"):
                        await send(hub.snapshot("rejected"))
                elif kind == "capture_now":
                    if sess.recent and not sess.capturing:
                        frames = list(sess.recent)
                        asyncio.create_task(capture(lambda f=frames: _extract_from_frames(f), "manual", len(frames)))
                    else:
                        await send({"type": "error", "message": "손바닥이 보일 때 눌러주세요"})
                elif kind == "ping":
                    await send({"type": "pong", **STATE})
    except WebSocketDisconnect:
        pass
    finally:
        hub.track.discard(send)
        sess.close()
        if not hub.track:
            await hub.set_phase("waiting")
        leave_if_empty(hub)


@app.exception_handler(Exception)
async def unhandled(_, exc):
    log.exception("요청 처리 실패")
    return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@app.get("/models/hand_landmarker.task")
def hand_model():
    """브라우저 손 추적용 MediaPipe 모델 (palmlines/assets 에 있는 것을 그대로 준다)."""
    return FileResponse(HAND_MODEL, media_type="application/octet-stream")


if SAMPLES.exists():
    app.mount("/samples", StaticFiles(directory=SAMPLES), name="samples")
if TESTSET.exists():
    app.mount("/testset", StaticFiles(directory=TESTSET), name="testset")
app.mount("/captcha/merge", StaticFiles(directory=CAPTCHA_MERGE), name="captcha-merge")   # 사전 생성한 합성 그림. dist 와 별개로 바로 서빙
LIVE_DIR = ROOT / "web" / "public" / "live"   # 슬라이드 라이브용 대시보드 스크린샷 (tools/slides_live.py 가 쓴다. 구글이 이 주소에서 그림을 가져간다)
LIVE_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/live", StaticFiles(directory=LIVE_DIR), name="live")
if DIST.exists():
    app.mount("/", StaticFiles(directory=DIST, html=True), name="web")
