"""화면 표시 보조: OpenCV가 그리지 못하는 한글 텍스트."""
from __future__ import annotations

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

FONT_PATH = "/System/Library/Fonts/AppleSDGothicNeo.ttc"


class TextDrawer:
    def __init__(self, path=FONT_PATH):
        self._fonts = {}
        self.path = path

    def __call__(self, img, text, xy, size=28, color=(255, 255, 255), anchor="la"):
        f = self._fonts.get(size)
        if f is None:
            try:
                f = ImageFont.truetype(self.path, size)
            except OSError:
                f = ImageFont.load_default()
            self._fonts[size] = f
        pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
        d = ImageDraw.Draw(pil)
        d.text(xy, text, font=f, fill=color[::-1], anchor=anchor, stroke_width=2, stroke_fill=(0, 0, 0))
        img[:] = cv2.cvtColor(np.asarray(pil), cv2.COLOR_RGB2BGR)
