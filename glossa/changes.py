"""Phát hiện vùng màn hình thay đổi để chỉ OCR lại phần cần thiết."""

from __future__ import annotations

import numpy as np


class ChangeDetector:
    """So sánh ảnh thu nhỏ (kênh xanh lá, lấy mẫu 1/factor) giữa hai lần chụp.

    - factor: hệ số lấy mẫu (4 => 1920x1080 còn 480x270)
    - cell: kích thước ô lưới tính theo pixel ảnh thu nhỏ
    - pix_thr: chênh lệch độ sáng để coi một điểm là đã đổi
    - cell_min: số điểm đổi tối thiểu để coi cả ô là đã đổi
    """

    def __init__(self, factor: int = 4, cell: int = 12, pix_thr: int = 28, cell_min: int = 3):
        self.f = factor
        self.cell = cell
        self.pix_thr = pix_thr
        self.cell_min = cell_min

    def small(self, frame: np.ndarray) -> np.ndarray:
        return frame[:: self.f, :: self.f, 1].astype(np.int16)

    def diff_mask(self, ref: np.ndarray, cur: np.ndarray) -> np.ndarray:
        return np.abs(cur - ref) > self.pix_thr

    def cell_grid(self, mask: np.ndarray) -> np.ndarray:
        """Lưới bool (gh, gw): ô nào có đủ điểm thay đổi."""
        c = self.cell
        h, w = mask.shape
        gh, gw = -(-h // c), -(-w // c)
        padded = np.zeros((gh * c, gw * c), dtype=np.uint16)
        padded[:h, :w] = mask
        counts = padded.reshape(gh, c, gw, c).sum(axis=(1, 3))
        return counts >= self.cell_min

    def regions(self, grid: np.ndarray, frame_w: int, frame_h: int, margin: int = 12) -> list:
        """Các vùng chữ nhật (pixel thật) bao các cụm ô thay đổi liền kề."""
        if not grid.any():
            return []
        # nới 1 ô để các thay đổi gần nhau nhập làm một
        g = grid.copy()
        g[1:, :] |= grid[:-1, :]
        g[:-1, :] |= grid[1:, :]
        g2 = g.copy()
        g2[:, 1:] |= g[:, :-1]
        g2[:, :-1] |= g[:, 1:]
        labels = _label(g2)
        px = self.cell * self.f
        out = []
        for gy0, gx0, gy1, gx1 in labels:
            x0 = max(0, gx0 * px - margin)
            y0 = max(0, gy0 * px - margin)
            x1 = min(frame_w, (gx1 + 1) * px + margin)
            y1 = min(frame_h, (gy1 + 1) * px + margin)
            if x1 > x0 and y1 > y0:
                out.append((x0, y0, x1, y1))
        return merge_overlapping(out)

    def changed_fraction(self, mask: np.ndarray, rect: tuple) -> float:
        """Tỉ lệ điểm thay đổi bên trong rect (pixel thật)."""
        f = self.f
        x0, y0, x1, y1 = rect
        sx0, sy0 = max(0, int(x0 // f)), max(0, int(y0 // f))
        sx1, sy1 = min(mask.shape[1], int(-(-x1 // f))), min(mask.shape[0], int(-(-y1 // f)))
        if sx1 <= sx0 or sy1 <= sy0:
            return 0.0
        sub = mask[sy0:sy1, sx0:sx1]
        return float(sub.mean())

    def update_ref(self, ref: np.ndarray, cur: np.ndarray, rect: tuple) -> None:
        f = self.f
        x0, y0, x1, y1 = rect
        sx0, sy0 = max(0, int(x0 // f)), max(0, int(y0 // f))
        sx1, sy1 = min(ref.shape[1], int(-(-x1 // f))), min(ref.shape[0], int(-(-y1 // f)))
        ref[sy0:sy1, sx0:sx1] = cur[sy0:sy1, sx0:sx1]


def _label(grid: np.ndarray) -> list:
    """Gán nhãn thành phần liên thông (8 hướng) trên lưới nhỏ; trả về bbox từng cụm."""
    gh, gw = grid.shape
    seen = np.zeros_like(grid, dtype=bool)
    boxes = []
    ys, xs = np.nonzero(grid)
    for y, x in zip(ys.tolist(), xs.tolist()):
        if seen[y, x]:
            continue
        stack = [(y, x)]
        seen[y, x] = True
        y0 = y1 = y
        x0 = x1 = x
        while stack:
            cy, cx = stack.pop()
            y0, y1 = min(y0, cy), max(y1, cy)
            x0, x1 = min(x0, cx), max(x1, cx)
            for ny in (cy - 1, cy, cy + 1):
                if ny < 0 or ny >= gh:
                    continue
                for nx in (cx - 1, cx, cx + 1):
                    if 0 <= nx < gw and grid[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        stack.append((ny, nx))
        boxes.append((y0, x0, y1, x1))
    return boxes


def rects_intersect(a: tuple, b: tuple) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def rect_union(a: tuple, b: tuple) -> tuple:
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def rect_area(r: tuple) -> float:
    return max(0, r[2] - r[0]) * max(0, r[3] - r[1])


def merge_overlapping(rects: list) -> list:
    rs = list(rects)
    changed = True
    while changed:
        changed = False
        out = []
        while rs:
            r = rs.pop()
            i = 0
            while i < len(rs):
                if rects_intersect(r, rs[i]):
                    r = rect_union(r, rs.pop(i))
                    changed = True
                else:
                    i += 1
            out.append(r)
        rs = out
    return rs


def expand_with_segments(rect: tuple, segments: list, frame_w: int, frame_h: int, pad: int = 4) -> tuple:
    """Nới vùng để chứa trọn các cụm chữ cũ cắt ngang nó (tránh OCR nửa dòng)."""
    r = rect
    for _ in range(4):
        grown = r
        for s in segments:
            sr = (s.x - pad, s.y - pad, s.right + pad, s.bottom + pad)
            if rects_intersect(grown, sr):
                grown = rect_union(grown, sr)
        if grown == r:
            break
        r = grown
    x0, y0, x1, y1 = r
    return (max(0, int(x0)), max(0, int(y0)), min(frame_w, int(-(-x1 // 1))), min(frame_h, int(-(-y1 // 1))))


def ensure_min_size(rect: tuple, min_w: int, min_h: int, frame_w: int, frame_h: int) -> tuple:
    x0, y0, x1, y1 = rect
    if x1 - x0 < min_w:
        cx = (x0 + x1) // 2
        x0, x1 = max(0, cx - min_w // 2), min(frame_w, cx + min_w // 2)
    if y1 - y0 < min_h:
        cy = (y0 + y1) // 2
        y0, y1 = max(0, cy - min_h // 2), min(frame_h, cy + min_h // 2)
    return (x0, y0, x1, y1)
