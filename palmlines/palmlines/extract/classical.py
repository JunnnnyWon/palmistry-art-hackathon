"""Tier A: 고전 CV 손금 추출 (모델 없이 항상 동작하는 기준선).

정면화된 손바닥 ROI → 조명 평탄화 → CLAHE → 다중 스케일 어두운 릿지 필터(Sato)
→ 손바닥 마스크 → 히스테리시스 이진화 → 작은 조각 제거 → 스켈레톤.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np
from skimage.filters import apply_hysteresis_threshold, sato
from skimage.morphology import remove_small_objects, skeletonize


@dataclass
class ClassicalParams:
    sigmas: tuple[float, ...] = (2.0, 3.0, 4.0)   # 512px ROI 기준 주요 손금 굵기
    clahe_clip: float = 2.5
    clahe_grid: int = 8
    flat_sigma: float = 25.0       # 조명 평탄화용 큰 블러
    high_pct: float = 92.0         # 마스크 안 릿지 응답 상위 % (강한 선 씨앗)
    low_pct: float = 80.0          # 씨앗과 이어진 약한 선까지 확장
    min_area: int = 120            # 이보다 작은 조각 제거 (px)


@dataclass
class ClassicalResult:
    ridge: np.ndarray       # float32 0~1, 릿지 응답
    binary: np.ndarray      # bool, 선 영역
    skeleton: np.ndarray    # bool, 1px 중심선
    debug: dict = field(default_factory=dict)


def enhance(roi_bgr: np.ndarray, p: ClassicalParams) -> np.ndarray:
    """손금 대비를 키운 그레이 이미지 (uint8)."""
    lab = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2LAB)
    L = lab[:, :, 0].astype(np.float32)
    # 손바닥 주름은 피부보다 붉은 기가 적고 어두움 → 초록 채널을 섞어 대비 보강
    g = roi_bgr[:, :, 1].astype(np.float32)
    gray = 0.6 * L + 0.4 * g
    bg = cv2.GaussianBlur(gray, (0, 0), p.flat_sigma)
    flat = gray / np.maximum(bg, 1.0)
    flat = cv2.normalize(flat, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    clahe = cv2.createCLAHE(clipLimit=p.clahe_clip, tileGridSize=(p.clahe_grid, p.clahe_grid))
    return clahe.apply(flat)


def extract(roi_bgr: np.ndarray, mask: np.ndarray, p: ClassicalParams | None = None) -> ClassicalResult:
    p = p or ClassicalParams()
    m = mask > 0
    enh = enhance(roi_bgr, p)
    img = enh.astype(np.float32) / 255.0
    ridge = sato(img, sigmas=p.sigmas, black_ridges=True).astype(np.float32)
    ridge[~m] = 0.0
    vals = ridge[m]
    if vals.size == 0 or vals.max() <= 0:
        z = np.zeros_like(m)
        return ClassicalResult(ridge, z, z, {"enhanced": enh})
    hi = np.percentile(vals, p.high_pct)
    lo = np.percentile(vals, p.low_pct)
    binary = apply_hysteresis_threshold(ridge, lo, hi) & m
    binary = remove_small_objects(binary, max_size=p.min_area)
    # 스켈레톤 전 살짝 닫아 끊김을 줄인다
    closed = cv2.morphologyEx(binary.astype(np.uint8), cv2.MORPH_CLOSE,
                              cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))) > 0
    skel = skeletonize(closed & m)
    ridge_n = ridge / max(float(np.percentile(vals, 99.5)), 1e-6)
    return ClassicalResult(np.clip(ridge_n, 0, 1), binary, skel, {"enhanced": enh})
