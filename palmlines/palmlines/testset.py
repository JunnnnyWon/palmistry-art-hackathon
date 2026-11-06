"""평가용 테스트 케이스 입출력.

케이스 = 이미지 파일 1장 또는 프레임 폴더(앱에서 c 키로 저장한 정지 구간 프레임들).
라벨 = 표준 손바닥 좌표계(0~1)의 손금 폴리라인. 이미지면 `<이름>.label.json`, 폴더면 `<폴더>/label.json`.
"""
from __future__ import annotations

import glob
import json
from pathlib import Path

import cv2
import numpy as np

from .hand import HandTracker
from .rectify import rectify

IMG_EXT = (".jpg", ".jpeg", ".png")


def expand_cases(patterns: list[str]) -> list[Path]:
    out = []
    for pat in patterns:
        for p in sorted(glob.glob(pat)) or [pat]:
            p = Path(p)
            if p.is_dir():
                if any(p.glob("frame_*.png")):
                    out.append(p)
                else:   # 테스트 세트 루트면 그 아래 케이스 폴더들
                    out += sorted(d for d in p.iterdir() if d.is_dir() and any(d.glob("frame_*.png")))
                    out += sorted(f for f in p.iterdir() if f.suffix.lower() in IMG_EXT)
            elif p.suffix.lower() in IMG_EXT and p.exists():
                out.append(p)
    return out


def load_frames(case: Path, max_side: int = 1600) -> list[np.ndarray]:
    files = sorted(case.glob("frame_*.png")) if case.is_dir() else [case]
    frames = []
    for f in files:
        img = cv2.imread(str(f))
        if img is None:
            continue
        s = max_side / max(img.shape[:2])
        frames.append(cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA) if s < 1 else img)
    return frames


def detect_all(tracker: HandTracker, frames: list[np.ndarray]):
    """손이 검출된 (프레임, 관측) 쌍만 반환."""
    pairs = []
    for f in frames:
        o = tracker.detect(f)
        if o is not None:
            pairs.append((f, o))
    return pairs


def median_roi(pairs, size: int = 512):
    rects = [rectify(f, o, size) for f, o in pairs]
    roi = np.median(np.stack([r.roi for r in rects]), axis=0).astype(np.uint8)
    lm = np.mean(np.stack([r.landmarks for r in rects]), axis=0).astype(np.float32)
    return roi, lm


def label_path(case: Path) -> Path:
    return case / "label.json" if case.is_dir() else case.with_suffix(".label.json")


def load_label(case: Path) -> dict | None:
    p = label_path(case)
    return json.loads(p.read_text()) if p.exists() else None


def save_label(case: Path, lines: list[dict], landmarks: np.ndarray):
    data = {"version": 1, "landmarks": landmarks.astype(float).round(5).tolist(), "lines": lines}
    label_path(case).write_text(json.dumps(data, ensure_ascii=False, indent=1))
