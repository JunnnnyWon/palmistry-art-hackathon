"""프레임(들)에서 PalmLineSet을 만드는 진입점.

method
- "classical": 고전 CV (모델 불필요)
- "unet"     : yeonsumia U-Net
- "merged"   : 계층 방식. 주요 선(감정·두뇌·생명·운명)은 U-Net에서, U-Net 선과 겹치지 않는
               고전 CV 선은 '기타(minor)' 잔선으로 덧붙인다. 잔선은 확장 단계에서 노브로 드러낼 재료.
"""
from __future__ import annotations

import time

import cv2
import numpy as np

from .extract import classical
from .extract.classify import classify
from .extract.postprocess import to_polylines
from .hand import HandObservation
from .model import PalmLineSet
from .rectify import Rectified, palm_mask, rectify
from .shape import clip_lines, outline_mask, prior_shape, refine_shape

METHODS = ("classical", "unet", "merged")
_UNET = None


def get_unet():
    """U-Net은 무겁고 torch가 필요하므로 처음 쓸 때 한 번만 로드한다."""
    global _UNET
    if _UNET is None:
        from .extract.unet import UNetExtractor
        _UNET = UNetExtractor()
        # Apple GPU(MPS) 첫 추론은 1초 이상 걸리므로 미리 한 번 돌려 둔다
        dummy = np.full((480, 640, 3), 200, np.uint8)
        lm = np.array([[320 + 60 * np.cos(a), 240 + 60 * np.sin(a)] for a in np.linspace(0, 6, 21)], np.float32)
        _UNET.logits_roi(dummy, lm, np.float32([[1, 0, 0], [0, 1, 0]]), 512)
    return _UNET


def _minor_from_classical(cls_lines, unet_bin: np.ndarray, size: int, reach_px: int = 10,
                          max_overlap: float = 0.3, max_lines: int = 8):
    """고전 CV 선 중 U-Net 선과 거의 겹치지 않는 것만 기타 선으로 남긴다."""
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * reach_px + 1,) * 2)
    near = cv2.dilate(unet_bin.astype(np.uint8), k) > 0
    out = []
    for l in cls_lines:
        p = np.clip((l.points * size).astype(int), 0, size - 1)
        if near[p[:, 1], p[:, 0]].mean() <= max_overlap:
            l.kind, l.confidence = "minor", 0.0
            out.append(l)
    return out[:max_lines]


def extract_lines(frames: list[tuple[np.ndarray, HandObservation]], method: str = "merged",
                  size: int = 512, params: classical.ClassicalParams | None = None,
                  keep_roi: bool = True, outline_mode: str = "traced") -> tuple[PalmLineSet, dict]:
    """frames: 같은 자세로 정지한 (프레임, 관측) 목록. 한 장이어도 된다."""
    if method not in METHODS:
        raise ValueError(f"method는 {METHODS} 중 하나")
    t0 = time.perf_counter()
    rects: list[Rectified] = [rectify(f, o, size) for f, o in frames]
    roi = np.median(np.stack([r.roi for r in rects]), axis=0).astype(np.uint8) if len(rects) > 1 else rects[0].roi
    lm = np.mean(np.stack([r.landmarks for r in rects]), axis=0).astype(np.float32)
    # 손 윤곽 하나를 추출 마스크·렌더 윤곽·선 잘라내기에 함께 쓴다
    # outline_mode: "traced" = 실제 손 경계에 맞춰 다듬은 윤곽, "simple" = 랜드마크 기반 단순 윤곽
    # 윤곽 보정(GrabCut)은 320px 캔버스에서 돌린다. 매끄럽게 다듬을 윤곽이라 해상도를 낮춰도 충분하다.
    shape = refine_shape(frames, rects, lm, 320) if outline_mode == "traced" else prior_shape(lm, size)
    inside = outline_mask(shape.outline, size)
    k = lambda r: cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
    inside_clip = cv2.erode(inside, k(max(size // 128, 2)))       # 선이 윤곽선에 겹치지 않게 약간 안쪽
    mask = cv2.bitwise_and(palm_mask(lm, size), cv2.erode(inside, k(max(size // 64, 3))))
    dbg: dict = {"mask": mask, "roi": roi, "shape_source": shape.source, "shape_iou": shape.iou_with_prior}

    cls = None
    if method in ("classical", "merged"):
        cls = classical.extract(roi, mask, params)
        dbg.update(enhanced=cls.debug.get("enhanced"), cls_ridge=cls.ridge, cls_binary=cls.binary)

    if method in ("unet", "merged"):
        from .extract.unet import UNetExtractor
        net = get_unet()
        logits, net_in = [], None
        for (f, o), r in zip(frames, rects):
            lg, net_in = net.logits_roi(f, o.landmarks_px, r.M, size)
            logits.append(lg)
        ures = UNetExtractor.postprocess(np.mean(logits, axis=0), mask, net_in)
        dbg.update(unet_prob=ures.prob, unet_binary=ures.binary, net_input=net_in)

    if method == "classical":
        lines = classify(to_polylines(cls.skeleton, cls.ridge, size), lm)
        binary, skel, ridge = cls.binary, cls.skeleton, cls.ridge
    else:
        # U-Net 선은 잡음이 적으므로 짧은 조각도 살린다
        lines = classify(to_polylines(ures.skeleton, ures.prob, size, min_len_ratio=0.05), lm)
        binary, skel, ridge = ures.binary, ures.skeleton, ures.prob
        if method == "merged":
            cls_lines = to_polylines(cls.skeleton, cls.ridge, size, max_lines=20)
            lines += _minor_from_classical(cls_lines, ures.binary, size)
            skel = skel | cls.skeleton
    lines = clip_lines(lines, inside_clip, size)
    ls = PalmLineSet(lines=lines, handedness=frames[-1][1].handedness, landmarks=lm,
                     roi_image=roi if keep_roi else None, source=method, outline=shape.outline)
    dbg.update(binary=binary, skeleton=skel, ridge=ridge, ms=(time.perf_counter() - t0) * 1000,
               rectified=rects[-1])
    return ls, dbg


def lines_from_frame(bgr: np.ndarray, obs: HandObservation, method: str = "merged", **kw):
    return extract_lines([(bgr, obs)], method=method, **kw)
