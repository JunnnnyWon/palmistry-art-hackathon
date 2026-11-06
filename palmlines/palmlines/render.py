"""손 아웃라인 + 손금 렌더 (OpenCV 안티에일리어싱). P2에서 pycairo/SVG 출력으로 확장 예정."""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .model import PalmLineSet, Polyline
from .shape import prior_shape, prior_silhouette, view_transform

@dataclass
class Style:
    bg: tuple = (18, 16, 14)
    outline: tuple = (235, 230, 225)
    outline_px: float = 2.0
    line_colors: dict = None
    line_px: dict = None

    def __post_init__(self):
        self.line_colors = self.line_colors or {
            "heart": (110, 90, 255), "head": (255, 200, 90), "life": (120, 230, 140),
            "fate": (90, 210, 255), "minor": (170, 160, 150),
        }
        self.line_px = self.line_px or {"heart": 3.0, "head": 3.0, "life": 3.0, "fate": 2.5, "minor": 1.5}


def hand_silhouette(lm: np.ndarray, size: int) -> np.ndarray:
    """랜드마크(이미 캔버스 0~1 좌표)로 그린 기본 손 실루엣. 실시간 추적 화면용."""
    return prior_silhouette(lm, size)


def _poly(img, pts01: np.ndarray, size: int, color, px: float, closed=False):
    shift = 4
    p = np.round(pts01 * size * (1 << shift)).astype(np.int32)
    cv2.polylines(img, [p], closed, color, max(int(round(px)), 1), cv2.LINE_AA, shift)


def fit_transform(lm: np.ndarray, margin: float = 0.06):
    return view_transform(lm, margin)


def render(ls: PalmLineSet, size: int = 800, style: Style | None = None,
           lines: list[Polyline] | None = None) -> np.ndarray:
    st = style or Style()
    img = np.full((size, size, 3), st.bg, np.uint8)
    scale, offset = view_transform(ls.landmarks)
    tf = lambda p: p * scale + offset
    outline = ls.outline if ls.outline is not None else prior_shape(ls.landmarks).outline
    _poly(img, tf(outline), size, st.outline, max(st.outline_px * size / 800, 1), closed=True)
    px_scale = size / 800
    for l in sorted(lines if lines is not None else ls.lines, key=lambda l: l.kind != "minor"):
        _poly(img, tf(l.points), size, st.line_colors[l.kind], st.line_px[l.kind] * px_scale * 1.6)
    return img
