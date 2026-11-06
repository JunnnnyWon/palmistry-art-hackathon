"""손 모양(윤곽) — 추출 마스크, 렌더 윤곽, 선 잘라내기가 모두 이 하나의 모양을 쓴다.

1. 랜드마크로 해부학적 기본 윤곽을 만든다 (손바닥 다각형 + 손가락 캡슐).
   새끼손가락 쪽 두덩이와 엄지 두덩이를 바깥으로 부풀려 실제 손바닥 폭에 가깝게 한다.
2. 캡처 때는 실제 영상에서 GrabCut으로 경계를 다듬는다. 기본 윤곽을 강한 사전 정보로 써서,
   배경색이 피부와 비슷해도 윤곽이 크게 벗어나지 않게 한다. 결과가 기본 윤곽과 너무 다르면 버린다.
3. 최종 윤곽은 표준 좌표계(0~1) 다각형으로 PalmLineSet.outline 에 저장된다.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .hand import (INDEX_MCP, MIDDLE_MCP, PINKY_MCP, RING_MCP, THUMB_CMC, THUMB_MCP, WRIST)
from .model import Polyline

FINGER_CHAINS = [(1, 2, 3, 4), (5, 6, 7, 8), (9, 10, 11, 12), (13, 14, 15, 16), (17, 18, 19, 20)]
FINGER_WIDTH = [0.30, 0.25, 0.26, 0.24, 0.20]   # 엄지~소지 굵기 (손바닥 폭 대비)


def _axes(lm: np.ndarray):
    i, p, w, m = lm[INDEX_MCP], lm[PINKY_MCP], lm[WRIST], lm[MIDDLE_MCP]
    pw = float(np.linalg.norm(i - p))
    across = (p - i) / max(pw, 1e-6)                    # 검지 → 소지
    down = (w - m) / max(float(np.linalg.norm(w - m)), 1e-6)   # 손가락 → 손목
    return pw, across, down


def palm_outline_polygon(lm: np.ndarray) -> np.ndarray:
    """손가락을 뺀 손바닥 바깥 윤곽 (표준 좌표, 시계 방향). 손가락 캡슐과 합쳐 손 실루엣이 된다."""
    pw, a, d = _axes(lm)
    i, m, r, p = lm[INDEX_MCP], lm[MIDDLE_MCP], lm[RING_MCP], lm[PINKY_MCP]
    w, tc, tm = lm[WRIST], lm[THUMB_CMC], lm[THUMB_MCP]
    return np.array([
        i - a * 0.10 * pw - d * 0.02 * pw,          # 검지 뿌리 바깥
        i - d * 0.06 * pw,
        m - d * 0.06 * pw,
        r - d * 0.06 * pw,
        p - d * 0.04 * pw + a * 0.06 * pw,
        p + a * 0.17 * pw + d * 0.12 * pw,          # 새끼손가락 아래 바깥
        p + a * 0.22 * pw + d * 0.45 * pw,          # 새끼손가락 쪽 두덩이
        w + a * 0.42 * pw - d * 0.10 * pw,
        w + a * 0.36 * pw + d * 0.12 * pw,          # 손목 소지 쪽
        w - a * 0.34 * pw + d * 0.12 * pw,          # 손목 엄지 쪽
        w - a * 0.40 * pw - d * 0.12 * pw,
        tc - a * 0.18 * pw + d * 0.02 * pw,         # 엄지 두덩이
        tm - a * 0.06 * pw + d * 0.12 * pw,
        (tm + i) / 2 + a * 0.02 * pw,               # 엄지-검지 사이 물갈퀴
    ], dtype=np.float32)


def prior_silhouette(lm: np.ndarray, size: int, scale: float = 1.0, offset=(0.0, 0.0),
                     wrist_ext: float = 0.35) -> np.ndarray:
    """랜드마크 기반 손 실루엣 (uint8 0/255). 좌표 변환: px = (lm*scale + offset) * size."""
    off = np.asarray(offset, np.float32)
    tf = lambda q: (np.asarray(q, np.float32) * scale + off) * size
    pts = tf(lm)
    pw, a, d = _axes(lm)
    pw_px = pw * scale * size
    mask = np.zeros((size, size), np.uint8)
    poly = tf(palm_outline_polygon(lm))
    cv2.fillPoly(mask, [np.round(poly).astype(np.int32)], 255, cv2.LINE_AA)
    # 손목은 아래로 조금 연장 (화면 아래로 이어지는 팔)
    wr = lm[WRIST]
    wrist_quad = tf(np.array([wr + a * 0.36 * pw + d * 0.10 * pw, wr - a * 0.34 * pw + d * 0.10 * pw,
                              wr - a * 0.30 * pw + d * wrist_ext * pw, wr + a * 0.32 * pw + d * wrist_ext * pw]))
    cv2.fillPoly(mask, [np.round(wrist_quad).astype(np.int32)], 255, cv2.LINE_AA)
    for chain, wr_ratio in zip(FINGER_CHAINS, FINGER_WIDTH):
        t = max(int(pw_px * wr_ratio), 2)
        for s, e in zip(chain[:-1], chain[1:]):
            cv2.line(mask, tuple(np.round(pts[s]).astype(int)), tuple(np.round(pts[e]).astype(int)), 255, t, cv2.LINE_AA)
        cv2.circle(mask, tuple(np.round(pts[chain[-1]]).astype(int)), t // 2, 255, -1, cv2.LINE_AA)
    k = max(int(pw_px * 0.08) | 1, 3)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    mask = cv2.GaussianBlur(mask, (0, 0), pw_px * 0.02 + 1)
    return ((mask > 127) * 255).astype(np.uint8)


def view_transform(lm: np.ndarray, margin: float = 0.06):
    """손 전체가 캔버스에 들어오는 표준좌표 → 캔버스(0~1) 변환 (scale, offset)."""
    pw = float(np.linalg.norm(lm[INDEX_MCP] - lm[PINKY_MCP]))
    lo = lm.min(axis=0) - pw * 0.2
    hi = lm.max(axis=0) + pw * 0.2
    hi[1] = max(hi[1], lm[WRIST, 1] + pw * 0.3)
    span = float((hi - lo).max())
    scale = (1 - 2 * margin) / span
    offset = 0.5 - (lo + hi) / 2 * scale
    return scale, offset.astype(np.float32)


def contour_of(mask: np.ndarray) -> np.ndarray | None:
    cs, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not cs:
        return None
    c = max(cs, key=cv2.contourArea)
    c = cv2.approxPolyDP(c, 1.5, True)
    return c[:, 0, :].astype(np.float32)


@dataclass
class HandShape:
    outline: np.ndarray     # (N, 2) 표준 좌표 윤곽 다각형
    source: str             # "grabcut" | "prior"
    iou_with_prior: float


def refine_shape(frames, rects, lm: np.ndarray, size: int = 512, iters: int = 4,
                 min_iou: float = 0.78, band: float = 0.07) -> HandShape:
    """실제 영상으로 손 윤곽을 다듬는다. frames: [(BGR, HandObservation)], rects: 각 프레임 Rectified."""
    scale, offset = view_transform(lm)
    prior = prior_silhouette(lm, size, scale, offset)
    pw = float(np.linalg.norm(lm[INDEX_MCP] - lm[PINKY_MCP])) * scale * size

    # 각 프레임을 '손 전체 보기' 캔버스로 변환 (ROI 정면화 변환 뒤에 보기 변환을 합성)
    views, valid = [], None
    for (f, _), r in zip(frames, rects):
        K = scale * size / r.size
        A = (r.M * K).astype(np.float32)
        A[:, 2] += offset * size
        views.append(cv2.warpAffine(f, A, (size, size), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE))
        v = cv2.warpAffine(np.full(f.shape[:2], 255, np.uint8), A, (size, size), flags=cv2.INTER_NEAREST,
                           borderValue=0) > 0
        valid = v if valid is None else (valid & v)
    view = np.median(np.stack(views), axis=0).astype(np.uint8) if len(views) > 1 else views[0]

    def kern(r):
        r = max(int(r), 1)
        return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))

    sure_fg = cv2.erode(prior, kern(pw * 0.12)) > 0
    near = cv2.dilate(prior, kern(pw * 0.10)) > 0
    far = ~(cv2.dilate(prior, kern(pw * 0.22)) > 0)
    gc = np.full((size, size), cv2.GC_PR_BGD, np.uint8)
    gc[near] = cv2.GC_PR_BGD
    gc[prior > 0] = cv2.GC_PR_FGD
    gc[sure_fg] = cv2.GC_FGD
    gc[far] = cv2.GC_BGD
    try:
        bgd, fgd = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
        cv2.grabCut(view, gc, None, bgd, fgd, iters, cv2.GC_INIT_WITH_MASK)
        fg = np.isin(gc, (cv2.GC_FGD, cv2.GC_PR_FGD))
    except cv2.error:
        fg = prior > 0
    # 화면 밖(복제된 가장자리) 영역은 영상 근거가 없으므로 기본 윤곽을 따른다
    fg = np.where(valid, fg, prior > 0)
    # 기본 윤곽에서 band(손바닥 폭 대비) 안쪽·바깥쪽 띠 안에서만 경계를 움직인다.
    # 배경(머리카락, 피부색 벽 등)이 손에 붙어 혹이 생기거나 손가락이 끊기는 것을 막는다.
    inner = cv2.erode(prior, kern(pw * band)) > 0
    outer = cv2.dilate(prior, kern(pw * band)) > 0
    fg = (fg & outer) | inner
    m = (fg * 255).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, kern(pw * 0.03))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, kern(pw * 0.05))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m)
    if n > 1:
        m = ((lab == 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))) * 255).astype(np.uint8)
    # 단순한 선 그림이 되도록 충분히 매끄럽게
    m = ((cv2.GaussianBlur(m, (0, 0), pw * 0.035 + 1) > 127) * 255).astype(np.uint8)
    inter = np.logical_and(m > 0, prior > 0).sum()
    union = np.logical_or(m > 0, prior > 0).sum()
    iou = float(inter / max(union, 1))
    use = m if iou >= min_iou else prior
    c = contour_of(use)
    if c is None:
        c, use_src = contour_of(prior), "prior"
    else:
        use_src = "grabcut" if iou >= min_iou else "prior"
    outline = (c / size - offset) / scale
    return HandShape(outline.astype(np.float32), use_src, iou)


def prior_shape(lm: np.ndarray, size: int = 512) -> HandShape:
    scale, offset = view_transform(lm)
    c = contour_of(prior_silhouette(lm, size, scale, offset))
    return HandShape(((c / size - offset) / scale).astype(np.float32), "prior", 1.0)


def outline_mask(outline01: np.ndarray, size: int, scale: float = 1.0, offset=(0.0, 0.0)) -> np.ndarray:
    m = np.zeros((size, size), np.uint8)
    p = (outline01 * scale + np.asarray(offset, np.float32)) * size
    cv2.fillPoly(m, [np.round(p).astype(np.int32)], 255)
    return m


def clip_lines(lines: list[Polyline], inside: np.ndarray, size: int, min_len: float = 0.04) -> list[Polyline]:
    """윤곽 안쪽 마스크(inside, ROI 좌표) 밖으로 나간 부분을 잘라낸다.
    한 선이 여러 조각으로 갈리면 가장 긴 조각이 원래 종류를 갖고 나머지는 기타 선이 된다."""
    out = []
    for l in lines:
        q = np.clip(np.round(l.points * size).astype(int), 0, size - 1)
        ok = inside[q[:, 1], q[:, 0]] > 0
        if ok.all():
            out.append(l)
            continue
        pieces, cur = [], []
        for pt, flag in zip(l.points, ok):
            if flag:
                cur.append(pt)
            elif cur:
                pieces.append(np.array(cur, np.float32))
                cur = []
        if cur:
            pieces.append(np.array(cur, np.float32))
        pieces = [Polyline(p, "minor", 0.0, l.strength) for p in pieces if len(p) >= 2]
        pieces = [p for p in pieces if p.length >= min_len]
        if not pieces:
            continue
        pieces.sort(key=lambda p: p.length, reverse=True)
        pieces[0].kind, pieces[0].confidence = l.kind, l.confidence
        out += pieces
    return out
