# Glossa — dịch mọi chữ trên màn hình sang tiếng Việt

Glossa chạy ngầm ở khay hệ thống giống Unikey: biểu tượng **V đỏ** = đang dịch sang tiếng Việt,
**E xanh** = tạm dừng (đang xem chữ gốc). Nó liên tục chụp màn hình,
nhận dạng chữ bằng OCR có sẵn của Windows, dịch sang tiếng Việt rồi **vẽ bản dịch đè lên đúng chỗ
chữ gốc**, cùng màu nền và màu chữ, giống như trang đó vốn được viết bằng tiếng Việt.

- Bản dịch dùng **đúng cỡ chữ gốc** (chữ đậm vẫn đậm), nằm đúng dòng của chữ gốc. Câu dịch dài hơn thì
  khung được nới ra, chữ không bị thu nhỏ; các mục menu/thanh công cụ được xếp nối tiếp, không đè nhau.
- **Không giật:** chữ gốc không đổi thì bản dịch đứng yên tuyệt đối (rê chuột, con trỏ nhấp nháy, đồng hồ
  chạy… không làm nó nháy hay nhảy cỡ); bản dịch mới hiện mờ dần thay vì bật lên đột ngột.
- Bản dịch không chặn chuột: bạn vẫn bấm nút, cuộn trang, gõ phím như bình thường.
- Chữ đã là tiếng Việt được giữ nguyên, không dịch lại.
- **Đọc chữ nhỏ chính xác hơn:** lượt đầu đọc nhanh cả màn hình để bản dịch hiện ngay, sau đó tự đọc lại
  riêng các dòng chữ nhỏ (menu, nút, chữ giao diện dưới ~17px) ở độ phân giải gấp đôi rồi sửa bản dịch.
- **Nhớ bản dịch:** chữ giao diện ngắn đã dịch được nhớ qua các lần mở máy, mở lại là hiện ngay. Không lưu
  đoạn văn, tin nhắn, email, đường link hay dãy số. Xoá bằng menu **Xoá bộ nhớ bản dịch**.
- Khi màn hình đứng yên, chương trình gần như không tốn CPU (~0,3%, đứng yên lâu còn quét thưa hơn); khi chỉ một phần màn hình đổi
  (video, tin nhắn mới) nó chỉ đọc lại phần đó. Chữ mới hiện bản dịch sau khoảng 0,7 giây.

## Cài đặt

Cần Windows 10 (bản 1809 trở lên) hoặc Windows 11, 64-bit, có Internet. **Không cần cài Python, không cần
quyền Admin.**

1. Tải **Glossa-Setup-x.y.z.exe** ở trang <https://github.com/TuMo205/Glossa/releases/latest>.
2. Mở file vừa tải. Nếu Windows hiện *"Windows protected your PC"* (vì phần mềm miễn phí chưa mua chữ ký
   số), bấm **More info → Run anyway**.
3. Bấm **Tiếp** vài lần → **Cài đặt**. Có thể tích *Tự chạy Glossa mỗi khi bật máy*.
4. Glossa mở lên với bảng điều khiển; biểu tượng **V đỏ** nằm ở khay hệ thống (góc phải thanh tác vụ,
   có thể nằm trong mũi tên ^).

**Bản di động (không cần cài):** tải **Glossa-Portable-x.y.z.zip**, giải nén rồi chạy `Glossa.exe`. Cài đặt
được lưu trong thư mục `data` cạnh `Glossa.exe`, chép cả thư mục sang máy khác vẫn dùng được.

Máy báo chưa có gói nhận dạng chữ (OCR)? Bảng điều khiển → **Thêm tiếng…** → tích **Tiếng Anh** (và các
tiếng khác bạn cần) → **Cài**. Tự kiểm tra: Start → *Glossa - công cụ* → **Tự kiểm tra Glossa**.

## Cập nhật lên bản mới

Tải bộ cài bản mới và cài đè lên (nếu Glossa đang chạy, bộ cài sẽ nhắc thoát trước). Cài đặt và bộ nhớ bản
dịch được giữ nguyên. Đang dùng bản cũ tên **ViTranslator**? Cứ cài Glossa: lần mở đầu tiên Glossa tự chép
cài đặt cũ và chế độ khởi động cùng Windows sang; sau đó xoá thư mục `ViTranslator` cũ và lối tắt cũ trên
Desktop.

## Bảng điều khiển (giống Unikey)

Mở bằng **nhấp đúp** biểu tượng V/E ở khay, phím **Ctrl+Alt+P**, mục **Bảng điều khiển…** trong menu,
hoặc mở lại lối tắt **Glossa** khi chương trình đang chạy.

- Phần cơ bản: chế độ dịch (toàn màn hình / vùng đã chọn), đọc chữ tiếng (+ nút **Thêm tiếng…**),
  dịch sang tiếng nào, dịch vụ dịch, tốc độ quét,
  nút lớn **V Đang dịch / E Tạm dừng**.
- **Mở rộng ▾**: màu nền, cỡ chữ (100% = đúng cỡ gốc), độ che, phông chữ; các tuỳ chọn bật/tắt;
  **đổi phím tắt** (bấm vào ô rồi nhấn tổ hợp mới); xoá bộ nhớ bản dịch, cài thêm ngôn ngữ OCR, mặc định.
