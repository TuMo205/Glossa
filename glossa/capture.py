"""Chụp màn hình bằng mss (GDI BitBlt). Phải tạo và dùng trong cùng một luồng."""

from __future__ import annotations

import numpy as np


class Capturer:
    def __init__(self):
        import mss  # import muộn: sau khi Qt đã đặt chế độ DPI cho tiến trình

        self._sct = mss.mss()

    def grab(self, rect: tuple) -> np.ndarray:
        """rect = (x, y, w, h) pixel thật trên màn hình ảo. Trả về mảng BGRA (H, W, 4)."""
        x, y, w, h = (int(v) for v in rect)
        shot = self._sct.grab({"left": x, "top": y, "width": max(1, w), "height": max(1, h)})
        return np.frombuffer(shot.raw, dtype=np.uint8).reshape(shot.height, shot.width, 4)

    def close(self) -> None:
        try:
            self._sct.close()
        except Exception:
            pass
