"""Dịch văn bản sang tiếng Việt qua Google Dịch (2 cách gọi, tự chuyển khi lỗi), có bộ nhớ đệm và gom lô."""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from collections import OrderedDict
from urllib.parse import quote

import requests

from .textutil import clean_text, dominant_script, same_text

log = logging.getLogger(__name__)

SKIP = "\x00"  # đánh dấu: không cần hiển thị (đã là tiếng Việt / giống bản gốc)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)


class ProviderError(Exception):
    pass


class RateLimited(ProviderError):
    pass


PERSIST_MAX_LEN = 80       # chỉ lưu chữ ngắn (nhãn, menu, nút) - không lưu đoạn văn/tin nhắn
PERSIST_MAX_ITEMS = 8000
_PRIVATE = re.compile(r"@|https?://|www\.|\d[\d .\-]{3,}\d")  # email, link, dãy số (sđt, mã, số tiền)


def persistable(src: str, value) -> bool:
    """Có nên ghi cặp này xuống đĩa không: chỉ chữ giao diện ngắn, không có thông tin riêng tư."""
    return (value is not None and 0 < len(src) <= PERSIST_MAX_LEN and len(value) <= 4 * PERSIST_MAX_LEN
            and not _PRIVATE.search(src))


class TranslationCache:
    """Bộ nhớ đệm LRU an toàn đa luồng: văn bản gốc -> bản dịch (hoặc SKIP).

    Có thể ghi xuống đĩa để lần mở sau hiện bản dịch ngay, không phải hỏi lại Google: chỉ lưu
    chữ giao diện ngắn (<= 80 ký tự), bỏ qua email, đường link và dãy số."""

    def __init__(self, maxsize: int = 20000):
        self.maxsize = maxsize
        self._d: OrderedDict = OrderedDict()
        self._lock = threading.Lock()
        self.dirty = False

    def get(self, key: str):
        with self._lock:
            v = self._d.get(key)
            if v is not None:
                self._d.move_to_end(key)
            return v

    def put(self, key: str, value: str) -> None:
        with self._lock:
            if self._d.get(key) != value and persistable(key, value):
                self.dirty = True
            self._d[key] = value
            self._d.move_to_end(key)
            while len(self._d) > self.maxsize:
                self._d.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._d.clear()
            self.dirty = False

    def save(self, path: str, target: str = "vi") -> int:
        """Ghi các bản dịch nên giữ (mới dùng gần nhất) xuống đĩa. Trả về số mục đã ghi."""
        with self._lock:
            items = [[k, v] for k, v in self._d.items() if persistable(k, v)][-PERSIST_MAX_ITEMS:]
            self.dirty = False
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "target": target, "items": items}, f, ensure_ascii=False)
        os.replace(tmp, path)  # ghi nguyên khối: không bao giờ để lại file hỏng
        return len(items)

    def load(self, path: str, target: str = "vi") -> int:
        """Nạp bộ nhớ đã lưu (bỏ qua nếu thiếu file, hỏng hoặc khác ngôn ngữ đích)."""
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return 0
        if not isinstance(data, dict) or data.get("version") != 1 or data.get("target") != target:
            return 0
        n = 0
        with self._lock:
            for item in data.get("items", []):
                if (isinstance(item, list) and len(item) == 2 and all(isinstance(x, str) for x in item)
                        and persistable(item[0], item[1]) and item[0] not in self._d):
                    self._d[item[0]] = item[1]
                    n += 1
            while len(self._d) > self.maxsize:
                self._d.popitem(last=False)
        return n

    def __len__(self) -> int:
        return len(self._d)


# --- Nhà cung cấp -----------------------------------------------------------

def parse_google(data) -> tuple:
    """Đọc JSON của translate_a/single (dj=1 hoặc dạng mảng). Trả về (bản dịch, ngôn ngữ nguồn)."""
    if isinstance(data, dict):
        sents = data.get("sentences") or []
        trans = "".join(s.get("trans", "") for s in sents if isinstance(s, dict) and "trans" in s)
        return trans, data.get("src")
    if isinstance(data, list) and data:
        parts = data[0] or []
        trans = "".join(p[0] for p in parts if isinstance(p, list) and p and isinstance(p[0], str))
        src = data[2] if len(data) > 2 and isinstance(data[2], str) else None
        return trans, src
    raise ProviderError("Google trả về dữ liệu không đúng định dạng")


