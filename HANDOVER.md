# Bản bàn giao — không phải sản phẩm

Đây là **ảnh chụp để bàn giao** phần reverse-engineer đường tự động hoá Google Flow, cho dev tiếp
nhận đọc và dựng lại. Nó **không phải** một sản phẩm hoàn chỉnh, và một số năng lực trong đây **chưa
bao giờ được chạy thật**.

Đọc `docs/spec.md` trước mọi thứ khác.

---

## Xuất xứ — ba nguồn, ba tình trạng pháp lý khác nhau

Phải nói rõ vì repo này là công khai.

### 1. `crisng95/flowboard` — **KHÔNG có giấy phép**

Cây này là **fork** của `https://github.com/crisng95/flowboard`. Commit gốc trong history
(`602f953`, *"docs: point community group to Vibe Code Era"*) là của tác giả upstream, **không phải
của người bàn giao**.

Repo đó **không có file LICENSE và không khai giấy phép** (`license: null` theo GitHub API, kiểm
02/10/2026). Theo mặc định, điều đó nghĩa là **tác giả giữ toàn quyền** — không cấp quyền phân phối
lại hay sửa đổi. 73 file trong cây này là file sửa từ upstream, nên phần đó là phái sinh của code
không có giấy phép.

Ai dùng lại cần tự xác định điều kiện với tác giả upstream.

### 2. `crisng95/flowkit` — **MIT**

Tầng transport (`agent/flowboard/services/flow_batch.py` — codec envelope, các builder và reader
`batchexecute`, bộ golden test) được port từ `https://github.com/crisng95/flowkit`, **giấy phép MIT**,
mốc đã đối chiếu `d7977fd5` (20/09/2026). MIT cho phép dùng lại, chỉ cần giữ ghi nhận.

Phần này **khác với upstream ở đúng chỗ tiền**: flowkit gập một model key lạ về mặc định, còn bản này
**từ chối**. Lý do: `veo_3_1_i2v_s_fast_ultra_relaxed` (0 credit) khớp chuỗi con `ultra` nên bị gập vào
`veo_3_1_i2v_s_fast_ultra` (**trả phí**). Xem `docs/spec.md §4.0`.

### 3. Phép đo của người bàn giao — **của riêng**

Các số và kết luận trong `docs/spec.md` phần lớn là **đo trên tài khoản thật**, không phải đọc từ đâu.
Chúng được gắn nhãn trong tài liệu. Ví dụ đã chạy thật: tạo project · upload ảnh · sinh 1 ảnh · 3 clip
Omni 4s (4,01s · 720×1280 · h264+aac) · 1 clip Veo t2v 8s (8,00s · 1280×720) · đọc số dư credit · tạo
1 nhân vật có entityId thật.

Một kết luận đáng giá nhất, và nó chỉ có được bằng cách đo: **gói Pro KHÔNG có làn 0 credit.**
`veo_3_1_i2v_lite_low_priority` trả `PUBLIC_ERROR_MODEL_ACCESS_DENIED`. Ai dựng cả board 15 node trên
làn đó rồi mới biết là mất một buổi.

---

## Thang bằng chứng — đọc tài liệu theo nhãn này

Repo này cố ý phân biệt ba mức. Trộn chúng lại là cách vừa mất thời gian kiểm lại thứ đã chắc, vừa tin
tưởng thứ chưa ai thử.

| Nhãn | Nghĩa |
| --- | --- |
| 🟢 **ĐO THẬT** | Đã chạy trên tài khoản thật, có artefact |
| 🟡 **ĐỌC ĐƯỢC** | Đọc từ code/capture của người khác, chưa tự chạy |
| 🔴 **CHƯA KIỂM** | Suy luận, hoặc hai nguồn không khớp — **không dựa vào** |

Những thứ **chưa bao giờ chạy**, nói trước để không ai tưởng là xong: upscale 1080p · nối tiếp clip
(extend) · tạo scene. Code có, test có, nhưng chưa có lần chạy thật nào, và **giá của chúng chưa đo**.

Upscale 4K thì **bị từ chối có chủ ý**: hai capture độc lập không khớp ở ô tier, và 4K tốn 50 credit —
đoán ở đó là tiêu tiền của người dùng để biết ai đúng.

---

## Thứ KHÔNG có trong repo này, và vì sao

| Thứ | Vì sao |
| --- | --- |
| `RUN_VEO_3_ULTRA_PROMAX.exe` (526 MB) và gói release (591 MB) | Vượt giới hạn cứng 100 MB/file của GitHub |
| 9 template workflow đóng gói, kho tri thức, mẫu giọng | Nội dung của một tool thương mại. Code đọc chúng từ đĩa **lúc chạy**, không nhúng vào repo |
| Profile Chrome | Chứa cookie phiên của tài khoản thật |
| Khoá API | Nằm ở `~/.flowboard/secrets.json`, **ngoài repo** |

Vì vậy **repo này không chạy được một mình.** Nó cần bản tool đóng gói trên cùng máy để đọc asset, và
cần một tab Chrome đã đăng nhập Flow.

---

## Ràng buộc cấu trúc — đọc trước khi thiết kế lại

Google ngừng phát token `Bearer` cho Flow từ tháng 9/2026. Trang tự ký lệnh bằng cookie phiên cộng một
token CSRF sống theo từng lần tải trang, và **hai thứ đó không rời khỏi tab**.

Hệ quả: **không có chế độ headless.** Backend không gọi Flow một mình được — bắt buộc có một tab đã
đăng nhập và một extension đứng trong tab đó gửi lệnh hộ. Đây là cấu trúc, không phải cấu hình.

---

## Luật tiền — phần đừng bỏ khi viết lại

> **Thiếu thông tin thì TỪ CHỐI, không tự thay thế.**

Mỗi dòng dưới đây là một lỗi đã thật sự xảy ra trong quá trình dựng:

- key model **lạ** không bao giờ được coi là miễn phí — gập key lạ về mặc định chính là cách một làn
  0 credit biến thành làn trả phí;
- board xin làn A mà hệ thống **lặng lẽ** chạy làn B đắt hơn là lỗi, dù video vẫn ra;
- giá **chưa đo** phải hiện là *"chưa đo được giá"*, **không** hiện là 0 — số 0 là con số người ta dám bấm;
- vòng tự động (tự chấm rồi render lại) chỉ được chạy trên làn **đã đo** đúng 0 credit;
- upscale ghi vào key riêng, **không bao giờ** đè bản gốc — bản gốc là thứ đã trả tiền, và thay thế nó
  là mất không đảo được.

Và một việc đã **từ chối không làm**, nên giữ nguyên quyết định đó: tool đóng gói có đoạn bịa telemetry
giả gửi Google cho giống người thật. Đó là đánh lừa hệ thống chống lạm dụng, và cái mất khi bị phát
hiện là **tài khoản của người dùng**.

---

## Trạng thái đo được lúc bàn giao (02/10/2026)

- backend **2811 test passed** + 2 lỗi chỉ xảy ra trên POSIX + 2 skip
- frontend **230 test passed**, `tsc` sạch, `vite build` sạch
- `ruff check .` sạch
- đột biến: 6 bộ runner trong `agent/tools/mutations/` và `frontend/tools/mutations/`

Chạy test cần đúng interpreter trong `agent/.venv` — xem `.claude/.ckignore` để biết vì sao.