- Đổi gì có hiệu lực ngay và được lưu lại. **Đóng** chỉ ẩn bảng (chương trình vẫn chạy ngầm),
  **Kết thúc** mới thoát hẳn.
- Bỏ chọn **Bật bảng điều khiển khi khởi động** nếu không muốn bảng hiện mỗi lần mở máy.

Ở khay: **nhấp 1 lần** để chuyển V ↔ E (bật/tạm dừng), **nhấp đúp** mở bảng điều khiển,
**nhấp chuột giữa** ẩn/hiện bản dịch, **nhấp phải** mở menu.

## Phím tắt

| Phím | Tác dụng |
|---|---|
| **Ctrl+Alt+T** | Bật / tạm dừng dịch |
| **Ctrl+Alt+H** | Ẩn / hiện bản dịch (xem lại chữ gốc) |
| **Ctrl+Alt+R** | Khoanh một vùng để chỉ dịch vùng đó (phụ đề video, game, cửa sổ chat…) |
| **Ctrl+Alt+F** | Quay lại dịch toàn màn hình |
| **Ctrl+Alt+P** | Mở bảng điều khiển |
| **Ctrl+Alt+Q** | Thoát |

Đổi phím tắt trong **Bảng điều khiển → Mở rộng → Phím tắt** (có hiệu lực ngay, báo nếu bị trùng).

## Dịch nhiều thứ tiếng

**Đọc chữ tiếng nào:** Glossa đọc chữ bằng gói nhận dạng (OCR) của Windows, tiếng nào đã cài gói
thì đọc và dịch được tiếng đó. Chế độ **Tự động** dùng mọi gói đã cài cùng lúc và tự chọn kết quả đúng
hệ chữ cho từng dòng (một màn hình lẫn cả Anh, Trung, Nhật, Hàn… vẫn dịch đúng từng chỗ). Để không tốn
CPU, khi chỉ một phần màn hình thay đổi, chương trình chỉ chạy gói của những thứ tiếng đang có trên
màn hình; khoảng 20 giây một lần mọi gói đọc thử để phát hiện thứ tiếng mới.

- **Cài thêm tiếng:** Bảng điều khiển → **Thêm tiếng…** → tích chọn (Nhật, Hàn, Nga, Ả Rập, Hy Lạp,
  Trung phồn thể…) → **Cài các tiếng đã chọn** → bấm **Yes** khi Windows hỏi quyền Admin. Mỗi tiếng mất
  1–3 phút, cần Internet; cài xong chương trình tự dùng luôn, không cần mở lại.
- Tiếng Pháp, Đức, Tây Ban Nha… (chữ Latin) đã đọc được tạm bằng gói tiếng Anh; cài gói riêng giúp đọc
  đúng dấu hơn. Tiếng Thái, Hindi… Windows chưa có gói nhận dạng.
- Chỉ đọc một thứ tiếng (nhanh hơn, ít nhầm hơn): chọn tiếng đó ở ô **Đọc chữ tiếng**.
- Cách cũ vẫn dùng được: chạy **CAI_NGON_NGU_OCR.bat** và chọn số thứ tự.

**Dịch sang tiếng nào:** mặc định là tiếng Việt. Đổi ở ô **Dịch sang** (hoặc menu khay → *Dịch sang*):
Anh, Trung, Nhật, Hàn, Pháp, Đức, Tây Ban Nha, Nga, Thái, Indonesia, Lào, Khmer. Mỗi ngôn ngữ đích có
bộ nhớ bản dịch riêng. Khi dịch sang tiếng khác, chữ tiếng Việt trên màn hình cũng được dịch.

## Tuỳ chỉnh thêm (config.json)

Menu khay → *Mở file cấu hình*, sửa rồi chọn *Tải lại cấu hình*.

| Khoá | Ý nghĩa |
|---|---|
| `interval_ms` | Chu kỳ quét khi màn hình yên (ms). Vừa có chữ mới thì tự quét dày 0,15 giây/lần trong 1,5 giây |
| `cpu_budget` | Trần CPU cho việc quét (phần của một lõi, vd `0.3`). Bỏ trống = tự theo tốc độ quét: Nhanh 0.5, Vừa 0.3, Tiết kiệm pin 0.15 |
| `style` | `auto` (giống màu gốc), `dark`, `light` |
| `font_family`, `font_scale` | Phông chữ bản dịch; tỉ lệ cỡ chữ (`1.0` = đúng cỡ chữ gốc, `1.1` = to hơn 10%) |
| `refine_small_text` | Đọc lại chữ nhỏ ở độ phân giải 2× (`true`); tắt (`false`) để tiết kiệm CPU hơn |
| `persist_cache` | Nhớ bản dịch chữ giao diện ngắn qua các lần mở (`true`); `false` = không ghi gì xuống đĩa |
| `stale_fraction` | Phần khối chữ phải thay đổi thì mới gỡ bản dịch ngay (mặc định `0.25`; nhỏ hơn = nhạy hơn nhưng dễ nháy) |
| `box_opacity` | Độ đục nền bản dịch (1.0 = che hẳn chữ gốc) |
| `hover_reveal` | `true` = rê chuột lên bản dịch để thấy chữ gốc |
| `include_taskbar` | `true` = dịch cả thanh tác vụ |
| `exclude_from_capture` | `true` = bản dịch không lọt vào ảnh chụp/quay màn hình (xem lưu ý bên dưới) |
| `hotkeys` | Đổi phím tắt, ví dụ `"toggle": "Ctrl+Shift+F9"` |
| `max_ocr_engines` | Số gói OCR tối đa ở chế độ Tự động (mặc định 8, mỗi hệ chữ một gói) |
| `target_lang` | Ngôn ngữ đích, mã của Google Dịch (`vi`, `en`, `ja`, `zh-CN`…) |

