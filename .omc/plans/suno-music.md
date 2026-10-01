# Suno — sinh nhạc nền (DEFERRED)

**Trạng thái:** DEFERRED từ 2026-09-01. Không lên lịch. Chờ user dán API key `sunoapi.org`.
**Phạm vi đã chốt với user:** port **đầy đủ như flowkit**, không cắt bớt.
**Ràng buộc cứng:** Suno **tiêu tiền thật** → phải hỏi user trước mỗi lần gọi sinh nhạc.

File này tồn tại để lần sau mở ra là làm được ngay, không phải khảo sát lại.

---

## Nguồn để port

| Thứ | Đường dẫn | Kích thước |
| --- | --- | --- |
| Client | `D:\TOOL_VIDEO\_upstream\flowkit\agent\services\suno.py` | 280 dòng |
| Routes | `D:\TOOL_VIDEO\_upstream\flowkit\agent\api\music.py` | 339 dòng |
| Config | `D:\TOOL_VIDEO\_upstream\flowkit\agent\config.py:100-102` | 3 dòng |

Lưu ý: `_upstream/` nằm **cùng cấp với** `flowboard-local`, không nằm bên trong nó.

Mặc định trong config gốc:

```python
SUNO_BASE_URL   = "https://api.sunoapi.org"
SUNO_MODEL      = "V4"
SUNO_CALLBACK_URL = f"http://{API_HOST}:{API_PORT}/api/music/callback"
```

## Bề mặt API của flowkit (đã đọc, không phải đoán)

Client `SunoClient`: `generate`, `get_task`, `poll_task`, `generate_lyrics`, `extend`,
`vocal_removal`, `convert_to_wav`, `get_credits`, cộng `_check_key` / `_check_response` và factory
`get_suno_client()`.

Routes `/api/music`:

| Route | Ghi chú khi port |
| --- | --- |
| `GET /templates`, `GET /templates/{id}` | Danh sách preset thể loại |
| `POST /generate` | Tốn tiền — cổng xác nhận nằm ở đây |
| `GET /tasks/{id}`, `POST /tasks/{id}/poll` | Đường **poll**, xem mục callback bên dưới |
| `POST /tasks/{id}/download` | **Điểm nối quan trọng**, xem bên dưới |
| `POST /generate-lyrics`, `POST /extend`, `POST /vocal-removal`, `POST /convert-to-wav` | Port thẳng |
| `POST /callback` | Xem mục callback |
| `GET /credits` | Nên gọi trước khi generate để hiện số dư |

### Callback không dùng được nguyên trạng

`SUNO_CALLBACK_URL` trỏ vào `http://{API_HOST}:{API_PORT}` — một địa chỉ **local**. Suno chạy trên
Internet nên không gọi ngược vào máy user được, trừ khi có tunnel. Vậy **đường poll
(`POST /tasks/{id}/poll`) mới là đường thật sự chạy** trong bản local này. Vẫn port endpoint
`/callback` cho đủ, nhưng đừng thiết kế luồng phụ thuộc vào nó.

### Điểm nối quan trọng

Clip tải về phải đi qua `media.ingest_local_file` để thành **media id**, nhờ đó `add_bgm` và
`edit_video` dùng được ngay mà không cần import tay.

## Khoảng trống thật mà nó vá — số liệu đo ngày 2026-09-01

Đây là chỗ tôi từng nói quá lên; số thật nhỏ hơn.

Thư viện nhạc nền tại `data_general/nhac_nen/` có **9 file**. Trong 9 workflow mẫu, **4 file template
tham chiếu nhạc** và cả 4 đều trỏ vào máy tác giả gốc:

| Template | Giá trị | Có giải quyết được không |
| --- | --- | --- |
| `nguoi_que_new.json` | `d:\ABCD\VEO3_GROK_NEW\...\1. Nhạc.MP3` | ✅ basename có trong thư viện |
| `nguoi_que_tao_tu_anh.json` | như trên | ✅ |
| `sao_chep_nguoi_que_new_1706.json` | như trên | ✅ |
| `thoi_trang_nu_cam_dien_thoai.json` | `F:/This PC/Downloads/nhac nền  hàn.mp3` | ❌ **không có trong thư viện** |

`postprod_plan._bgm_track` đã cắt đường dẫn chết về basename, nên **3/4 tham chiếu đã chạy được rồi**.
Ổ `d:\ABCD` và file `nhac nền  hàn.mp3` đều không tồn tại trên máy này (đã kiểm).

**Vậy giá trị thật của Suno là:** sinh nhạc gốc thay vì xoay vòng 9 track cố định, và vá đúng 1 tham
chiếu còn hỏng. Không phải "hiện không có cách nào lấy nhạc nền" — câu đó sai.

## Quyết định thiết kế đã chốt

- Key đọc qua `services/llm/secrets.py` — **không** dựng kho key thứ hai. Đã có sẵn đường cho key
  user dán.
- Không key ⇒ `available()` trả false + thông điệp rõ ràng, theo đúng khuôn `gemini_keys`. Không
  stack trace.
- UI: một tab/panel để sinh nhạc và chọn kết quả làm nhạc nền.

## Tiêu chí nghiệm thu khi mở lại

1. Không key → mọi endpoint trả lỗi rõ, không stack trace.
2. Có key → **một** lần sinh nhạc thật, tối thiểu, **hỏi user trước khi tiêu quota**.
3. Clip tải về xuất hiện như media id và `add_bgm` dùng được ngay — đây là thứ chứng minh điểm nối,
   không thay bằng unit test được.
4. Không log giá trị key.

## Câu hỏi còn treo

- User chưa cung cấp key `sunoapi.org`. Đây là thứ duy nhất chặn.
- `SUNO_MODEL` mặc định `V4`; chưa xác minh version nào còn được backend hỗ trợ tại thời điểm mở lại.
