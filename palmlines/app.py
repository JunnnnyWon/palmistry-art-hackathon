"""웹캠 실시간 손금 캡처 앱 (M0~M1).

    uv run python app.py                 # 기본 카메라
    uv run python app.py --pick-camera   # 카메라 미리보기를 보고 골라 저장 (다음부터 기본값)
    uv run python app.py --camera 1      # 이번만 특정 번호
    uv run python app.py --source samples/hand70.jpg --headless --frames 60   # 카메라 없이 점검

흐름: 손바닥을 펴서 보여줌 → 정면·거리·정지 확인(게이지) → 자동 캡처 → 손 아웃라인 위 손금 표시.
키: space 강제 캡처 · r 다시 · s 저장(PNG+JSON) · m 추출 방식 전환 · o 윤곽 방식 전환 · c 테스트 세트에 저장
    d 디버그 · f 전체화면 · q/esc 종료
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import time
from collections import deque
from pathlib import Path

import cv2
import numpy as np

from palmlines.cameras import contact_sheet, probe, save_index, saved_index
from palmlines.hand import HandTracker, StillnessGate
from palmlines.model import PalmLineSet, to_json
from palmlines.pipeline import METHODS, extract_lines, get_unet
from palmlines.render import Style, hand_silhouette, fit_transform, render
from palmlines.ui import TextDrawer

HAND_CONNECTIONS = [(0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (5, 9), (9, 10),
                    (10, 11), (11, 12), (9, 13), (13, 14), (14, 15), (15, 16), (13, 17), (0, 17),
                    (17, 18), (18, 19), (19, 20)]
AVG_FRAMES = 5          # 캡처 시 정면화 ROI를 몇 장 겹쳐 노이즈를 줄일지
RESET_AFTER_S = 4.0     # 캡처 후 손이 이만큼 안 보이면 처음으로


def list_cameras() -> list[str]:
    """AVFoundation 카메라 번호와 이름 (ffmpeg가 있으면). OpenCV 번호와 같은 순서다."""
    if not shutil.which("ffmpeg"):
        return []
    r = subprocess.run(["ffmpeg", "-hide_banner", "-f", "avfoundation", "-list_devices", "true", "-i", ""],
                       capture_output=True, text=True)
    names, video = [], False
    for line in r.stderr.splitlines():
        if "video devices" in line:
            video = True
        elif "audio devices" in line:
            break
        elif video and "] [" in line:
            names.append(line.split("] ", 1)[1])
    return names


def camera_help(source: str) -> str:
    lines = [f"카메라를 열 수 없습니다: {source}"]
    if os.environ.get("__CFBundleIdentifier", "").startswith("com.anthropic"):
        lines += ["",
                  "지금 Claude 앱 안의 터미널에서 실행 중입니다. 이 경우 macOS가 카메라 권한 창을 띄우지 않고 거부합니다.",
                  "macOS '터미널' 앱에서 실행하거나, Finder에서 '웹캠_실행.command'를 더블클릭하세요."]
    else:
        lines += ["",
                  "시스템 설정 > 개인정보 보호 및 보안 > 카메라 에서 지금 쓰는 터미널 앱을 켜고,",
                  "터미널 앱을 완전히 종료(⌘Q)한 뒤 다시 실행하세요.",
                  "다른 앱(FaceTime, Teams, Zoom 등)이 카메라를 쓰고 있으면 먼저 닫으세요."]
    cams = list_cameras()
    lines += ["", "카메라 번호를 바꾸려면 --pick-camera 로 미리보기를 보고 고르세요."]
    if cams:
        lines += ["연결된 카메라 이름 (번호 순서는 OpenCV와 다를 수 있음):"] + [f"  {c}" for c in cams]
    return "\n".join(lines)


class FrameSource:
    """카메라 인덱스 또는 이미지/영상 파일."""

    def __init__(self, source: str, width=1280, height=720):
        self.still = None
        if source.isdigit():
            self.cap = cv2.VideoCapture(int(source), cv2.CAP_AVFOUNDATION)
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
            self.mirror = True
        elif Path(source).suffix.lower() in (".jpg", ".jpeg", ".png"):
            img = cv2.imread(source)
            s = 1280 / max(img.shape[:2])
            self.still = cv2.resize(img, None, fx=s, fy=s) if s < 1 else img
            self.cap, self.mirror = None, False
        else:
            self.cap, self.mirror = cv2.VideoCapture(source), False
        if self.cap is not None and not self.cap.isOpened():
            raise SystemExit(camera_help(source) if source.isdigit() else f"영상을 열 수 없습니다: {source}")
        self.live = source.isdigit()

    def read(self, wait_s: float = 3.0):
        """카메라는 켜지는 데 시간이 걸리므로 첫 프레임까지 잠시 기다린다."""
        if self.still is not None:
            return self.still.copy()
        t = time.time()
        while True:
            ok, frame = self.cap.read()
            if ok and frame is not None:
                return cv2.flip(frame, 1) if self.mirror else frame
            if not self.live or time.time() - t > wait_s:
                return None
            time.sleep(0.05)

    def release(self):
        if self.cap is not None:
            self.cap.release()


def draw_camera(frame, obs, msg, progress, text):
    vis = frame.copy()
    h, w = vis.shape[:2]
    if obs is not None:
        pts = obs.landmarks_px.astype(int)
        col = (120, 230, 140) if obs.palm_facing else (80, 80, 255)
        for a, b in HAND_CONNECTIONS:
            cv2.line(vis, tuple(pts[a]), tuple(pts[b]), col, 2, cv2.LINE_AA)
        for p in pts:
            cv2.circle(vis, tuple(p), 4, (255, 255, 255), -1, cv2.LINE_AA)
    if msg:
        text(vis, msg, (w // 2, h - 40), size=max(h // 22, 20), anchor="mm")
    if progress > 0:
        cv2.rectangle(vis, (0, h - 8), (int(w * progress), h), (120, 230, 140), -1)
    return vis


def live_outline(obs, frame_shape, size, style):
    """캡처 전: 랜드마크를 따라 움직이는 손 아웃라인만 그린다."""
    canvas = np.full((size, size, 3), style.bg, np.uint8)
    if obs is None:
        return canvas
    h, w = frame_shape[:2]
    lm = obs.landmarks_px / np.array([w, h], np.float32)
    lm = lm * np.array([w / max(w, h), h / max(w, h)], np.float32)
    scale, offset = fit_transform(lm)
    sil = hand_silhouette(lm * scale + offset, size)
    cs, _ = cv2.findContours(sil, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    cv2.polylines(canvas, cs, True, (120, 115, 110), 2, cv2.LINE_AA)
    return canvas


def debug_panel(dbg, size):
    tiles = []
    for key in ("enhanced", "ridge", "skeleton") if dbg.get("enhanced") is not None else ("ridge", "skeleton"):
        t = dbg.get(key)
        if t is None:
            continue
        if t.dtype == bool:
            t = t.astype(np.uint8) * 255
        elif t.dtype != np.uint8:
            t = (np.clip(t, 0, 1) * 255).astype(np.uint8)
        tiles.append(cv2.cvtColor(cv2.resize(t, (size // 3, size // 3)), cv2.COLOR_GRAY2BGR))
    return np.hstack(tiles) if tiles else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--camera", default=None,
                    help="카메라 번호. 생략하면 --pick-camera 로 저장한 번호(없으면 0)")
    ap.add_argument("--pick-camera", action="store_true",
                    help="카메라마다 한 장씩 찍어 보여주고 숫자 키로 골라 저장")
    ap.add_argument("--list-cameras", action="store_true", help="ffmpeg 기준 카메라 이름 목록 (번호 순서는 OpenCV와 다를 수 있음)")
    ap.add_argument("--source", default=None, help="카메라 대신 이미지/영상 파일")
    ap.add_argument("--out", default="captures")
    ap.add_argument("--outline", default="traced", choices=("traced", "simple"),
                    help="손 윤곽: traced=실제 손 경계에 맞춤, simple=랜드마크 기반 단순 윤곽 (o 키로 전환)")
    ap.add_argument("--testset", default="tests/testset", help="c 키로 저장할 테스트 세트 폴더")
    ap.add_argument("--method", default="merged", choices=METHODS,
                    help="손금 추출 방식 (실행 중 m 키로 전환)")
    ap.add_argument("--headless", action="store_true", help="창 없이 실행 (점검용)")
    ap.add_argument("--frames", type=int, default=0, help="이 프레임 수 후 종료 (0=무한)")
    args = ap.parse_args()
    if args.list_cameras:
        cams = list_cameras()
        print("\n".join(cams) if cams else "ffmpeg가 없어 목록을 볼 수 없습니다 (brew install ffmpeg)")
        return

    if args.pick_camera:
        if not pick_camera():
            return
    camera = args.camera if args.camera is not None else str(saved_index(0))
    src = FrameSource(args.source or camera)
    print(f"[camera] {args.source or camera} 열림. 창이 뜨면 손바닥을 보여주세요. 종료는 q")
    style = Style()
    text = TextDrawer()
    gate = StillnessGate()
    recent: deque[tuple[np.ndarray, object]] = deque(maxlen=AVG_FRAMES)
    out_dir = Path(args.out)
    state = "TRACKING"
    result: PalmLineSet | None = None
    result_img = None
    dbg = {}
    last_seen = time.time()
    show_debug = False
    method = args.method
    outline_mode = args.outline
    last_frames = []
    if method != "classical":
        try:
            get_unet()   # 첫 캡처 지연을 없애려고 미리 로드
        except Exception as e:   # torch 미설치, 체크포인트 없음 등
            print(f"[warn] U-Net을 쓸 수 없어 고전 CV로 전환합니다: {e}")
            method = "classical"
    win = "palmlines"
    if not args.headless:
        cv2.namedWindow(win, cv2.WINDOW_NORMAL)

    def capture(obs_frames):
        nonlocal state, result, result_img, dbg, last_frames
        last_frames = obs_frames
        result, dbg = extract_lines(obs_frames, method=method, outline_mode=outline_mode)
        result_img = render(result, 720, style)
        state = "PLAYING"
        kinds = ", ".join(l.kind for l in result.lines if l.kind != "minor")
        print(f"[capture:{method}] {len(result.lines)}선 ({kinds}) {dbg['ms']:.0f}ms")

    with HandTracker(mode="video") as tracker:
        t0 = time.time()
        n = 0
        while True:
            frame = src.read()
            if frame is None:
                if n == 0 and src.live:
                    print("카메라는 열렸지만 영상이 오지 않습니다. 다른 앱이 카메라를 쓰고 있지 않은지 확인하고, "
                          "--pick-camera 로 다른 카메라를 고르세요.")
                break
            n += 1
            obs = tracker.detect(frame, int((time.time() - t0) * 1000))
            if obs is not None:
                last_seen = time.time()

            if state == "TRACKING":
                ready, msg, prog = gate.update(obs)
                if obs is not None and obs.palm_facing:
                    recent.append((frame, obs))
                else:
                    recent.clear()
                if ready and len(recent) >= 2:
                    capture(list(recent))
                right = live_outline(obs, frame.shape, 720, style)
            else:
                msg, prog = f"[{method}/{outline_mode}]  r 다시 · s 저장 · m 방식 · o 윤곽 · c 테스트 세트", 0.0
                if time.time() - last_seen > RESET_AFTER_S:
                    state, result = "TRACKING", None
                    gate.reset()
                    recent.clear()
                right = result_img if result_img is not None else np.zeros((720, 720, 3), np.uint8)

            if args.headless:
                if args.frames and n >= args.frames:
                    break
                continue

            cam = draw_camera(frame, obs, msg, prog, text)
            ch = 720
            cam = cv2.resize(cam, (int(cam.shape[1] * ch / cam.shape[0]), ch))
            view = np.hstack([cam, right])
            if show_debug and state == "PLAYING":
                dp = debug_panel(dbg, view.shape[1] // 2)
                if dp is not None:
                    view[:dp.shape[0], -dp.shape[1]:] = dp
            cv2.imshow(win, view)
            k = cv2.waitKey(1) & 0xFF
            if k in (ord("q"), 27):
                break
            elif k == ord(" ") and obs is not None:
                capture(list(recent) or [(frame, obs)])
            elif k == ord("r"):
                state, result = "TRACKING", None
                gate.reset()
                recent.clear()
            elif k == ord("m"):
                method = METHODS[(METHODS.index(method) + 1) % len(METHODS)]
                print(f"[method] {method}")
                if state == "PLAYING" and last_frames:
                    capture(last_frames)   # 같은 캡처를 새 방식으로 다시 추출
            elif k == ord("o"):
                outline_mode = "simple" if outline_mode == "traced" else "traced"
                print(f"[outline] {outline_mode}")
                if state == "PLAYING" and last_frames:
                    capture(last_frames)
            elif k == ord("c") and state == "PLAYING":
                save_sample(last_frames, Path(args.testset))
            elif k == ord("d"):
                show_debug = not show_debug
            elif k == ord("f"):
                full = cv2.getWindowProperty(win, cv2.WND_PROP_FULLSCREEN) == cv2.WINDOW_FULLSCREEN
                cv2.setWindowProperty(win, cv2.WND_PROP_FULLSCREEN,
                                      cv2.WINDOW_NORMAL if full else cv2.WINDOW_FULLSCREEN)
            elif k == ord("s") and result is not None:
                save(result, result_img, out_dir)
            if args.frames and n >= args.frames:
                break

    if args.headless and result is not None:
        save(result, result_img, out_dir)
    elif args.headless:
        print("캡처되지 않음")
    src.release()
    cv2.destroyAllWindows()


def pick_camera() -> bool:
    """카메라 번호별 미리보기를 띄우고 숫자 키로 고르게 한다. 고른 번호는 .camera 에 저장."""
    print("카메라 확인 중... (번호마다 몇 초씩 걸립니다)")
    frames = probe(6)
    if not frames:
        print(camera_help("0"))
        return False
    sheet = contact_sheet(frames)
    TextDrawer()(sheet, "쓸 카메라의 번호 키를 누르세요 (esc 취소)", (10, sheet.shape[0] - 40), 24)
    win = "카메라 선택"
    cv2.imshow(win, sheet)
    while True:
        k = cv2.waitKey(50) & 0xFF
        if k == 27:
            cv2.destroyWindow(win)
            return False
        if ord("0") <= k <= ord("9") and (k - ord("0")) in frames:
            i = k - ord("0")
            save_index(i)
            cv2.destroyWindow(win)
            print(f"[camera] {i}번을 기본 카메라로 저장했습니다")
            return True


def save_sample(frames, out_dir: Path):
    """평가용 테스트 세트에 정지 구간 프레임들을 케이스 폴더 하나로 저장한다.
    라벨링은 label_tool.py, 평가는 eval_lines.py."""
    case = out_dir / time.strftime("%Y%m%d-%H%M%S")
    case.mkdir(parents=True, exist_ok=True)
    for i, (f, _) in enumerate(frames):
        cv2.imwrite(str(case / f"frame_{i}.png"), f)
    print(f"[testset] {case} ({len(frames)}장)")


def save(result: PalmLineSet, img, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    cv2.imwrite(str(out_dir / f"palm_{stamp}.png"), img)
    (out_dir / f"palm_{stamp}.json").write_text(json.dumps(to_json(result), ensure_ascii=False))
    print(f"[save] {out_dir}/palm_{stamp}.png, .json")


if __name__ == "__main__":
    main()
