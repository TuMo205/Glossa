"""Biểu tượng vẽ bằng code (không cần file ảnh), kiểu Unikey:
chữ V nền đỏ = đang dịch sang tiếng Việt, chữ E nền xanh = tạm dừng (đang xem chữ gốc)."""

from __future__ import annotations

import struct

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap

RED = "#D8231B"
BLUE = "#1F5FC6"


def _pixmap(size: int, active: bool) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(RED if active else BLUE))
    r = size * 0.2
    p.drawRoundedRect(QRectF(0, 0, size, size), r, r)
    f = QFont("Segoe UI")
    f.setWeight(QFont.Weight.Black)
    f.setPixelSize(max(9, int(size * 0.86)))
    p.setFont(f)
    p.setPen(QColor("#FFFFFF"))
    p.drawText(QRectF(0, size * 0.02, size, size), int(Qt.AlignmentFlag.AlignCenter.value), "V" if active else "E")
    p.end()
    return pm


def make_icon(active: bool = True) -> QIcon:
    icon = QIcon()
    for s in (16, 20, 24, 32, 40, 48, 64, 128, 256):
        icon.addPixmap(_pixmap(s, active))
    return icon


ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def _dib(img) -> bytes:
    """Ảnh 32 bit kiểu BMP trong file .ico (dòng từ dưới lên, kèm mặt nạ AND rỗng)."""
    w, h = img.width(), img.height()
    bpl = img.bytesPerLine()
    raw = bytes(img.constBits())[: bpl * h]
    rows = [raw[y * bpl: y * bpl + w * 4] for y in range(h)]
    header = struct.pack("<IiiHHIIiiII", 40, w, h * 2, 1, 32, 0, w * h * 4, 0, 0, 0, 0)
    mask = b"\x00" * (((w + 31) // 32) * 4 * h)
    return header + b"".join(reversed(rows)) + mask


def ico_bytes(active: bool = True, sizes=ICO_SIZES) -> bytes:
    """File .ico nhiều cỡ (16…256 px) để Windows hiển thị nét ở mọi chỗ (thanh tác vụ, Desktop, Start)."""
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    from PySide6.QtGui import QImage

    images = []
    for size in sizes:
        pm = _pixmap(size, active)
        if size >= 256:
            ba = QByteArray()
            buf = QBuffer(ba)
            buf.open(QIODevice.OpenModeFlag.WriteOnly)
            pm.save(buf, "PNG")
            buf.close()
            images.append((size, bytes(ba.data())))
        else:
            images.append((size, _dib(pm.toImage().convertToFormat(QImage.Format.Format_ARGB32))))
    out = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    body = b""
    for size, data in images:
        out += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset + len(body))
        body += data
    return out + body


def save_ico(path: str) -> bool:
    """Lưu biểu tượng ra file .ico (lối tắt trên Desktop, file Glossa.exe, bộ cài)."""
    try:
        data = ico_bytes()
        with open(path, "wb") as f:
            f.write(data)
        return True
    except Exception:
        return _pixmap(256, True).toImage().save(path, "ICO")
