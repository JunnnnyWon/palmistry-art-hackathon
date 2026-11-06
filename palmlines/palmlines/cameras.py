"""카메라 번호 확인·선택.

OpenCV(AVFoundation)의 카메라 번호 순서는 ffmpeg나 시스템 정보의 순서와 다를 수 있다.
그래서 이름 대신 번호마다 실제로 한 장씩 찍어서 보고 고른다.
고른 번호는 프로젝트 폴더의 .camera 파일에 저장해 다음 실행부터 기본값으로 쓴다.
"""
from __future__ import annotations

import time
from pathlib import Path

import cv2
import numpy as np

CONFIG = Path(__file__).resolve().parent.parent / ".camera"


def saved_index(default: int = 0) -> int:
    try:
        return int(CONFIG.read_text().strip())
    except (OSError, ValueError):
        return default


def save_index(i: int):
    CONFIG.write_text(f"{i}\n")


def grab(i: int, wait_s: float = 2.5) -> np.ndarray | None:
    cap = cv2.VideoCapture(i, cv2.CAP_AVFOUNDATION)
    if not cap.isOpened():
        return None
    frame, t = None, time.time()
    # 켜진 직후 몇 장은 검거나 노출이 안 맞으므로 잠시 읽어 버린다
    while time.time() - t < wait_s:
        ok, f = cap.read()
        if ok and f is not None:
            frame = f
            if time.time() - t > 0.8:
                break
        time.sleep(0.03)
    cap.release()
    return frame


def probe(max_index: int = 6) -> dict[int, np.ndarray]:
    out = {}
    for i in range(max_index):
        f = grab(i)
        if f is not None:
            out[i] = f
    return out


def contact_sheet(frames: dict[int, np.ndarray], tile: int = 360) -> np.ndarray:
    tiles = []
    for i, f in frames.items():
        h, w = f.shape[:2]
        t = cv2.resize(f, (tile, int(tile * h / w)))
        t = cv2.copyMakeBorder(t, 0, tile - t.shape[0], 0, 0, cv2.BORDER_CONSTANT) if t.shape[0] < tile else t[:tile]
        cv2.rectangle(t, (0, 0), (150, 44), (0, 0, 0), -1)
        cv2.putText(t, f"[{i}] {w}x{h}", (8, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2, cv2.LINE_AA)
        tiles.append(t)
    if not tiles:
        return np.zeros((tile, tile, 3), np.uint8)
    cols = min(3, len(tiles))
    while len(tiles) % cols:
        tiles.append(np.zeros_like(tiles[0]))
    rows = [np.hstack(tiles[r:r + cols]) for r in range(0, len(tiles), cols)]
    return np.vstack(rows)