def _chunks_by_len(items: list, limit: int, measure, max_items: int = 1000):
    chunk, size = [], 0
    for t in items:
        n = measure(t) + 3
        if chunk and (size + n > limit or len(chunk) >= max_items):
            yield chunk
            chunk, size = [], 0
        chunk.append(t)
        size += n
    if chunk:
        yield chunk


class GoogleProvider:
    """translate_a/single (client=gtx): gom lô bằng cách nối các dòng bằng ký tự xuống dòng."""

    name = "google_gtx"
    URL = "https://translate.googleapis.com/translate_a/single"
    GET_LIMIT = 4200  # độ dài q sau khi mã hoá URL

    def __init__(self, session: requests.Session):
        self.s = session

    def translate(self, texts: list, target: str) -> list:
        # cả lô chỉ có MỘT ngôn ngữ nguồn => gom riêng từng hệ chữ (Latin, Hán, Kana...)
        groups: dict = {}
        for i, t in enumerate(texts):
            groups.setdefault(dominant_script(t), []).append(i)
        out: list = [None] * len(texts)
        for idxs in groups.values():
            items = [texts[i] for i in idxs]
            res = []
            for chunk in _chunks_by_len(items, self.GET_LIMIT, lambda t: len(quote(t, safe="")), 80):
                res.extend(self._chunk(chunk, target))
            for i, r in zip(idxs, res):
                out[i] = r
        return out

    def _chunk(self, chunk: list, target: str) -> list:
        if len(chunk) == 1:
            return [self._one(chunk[0], target)]
        trans, _src = self._request("\n".join(chunk), target)
        parts = trans.replace("\r\n", "\n").split("\n")
        if len(parts) == len(chunk):
            # ngôn ngữ nguồn của cả lô không đúng cho từng dòng => bỏ qua
            return [(p.strip(), None) for p in parts]
        log.debug("Google: lô %d dòng trả về %d dòng, dịch lẻ từng dòng", len(chunk), len(parts))
        return [self._one(t, target) for t in chunk]

    def _one(self, text: str, target: str) -> tuple:
        trans, src = self._request(text, target)
        return trans.strip(), src

    def _request(self, q: str, target: str) -> tuple:
        params = [
            ("client", "gtx"), ("sl", "auto"), ("tl", target), ("dt", "t"),
            ("dj", "1"), ("ie", "UTF-8"), ("oe", "UTF-8"),
        ]
        if len(quote(q, safe="")) <= self.GET_LIMIT:
            r = self.s.get(self.URL, params=params + [("q", q)], timeout=(5, 12))
        else:
            r = self.s.post(self.URL, params=params, data={"q": q}, timeout=(5, 15))
        if r.status_code in (429, 503):
            raise RateLimited(f"Google giới hạn tốc độ (HTTP {r.status_code})")
        if r.status_code != 200:
            raise ProviderError(f"Google HTTP {r.status_code}")
        try:
            data = r.json()
        except ValueError as e:
            raise ProviderError("Google trả về dữ liệu lạ") from e
        return parse_google(data)


class GoogleDictProvider:
    """translate_a/t (client=dict-chrome-ex): nhận nhiều tham số q một lúc và trả về
    [[bản dịch, ngôn ngữ phát hiện], ...] cho TỪNG câu."""

    name = "google"
    URL = "https://clients5.google.com/translate_a/t"
    URL_LIMIT = 6000  # tổng độ dài các q sau khi mã hoá

    def __init__(self, session: requests.Session):
        self.s = session

    def translate(self, texts: list, target: str) -> list:
        out = []
        for chunk in _chunks_by_len(texts, self.URL_LIMIT, lambda t: len(quote(t, safe="")), 100):
            out.extend(self._chunk(chunk, target))
        return out

    def _chunk(self, chunk: list, target: str) -> list:
        params = [("client", "dict-chrome-ex"), ("sl", "auto"), ("tl", target)] + [("q", t) for t in chunk]
        r = self.s.get(self.URL, params=params, timeout=(5, 12))
        if r.status_code in (429, 503):
            raise RateLimited(f"Google giới hạn tốc độ (HTTP {r.status_code})")
        if r.status_code != 200:
            raise ProviderError(f"Google HTTP {r.status_code}")
        try:
            data = r.json()
        except ValueError as e:
            raise ProviderError("Google trả về dữ liệu lạ") from e
        return parse_google_dict(data, len(chunk))


