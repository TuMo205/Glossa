"""Tiện ích xử lý chuỗi: nhận diện hệ chữ, lọc dòng không cần dịch, ghép từ."""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

# --- Bảng hệ chữ -----------------------------------------------------------

_RANGES = {
    "han": ((0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xF900, 0xFAFF), (0x20000, 0x2FA1F)),
    "kana": ((0x3040, 0x30FF), (0x31F0, 0x31FF), (0xFF66, 0xFF9F)),
    "hangul": ((0x1100, 0x11FF), (0x3130, 0x318F), (0xAC00, 0xD7AF)),
    "cjkp": ((0x3000, 0x303F), (0xFF00, 0xFF65), (0xFE30, 0xFE4F)),
    "cyrl": ((0x0400, 0x052F),),
    "grek": ((0x0370, 0x03FF), (0x1F00, 0x1FFF)),
    "arab": ((0x0600, 0x06FF), (0x0750, 0x077F), (0xFB50, 0xFDFF), (0xFE70, 0xFEFF)),
    "hebr": ((0x0590, 0x05FF),),
    "thai": ((0x0E00, 0x0E7F),),
}


def char_script(ch: str) -> str:
    """Trả về tên hệ chữ của một ký tự."""
    cp = ord(ch)
    if cp < 0x0250 or 0x1E00 <= cp <= 0x1EFF:
        return "latn" if ch.isalpha() else "other"
    for name, ranges in _RANGES.items():
        for lo, hi in ranges:
            if lo <= cp <= hi:
                return name
    return "other"


def script_counts(text: str) -> dict:
    """Đếm số ký tự theo từng hệ chữ; khoá 'letters' = tổng số chữ cái."""
    counts = {"letters": 0}
    for ch in text:
        if ch.isspace():
            continue
        s = char_script(ch)
        counts[s] = counts.get(s, 0) + 1
        if ch.isalpha():
            counts["letters"] += 1
    return counts


def dominant_script(text: str) -> str:
    c = script_counts(text)
    best, n = "latn", 0
    for k, v in c.items():
        if k in ("letters", "other", "cjkp"):
            continue
        if v > n:
            best, n = k, v
    # Kanji + kana => tiếng Nhật
    if best == "han" and c.get("kana", 0) > 0:
        return "kana"
    return best


def is_nospace_char(ch: str) -> bool:
    """Ký tự thuộc hệ chữ viết liền không cách (Hán, Kana, dấu câu CJK)."""
    return char_script(ch) in ("han", "kana", "cjkp")


# --- Tiếng Việt -----------------------------------------------------------

# Chữ cái chỉ tiếng Việt mới có: ơ ư + các nguyên âm có dấu nặng/hỏi/ngã... (U+1EA0-U+1EF9)
_VI_UNIQUE = set("ơưƠƯ") | {chr(c) for c in range(0x1EA0, 0x1EFA)}
_VI_COMMON = set("ăđĂĐ")
_VI_SHARED = set("àáâãèéêìíòóôõùúýÀÁÂÃÈÉÊÌÍÒÓÔÕÙÚÝ")


def looks_vietnamese(text: str) -> bool:
    """Đoán chuỗi đã là tiếng Việt (để khỏi dịch lại)."""
    unique = common = shared = 0
    for ch in text:
        if ch in _VI_UNIQUE:
            unique += 1
        elif ch in _VI_COMMON:
            common += 1
        elif ch in _VI_SHARED:
            shared += 1
    if unique >= 1:
        return True
    return common >= 1 and shared >= 1


def strip_accents(text: str) -> str:
    t = text.replace("đ", "d").replace("Đ", "D")
    t = unicodedata.normalize("NFKD", t)
    return "".join(c for c in t if not unicodedata.combining(c))


def norm_key(text: str) -> str:
    """Khoá so sánh: bỏ dấu, chữ thường, chỉ giữ chữ và số."""
    return "".join(c for c in strip_accents(text).casefold() if c.isalnum())


