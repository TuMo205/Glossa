"""Ước lượng màu nền và màu chữ gốc để bản dịch trông như chữ thật trên màn hình."""

from __future__ import annotations

import numpy as np

DARK_STYLE = ((24, 26, 32), (255, 255, 255))
LIGHT_STYLE = ((250, 250, 250), (20, 20, 20))


def _lum(rgb) -> float:
    def ch(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = rgb
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast(a, b) -> float:
    la, lb = _lum(a), _lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def estimate_colors(frame: np.ndarray, rect: tuple, pad: int = 3) -> tuple:
    """frame: ảnh BGRA (H, W, 4). rect: (x0, y0, x1, y1). Trả về (nền RGB, chữ RGB)."""
    H, W = frame.shape[:2]
    x0, y0, x1, y1 = (int(round(v)) for v in rect)
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(W, max(x0 + 1, x1)), min(H, max(y0 + 1, y1))
    if x1 - x0 < 2 or y1 - y0 < 2:
        return DARK_STYLE

    # Viền bao quanh khối chữ => màu nền
    ring = []
    if y0 - pad >= 0:
        ring.append(frame[y0 - pad : y0, x0:x1, :3].reshape(-1, 3))
    if y1 + pad <= H:
        ring.append(frame[y1 : y1 + pad, x0:x1, :3].reshape(-1, 3))
    if x0 - pad >= 0:
        ring.append(frame[y0:y1, x0 - pad : x0, :3].reshape(-1, 3))
    if x1 + pad <= W:
        ring.append(frame[y0:y1, x1 : x1 + pad, :3].reshape(-1, 3))
    inner = frame[y0:y1, x0:x1, :3].reshape(-1, 3)
    ring_px = np.concatenate(ring) if ring else inner
    bg = np.median(ring_px, axis=0)

    # Điểm ảnh khác hẳn nền bên trong khối => màu chữ
    if len(inner) > 6000:
        inner = inner[:: len(inner) // 6000 + 1]
    d = np.abs(inner.astype(np.int16) - bg.astype(np.int16)).sum(axis=1)
    cand = d > 90
    bg_rgb = (int(bg[2]), int(bg[1]), int(bg[0]))
    if cand.sum() >= 6:
        dd = d[cand]
        thr = np.percentile(dd, 60)
        fg = np.median(inner[d >= thr], axis=0)
        fg_rgb = (int(fg[2]), int(fg[1]), int(fg[0]))
    else:
        fg_rgb = (0, 0, 0) if _lum(bg_rgb) > 0.4 else (255, 255, 255)

    if contrast(fg_rgb, bg_rgb) < 3.0:
        fg_rgb = (0, 0, 0) if _lum(bg_rgb) > 0.4 else (255, 255, 255)
    return bg_rgb, fg_rgb


BOLD_MIN_PX = 20.0     # chữ nhỏ hơn thì nét chỉ 1-2 px, đo độ đậm không đáng tin
BOLD_RATIO = 0.115     # bề dày nét / cỡ chữ: thường 0.075-0.10, đậm 0.125-0.21


def stroke_ratio(frame: np.ndarray, rect: tuple, bg: tuple, fg: tuple, font_px: float):
    """Bề dày nét chữ (trung vị độ dài các đoạn 'mực' theo hàng ngang) chia cỡ chữ, hoặc None."""
    H, W = frame.shape[:2]
    x0, y0, x1, y1 = (int(round(v)) for v in rect)
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(W, x1), min(H, y1)
    if x1 - x0 < 8 or y1 - y0 < 6 or font_px <= 0:
        return None
    crop = frame[y0:y1, x0:x1, :3].astype(np.int16)
    fg_bgr = np.array((fg[2], fg[1], fg[0]), np.int16)
    bg_bgr = np.array((bg[2], bg[1], bg[0]), np.int16)
    ink = np.abs(crop - fg_bgr).sum(axis=2) < np.abs(crop - bg_bgr).sum(axis=2)
    padded = np.pad(ink, ((0, 0), (1, 1))).astype(np.int8)
    d = np.diff(padded, axis=1)
    starts = np.argwhere(d == 1)
    ends = np.argwhere(d == -1)
    if len(starts) < 12 or len(starts) != len(ends):
        return None
    runs = ends[:, 1] - starts[:, 1]
    return float(np.median(runs)) / font_px


def is_bold(frame: np.ndarray, rect: tuple, bg: tuple, fg: tuple, font_px: float) -> bool:
    if font_px < BOLD_MIN_PX:
        return False
    r = stroke_ratio(frame, rect, bg, fg, font_px)
    return r is not None and r >= BOLD_RATIO