def parse_google_dict(data, n: int) -> list:
    """[["Xin chào","en"], ...] hoặc ["Xin chào", ...] -> [(bản dịch, ngôn ngữ|None), ...]."""
    if isinstance(data, str) and n == 1:
        return [(data, None)]
    if not isinstance(data, list) or len(data) != n:
        raise ProviderError("Google (dict) trả về số câu không khớp")
    out = []
    for item in data:
        if isinstance(item, str):
            out.append((item, None))
        elif isinstance(item, list) and item and isinstance(item[0], str):
            lang = item[1] if len(item) > 1 and isinstance(item[1], str) else None
            out.append((item[0], lang))
        else:
            raise ProviderError("Google (dict) trả về dữ liệu không đúng định dạng")
    return out


PROVIDER_NAMES = {"google": "Google Dịch", "google_gtx": "Google Dịch (dự phòng)"}

# ngôn ngữ đích (mã của Google Dịch)
TARGETS = [
    ("vi", "Tiếng Việt"), ("en", "Tiếng Anh"), ("zh-CN", "Tiếng Trung (giản thể)"),
    ("zh-TW", "Tiếng Trung (phồn thể)"), ("ja", "Tiếng Nhật"), ("ko", "Tiếng Hàn"), ("fr", "Tiếng Pháp"),
    ("de", "Tiếng Đức"), ("es", "Tiếng Tây Ban Nha"), ("ru", "Tiếng Nga"), ("th", "Tiếng Thái"),
    ("id", "Tiếng Indonesia"), ("lo", "Tiếng Lào"), ("km", "Tiếng Khmer"),
]


# --- Bộ điều phối -------------------------------------------------------------

