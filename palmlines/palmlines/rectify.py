"""손 랜드마크 → 표준 손바닥 좌표계 정면화.

손목(0)과 중지뿌리(9)로 회전·크기·위치를 맞추는 닮음변환을 쓴다.
원근 왜곡까지 펴는 호모그래피보다 덜 공격적이라 선 모양이 찌그러지지 않는다.
엄지는 항상 왼쪽에 오도록 필요하면 좌우 반전한다.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .hand import (HandObservation, WRIST, THUMB_CMC, THUMB_MCP, INDEX_MCP,
                   MIDDLE_MCP, RING_MCP, PINKY_MCP)

# 표준 좌표계에서 손목과 중지뿌리가 놓일 위치 (0~1)
# 엄지 두덩이(왼쪽)가 잘리지 않도록 축을 약간 오른쪽에 둔다
CANON_WRIST = np.array([0.56, 0.93], dtype=np.float32)
CANON_MIDDLE = np.array([0.56, 0.25], dtype=np.float32)


@dataclass
class Rectified:
    roi: np.ndarray            # (S, S, 3) BGR
    M: np.ndarray              # 2x3, 원본 픽셀 → ROI 픽셀
    landmarks: np.ndarray      # (21, 2) 0~1 표준 좌표
    mirrored: bool             # 왼손 등으로 좌우 반전했는가
    size: int


def _similarity(src_a, src_b, dst_a, dst_b, mirror: bool) -> np.ndarray:
    """src_a→dst_a, src_b→dst_b 를 만족하는 닮음변환 2x3 행렬. mirror면 x 반전을 먼저 적용."""
    F = np.diag([-1.0, 1.0]) if mirror else np.eye(2)
    sa = F @ np.asarray(src_a, np.float64)
    sb = F @ np.asarray(src_b, np.float64)
    da, db = np.asarray(dst_a, np.float64), np.asarray(dst_b, np.float64)
    vs, vd = sb - sa, db - da
    scale = np.linalg.norm(vd) / max(np.linalg.norm(vs), 1e-6)
    ang = np.arctan2(vd[1], vd[0]) - np.arctan2(vs[1], vs[0])
    c, s = np.cos(ang) * scale, np.sin(ang) * scale
    R = np.array([[c, -s], [s, c]])
    t = da - R @ sa
    return np.hstack([R @ F, t[:, None]]).astype(np.float32)


def rectify(bgr: np.ndarray, obs: HandObservation, size: int = 512) -> Rectified:
    lm = obs.landmarks_px
    mirror = not obs.thumb_on_left
    M = _similarity(lm[WRIST], lm[MIDDLE_MCP], CANON_WRIST * size, CANON_MIDDLE * size, mirror)
    roi = cv2.warpAffine(bgr, M, (size, size), flags=cv2.INTER_CUBIC,
                         borderMode=cv2.BORDER_REPLICATE)
    pts = (np.hstack([lm, np.ones((21, 1), np.float32)]) @ M.T) / size
    return Rectified(roi, M, pts.astype(np.float32), mirror, size)


def palm_polygon(lm: np.ndarray) -> np.ndarray:
    """표준 좌표계 랜드마크로 손바닥(손가락 제외) 다각형을 만든다. 반환은 0~1 좌표."""
    w, tc, tm = lm[WRIST], lm[THUMB_CMC], lm[THUMB_MCP]
    i, m, r, p = lm[INDEX_MCP], lm[MIDDLE_MCP], lm[RING_MCP], lm[PINKY_MCP]
    palm_w = np.linalg.norm(i - p)
    across = (p - i) / max(palm_w, 1e-6)                  # 검지→소지 방향 단위벡터
    down = (w - m) / max(np.linalg.norm(w - m), 1e-6)     # 중지뿌리→손목 단위벡터
    # 손가락 사이 주름을 피하려고 MCP 라인을 살짝 아래로 내린다
    knuckle_shift = down * 0.06 * palm_w
    pinky_edge = p + across * 0.18 * palm_w + down * 0.10 * palm_w
    wrist_pinky = w + across * 0.42 * palm_w - down * 0.04 * palm_w
    wrist_thumb = w - across * 0.30 * palm_w - down * 0.04 * palm_w
    thumb_side = tc - across * 0.12 * palm_w
    poly = np.array([
        i + knuckle_shift - across * 0.05 * palm_w,
        m + knuckle_shift,
        r + knuckle_shift,
        p + knuckle_shift + across * 0.05 * palm_w,
        pinky_edge,
        wrist_pinky,
        wrist_thumb,
        thumb_side,
        tm + down * 0.15 * palm_w,
        (tm + i) / 2 + knuckle_shift,
    ], dtype=np.float32)
    return poly


def palm_mask(lm: np.ndarray, size: int, erode_px: int = 6) -> np.ndarray:
    poly = (palm_polygon(lm) * size).astype(np.int32)
    mask = np.zeros((size, size), np.uint8)
    cv2.fillPoly(mask, [poly], 255, lineType=cv2.LINE_AA)
    mask = (mask > 127).astype(np.uint8) * 255
    if erode_px > 0:
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * erode_px + 1,) * 2)
        mask = cv2.erode(mask, k)
    return mask