## Lưu ý

- **Quyền riêng tư:** chữ trên màn hình được gửi tới Google Dịch để dịch. Khi xem
  nội dung nhạy cảm (ngân hàng, tin nhắn riêng…) hãy bấm **Ctrl+Alt+T** để tạm dừng.
- **Quay/chia sẻ màn hình:** mặc định bản dịch được ẩn khỏi mọi ảnh chụp và phần mềm quay màn hình
  (Zoom, OBS, Snipping Tool…), nên người xem chỉ thấy chữ gốc. Muốn quay cả bản dịch thì đặt
  `exclude_from_capture` = `false` (bản dịch sẽ nháy nhẹ mỗi lần quét).
- **Game toàn màn hình độc quyền** (exclusive fullscreen) sẽ che lớp phủ: chuyển game sang
  *Borderless/Windowed fullscreen*.
- Khi chơi game hoặc cần hiệu năng tối đa, tạm dừng dịch bằng **Ctrl+Alt+T**.
- Cài đặt (`config.json`), nhật ký lỗi (`logs`) và bộ nhớ bản dịch (`cache`) nằm ở `%APPDATA%\Glossa`
  (dán vào thanh địa chỉ của File Explorer để mở). Bảng điều khiển → **Mở thư mục nhật ký** mở ngay chỗ đó.

## Gỡ cài đặt

**Settings → Apps → Installed apps → Glossa → Uninstall** (hoặc Start → *Glossa - công cụ* → *Gỡ cài đặt*).
Bộ gỡ hỏi có xoá luôn cài đặt và bộ nhớ bản dịch hay không.

## Chạy từ mã nguồn (cho người lập trình)

1. Nhấp đúp **CAI_DAT.bat**: tự tìm Python 3.10–3.14 (không có thì cài bằng winget), tạo `.venv`, cài thư
   viện, tạo lối tắt trên Desktop. Chạy bằng **CHAY.bat**, tự kiểm tra bằng **KIEM_TRA.bat**, gỡ bằng
   **GO_CAI_DAT.bat**. Khi chạy từ mã nguồn, cài đặt và nhật ký nằm ngay trong thư mục mã nguồn.
2. Tự đóng gói: `python -m pip install pyinstaller pytest` rồi `python packaging\build.py`
   (cần [Inno Setup 6.5+](https://jrsoftware.org/isdl.php) cho bước tạo bộ cài). Kết quả ở thư mục `dist`.
   Mỗi lần đẩy mã lên nhánh `main`, GitHub Actions tự build và đăng bản mới lên trang Releases.

## Cấu trúc mã nguồn

```
app.py                    điểm khởi động (pythonw app.py; bản cài là Glossa.exe)
packaging/                đóng gói: Glossa.spec (PyInstaller), Glossa.iss (Inno Setup), build.py
glossa/
  controller.py           điều phối: lớp phủ, khay hệ thống, phím tắt
  engine.py               luồng chụp → phát hiện thay đổi → OCR → gom khối → yêu cầu dịch
  ocr_windows.py          Windows.Media.Ocr (nhiều ngôn ngữ cùng lúc)
  layout.py               gom từ → cụm → đoạn văn, ước lượng cỡ chữ
  changes.py              phát hiện vùng thay đổi để OCR từng phần
  translator.py           Google / Microsoft, gom lô, bộ nhớ đệm, tự chuyển dịch vụ khi lỗi
  overlay.py              cửa sổ trong suốt xuyên chuột vẽ bản dịch, chống giật (giữ vị trí/cỡ/bản dịch)
  panel.py                bảng điều khiển kiểu Unikey
  langdialog.py           hộp thoại cài thêm gói nhận dạng chữ cho nhiều thứ tiếng
  textlayout.py           đặt chữ đúng cỡ gốc & đường chân chữ, gom cỡ, xếp nối tiếp trên cùng hàng
  colors.py               đoán màu nền/màu chữ gốc, nhận biết chữ đậm
  paths.py, legacy.py     thư mục dữ liệu (mã nguồn / bản cài / bản di động), chuyển cài đặt từ bản cũ
  selector.py, hotkeys.py, winutil.py, icons.py, selftest.py
tests/                    kiểm thử (python -m pytest tests)
tools/calibrate_font.py   đo lại tỉ lệ ước lượng cỡ chữ trên máy
```