def same_text(a: str, b: str, threshold: float = 0.8) -> bool:
    """Hai chuỗi (gần như) giống nhau sau khi bỏ dấu/khoảng trắng/dấu câu.
    Dùng để bỏ qua 'bản dịch' chỉ là chữ gốc thêm dấu hoặc sửa lỗi OCR."""
    ka, kb = norm_key(a), norm_key(b)
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    if min(len(ka), len(kb)) < 4:
        return False
    return SequenceMatcher(None, ka, kb, autojunk=False).ratio() >= threshold


# Âm tiết tiếng Việt thông dụng (đã bỏ dấu) mà gần như không phải từ tiếng Anh/châu Âu
_VI_SYLLABLES = set("""
cua khong nguoi duoc nhung voi cac khi toi minh nhu vao nhieu ngay thang chung dang cung biet
viet tieng tien viec truong thay sinh vien hoi muoi tren duoi ngoai truoc giua hoac chua xong
boi nhan xuong vui nhap khau trang chu kiem chon huy tep muc phan mem tinh dien thoai duong
thanh luu cai xem xin chao nhe nha anh bao lai dau gio phut giay tuan thoi nghe nhin thich
mua nuoc uong muon nhanh cham dep xau buon ngu dung chuyen nguyen huong dan doi gui bai
hom troi khoe tiep tuc thong tai tin moi nhom lien ket quen khach hang
""".split())

# Từ chức năng tiếng Anh/Pháp/Đức/Tây Ban Nha KHÔNG trùng âm tiết tiếng Việt bỏ dấu
# (đã loại: can/my/me/le/que/qui/con/mit/nao/com... vì trùng cần/mỹ/mẹ/lê/quê/quý/con/mít/nào/cơm)
_FOREIGN_STOP = set("""
and is are was were will would should could of for with you your this that from have has been
what when where which who how not please click here or if as by it its at we our they their his
her she does did shall about into than these those there
les des est je vous nous pour dans une sur avec pas die der das und ist nicht sie ich los las para
por una del
""".split())

# Âm tiết tiếng Việt (bỏ dấu) = phụ âm đầu (có thể không có) + vần hợp lệ
_VI_ONSET = r"(?:ngh|ng|nh|ch|gh|gi|kh|ph|th|tr|qu|[bcdghklmnprstvx])"
_VI_RHYMES = sorted(set("""
a ac ach ai am an ang anh ao ap at au ay e ec em en eng eo ep et eu ech enh i ia ich iec iem ien
ieng iep iet ieu im in inh ip it iu o oa oac oach oai oam oan oang oanh oao oap oat oay oc oe oen
oeo oet oi om on ong oo ooc oong op ot u ua uan uang uat uay uc ue uech uenh ui um un ung uo uoc
uoi uom uon uong uop uot uou up ut uu uy uya uych uyen uyet uyn uynh uyp uyt uyu y yem yen yet yeu
""".split()), key=len, reverse=True)
_VI_SYL_RE = re.compile(rf"^({_VI_ONSET})?({'|'.join(_VI_RHYMES)})$")
_DISTINCT_ONSETS = {"ngh", "ng", "nh", "kh", "gh"}
_DISTINCT_RHYME_PREFIX = ("uo", "ie", "ye", "oa", "oe", "uy")
_DISTINCT_RHYMES = {"anh", "inh", "enh", "ach", "ech"}
# Windows OCR tiếng Anh hay đọc dấu tiếng Việt thành các chữ này
_GARBLE = set("äåöüÄÅÖÜ")


def _vi_tokens(text: str) -> list:
    out = []
    for raw in re.findall(r"[^\W_]+", strip_accents(text).lower()):
        if raw.isdigit():
            continue
        t = raw.replace("0", "o").replace("6", "o")  # OCR đọc "ố/ơ" thành 0/6
        if any(c.isdigit() for c in t):
            continue  # mã số kiểu x4, H02, v2: trung tính, không phải tiếng Việt cũng không phải tiếng nước ngoài
        out.append(t)
    return out


