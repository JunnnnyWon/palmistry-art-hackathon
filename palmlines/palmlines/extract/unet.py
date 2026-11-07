"""Tier B: yeonsumia U-Net 손금 분할.

학습 분포를 맞추기 위해 원본 파이프라인의 입력 기하를 재현한다.
1. 원본 프레임의 21개 랜드마크 → 원본 템플릿(3:4 세로 프레임)으로 호모그래피
2. HSV 피부색 밖은 흰색으로 지움 (원본 remove_background)
3. 256×256으로 눌러 넣고 RGB/255 → U-Net → 로짓 > 0.03 이면 선
4. 결과 마스크를 다시 표준 좌표계 ROI로 되돌림
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from skimage.morphology import remove_small_objects, skeletonize

DEFAULT_CKPT = Path(__file__).resolve().parents[2] / "assets" / "checkpoints" / "yeonsumia_unet_aug_epoch70.pth"

# 원본 rectification.py 의 21점 템플릿 (x는 원본에서 1-x 로 반전된 값)
_T = np.float32([
    [0.48203, 0.90634], [0.60436, 0.81194], [0.67632, 0.67903], [0.73407, 0.57167], [0.78965, 0.50984],
    [0.56557, 0.51170], [0.59794, 0.36576], [0.61353, 0.27135], [0.61965, 0.19251], [0.49288, 0.49826],
    [0.48999, 0.32138], [0.48947, 0.21283], [0.48335, 0.12900], [0.42588, 0.51809], [0.40335, 0.35820],
    [0.39381, 0.26169], [0.38609, 0.17752], [0.36369, 0.56422], [0.33553, 0.44737], [0.32091, 0.37496],
    [0.31214, 0.30270]])
TEMPLATE = np.stack([1 - _T[:, 0], _T[:, 1]], axis=1)
FRAME_W, FRAME_H = 768, 1024     # 원본 입력(아이폰 세로 사진) 비율
NET_SIZE = 256
LOGIT_THRESH = 0.03


@dataclass
class UNetResult:
    prob: np.ndarray        # float32 0~1, 표준 ROI 크기
    binary: np.ndarray      # bool
    skeleton: np.ndarray    # bool
    net_input: np.ndarray   # 디버그: 모델에 들어간 256 RGB


def _remove_background(bgr: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    skin = cv2.inRange(hsv, np.array([0, 20, 80], np.uint8), np.array([50, 255, 255], np.uint8))
    out = bgr.copy()
    g = cv2.bitwise_and(bgr, bgr, mask=skin)[:, :, 1]
    out[g <= 10] = 255
    return out


class UNetExtractor:
    def __init__(self, ckpt: str | Path = DEFAULT_CKPT, device: str | None = None, bn_mode: str = "eval"):
        """bn_mode: 원본 추론 코드는 net.eval()을 부르지 않아 BatchNorm이 학습 모드(배치 통계)로 돈다.
        "train"이 원본 재현, "eval"은 저장된 이동평균 사용.
        샘플 비교에서 eval이 선을 더 온전하게 이어서 기본값으로 둔다."""
        import torch
        from .unet_model import UNet
        self.torch = torch
        if device is None:
            device = ("cuda" if torch.cuda.is_available() else
                      "mps" if torch.backends.mps.is_available() else "cpu")
        self.device = torch.device(device)
        self.net = UNet(3, 1)
        self.net.load_state_dict(torch.load(str(ckpt), map_location="cpu", weights_only=True))
        self.net.to(self.device)
        self.set_bn_mode(bn_mode)
        import contextlib
        import threading
        self._lock = threading.Lock() if self.device.type != "cuda" else contextlib.nullcontext()

    def set_bn_mode(self, mode: str):
        self.bn_mode = mode
        self.net.train() if mode == "train" else self.net.eval()

    def logits_roi(self, frame_bgr: np.ndarray, landmarks_px: np.ndarray, M_roi: np.ndarray, S: int) -> tuple[np.ndarray, np.ndarray]:
        """원본 프레임 → 원본 템플릿 프레임 → U-Net → 표준 ROI 좌표의 로짓 (S×S).

        landmarks_px: 원본 프레임 픽셀 랜드마크, M_roi: 원본 → 표준 ROI 2x3 (rectify 결과).
        """
        torch = self.torch
        dst = (TEMPLATE * np.array([FRAME_W, FRAME_H], np.float32)).astype(np.float32)
        H, _ = cv2.findHomography(landmarks_px.astype(np.float32), dst, cv2.RANSAC, 5.0)
        frame = cv2.warpPerspective(frame_bgr, H, (FRAME_W, FRAME_H), flags=cv2.INTER_LINEAR,
                                    borderMode=cv2.BORDER_REPLICATE)
        frame = _remove_background(frame)
        small = cv2.resize(frame, (NET_SIZE, NET_SIZE), interpolation=cv2.INTER_NEAREST)
        rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
        x = torch.from_numpy(rgb.astype(np.float32) / 255.0).permute(2, 0, 1).unsqueeze(0).to(self.device)
        # 여러 워커 스레드가 함께 부른다. CUDA는 동시 호출에 안전하지만 Apple GPU(MPS)는 아니어서 잠근다
        with self._lock, torch.no_grad():
            logit = self.net(x)[0, 0].float().cpu().numpy()
        logit_full = cv2.resize(logit, (FRAME_W, FRAME_H), interpolation=cv2.INTER_LINEAR)
        # ROI 픽셀 q → 원본 p = M⁻¹q → 템플릿 프레임 H p
        Minv = cv2.invertAffineTransform(M_roi)
        T = H @ np.vstack([Minv, [0, 0, 1]])
        logit_roi = cv2.warpPerspective(logit_full, T, (S, S), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                                        borderMode=cv2.BORDER_CONSTANT, borderValue=-10.0)
        return logit_roi.astype(np.float32), rgb

    @staticmethod
    def postprocess(logit_roi: np.ndarray, mask: np.ndarray | None, net_input=None) -> UNetResult:
        prob = 1.0 / (1.0 + np.exp(-logit_roi))
        binary = logit_roi > LOGIT_THRESH
        if mask is not None:
            binary &= mask > 0
            prob = prob * (mask > 0)
        binary = remove_small_objects(binary, max_size=80)
        closed = cv2.morphologyEx(binary.astype(np.uint8), cv2.MORPH_CLOSE,
                                  cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))) > 0
        return UNetResult(prob.astype(np.float32), binary, skeletonize(closed), net_input)