class Translator:
    """Chạy nền: nhận danh sách câu cần dịch, gom lô, gọi dịch vụ, ghi vào cache."""

    MAX_BATCH = 120

    def __init__(self, cache: TranslationCache, target: str = "vi", provider: str = "google",
                 on_update=None, on_error=None):
        self.cache = cache
        self.target = target
        self.primary = provider if provider in PROVIDER_NAMES else "google"
        self.on_update = on_update
        self.on_error = on_error
        self.session = requests.Session()
        self.session.headers["User-Agent"] = UA
        self.providers = {
            "google": GoogleDictProvider(self.session),
            "google_gtx": GoogleProvider(self.session),
        }
        self._pending: dict = {}
        self._inflight: set = set()
        self._cv = threading.Condition()
        self._stop = False
        self._thread = None
        self._fail = {k: 0 for k in self.providers}
        self._demote_until = 0.0
        self.last_error = None
        self.active_provider = self.primary
        self.n_batches = 0     # số lần gọi dịch thành công (để ghi log hiệu năng)
        self.n_sentences = 0

    # -- điều khiển --
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="translator", daemon=True)
        self._thread.start()

    @property
    def thread(self):
        return getattr(self, "_thread", None)

    def stop(self) -> None:
        with self._cv:
            self._stop = True
            self._cv.notify_all()

    def set_provider(self, name: str) -> None:
        if name in PROVIDER_NAMES:
            self.primary = name
            self._demote_until = 0.0

    def set_target(self, target: str) -> None:
        """Đổi ngôn ngữ đích (bộ nhớ đệm phải được làm mới bởi bên gọi)."""
        with self._cv:
            self.target = target
            self._pending.clear()

    def request(self, texts: list) -> None:
        now = time.monotonic()
        new = False
        with self._cv:
            for t in texts:
                if t in self._inflight or self.cache.get(t) is not None:
                    continue
                if t not in self._pending:
                    new = True
                self._pending[t] = now
            if new:
                self._cv.notify()

    def pending_count(self) -> int:
        with self._cv:
            return len(self._pending) + len(self._inflight)

    # -- luồng nền --
    def _take_batch(self) -> list:
        now = time.monotonic()
        for t, ts in list(self._pending.items()):
            if now - ts > 8.0:  # đã cuộn qua, không còn trên màn hình
                del self._pending[t]
        items = sorted(self._pending.items(), key=lambda kv: -kv[1])[: self.MAX_BATCH]
        batch = [t for t, _ in items]
        for t in batch:
            del self._pending[t]
            self._inflight.add(t)
        return batch

    def _run(self) -> None:
        try:  # làm ấm kết nối TLS để bản dịch đầu tiên hiện nhanh hơn
            self.providers[self.primary].translate(["Hello"], self.target)
        except Exception:
            pass
        backoff = 0.0
        while True:
            with self._cv:
                while not self._stop and not self._pending:
                    self._cv.wait()
                if self._stop:
                    return
            time.sleep(0.05)  # gom thêm các yêu cầu đến sát nhau
            with self._cv:
                batch = self._take_batch()
                target = self.target
            if not batch:
                continue
            try:
                results = self.translate_now(batch)
                if target != self.target:  # vừa đổi ngôn ngữ đích giữa chừng: bỏ kết quả cũ
                    with self._cv:
                        self._inflight.difference_update(batch)
                    continue
                backoff = 0.0
                self.last_error = None
            except Exception as e:  # mạng lỗi, bị giới hạn...
                self.last_error = str(e) or e.__class__.__name__
                log.warning("Dịch lỗi: %s", self.last_error)
                with self._cv:
                    self._inflight.difference_update(batch)
                    now = time.monotonic()
                    for t in batch:
                        self._pending.setdefault(t, now)
                backoff = min(30.0, backoff * 2 if backoff else 2.0)
                cb = self.on_error
                if cb:
                    cb(self.last_error)
                with self._cv:
                    if not self._stop:
                        self._cv.wait(backoff)
                continue
            self.n_batches += 1
            self.n_sentences += len(batch)
            for src, val in results.items():
                self.cache.put(src, val)
            with self._cv:
                self._inflight.difference_update(batch)
            cb = self.on_update
            if cb:
                cb()

    # -- dịch đồng bộ --
    def translate_now(self, texts: list) -> dict:
        """Dịch ngay (chặn). Trả về {gốc: bản dịch hoặc SKIP}.

        Gửi tất cả trong MỘT yêu cầu: dịch vụ chính nhận diện ngôn ngữ cho từng câu, còn dịch vụ
        dự phòng tự gom theo hệ chữ - không phải chờ lần lượt từng nhóm."""
        pairs = self._call(list(texts))
        return {src: self._post(src, tr, lang) for src, (tr, lang) in zip(texts, pairs)}

    def _order(self) -> list:
        other = "google_gtx" if self.primary == "google" else "google"
        if time.monotonic() < self._demote_until:
            return [other, self.primary]
        return [self.primary, other]

    def _call(self, items: list) -> list:
        last = None
        for name in self._order():
            try:
                res = self.providers[name].translate(items, self.target)
                if len(res) != len(items):
                    raise ProviderError("số dòng trả về không khớp")
                self._fail[name] = 0
                self.active_provider = name
                return res
            except Exception as e:
                last = e
                self._fail[name] += 1
                log.info("%s lỗi (%s), thử dịch vụ khác", name, e)
                if name == self.primary and self._fail[name] >= 3:
                    self._demote_until = time.monotonic() + 300
        raise last if last else ProviderError("không có dịch vụ dịch")

    def _post(self, src: str, trans: str, lang) -> str:
        t = clean_text(trans or "")
        if not t:
            return SKIP
        if lang and str(lang).lower().split("-")[0] == self.target.split("-")[0]:
            return SKIP
        if same_text(src, t):
            return SKIP
        return t