def looks_unaccented_vietnamese(text: str) -> bool:
    """Đoán chữ tiếng Việt bị OCR làm mất/méo dấu, vd 'Cåi dät dä dddc Idu', 'Ban c6 mu6n luu khong?'."""
    toks = _vi_tokens(text)
    if not toks or any(t in _FOREIGN_STOP for t in toks):
        return False
    valid = distinct = 0
    for t in toks:
        m = _VI_SYL_RE.match(t)
        if not m:
            continue
        valid += 1
        onset, rhyme = m.group(1) or "", m.group(2)
        if (t in _VI_SYLLABLES or onset in _DISTINCT_ONSETS or rhyme.startswith(_DISTINCT_RHYME_PREFIX)
                or rhyme in _DISTINCT_RHYMES):
            distinct += 1
    n = len(toks)
    if n == 1:
        m = _VI_SYL_RE.match(toks[0])
        return bool(m) and (toks[0] in _VI_SYLLABLES or (m.group(1) or "") in _DISTINCT_ONSETS
                            or m.group(2).startswith(_DISTINCT_RHYME_PREFIX))
    ratio = valid / n
    garble = sum(1 for c in text if c in _GARBLE)
    return (ratio >= 0.7 and distinct >= 1) or (garble >= 2 and ratio >= 0.6)


# --- Lọc dòng không nên dịch ------------------------------------------------

_URL_RE = re.compile(r"(https?://|ftp://|www\.)\S+|[\w.+-]+@[\w-]+\.[\w.-]+", re.I)
_WINPATH_RE = re.compile(r"^[A-Za-z]:[\\/]|\\\\")
_FILENAME_RE = re.compile(r"^[\w\-]+\.[A-Za-z0-9]{1,5}$")
_IDENT_RE = re.compile(r"^[A-Za-z_][\w]*(\.[A-Za-z_]\w*)+\(?\)?$|^\w+_\w+$|^[a-z]+[A-Z]\w*$")


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def should_translate(text: str, min_letters: int = 2, target: str = "vi") -> bool:
    """True nếu dòng chữ đáng để dịch. Khi dịch sang tiếng Việt thì bỏ qua chữ đã là tiếng Việt
    (dịch sang ngôn ngữ khác thì để dịch vụ dịch tự nhận ra chữ đã đúng ngôn ngữ đích)."""
    t = text.strip()
    if not t:
        return False
    letters = sum(1 for c in t if c.isalpha())
    if letters < min_letters:
        return False
    nonspace = sum(1 for c in t if not c.isspace())
    if letters / max(1, nonspace) < 0.45:  # toàn số liệu, ký hiệu, code
        return False
    if _URL_RE.search(t) and len(_URL_RE.sub("", t).strip()) < 3:
        return False
    if " " not in t and (_WINPATH_RE.search(t) or _FILENAME_RE.match(t) or _IDENT_RE.match(t)):
        return False
    if "/" in t and " " not in t and t.count("/") >= 2:
        return False
    if str(target).lower().startswith("vi") and (looks_vietnamese(t) or looks_unaccented_vietnamese(t)):
        return False
    return True


# --- Ghép từ/dòng -----------------------------------------------------------

def join_words(words: list[str]) -> str:
    """Ghép từ OCR: hệ chữ Hán/Kana viết liền, còn lại cách bằng dấu cách."""
    out = ""
    for w in words:
        w = w.strip()
        if not w:
            continue
        if not out:
            out = w
        elif is_nospace_char(out[-1]) and is_nospace_char(w[0]):
            out += w
        elif is_nospace_char(out[-1]) and w[0] in ",.!?;:)]}%":
            out += w
        else:
            out += " " + w
    return out


def join_lines(lines: list[str]) -> str:
    """Ghép các dòng của một đoạn văn (xử lý gạch nối cuối dòng)."""
    out = ""
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        if not out:
            out = ln
        elif out.endswith("-") and len(out) > 1 and out[-2].isalpha() and ln[0].islower():
            out = out[:-1] + ln
        elif is_nospace_char(out[-1]) and is_nospace_char(ln[0]):
            out += ln
        else:
            out += " " + ln
    return out
