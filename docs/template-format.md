# Định dạng file workflow đóng gói

Tài liệu này mô tả **định dạng** của các file workflow mà tool đóng gói đọc, để viết parser. Nó
**không chứa nội dung** của các template đó — nội dung thuộc về tác giả tool, và ai cần thì đọc trên
bản cài của chính mình.

Nguồn: `agent/flowboard/services/template_import.py` — bộ đọc thật trong repo này, đã chạy trên 9
template đóng gói và 11 file mẫu.

---

## Chỗ các file nằm

Hai thư mục, cả hai **chỉ đọc**:

```
<thư mục cài tool>/data_general/system_workflows/*.json   ← 9 template đóng gói
<thư mục cài tool>/Workflows/*.json                       ← workflow người dùng tự lưu
```

Repo này đọc chúng lúc chạy qua `ASSET_ROOT`; không nhúng bản sao nào. Thiếu thư mục thì danh sách
template trả về rỗng và `/api/health` bật cờ — không sập.

---

## Hình dạng một file

```json
{
  "nodes": [
    {
      "id": "...",
      "type": "<loại node của exe>",
      "x": 0, "y": 0, "w": 260, "h": 180,
      "settings": { "...": "..." }
    }
  ],
  "edges": [
    { "src": "<id node>", "dst": "<id node>", "srcPort": "...", "dstPort": "..." }
  ]
}
```

Ba điều về `settings` mà parser phải xử đúng:

1. **Giữ nguyên văn.** Mỗi loại node có bộ khoá riêng, và nhiều khoá chỉ có ý nghĩa với exe. Bỏ khoá
   lạ là mất cấu hình người dùng không dựng lại được. Repo này giữ cả cục vào `sourceSettings`.
2. **Giá trị có khi là chuỗi chứ không phải số hay bool.** Ví dụ đã đo trên 11 file mẫu: trường độ
   phân giải upscale là chuỗi `'None'` (25 lần), `'2K'` (4 lần), `'4K'` (3 lần) — không phải số. Một
   bộ kiểm chỉ nhận `int` sẽ vứt hết rồi rơi về mặc định.
3. **Có khoá là *dropdown nhớ lại*, không phải yêu cầu.** Đo được: 4 node mang `'2K'` trong khi công
   tắc bật upscale vẫn tắt. Coi giá trị đó là yêu cầu thì re-encode vô ích cả 4 board.

---

## Loại node của exe → loại node trên canvas

Ánh xạ **nhiều-về-một**, và đó là điểm dễ mất dữ liệu nhất.

| Loại của exe | Trên canvas | Ghi chú |
| --- | --- | --- |
| `gen_image` | `image` | |
| `gen_video` | `video` | |
| `text_prompt`, `prompt_list`, `prompt_mau`, `gemini_prompt` | `prompt` | Exe phân biệt bằng **cách soạn chữ** (gõ tay, danh sách, mẫu, hay để Gemini viết); vị trí trong graph và payload thì giống nhau |
| `upload_media`, `link_list`, `video_image_list` | `visual_asset` | Đều là một túi media |
| `analyze_video` | `analyze_video` | |
| `merge_video` | `merge_video` | |
| `edit_video` | `edit_video` | Một node có thể là nhiều pass trên cùng clip |
| `extract_last_frame` | `extract_last_frame` | Mắt nối cảnh: cảnh *n* → cảnh *n+1* |
| `add_bgm` | `add_bgm` | |
| `create_voice` | `create_voice` | |
| `align_video_voice` | `align_video_voice` | |
| `sync_image_voice` | `sync_image_voice` | |
| `motion_control` | **`note`** | Xem dưới |

### Hai cảnh báo từ bảng này

**`motion_control` nhập về `note`, không phải node chạy được.** Nó là tính năng **Edit Video** của
Flow (`abra_edit` + upload video theo chunk), và bản này **chưa capture được endpoint đó**. Nhập nó
thành một node dispatch được sẽ tiêu credit cho một request ảnh-tham-chiếu thay vì làm motion control
— tức là trả tiền cho thứ sai mà vẫn ra kết quả. Nên nó vào canvas dạng ghi chú: thấy được, làm tay
được, không tự chạy.

**Chiều ngược lại mất nhiều hơn.** Canvas này có **5 loại node mà exe không có** (`character`,
`note`, `Storyboard`, `review_video`, `remove_watermark`). Lưu canvas ra **định dạng của exe** là
**âm thầm rơi mất node của chính người dùng**. Vì vậy repo này lưu template cá nhân bằng **định dạng
riêng**, và chỉ *đọc* được định dạng exe.

---

## Cạnh (`edges`) — cổng là phần quyết định

`srcPort` / `dstPort` không phải trang trí: executor **phân biệt khung hình đầu với ảnh tham chiếu
bằng cổng**. Dây mất tên cổng thì cùng hai node, cùng cách nối, lại ra hành vi khác nhau tuỳ board
đến từ đâu.

Các cổng đã gặp:

| Cổng | Nghĩa |
| --- | --- |
| `start_frame` | ảnh làm khung hình **đầu** của clip (image-to-video) |
| `character_1` … `character_N` | ảnh/entity nhân vật. Trần theo làn: LITE 3, OMNI 10 |
| `image_prompts`, `video_prompts`, `voice_prompts` | ba nhánh của **một** node prompt |
| `thumbnail_prompt` | nhánh thứ tư, cho ảnh bìa |
| `image_thumbnail` | ổ nhận ảnh bìa trên node `edit_video` |
| `motion_input_video`, `image_1/2/3` | các ổ của motion control |

### Lỗi đã trả giá để biết

Node `prompt` có **ba (thực ra bốn) câu trả lời khác nhau**, và cổng quyết định consumer lấy câu nào.
Gộp làm một thì **giọng đọc sẽ đọc to lời chỉ đạo máy quay**. Tương tự, `thumbnail_prompt` từng không
có trong bảng nhánh nên dây rơi về `prompt` thường — mà `prompt` của node phân tích là **mô tả cảnh**,
nên ảnh bìa được sinh, và trả tiền, từ câu sai.

---

## Giới hạn hình học

Canvas vẽ node ở kích thước của nó, gần nhưng không bằng exe. Bộ đọc kẹp lại:

| | |
| --- | --- |
| rộng/cao tối thiểu | 160 × 90 |
| rộng/cao tối đa | 2000 × 2000 |
| toạ độ tối đa | ±1 000 000 |

---

## Khoá chứa chữ nên nhấc lên node

Ba khoá này giữ phần văn bản đáng hiện ngay trên node thay vì nằm trong `settings`:
`prompt`, `custom_voice_prompt`, `thumbnail_prompt`.

---

## Một định dạng thứ hai: sheet Excel hàng loạt

Tool còn nhận một file Excel. Đo trên file mẫu đóng gói:

- sheet tên **`Workflow Batch`**
- header: `image_prompt | video_prompt | Lời Thoại`
- **mỗi ô chứa NHIỀU DÒNG**, và dòng *i* của ba cột là **cùng một cảnh**

Nên **một HÀNG là một video N cảnh, không phải một cảnh.** Hàng nào lệch số dòng giữa ba cột thì
**báo, đừng sửa**: đệm thêm là đặt một câu thoại dưới bức ảnh sai, bỏ bớt là mất một cảnh mà không nói.
