# UI Spec — NTA VEO3 TOOL bản local

> **Đây là hợp đồng dựng UI. KHÔNG rút gọn file này.**
> Plan (`plans/…squishy-summit.md`) là lộ trình theo phase và được phép ngắn;
> mọi chi tiết per-tab sống ở đây. Mỗi lần nén plan trước đây đều làm mất
> phần chi tiết UI — tách ra để việc đó không xảy ra nữa.

## Nguồn của sự thật

| Thứ | Lấy từ đâu |
| --- | --- |
| Nhãn tiếng Việt, tập giá trị dropdown | Trích byte-exact từ bảng hằng Nuitka trong `d:\TOOL_VIDEO\TOOL\RUN_VEO_3_ULTRA_PROMAX.exe` |
| Mọi giá trị mặc định | `d:\TOOL_VIDEO\TOOL\data_general\config.json` (ghi rõ khoá bên cạnh) |
| Luồng thao tác | `d:\TOOL_VIDEO\TOOL\huong_dan_su_dung_tool.md` (hướng dẫn của chính tác giả) |
| API dùng lại | `D:\TOOL_VIDEO\flowboard-local\frontend\src\api\client.ts` |

**Quy tắc bất di bất dịch:** nhãn hiển thị phải **đúng từng byte** như đã trích.
Không dịch, không tự chế, không thêm/bớt emoji hay variation-selector.

---

## Khung dùng chung (shell)

```text
TopBar h52 │ ☀VEO3 ⚡GROK 📕NHẬT KÝ 🔗WORKFLOW ❤️ỦNG HỘ │ Tên · SĐT: · 💬Zalo · 🔔
───────────┼──────────────────────────────────────────────────────────────
Sidebar 232│ ToolHeader (ô icon bo tròn + tên tab)
VIDEO TOOLS│ Card A: Chọn dự án · 📁 Xem kết quả · <CTA> · DỪNG
IMAGE TOOLS│         📏 Tỷ lệ · ⏱ Thời lượng · 🎬 Model
SYSTEM     │ Card B: input riêng từng tab
           │ StatusPanel (bảng kết quả + toolbar)
───────────┴──────────────────────────────────────────────────────────────
StatusBar h30                                   VPN : OFF · Profile_CAPTCHA
```

Nhãn nhóm sidebar (đã trích): `VIDEO TOOLS` · `IMAGE TOOLS` · `SYSTEM`.
Chip header phải: `Tên`, `SĐT:` — **để TRỐNG** trong bản local.
`branding_state.json` của bản gốc chứa tên thật + số điện thoại của người khác;
đó là PII, **không migrate**, để người dùng tự nhập.

### Token màu

| Vai trò | Giá trị |
| --- | --- |
| nền · panel · card · sidebar | `#0a0a14` · `#10101f` · `#161628` · `#0c0c18` |
| accent cyan (pill active, nhãn nhóm) | `#22d3ee` |
| tím dropdown · gradient nav active | `#7c3aed` · `#4f46e5 → #7c3aed` |
| CTA xanh · DỪNG đỏ | `#2563eb` · `#dc2626` |
| sidebar 232px · topbar 52px · statusbar 30px | |
| font | `"Segoe UI", Inter, system-ui` |

> **Đính chính so với bản plan:** icon ToolHeader **không** cố định magenta.
> Bản gốc tô icon theo **màu của mục sidebar đang chọn** (`_update_mode_header`,
> fallback `#334155`). Màu `#ec4899` là của riêng tab Phụ Đề.
> Tương tự, nhãn đúng là **`📏 Tỷ lệ`** (không phải `✏️`) và **`⏱ Thời lượng`**
> (không có variation-selector `️`).

### Primitive dùng chung

`Card` · `CardRow` · `SectionHeader` · `LabeledSelect` · `PrimaryButton` ·
`GhostButton` · `StopButton` · `PromptTable` · `MediaPicker` ·
`FilePickerButton` · `ProgressBar` · `StatusChip` · `Field` · `EmptyState` ·
`JobList` · `JobCard` · `ResultsDrawer` · `MediaLibraryDrawer`.

### Điều hướng

Zustand store + render có điều kiện. **Không** thêm react-router: app là cửa sổ
đơn giống desktop gốc, không có URL để đồng bộ. Nhớ tab cuối bằng `localStorage`.

---



# VIDEO TOOLS — nhóm A

> **Nguồn nhãn:** mọi chuỗi trong dấu ` ` ` ` dưới đây được khai thác byte-exact từ bảng hằng Nuitka của `d:\TOOL_VIDEO\TOOL\RUN_VEO_3_ULTRA_PROMAX.exe` (định dạng `\x00u<chuỗi>\x00` / `T\x01u<chuỗi>\x00`). Mọi mặc định trích từ `d:\TOOL_VIDEO\TOOL\data_general\config.json`. Module gốc: `qt_ui/tab_text_to_video.py`, `qt_ui/tab_image_to_video.py`, `qt_ui/tab_character_sync.py`, `qt_ui/ui.py`, `qt_ui/status_panel.py`.

---

## 0. Khung dùng chung cho cả 4 tab (mined — KHÔNG lặp lại trong từng tab)

**Sidebar (mined nguyên vẹn, nhóm `VIDEO TOOLS`)** — mỗi mục là `(tiêu đề, phụ đề, thuộc-tính-lá, tên-icon, emoji, màu)`:

| Tiêu đề | Phụ đề | Leaf attr | Emoji | Màu chip |
|---|---|---|---|---|
| `Text to Video` | `Tạo video từ prompt` | — | `✨` | `#7c3aed` |
| `Image to Video` | `Tạo video từ ảnh` | `single_tab` | `🎬` | `#0ea5e9` |
| `Video Start-End` | `Làm video đầu - cuối` | `start_end_tab` | `🎞` | `#6366f1` |
| `Character Sync` | `Đồng nhất nhân vật` | `sync` | `👤` | `#f59e0b` |

> ⚠️ Sửa lại "verified fact": icon ToolHeader **không** cố định magenta. `lbl_mode_icon` được `_update_mode_header()` tô đúng màu của mục sidebar đang chọn (fallback `#334155`). Style mined: `QLabel{background:<màu>;color:#ffffff;border:1px solid #475569;border-radius:9px;font-size:15px;font-weight:700;}`. `db2777` là màu của tab `Phụ Đề & Xóa Logo` (`#ec4899`), không phải 4 tab này.

**Card A — hàng 1 (`project_wrap`)**

| Nhãn | Loại | Giá trị / tập chọn | Mặc định (khoá config) | Ghi chú |
|---|---|---|---|---|
| `Chọn dự án` | QLabel + `combo_project` (CfgCombo tím) | `➕ Tạo Dự Án Mới` (data `__ACTION_NEW__`) · `📁 <tên>` (data = tên) · `🗑 Xóa Dự Án` (data `__ACTION_DELETE__`) | `default_project` (`CURRENT_PROJECT` / `current_project`; danh sách `PROJECTS` / `projects`) | Nhãn màu `#10b981`, 13px/800. Sort tự nhiên theo số. |
| `📁 Xem kết quả` | GhostButton (`btn_view_result`, objectName `TopAction`, gradient xanh) | — | — | Mở `<VIDEO_OUTPUT_DIR>/<dự án>/video` |

Dialog tạo dự án: tiêu đề `Tạo Dự Án Mới`, label `Nhập tên dự án mới`, hint `Dữ liệu sẽ được lưu theo cấu trúc: thư mục gốc / tên dự án / image, video, thumbnail`, placeholder `Ví dụ: QuangCao_TraSua_03`, nút `Xác nhận` / `Hủy`. Xoá: `Thông báo` / `Không có dự án để xóa.` / `Xóa dự án`.

**Card A — hàng 2 (`cfg_wrap` / objectName `BottomCfgWrap`)** — chỉ 4 cột này hiện trên tab video (cột `🎨 Model` + `🖼 Quality` chỉ dành cho tab ảnh):

| Nhãn (byte-exact) | Loại | Giá trị / tập chọn | Mặc định (khoá config) | Ghi chú |
|---|---|---|---|---|
| `📏 Tỷ lệ` | LabeledSelect (`combo_aspect`) | `Dọc 9:16` → `9:16` · `Ngang 16:9` → `16:9` | `Dọc 9:16` (`VIDEO_ASPECT_RATIO` = `"9:16"`) | ⚠️ **KHÔNG** phải `✏️ Tỷ lệ` |
| `⏱ Thời lượng` | LabeledSelect (`combo_duration`) | `4s`→4 · `6s`→6 · `8s`→8 · `10s`→10 | `8s` (`VIDEO_DURATION_SECONDS` = 8) | ⚠️ **KHÔNG** có variation-selector (`⏱`, không phải `⏱️`). `_update_duration_options()` lọc lại theo model |
| `🎬 Model` | LabeledSelect (`combo_veo_model`) | `Veo 3.1 - Fast` · `Veo 3.1 - Quality` · `Veo 3.1 - Lite` · `Veo 3.1 - Lite [Lower Priority]` · `OMNI Flash` | `Veo 3.1 - Lite [Lower Priority]` (`VEO_MODEL`) | Đổi model → gọi `_update_duration_options` |
| `📂 Thư mục lưu Video` | FilePickerButton (`out_dir`, `_ClickPickLineEdit`) | đường dẫn | `D:/TOOL_VIDEO/VIDEO_OUT` (`VIDEO_OUTPUT_DIR` / `video_output_dir`) | Bản React: **bỏ** — browser không mở được thư mục Windows |

**Card A — cột phải (`veo_common_left`)**

| Nhãn | Loại | Trạng thái khác | Mặc định | Ghi chú |
|---|---|---|---|---|
| `TẠO VIDEO` | PrimaryButton (`btn_start`, gradient `#3b82f6→#38bdf8`) | `THÊM <n> VÀO HÀNG CHỜ` khi đã có workflow chạy | — | Tab ảnh dùng `TẠO ẢNH` |
| `DỪNG` | StopButton (`btn_stop`, objectName `DangerSoft`, gradient `#ef4444→#fb923c`) | `DỪNG ĐANG CHẠY` khi đang chạy | disabled | `_on_stop_all` |

Popup dùng chung: `Xác nhận thêm hàng chờ` / `Bạn muốn thêm ` + n + ` prompt vào hàng chờ không ?` / `Có` / `Không` → `Thêm hàng chờ` / `Đã thêm <n> prompt vào hàng chờ` / `Bạn có thể chuyển sang tab Nhật ký để xem tiến trình tạo video/ảnh`. Khởi động: `Khởi động thành công` / `Đã khởi động chế độ tạo ` + `video` + ` từ ` + n + ` prompt`.
Chặn model: `Model không hỗ trợ` / `Model Lower Priority chỉ hỗ trợ tài khoản ULTRA.`
Chặn tài khoản thường: `⚠️ Tài khoản thường không hỗ trợ đồng bộ giọng nói. Đã tự động tắt.`

**JobList = `StatusPanel` (dùng chung, objectName `SectionBoard`)**
Toolbar theo đúng thứ tự: `🔗 Nối video` · `🔄 Tạo lại` · `⚠️ Tạo lại video lỗi` · `🔄 Tạo lại Hủy` · `✂️ Cắt ảnh cuối` · `🗑️ Xóa kết quả` · `⏸️ DỪNG LẠI` · `📁 XEM KẾT QUẢ` · ` Zalo Hỗ Trợ` · 🔔.
Dòng tóm tắt (RichText, `lbl_status_summary`): `HOÀN THÀNH(n)` `#16a34a` · `ĐANG TẠO(n)` `#f59e0b` · `ĐANG CHỜ(n)` `#3b82f6` · `HỦY(n)` `#f97316` · `LỖI(n)` `#ef4444`.
Cột bảng: `Chọn` · `STT` · `Video` · `Trạng thái` · `Mode` · `Prompt` · `Link Ảnh`.
Tập trạng thái: `SẴN SÀNG` · `ĐANG LẤY TOKEN` · `ĐÃ GỬI REQUEST` · `ĐANG CHỜ` · `ĐANG TẠO` · `ĐANG TẢI` · `ĐANG UPSCALE` · `HOÀN THÀNH` · `LỖI` · `HỦY`.
Sửa trong bảng: double-click cột `Prompt` → dialog `Sửa Prompt` (nền sáng `#f8fafc`), label `Prompt hiện tại / chỉnh sửa:`, nút `Xác nhận` / `Hủy`. Double-click `Link Ảnh` → dialog `Sửa Link Ảnh`, hai ô `Link ảnh bắt đầu (image_link):` (placeholder `Nhập đường dẫn / URL ảnh bắt đầu...`) và `Link ảnh kết thúc (end_image_link) — để trống nếu không có:` (placeholder `Nhập đường dẫn / URL ảnh kết thúc (tùy chọn)...`), nút `...` (`browseBtn`) mở `Chọn file ảnh` / `Image Files (*.png *.jpg *.jpeg *.webp)`. Ô "ảnh cuối" bị `setDisabled` với tooltip `Chỉ khả dụng trong chế độ Start - End Image.`
Xoá kết quả: `Không thể xóa` / `Workflow đang chạy. Vui lòng đợi hoàn thành hoặc dừng trước khi xóa kết quả.`; `Chưa chọn` / `Hãy tích chọn các dòng cần xóa kết quả.`; `Bạn có chắc muốn xóa <n> kết quả đã chọn?\nDòng đã chọn: `.

**Endpoint Flow thật (mined từ exe — đây là bằng chứng cứng để đối chiếu backend cục bộ)**

| Tab | URL Flow | Hằng trong exe |
|---|---|---|
| Text to Video | `POST https://aisandbox-pa.googleapis.com/v1/video:batchAsyncGenerateVideoText` | `URL_GENERATE_TEXT_TO_VIDEO` |
| Image to Video | `POST .../v1/video:batchAsyncGenerateVideoStartImage` | `URL_IMGAE_TO_VIDEO` *(typo gốc)* |
| Video Start-End | `POST .../v1/video:batchAsyncGenerateVideoStartAndEndImage` | `URL_IMAGE_TO_VIDEO_START_END` |
| Character Sync | `POST .../v1/video:batchAsyncGenerateVideoReferenceImages` | `API_sync_chactacter` *(typo gốc)* |
| Poll | `POST .../v1/video:batchCheckAsyncVideoGenerationStatus` | dùng chung |
| Upload ảnh | `POST .../v1/flow/uploadImage`, `.../v1:uploadUserImage` | dùng chung |
| Credits | `GET .../v1/credits?key=[redacted]` | key name: `credits endpoint `key` query param` |

Payload item chung (mined): `aspectRatio` · `seed` · `videoModelKey` · `textInput.structuredPrompt.parts[].text` · `metadata.sceneId` · `startImage.mediaId` · `endImage.mediaId` · `referenceImages[].{mediaId,imageUsageType:"IMAGE_USAGE_TYPE_ASSET"}` · `referenceEntities[].entityId`; body: `clientContext{projectId,tool:"PINHOLE",userPaygateTier,sessionId,recaptchaContext{token,applicationType:"RECAPTCHA_APPLICATION_TYPE_WEB"}}` · `mediaGenerationContext{batchId,audioFailurePreference}` · `requests[]` · `useV2ModelConfig:true`.
`audioFailurePreference` = `RETURN_SILENCED_VIDEOS` khi `SKIP_AUDIO_ERROR: true` (mặc định), ngược lại `BLOCK_SILENCED_VIDEOS`.

**Hậu tố prompt BẮT BUỘC** — exe nối chuỗi này vào **mọi** prompt video trước khi gửi:
`" 🚫 ABSOLUTE CONSTRAINT - NO TEXT ON SCREEN, NO SUBTITLES, NO CAPTIONS, NO WATERMARK, NO LETTERS. Do not write, overlay or display any text, titles, subtitles, words, symbols, or captions on the video under any circumstances. (không có chú thích, không tự vẽ chữ lên video, cấm chữ xuất hiện trên màn hình)"`

---

### Text to Video — Tạo video từ prompt

**Mục đích:** Nhập một danh sách prompt (mỗi dòng = 1 video), bấm một nút, tool đẩy cả loạt vào hàng đợi và theo dõi từng dòng trong JobList.

**Bố cục:**
```
┌ ToolHeader ─────────────────────────────────────────────────────────┐
│ [✨ #7c3aed]  Text to Video          Tên: …  SĐT: …  [💬 Zalo] [🔔] │
└─────────────────────────────────────────────────────────────────────┘
┌ Card A (top_action_area) ───────────────────────────────────────────┐
│ Chọn dự án [📁 default_project ▾]  [📁 Xem kết quả]   ┌───────────┐ │
│ ┌────────┬───────────┬─────────┬──────────────────┐   │TẠO VIDEO  │ │
│ │📏 Tỷ lệ│⏱ Thời lượng│🎬 Model │📂 Thư mục lưu Video│  ├───────────┤ │
│ │Dọc 9:16│    8s     │Lite[LP] │ D:/…/VIDEO_OUT   │   │  DỪNG     │ │
│ └────────┴───────────┴─────────┴──────────────────┘   └───────────┘ │
└─────────────────────────────────────────────────────────────────────┘
┌ Card B  editor_card (SectionBoard) ─────────────────────────────────┐
│ ✏️  Nhập prompt (mỗi dòng là 1 prompt)                              │
│ 💡 Mỗi dòng = 1 video. Dòng trống sẽ bị bỏ qua.                     │
│ ┌──┬──────────────────────────────────────────────────────────────┐ │
│ │ 1│ Một con mèo cam ngồi trên nóc xe buýt Hà Nội lúc hoàng hôn   │ │
│ │ 2│ Cận cảnh giọt cà phê rơi xuống ly, ánh sáng ngược            │ │
│ │  │                                    ← dòng trống: KHÔNG có ID │ │
│ │ 3│ …                                                            │ │
│ └──┴──────────────────────────────────────────────────────────────┘ │
│  ↑ _PromptIdArea (gutter #1a1c23, gạch đứt #2d2f39, chữ #9ca3af)   │
│ [📄 Import prompt.txt]  [📄 canh1.txt ×] [📄 canh2.txt ×]           │
└─────────────────────────────────────────────────────────────────────┘
┌ JobList (StatusPanel) ──────────────────────────────────────────────┐
│ 🔗 Nối video │🔄 Tạo lại│⚠️ Tạo lại video lỗi│…│⏸️ DỪNG LẠI│📁 XEM… │
│ HOÀN THÀNH(2)   ĐANG TẠO(1)   ĐANG CHỜ(4)   HỦY(0)   LỖI(0)         │
│ ☐ │STT│ Video │ Trạng thái │ Mode │ Prompt │ Link Ảnh                │
└─────────────────────────────────────────────────────────────────────┘
```

**Điều khiển:**

| Nhãn | Loại | Giá trị / tập chọn | Mặc định (khoá config) | Ghi chú |
|---|---|---|---|---|
| `Text to Video` | ToolHeader title (`lbl_mode_title`) | tĩnh | — | Chip `✨` nền `#7c3aed` |
| `✏️  Nhập prompt (mỗi dòng là 1 prompt)` | SectionHeader | tĩnh | — | ⚠️ **hai dấu cách** sau `✏️`. Style `font-weight: 700; color: #10b981; font-size: 13px;`. **KHÔNG** phải `📝 Nhập prompt…` |
| `💡 Mỗi dòng = 1 video. Dòng trống sẽ bị bỏ qua.` | Hint | tĩnh | — | ⚠️ mined là **"1 video"**, không phải "1 prompt"; emoji `💡` không phải `⚡`. Style `color: #64748b; font-size: 11px; font-style: italic; padding: 0 0 2px 0;` |
| `Nhập một prompt cho mỗi dòng...` | placeholder `PromptEditor` (QPlainTextEdit) | text nhiều dòng | rỗng | Gutter trái `_PromptIdArea` đánh số **chỉ dòng không rỗng** (`cur_id` tăng khi `line.strip()` khác rỗng); bề rộng = `fontMetrics.horizontalAdvance(str(max_id))` |
| `📄 Import prompt.txt` | GhostButton (`btn_import`) | — | — | `QFileDialog.getOpenFileNames` → tiêu đề `Chọn file prompt.txt`, filter `Text Files (*.txt);;All Files (*.*)`, đọc `utf-8`, `rstrip`, **append** vào editor (chèn `\n` nếu nội dung hiện tại chưa kết thúc bằng newline). **Client-side, KHÔNG endpoint.** |
| `📄 <tên file>` + `×` | chip (FileTag) | chip xanh `rgba(16,185,129,0.12)`, chữ `#10b981` 11px/600, nút `×` `#ef4444` 14px/900 | không có | Bấm `×` gỡ **đúng những dòng** file đó đã chèn ra khỏi editor (`_remove_tag` diff theo `lines_to_remove`) |
| Card A (4 select + 2 nút) | — | xem §0 | xem §0 | Tab này **không** có `🎙️ Đồng Bộ Giọng Nói`, **không** có `🧩 Chạy từ các thành phần`, **không** có bảng ảnh. `TextToVideoTab` chỉ có `editor_card`, `editor`, `import_widget`, `get_prompts()` |

**Luồng:**
1. Người dùng dán/nhập nhiều dòng hoặc bấm `📄 Import prompt.txt` (FileReader trong trình duyệt, không gọi backend).
2. Bấm `TẠO VIDEO` → FE gọi `get_prompts()`: `text.split(/\r?\n/)` → `map(trim)` → `filter(Boolean)`. Nếu rỗng → cảnh báo `Thiếu prompt` / `Hãy nhập ít nhất một prompt ở khung bên trái.` (chuỗi mined của `enqueue_text_to_video`).
3. Nếu đang có workflow chạy → popup `Xác nhận thêm hàng chờ` (§0) và nút đổi thành `THÊM <n> VÀO HÀNG CHỜ`.
4. Đảm bảo project Flow: `ensureBoardProject(boardId)` → `{flow_project_id}` (dùng `getBoardProject` để đọc lại nếu đã có).
5. Đọc tier: `getAuthMe()` → `paygate_tier` (`PAYGATE_TIER_ONE`/`TWO`). **Bắt buộc** — worker trả `paygate_tier_unknown` nếu thiếu.
6. **Không có đường t2v trong backend cục bộ.** Chọn 1 trong 2:
   - **(A — khuyến nghị)** `createRequest({ type: "gen_video_text", params: { prompt, project_id, aspect_ratio, paygate_tier, video_quality, duration_s, output_count } })` → cần thêm loại request mới (xem `newEndpoints`).
   - **(B — chuỗi tạm, dùng ngay được)** cho **mỗi dòng**:
     a. `createRequest({ type: "gen_image", params: { prompt, project_id, aspect_ratio: "IMAGE_ASPECT_RATIO_PORTRAIT"|"…LANDSCAPE" } })`
     b. Poll `getRequest(idA)` cho tới `done` → lấy `media_id` đầu tiên từ `result`.
     c. `createRequest({ type: "gen_video", params: { prompt, project_id, start_media_id, aspect_ratio: "VIDEO_ASPECT_RATIO_PORTRAIT"|"…LANDSCAPE", paygate_tier, video_quality } })`
     d. Poll `getRequest(idB)` tới `done|failed|canceled|timeout`.
7. Mỗi dòng = 1 hàng trong JobList, `Mode` = `VEO3 - Tạo Video Từ Prompt`, `Prompt` = dòng gốc, `Link Ảnh` rỗng (nhánh B: hiển thị `media_id` của ảnh trung gian).
8. Song song tối đa `MULTI_VIDEO` (= 4) hàng — xem "Cạm bẫy".
9. `DỪNG` → `cancelActivity(id)` cho **mọi** request đang `queued|running` của loạt này; nhánh B phải chặn không dispatch bước (c) cho các dòng chưa sang video.

**Kết quả:** JobList cập nhật `Trạng thái` theo `getRequest().status`; khi `done`, cột `Video` render `<video src={mediaUrl(media_id)}>` với thumbnail. `📁 Xem kết quả` mở `ResultsDrawer` dựng từ `getActivityList({ type: "gen_video,gen_video_omni,gen_image" })` + `getActivityDetail(id)` (không mở được thư mục Windows từ browser).

**Cạm bẫy:**
- **Không có endpoint t2v trong backend cục bộ.** Flow *có* `batchAsyncGenerateVideoText` và exe *có* họ key `veo_3_1_t2v_fast*` / `veo_3_1_t2v_lite*` / `abra_t2v_*`, nhưng `flow_sdk.py` không định nghĩa `VIDEO_T2V_URL`, không có key `t2v` nào trong `VIDEO_MODEL_KEYS`, và `processor.py` không đăng ký handler nào tên `gen_video_text`. Đây **không phải** "Flow không hỗ trợ" — là backend cục bộ chưa port. Nếu đi nhánh B thì phải nói rõ trên UI.
- **Nhánh B tốn thêm credit ảnh.** Mỗi dòng = 1 credit ảnh + 1 credit video. Với 20 dòng là 40 lần tính phí, trong khi bản gốc chỉ 20. Phải hiện cảnh báo trước khi bấm `TẠO VIDEO`.
- **Ai sở hữu chuỗi (retry/cancel/credit).** Backend không có khái niệm "chuỗi": mỗi `Request` độc lập. Vì vậy **FE phải sở hữu state machine**: `row = {status, imageRequestId, videoRequestId, mediaId}`. `🔄 Tạo lại` chỉ chạy lại **chặng lỗi** (ảnh xong rồi thì chỉ gửi lại `gen_video` với `start_media_id` cũ — nếu gửi lại cả chuỗi là **tính phí ảnh lần 2 không cần thiết**). `DỪNG` phải cancel request đang chạy **và** set cờ để bước (c) không bao giờ được dispatch — nếu không, huỷ xong vẫn nổ ra một video và trừ credit.
- **`⏱ Thời lượng` và tier.** `gen_video()` **không nhận** `duration`; `resolve_video_model(tier, aspect, quality)` bỏ qua thời lượng hoàn toàn. Trong exe, thời lượng được mã hoá **vào chính model key** (`veo_3_1_t2v_fast_4s`, `_6s`, `veo_3_1_t2v_lite_4s`, …). Vậy: hoặc mở rộng `resolve_video_model` để nhận `duration_s`, hoặc **ẩn** `⏱ Thời lượng` trên tab này. Tuyệt đối không để select "sống" mà backend nuốt im lặng.
- **Không có select 480p/720p trên tab này.** `VIDEO_RESOLUTION: "480p"` trong config.json **chỉ** dùng cho GROK (`combo_grok_res`, nhãn `🎞 Chất lượng video`). Trên VEO3, độ phân giải đến từ `DOWNLOAD_MODE: "720"` + `AUTO_UPSCALE: true` (log mined: `🧩 Download Mode=720: bật upscale video`) chạy sau khi video xong, không phải tham số gửi đi. Đừng vẽ select 480p/720p ở đây.
- **`MULTI_VIDEO` ≠ số clip mỗi lần bấm.** Mined: `MULTI_VIDEO` được đọc bởi `_resolve_worker_max_in_flight` (số job chạy song song tối đa), còn `output_count` trong payload đến từ `_resolve_output_count` ← `OUTPUT_COUNT` (= **1**). Log runtime in cả hai: `⚙️ Cấu hình chạy: MULTI_VIDEO=<n> | OUTPUT_COUNT=<m>`. Nhưng trong `ImageToVideoWorkflow` có nhánh `output_count = … MULTI_VIDEO … 1` nên hai cách đọc mâu thuẫn. **Chốt số này trước khi tính credit** (xem `openQuestions`).
- **`Model Lower Priority chỉ hỗ trợ tài khoản ULTRA.`** Mặc định `VEO_MODEL` là `Veo 3.1 - Lite [Lower Priority]` — tài khoản Pro (`PAYGATE_TIER_ONE`) **không có làn 0-credit**. `resolve_video_model()` sẽ hạ xuống `CHEAPEST_VIDEO_QUALITY = "lite"` và ghi log, nghĩa là **default trong config.json không dùng được cho user Pro**. UI phải chặn từ đầu bằng đúng chuỗi mined thay vì để backend âm thầm đổi model.
- **Dòng trống bị bỏ qua nhưng vẫn "thấy được".** Gutter không đánh số dòng trống, nhưng nếu user copy từ Word có dòng chỉ chứa khoảng trắng, `strip()` biến nó thành rỗng → STT trong JobList lệch so với số dòng trên editor. Đánh số trong JobList **phải** dùng cùng hàm lọc với gutter.
- **Chip file `.txt` nhớ theo nội dung, không theo vị trí.** `_remove_tag` xoá đúng các dòng đã chèn; nếu user sửa một dòng đến từ file rồi bấm `×`, dòng đó **không** bị xoá (không khớp). Replica phải copy đúng hành vi này, đừng xoá theo range.

---

### Image to Video — Tạo video từ ảnh

**Mục đích:** Ghép ảnh đầu vào với prompt theo thứ tự trên-xuống, mỗi dòng cho ra một video i2v.

**Bố cục:** (`_SingleImageTab`, nằm trong `ImageToVideoTab.sub_tabs` tab `Tạo Video Từ Ảnh`, `current_mode = "single"`; thanh sub-tab bị ẩn vì sidebar đã chọn hộ)
```
┌ ToolHeader [🎬 #0ea5e9]  Image to Video ────────────────────────────┐
├ Card A (giống §0) ──────────────────────────────────────────────────┤
┌ Card B  header_card (SectionBoard) ─────────────────────────────────┐
│ 📁  Bước 1: Chọn hàng loạt ảnh                                      │
│ [📂 Chọn ảnh] [🗑 Xóa hết]   ☐ 🧩 Chạy từ các thành phần            │
│                              ☐ 🎙️ Đồng Bộ Giọng Nói [voice ▾][▶ Nghe thử]│
├─────────────────────────────────────────────────────────────────────┤
│ ✏️  Bước 2: Nhập hàng loạt prompt tương ứng                         │
│ ┌──┬──────────────────────────────────────────────────────────────┐ │
│ │ 1│ Nhập một prompt cho mỗi dòng...                              │ │
│ └──┴──────────────────────────────────────────────────────────────┘ │
│ [📄 Import prompt.txt]                                              │
├──── QSplitter (dọc) ────────────────────────────────────────────────┤
│ PromptTable  (drop ảnh trực tiếp vào bảng — DropOnly)               │
│ ┌────┬──────────────────────┬─────────────────────────────────────┐ │
│ │STT │ Ảnh                  │ Prompt                              │ │
│ ├────┼──────────────────────┼─────────────────────────────────────┤ │
│ │ 1  │ [thumb] a.png  🗑▲▼   │ Prompt cho ảnh này...               │ │
│ │ 2  │ [(Click chọn)] 🗑▲▼   │ …                                   │ │
│ └────┴──────────────────────┴─────────────────────────────────────┘ │
│  w(0)=44px · setStretchLastSection(true) → cột Prompt giãn          │
└─────────────────────────────────────────────────────────────────────┘
┌ JobList (StatusPanel) ──────────────────────────────────────────────┘
```

**Điều khiển:**

| Nhãn | Loại | Giá trị / tập chọn | Mặc định (khoá config) | Ghi chú |
|---|---|---|---|---|
| `📁  Bước 1: Chọn hàng loạt ảnh` | SectionHeader | tĩnh | — | **hai dấu cách** sau `📁`. Style `font-weight: 700; color: #10b981; font-size: 13px;` |
| `📂 Chọn ảnh` | PrimaryButton (`btn_pick`, objectName `Warning`) | multi-select | — | Dialog `Chọn ảnh`, filter `Images (*.png *.jpg *.jpeg *.webp *.bmp);;All Files (*.*)`. Sau khi chọn, nhãn đổi thành `📂 Đã chọn ` + n + ` ảnh`, hết ảnh quay lại `📂 Chọn ảnh` |
| `🗑 Xóa hết` | GhostButton (`btn_clear`, objectName `DangerSoft`) | — | — | Confirm: `Xác nhận xóa` / `Xóa hết ảnh và prompt?` / `Thao tác này sẽ xóa toàn bộ ảnh và prompt đã nhập.` / `Xóa hết` / `Hủy` |
| `🧩 Chạy từ các thành phần` | Checkbox (`chk_component_mode`) | on/off | **off** (`component_mode_enabled` — **không có** trong `config.json` hiện tại) | Tooltip mined: `Chạy tạo video từ ảnh và prompt gốc, bỏ qua dán nhãn nhân vật` |
| `🎙️ Đồng Bộ Giọng Nói` | Checkbox (`chk_voice`) | on/off | **false** (`VOICE_SYNC_ENABLED`) | VIP-gated: `Thông báo bản quyền` / `Bạn hãy nâng cấp tài khoản VIP để sử dụng full chức năng.` |
| (không nhãn) | `combo_voice` (`VoiceCombo`) | quét `data_general/Voice/*.wav`, item = `<i>` + `. ` + tên hiển thị (`style_voice_manager.get_voice_display_name(stem)`), data = `stem` | `""` (`VOICE_SYNC_ID`) | Ẩn khi checkbox off |
| `▶ Nghe thử` | GhostButton (`btn_play_voice`) | toggle | — | `winsound.PlaySound(SND_FILENAME|SND_ASYNC)`; lỗi: `Lỗi` / `Không tìm thấy file voice: ` hoặc `Không thể phát voice: `. Bản React: `<audio>` |
| `✏️  Bước 2: Nhập hàng loạt prompt tương ứng` | SectionHeader | tĩnh | — | **hai dấu cách** sau `✏️` |
| `Nhập một prompt cho mỗi dòng...` | placeholder `PromptEditor` | text | rỗng | Cùng widget với Text to Video (có gutter ID) |
| `📄 Import prompt.txt` | GhostButton | — | — | Giống Text to Video |
| Bảng | PromptTable | cột `STT` · `Ảnh` · `Prompt` | — | `SelectRows`, `NoEditTriggers`, `AlternatingRowColors`, `setColumnWidth(0,44)`, `setStretchLastSection(true)`, `setAcceptDrops(true)` + `DragDropMode.DropOnly` |
| ô Ảnh trống | `_ClickableThumb` | nhãn `(Click chọn)` | — | Click → dialog `Chọn ảnh (dòng ` + i + `)`; kéo-thả file vào bảng cũng được (`.jpeg .jpg .png .webp .bmp`) |
| ô Ảnh có ảnh | thumbnail + tên file + `🗑` `▲` `▼` | — | — | `▲`/`▼` = `_move_up`/`_move_down`; `🗑` = `_remove_row`. Thumb cache LRU `_THUMB_CACHE` |
| ô Prompt | inline edit | placeholder `Prompt cho ảnh này...` | dòng tương ứng trong editor | Sửa ở đây tạo `_prompt_overrides[i]` **đè** dòng editor; xoá trống → quay lại dòng editor |
| Card A | — | xem §0 | xem §0 | `Mode` trong JobList = `VEO3 - Tạo video từ Ảnh` |

**Luồng:**
1. `📂 Chọn ảnh` (hoặc kéo-thả) → `_images: string[]`; `_ensure_rows()` đồng bộ số dòng bảng = `max(len(_images), số dòng prompt không rỗng)`.
2. Nhập prompt vào `PromptEditor`. `_sync_table()` (debounce qua `_sync_timer`, single-shot) ghép **theo vị trí**: dòng prompt thứ *i* ↔ ảnh thứ *i*.
3. Bấm `TẠO VIDEO`. Guard mined: `Thiếu ảnh` / `Mode Tạo Video Từ Ảnh yêu cầu ảnh đầu vào cho mỗi dòng.\nDòng thiếu: <danh sách STT>`.
4. `ensureBoardProject(boardId)` → `flow_project_id`; `getAuthMe()` → `paygate_tier`.
5. Với mỗi dòng: `uploadImage(file, flow_project_id)` → `{ media_id, aspect_ratio, width, height }`.
6. `createRequest({ type: "gen_video", params: { prompt, project_id: flow_project_id, start_media_id: media_id, aspect_ratio: "VIDEO_ASPECT_RATIO_PORTRAIT"|"VIDEO_ASPECT_RATIO_LANDSCAPE", paygate_tier, video_quality } })`.
   `video_quality` map từ `🎬 Model`: `Veo 3.1 - Fast`→`"fast"` · `Veo 3.1 - Quality`→`"quality"` · `Veo 3.1 - Lite`→`"lite"` · `Veo 3.1 - Lite [Lower Priority]`→`"lite_relaxed"` (chỉ Ultra) · `OMNI Flash`→ **đổi sang** `gen_video_omni` (bước 7).
7. Nếu model = `OMNI Flash`: `createRequest({ type: "gen_video_omni", params: { prompt, project_id, ref_media_ids: [media_id], duration_s, aspect_ratio, paygate_tier } })` — đây là **đường duy nhất** honor được `⏱ Thời lượng` (`duration_s ∈ {4,6,8,10}` → key `abra_r2v_{n}s`).
8. Poll `getRequest(id)` mỗi ~2s tới trạng thái terminal. Song song ≤ `MULTI_VIDEO`.
9. `DỪNG` → `cancelActivity(id)` trên toàn bộ hàng `queued|running`.

**Kết quả:** Mỗi dòng thành một hàng JobList (`Mode` = `VEO3 - Tạo video từ Ảnh`, `Link Ảnh` = đường dẫn/`media_id` ảnh nguồn). Khi `done`, cột `Video` phát `<video src={mediaUrl(id)}>`. `📁 Xem kết quả` → `ResultsDrawer`.

**Cạm bẫy:**
- **Ghép theo vị trí là nguồn lỗi thầm lặng số 1.** Ảnh 5 + prompt 4 → dòng 5 nhận prompt rỗng (bị lọc) và **cả loạt lệch một nhịp** so với ý người dùng, nhưng vẫn chạy và vẫn trừ credit. Bảng **phải** highlight đỏ mọi dòng thiếu một trong hai vế **trước** khi bật `TẠO VIDEO`, và hiện đúng chuỗi `Dòng thiếu: ` như bản gốc.
- **`_prompt_overrides` vs editor.** Sửa prompt trong ô bảng tạo override; sau đó **sửa editor không cập nhật** dòng đó nữa. Người dùng rất dễ tưởng mình đã đổi prompt mà thực tế đang gửi bản override cũ. Replica phải hiện một dấu hiệu "đã ghi đè" trên ô đó.
- **`⏱ Thời lượng` chết trên đường Veo.** `gen_video()` không nhận `duration`; `VIDEO_MODEL_KEYS` trong `flow_sdk.py` chỉ có `veo_3_1_i2v_lite` / `_s_fast` / `_s` — **không có** biến thể `_4s` / `_6s` mà exe dùng (`veo_3_1_i2v_s_fast_4s`, `veo_3_1_i2v_s_lite_6s`, …). ⇒ chọn `4s` hay `6s` đều ra clip 8s. **Hoặc** mở rộng `resolve_video_model(..., duration_s)` để trả về các key `_4s`/`_6s` đã mined, **hoặc** disable `⏱ Thời lượng` khi model ≠ `OMNI Flash` với tooltip giải thích. Không được để select im lặng.
- **`🧩 Chạy từ các thành phần` không có đường backend.** Cờ này (bỏ qua dán nhãn nhân vật, dùng ảnh + prompt gốc) chỉ có nghĩa trong pipeline "Thành Phần" của tab Workflow. Ở replica, hoặc ẩn, hoặc render disabled với tooltip mined nguyên văn.
- **`🎙️ Đồng Bộ Giọng Nói` không có đường backend.** Trong exe, `voice_id` được đưa vào `build_payload_generate_video_reference` (đường **r2v**, không phải i2v) và có nhánh `referenceAudio`. `gen_video()` cục bộ không nhận `voice_id`. Ẩn hoặc disable, đừng gửi tham số bị nuốt.
- **`start_media_ids` (nhiều nguồn 1 request) khác hẳn "nhiều dòng".** Backend hỗ trợ `start_media_ids: string[]` → **một** request sinh N video. Đừng dùng nó cho batch của tab này (mỗi dòng có prompt riêng); dùng N request riêng. Nhầm chỗ này làm N dòng dùng chung một prompt.
- **`aspect_ratio` có hai không gian tên.** Upload trả `IMAGE_ASPECT_RATIO_*`; `gen_video` cần `VIDEO_ASPECT_RATIO_*`. Truyền nhầm → `no_video_model_for_tier_…_aspect_…`.
- **Tài khoản Pro + `Lite [Lower Priority]`.** Xem cạm bẫy tương ứng ở Text to Video — mặc định config.json rơi vào đúng trường hợp này.

---

### Video Start-End — Làm video đầu - cuối

**Mục đích:** Nội suy một clip từ **ảnh đầu** sang **ảnh cuối** theo prompt, mỗi dòng là một cặp ảnh.

**Bố cục:** (`_StartEndImageTab`, sub-tab `Tạo video từ Ảnh Đầu - Ảnh Cuối`, `current_mode = "start_end"`)
```
┌ ToolHeader [🎞 #6366f1]  Video Start-End ───────────────────────────┐
├ Card A (giống §0) ──────────────────────────────────────────────────┤
┌ Card B  header_card — QGridLayout 2 cột ────────────────────────────┐
│ 📁  Chọn ảnh BẮT ĐẦU          │ 📁  Chọn ảnh KẾT THÚC              │
│ [📂 Chọn ảnh bắt đầu][🗑 Xóa hết]│[📂 Chọn ảnh kết thúc][🗑 Xóa hết] │
├─────────────────────────────────────────────────────────────────────┤
│ ✏️  Bước 3: Nhập hàng loạt prompt tương ứng                         │
│ ┌──┬──────────────────────────────────────────────────────────────┐ │
│ │ 1│ Nhập một prompt cho mỗi dòng...                              │ │
│ └──┴──────────────────────────────────────────────────────────────┘ │
│ [📄 Import prompt.txt]                                              │
├──── QSplitter (dọc) ────────────────────────────────────────────────┤
│ ┌────┬─────────────┬─────────────┬────────────────────────────────┐ │
│ │STT │ Ảnh Start   │ Ảnh End     │ Prompt                         │ │
│ ├────┼─────────────┼─────────────┼────────────────────────────────┤ │
│ │ 1  │[thumb]🗑▲▼  │[thumb]🗑▲▼  │ Prompt cho ảnh này...          │ │
│ │ 2  │[(Click chọn)]│[(Click chọn)]│ …                             │ │
│ └────┴─────────────┴─────────────┴────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
┌ JobList (StatusPanel) ──────────────────────────────────────────────┘
```

**Điều khiển:**

| Nhãn | Loại | Giá trị / tập chọn | Mặc định (khoá config) | Ghi chú |
|---|---|---|---|---|
| `📁  Chọn ảnh BẮT ĐẦU` | SectionHeader (cột trái) | tĩnh | — | **hai dấu cách** sau `📁` |
| `📂 Chọn ảnh bắt đầu` | PrimaryButton (`btn_pick_start`, `Warning`) | multi-select | — | Dialog `Chọn ảnh BẮT ĐẦU`, filter `Images (*.png *.jpg *.jpeg *.webp *.bmp);;All Files (*.*)` |
| `🗑 Xóa hết` | GhostButton (`btn_clear_start`, `DangerSoft`) | — | — | Hằng dùng chung với `_SingleImageTab` (Nuitka dedupe) |
| `📁  Chọn ảnh KẾT THÚC` | SectionHeader (cột phải) | tĩnh | — | **hai dấu cách** sau `📁` |
| `📂 Chọn ảnh kết thúc` | PrimaryButton (`btn_pick_end`, `Warning`) | multi-select | — | Dialog `Chọn ảnh KẾT THÚC` |
| `🗑 Xóa hết` | GhostButton (`btn_clear_end`) | — | — | Confirm chung: `Xác nhận xóa` / `Xóa hết ảnh và prompt?` / `Thao tác này sẽ xóa toàn bộ danh sách ảnh đã chọn, toàn bộ prompt đã nhập, và prompt đã sửa theo từng ảnh.` / `Xóa hết` / `Hủy` |
| `✏️  Bước 3: Nhập hàng loạt prompt tương ứng` | SectionHeader | tĩnh | — | ⚠️ ghi **"Bước 3"** (không phải Bước 2) — bản gốc đếm 2 bước chọn ảnh trước |
| `Nhập một prompt cho mỗi dòng...` | placeholder `PromptEditor` | text | rỗng | Cùng widget |
| `📄 Import prompt.txt` | GhostButton | — | — | Giống các tab khác |
| Bảng | PromptTable | cột `STT` · `Ảnh Start` · `Ảnh End` · `Prompt` | — | Cell click → `Chọn ảnh BẮT ĐẦU (dòng <i>)` / `Chọn ảnh KẾT THÚC (dòng <i>)`; `get_items()` trả `{ id, prompt, start_image_link, end_image_link }` |
| `🎬 Model` (Card A) | LabeledSelect | **thực tế chỉ đường Fast khả dụng** | `Veo 3.1 - Lite [Lower Priority]` (`VEO_MODEL`) | Xem cạm bẫy — không tồn tại key FL cho Lite/Lower-Priority/Quality |
| `⏱ Thời lượng` (Card A) | LabeledSelect | 4s/6s/8s/10s | `8s` (`VIDEO_DURATION_SECONDS`) | **Vô nghĩa ở chế độ này** — xem cạm bẫy |

**Luồng:**
1. Chọn loạt ảnh bắt đầu → `_start_images`; loạt ảnh kết thúc → `_end_images`. `_ensure_rows()` = `max(len(_start_images), len(_end_images), số dòng prompt)`.
2. Nhập prompt. Ghép **theo vị trí ba chiều**: `_start_images[i]` + `_end_images[i]` + prompt dòng *i*.
3. Bấm `TẠO VIDEO`. Guard mined: `Thiếu ảnh` / `Mode Ảnh Đầu - Ảnh Cuối yêu cầu đủ 2 ảnh cho mỗi dòng.\nDòng thiếu: <STT>`.
4. `ensureBoardProject(boardId)` → `flow_project_id`; `getAuthMe()` → `paygate_tier`.
5. Mỗi dòng: `uploadImage(startFile, flow_project_id)` → `startMediaId`; `uploadImage(endFile, flow_project_id)` → `endMediaId`.
6. `createRequest({ type: "gen_video", params: { prompt, project_id: flow_project_id, start_media_id: startMediaId, end_media_id: endMediaId, aspect_ratio, paygate_tier, video_quality } })`.
   → `flow_sdk.gen_video()` tự route sang `VIDEO_I2V_FL_URL` (`…:batchAsyncGenerateVideoStartAndEndImage`) khi có `end_media_id`, và thêm `item.endImage = { mediaId }`.
7. Poll `getRequest(id)`; song song ≤ `MULTI_VIDEO`.
8. `DỪNG` → `cancelActivity(id)` cho mọi hàng đang chạy.

**Kết quả:** JobList, `Mode` = `VEO3 - Tạo video từ Ảnh (đầu-cuối)`, cột `Link Ảnh` hiển thị ảnh bắt đầu; double-click cột đó mở dialog `Sửa Link Ảnh` với **cả hai ô mở khoá** (bản gốc chỉ enable ô "ảnh kết thúc" ở đúng mode này — tooltip `Chỉ khả dụng trong chế độ Start - End Image.`). Khi `done`, cột `Video` phát clip.

**Cạm bẫy:**
- **🔴 Backend cục bộ gửi model key SAI cho endpoint FL.** `gen_video()` route đúng URL nhưng `resolve_video_model()` chỉ biết `veo_3_1_i2v_lite` / `veo_3_1_i2v_s_fast[_portrait][_ultra]` / `veo_3_1_i2v_s[_portrait]` — **không có một key `_fl` nào**. Trong exe, đường đầu-cuối chỉ dùng đúng 6 key: `veo_3_1_i2v_s_fast_fl` (LANDSCAPE_FL_NORMAL), `veo_3_1_i2v_s_fast_ultra_fl`, `veo_3_1_i2v_s_fast_fl_ultra_relaxed`, `veo_3_1_i2v_s_fast_portrait_fl`, `veo_3_1_i2v_s_fast_portrait_fl_ultra`, `veo_3_1_i2v_s_fast_portrait_fl_ultra_relaxed`. Gửi key non-FL vào endpoint FL rất có thể bị Flow từ chối, hoặc tệ hơn: **chấp nhận và bỏ qua `endImage`**, trả về một clip i2v thường mà vẫn trừ credit — người dùng không biết ảnh cuối đã bị vứt. **Phải bổ sung bảng key FL trước khi bật tab này.**
- **Không tồn tại FL cho Lite / Lower-Priority / Quality / OMNI.** Kiểm kê key trong exe: toàn bộ `_fl` đều thuộc họ `_s_fast`. ⇒ Ở chế độ Start-End, `🎬 Model` **chỉ nên** cho chọn `Veo 3.1 - Fast`; các lựa chọn khác phải bị disable kèm lý do, nếu không `Lite [Lower Priority]` (default trong config.json!) sẽ rơi vào nhánh trên.
- **Không tồn tại FL cho 4s/6s.** Không có `..._fl_4s` / `..._fl_6s`. ⇒ `⏱ Thời lượng` **luôn là 8s** ở chế độ này. Bản gốc cũng ép: `_update_duration_options` có nhánh kiểm tra `current_mode == "start_end"`. Replica **phải khoá select về `8s`** (hoặc ẩn) khi ở Video Start-End.
- **`end_media_id_requires_single_source`.** `gen_video()` từ chối thẳng khi `end_media_id` đi cùng `start_media_ids` có >1 phần tử — "một dàn ảnh nguồn chung một khung đích thì không bao giờ là ý người dùng". UI **tuyệt đối không** gửi `start_media_ids` trên tab này; luôn dùng `start_media_id` đơn lẻ, một request/dòng.
- **Ghép ba chiều dễ lệch hơn ghép hai chiều.** Chọn 5 ảnh đầu / 4 ảnh cuối / 5 prompt → dòng 5 thiếu ảnh cuối. Nếu FE chỉ kiểm tra `start && prompt`, dòng đó **rơi âm thầm về i2v thường** (không có `end_media_id` → URL khác → model khác → giá khác). Bắt buộc validate đủ cả 3 vế trước khi dispatch.
- **Thứ tự upload.** Nếu upload ảnh cuối lỗi sau khi ảnh đầu đã upload xong, đừng dispatch — sẽ tạo ra một i2v thường không ai yêu cầu. Upload cả cặp rồi mới `createRequest`.
- **`▲`/`▼` chỉ đổi chỗ một cột.** `_move_up`/`_move_down` trong `_StartEndImageTab` thao tác trên `_start_images` và `_end_images` riêng biệt. Di chuyển ảnh Start mà quên di chuyển ảnh End → cặp bị xáo. Replica nên đổi chỗ **cả hàng** (start+end+prompt) — và ghi rõ đây là chỗ cố ý khác bản gốc.

---

### Character Sync — Đồng nhất nhân vật

**Mục đích:** Nuôi một thư viện ảnh nhân vật có tên, rồi gọi tên trong prompt bằng cú pháp `{Tên nhân vật}` để mọi cảnh giữ nguyên nhận dạng nhân vật (đường reference-to-video của Flow).

**Bố cục:** (`CharacterSyncTab` — QHBoxLayout **hai cột**, khác hẳn 3 tab trên)
```
┌ ToolHeader [👤 #f59e0b]  Character Sync ────────────────────────────┐
├ Card A (giống §0) ──────────────────────────────────────────────────┤
┌ left_card (SectionBoard) ──────────┬ right_card ─────────────────────┐
│ ✏️  Prompt hàng loạt               │ [📂 Chọn ảnh nhân vật]          │
│ 💡 Gọi tên nhân vật và mô tả hành  │ ☐ 🎙️ Đồng Bộ Giọng Nói          │
│    động, bối cảnh. Tối đa 3 nhân   │   [voice ▾] [▶ Nghe thử]        │
│    vật/prompt.                     ├─────────────────────────────────┤
│ ┌──┬─────────────────────────────┐ │ _DropArea (#DropArea)           │
│ │ 1│{MaiAnh} bước vào quán cà phê│ │ ┌─────────┐ ┌─────────┐         │
│ │ 2│{MaiAnh} và {Hung} nói chuyện│ │ │ [ảnh] ×│ │ [ảnh] ×│         │
│ │ 3│Nhập một prompt cho mỗi dòng…│ │ │ MaiAnh  │ │ Hung    │         │
│ └──┴─────────────────────────────┘ │ └─────────┘ └─────────┘         │
│ [📄 Import prompt.txt]             │            ☁️                    │
│                                    │   Kéo thả ảnh vào đây           │
│                                    │   hoặc nhấp để chọn ảnh         │
│                                    │        Tối đa 15 ảnh            │
└────────────────────────────────────┴─────────────────────────────────┘
┌ JobList (StatusPanel) ──────────────────────────────────────────────┘
```

**Điều khiển:**

| Nhãn | Loại | Giá trị / tập chọn | Mặc định (khoá config) | Ghi chú |
|---|---|---|---|---|
| `✏️  Prompt hàng loạt` | SectionHeader (`left_card`) | tĩnh | — | **hai dấu cách** sau `✏️` |
| `💡 Gọi tên nhân vật và mô tả hành động, bối cảnh. Tối đa 3 nhân vật/prompt.` | Hint (`setWordWrap(true)`) | tĩnh | — | Byte-exact; giới hạn 3 là **cứng** cho họ Veo r2v |
| `Nhập một prompt cho mỗi dòng...` | placeholder `PromptEditor` | text nhiều dòng | rỗng | Cùng widget với Text to Video (có gutter ID) |
| `📄 Import prompt.txt` | GhostButton (`import_widget`) | — | — | Giống các tab khác |
| `📂 Chọn ảnh nhân vật` | PrimaryButton (`btn_pick`, `Warning`, icon `folder_icon.png`) | multi-select | — | Dialog `Chọn ảnh nhân vật`, filter `Images (*.png *.jpg *.jpeg *.webp *.bmp);;All Files (*.*)` |
| `🎙️ Đồng Bộ Giọng Nói` | Checkbox (`chk_voice`) | on/off | **false** (`VOICE_SYNC_ENABLED`) | VIP-gated (`Thông báo bản quyền` / `Bạn hãy nâng cấp tài khoản VIP để sử dụng full chức năng.`); NORMAL bị tắt tự động (`⚠️ Tài khoản thường không hỗ trợ đồng bộ giọng nói. Đã tự động tắt.`) |
| (không nhãn) | `combo_voice` (`VoiceCombo`, `AdjustToMinimumContentsLengthWithIcon`) | `data_general/Voice/*.wav` | `""` (`VOICE_SYNC_ID`) | Ẩn khi checkbox off |
| `▶ Nghe thử` | GhostButton (`btn_play_voice`) | toggle | — | Giống Image to Video |
| `☁️\n\nKéo thả ảnh vào đây\nhoặc nhấp để chọn ảnh` | EmptyState (`_empty`, QLabel `AlignCenter`) | tĩnh | hiện khi thư viện rỗng | Style `color:#64748b; font-weight:600; font-size: 13px; line-height: 1.6;` |
| `Tối đa 15 ảnh` | Hint (`_limit_label`) | tĩnh | — | Style `color:#475569; font-size: 10px; font-style: italic;` ⚠️ **15**, không phải 10 — xem cạm bẫy |
| thẻ nhân vật | `_CharacterCard` | ảnh + nút `×` + ô tên | — | `×` = QToolButton nền `rgba(239, 68, 68, 0.82)`, `border-radius: 10px`, 20×20. Ảnh hỏng → `(không đọc được ảnh)` |
| `Đặt tên nhân vật cho ảnh này...` | placeholder ô tên (`_name`, objectName `CharNameInput`) | chữ | rỗng | Validator mined ở node Workflow: `^[a-zA-Z0-9_]*$` với hint `Chỉ cho phép a-z, A-Z, 0-9 và dấu _ (không dấu, không khoảng trắng)` và ví dụ `Tên nhân vật (VD: MaiAnh, nhan_vat_1)`. Thông báo lỗi mined: `Tên nhân vật chỉ được dùng chữ không dấu (a-z, A-Z), số (0-9) và dấu gạch dưới (_).` |
| `🎬 Model` (Card A) | LabeledSelect | Veo r2v: chỉ tồn tại 4 key (xem cạm bẫy) | `Veo 3.1 - Lite [Lower Priority]` (`VEO_MODEL`) | `OMNI Flash` → nâng giới hạn lên 10 nhân vật |
| `⏱ Thời lượng` (Card A) | LabeledSelect | 4s/6s/8s (Veo) · +10s (OMNI) | `8s` (`VIDEO_DURATION_SECONDS`) | Quy tắc mined nguyên văn: `LOWER chỉ 4/6/8s, tối đa 3 Character/cảnh; OMNI thêm 10s, tối đa 10 Character/cảnh.` |

**Luồng:**
1. `📂 Chọn ảnh nhân vật` hoặc kéo-thả vào `_DropArea` (chấp nhận `.png .jpg .jpeg .webp .bmp .gif .tiff .tif .svg .ico .avif`) → mỗi ảnh thành một `_CharacterCard`. Quá hạn mức: `Đủ số lượng` / `Đã đủ số lượng nhân vật tối đa (15 ảnh).`
2. Đặt tên cho từng thẻ (`set_name(idx, name)`). Tên chưa đặt sẽ bị chặn ở bước 4.
3. Viết prompt, mỗi dòng gọi tên bằng `{Tên}`. Ví dụ `{MaiAnh} bước vào quán cà phê lúc mưa`.
4. Bấm `TẠO VIDEO`. Guard mined theo thứ tự:
   - `Thiếu prompt` / `Hãy nhập ít nhất 1 prompt ở tab Đồng bộ nhân vật.`
   - `Thiếu ảnh nhân vật` / `Hãy thêm ít nhất 1 ảnh nhân vật ở tab Đồng bộ nhân vật.`
   - `Các ảnh chưa đặt tên nhân vật: <danh sách>`
   - `Không có prompt hợp lệ để chạy Đồng bộ nhân vật.` / `Không có ảnh nhân vật hợp lệ để chạy Đồng bộ nhân vật.`
5. `ensureBoardProject(boardId)` → `flow_project_id`; `getAuthMe()` → `paygate_tier`.
6. Upload **một lần** mỗi nhân vật: `uploadImage(file, flow_project_id)` → `mediaId`; cache `name → mediaId` (bản gốc log `⬆️ Upload ảnh nhân vật dùng chung: <n> ảnh (tối đa <k> thread)`).
7. Với mỗi dòng prompt: quét `{Tên}` theo thứ tự xuất hiện → `refs: mediaId[]`. Chặn tại UI nếu `refs.length > 3` (Veo) hoặc `> 10` (OMNI); tên không có trong thư viện → đánh dấu dòng lỗi, không dispatch.
8. `createRequest({ type: "gen_video_omni", params: { prompt: promptText, project_id: flow_project_id, ref_media_ids: refs, duration_s, aspect_ratio: "VIDEO_ASPECT_RATIO_PORTRAIT"|"…LANDSCAPE", paygate_tier } })`.
   Backend gọi `ensure_media_ids_in_project(refs, project_id)` (re-upload nếu media thuộc project khác — Flow scope mediaId theo project), rồi `POST …:batchAsyncGenerateVideoReferenceImages` với `referenceImages[] = [{mediaId, imageUsageType:"IMAGE_USAGE_TYPE_ASSET"}]`.
9. Poll `getRequest(id)`. `DỪNG` → `cancelActivity(id)`.

**Kết quả:** Mỗi dòng prompt → một hàng JobList, `Mode` = `VEO3 - Video đồng nhất nhân vật`, `Link Ảnh` liệt kê ảnh nhân vật đã dùng. Log dòng chạy mined: `🚀 Khởi động workflow Đồng bộ nhân vật | project=<tên> | characters=<n>`; lỗi: `❌ Không thể khởi động workflow Đồng bộ nhân vật: `. Khi `done`, cột `Video` phát clip.

**Cạm bẫy:**
- **🔴 Bản gốc KHÔNG gửi prompt phẳng — nó gửi `structuredPrompt.parts` xen kẽ.** Hàm mined `_build_structured_prompt_parts` cắt prompt tại vị trí tag và thay `{Tên}` **tại chỗ** bằng một part `{ reference: { mediaId, handle, fileName } }` (hoặc `{ entity: { entityId } }`), tạo ra `parts = [text, reference, text, reference, text]`. `gen_video_omni()` cục bộ chỉ gửi `parts: [{text: prompt}]` + `referenceImages[]` **rời**. ⇒ Mô hình mất thông tin "nhân vật nào ở vị trí nào trong câu"; với 2–3 nhân vật, kết quả gán nhầm mặt là chuyện thường. **Đây là khác biệt lớn nhất giữa replica và bản gốc.** Cần thêm tham số `prompt_parts` cho `gen_video_omni` (xem `newEndpoints`).
- **🔴 `gen_video_omni` ép model `abra_r2v_*` (OMNI Flash), không đụng tới `veo_3_1_r2v_*`.** `resolve_omni_flash_model(duration_s)` chỉ trả `abra_r2v_{4,6,8,10}s`. Trong exe, đường Character Sync dùng 4 key Veo: `veo_3_1_r2v_fast_portrait`, `veo_3_1_r2v_fast_portrait_ultra`, `veo_3_1_r2v_fast_portrait_ultra_relaxed`, `veo_3_1_r2v_lite_low_priority`. ⇒ Chọn `Veo 3.1 - Fast` / `Quality` / `Lite` ở replica **âm thầm chạy OMNI Flash với biểu giá khác** (`OMNI_FLASH_CREDIT_COST` = 15/20/25/30 credit theo 4/6/8/10s). Hoặc thêm tham số `video_model_key` cho `gen_video_omni`, hoặc khoá `🎬 Model` về `OMNI Flash` trên tab này.
- **🔴 Chỉ có key r2v cho `portrait`.** Trong 4 key Veo r2v mined, 3 key là `_portrait` và 1 key là `_lite_low_priority` (không rõ hướng). **Không có** key r2v landscape họ fast. ⇒ Chọn `Ngang 16:9` với model Veo nhiều khả năng không có key hợp lệ. Cho tới khi bắt được curl 16:9 thật, hãy khoá `📏 Tỷ lệ` về `Dọc 9:16` trên tab này (đúng với default `VIDEO_ASPECT_RATIO: "9:16"`).
- **Số 15 vs số 10.** UI mined nói `Tối đa 15 ảnh` / `Đã đủ số lượng nhân vật tối đa (15 ảnh).`, nhưng `huong_dan_su_dung_tool.md` của chính tác giả viết "(Tối đa 10 nhân vật)". 15 là hạn mức **ảnh** trong thư viện; 10 là hạn mức **nhân vật/cảnh của OMNI**. Đừng gộp: enforce 15 ở drop area, 3 (Veo) / 10 (OMNI) ở mỗi prompt.
- **Không bao giờ trộn Character entity với ảnh tham chiếu trong cùng một video.** Payload có cả `referenceImages` lẫn `referenceEntities`; luật vận hành mined của tác giả là mỗi video chỉ đi **một** đường. Trộn → `INVALID_ARGUMENT`. Ở replica (chỉ có ảnh, chưa có entity) thì bất biến là: **không bao giờ gửi `referenceEntities`**. Nếu sau này thêm entity, phải là radio loại trừ, không phải checkbox.
- **Giới hạn ảnh tham chiếu do model quyết định, không do UI.** `reference_image_limit_for_model_key(key)` mined: `key.startswith("abra_r2v_")` → **10**, còn lại → **3**. Nếu UI cho phép 5 tag mà model là Veo, request bị cắt/từ chối im lặng. Hạn mức trong UI **phải** đọc từ model đang chọn, không hardcode.
- **Tên nhân vật phải không dấu.** Validator `^[a-zA-Z0-9_]*$`. Người dùng Việt gõ `{Mai Anh}` (có dấu cách) hoặc `{Mai Ánh}` (có dấu) → tag không match, dòng chạy **không có** ảnh tham chiếu nào, sinh ra một video hoàn toàn khác mà vẫn trừ credit. Validate ngay tại ô tên **và** tại lúc parse tag; hiện lỗi bằng đúng chuỗi mined `Tên nhân vật chỉ được dùng chữ không dấu (a-z, A-Z), số (0-9) và dấu gạch dưới (_).`
- **`ensure_media_ids_in_project` có thể re-upload âm thầm.** Nếu ảnh nhân vật đến từ board/project khác, backend upload lại (tốn thời gian ở lần đầu, cache về sau) và trả `sync_failures` từng phần — nó **vẫn chạy tiếp** với các ref đã sync được. ⇒ Một nhân vật có thể "bốc hơi" khỏi video mà JobList vẫn báo `HOÀN THÀNH`. FE phải đọc `result.sync_failures` và đánh dấu hàng đó là cảnh báo, không phải thành công sạch.
- **`voice_id` bị nuốt.** Trong exe, `voice_id` là tham số của `build_payload_generate_video_reference` và có nhánh `referenceAudio`. `gen_video_omni()` cục bộ không nhận `voice_id`. ⇒ Bật `🎙️ Đồng Bộ Giọng Nói` ở replica **không có tác dụng gì**. Disable với tooltip, hoặc bổ sung tham số.
- **`aspect_ratio` của OMNI chỉ nhận PORTRAIT/LANDSCAPE.** `OMNI_FLASH_VALID_ASPECTS` không có SQUARE → gửi `IMAGE_ASPECT_RATIO_SQUARE` trả `omni_aspect_unsupported_…`. Tab này chỉ có 9:16 / 16:9 nên an toàn, nhưng đừng tái dùng combo tỉ lệ của tab ảnh.
- **`audioFailurePreference` khác nhau giữa hai đường.** `gen_video_omni()` cục bộ hardcode `BLOCK_SILENCED_VIDEOS`; exe chọn theo `SKIP_AUDIO_ERROR: true` → `RETURN_SILENCED_VIDEOS`. ⇒ Với cùng một prompt, replica sẽ **fail** ở nơi bản gốc **trả về video câm**. Người dùng quen tool cũ sẽ thấy tỉ lệ lỗi tăng vọt mà không hiểu vì sao.


# VIDEO TOOLS — nhóm B

> **Ghi chú khai thác (đọc trước khi build)**
> Toàn bộ nhãn dưới đây được mine byte-exact từ `RUN_VEO_3_ULTRA_PROMAX.exe` (Nuitka const-blob, tên module `qt_ui\tab_idea_to_video.py`, `qt_ui\tab_video_clone.py`, `qt_ui\tab_create_sub.py`, `qt_ui\tab_remove_watermark.py`) và từ `d:\TOOL_VIDEO\TOOL\data_general\config.json`.
>
> **Sidebar thật của exe** (mine nguyên khối, nhóm `VIDEO TOOLS`) — dùng đúng nhãn + subtitle + icon + màu này:
> | Nhãn | Subtitle | key | icon | màu |
> |---|---|---|---|---|
> | `Text to Video` | `Tạo video từ prompt` | `edit` | `✨` | `#7c3aed` |
> | `Image to Video` | `Tạo video từ ảnh` | `image` | `🎬` | `#0ea5e9` |
> | `Video Start-End` | `Làm video đầu - cuối` | `start_end_tab` | `🎞` | `#6366f1` |
> | `Character Sync` | `Đồng nhất nhân vật` | `sync` | `👤` | `#f59e0b` |
> | **`Idea to Video`** | **`Biến ý tưởng thành video`** | `idea` | `💡` | `#10b981` |
> | **`Phân tích video`** | **`Phân tích & lấy prompt video`** | `tab_video_clone` | `📋` | `#f43f5e` |
> | **`Phụ Đề & Xóa Logo`** | **`Tạo phụ đề & xóa watermark video`** | `video` | `📝` | `#ec4899` |
>
> **QUAN TRỌNG — sai lệch so với brief:** trong exe, `tab_create_sub` và `tab_remove_watermark` **là hai module riêng nhưng chỉ có MỘT mục sidebar** (`Phụ Đề & Xóa Logo`), và mục đó mount `CreateSubTab` — class này **đã bao gồm cả** nhóm `✂️ Xóa Logo & Watermark` (combo `Chế độ xóa:` = `Làm Mờ` / `Crop/Zoom` / `Chèn Logo`). `RemoveWatermarkTab` (`qt_ui\tab_remove_watermark.py`, objectName `RemoveWatermarkTab`) tồn tại đầy đủ nhưng không tìm thấy điểm mount trong sidebar → là tab watermark-only độc lập/legacy. Spec dưới đây tách thành **2 tab riêng** như yêu cầu; nếu muốn 1:1 với exe thì gộp làm 1 mục sidebar `Phụ Đề & Xóa Logo` với 2 checkbox `Tạo Phụ Đề` / `Xóa Logo`.

---

### Idea to Video — Biến ý tưởng thành video

**Mục đích:** Dán một kịch bản/ý tưởng thô, tool gọi LLM dựng nhân vật → kịch bản → prompt từng cảnh → ảnh tham chiếu, rồi sinh loạt video theo cảnh và nối lại.

**Bố cục:**
```
┌ ToolHeader ─────────────────────────────────────────────────────────────┐
│ [💡]  Idea to Video            Biến ý tưởng thành video                  │
└─────────────────────────────────────────────────────────────────────────┘
┌ Card A ─────────────────────────────────────────────────────────────────┐
│ r1: [📁 Dự án ▾ purple] [📂 Xem kết quả] [🚀 CHẠY] [⏹️ DỪNG]  <trạng thái>│
│     [████████░░░░░ progress 0-100 ]                                     │
│ r2: [📐 Tỷ lệ video ▾] [⏱ Thời lượng ▾] [🤖 Model ▾]  [⚙️ Cài đặt]      │
└─────────────────────────────────────────────────────────────────────────┘
┌ Card B — ✏️  Kịch bản / Ý tưởng: ───────────────────────────────────────┐
│ ┌ idea_editor (multiline) ──────────────────────────────────────────┐   │
│ │ Nhập kịch bản hoặc ý tưởng tại đây...                             │   │
│ └───────────────────────────────────────────────────────────────────┘   │
│ 💡 Nhân vật hiện có: <lbl_char_hints>                                   │
│ r: [🎬 Số cảnh] [🎨 Phong cách ▾][✏️][➕][🗑] [🗣️ Ngôn Ngữ Thoại ▾]      │
│ r: [🎭 Kiểu thoại ▾] [🎶 Nhạc nền ▾] [⚙️ Backend / Model ▾]             │
│    [☐ 🎙️ Đồng Bộ Giọng Nói (VIP)] [voice ▾] [▶ Nghe thử]              │
└─────────────────────────────────────────────────────────────────────────┘
┌ result_tabs ────────────────────────────────────────────────────────────┐
│ [📋 Nhật ký] [👥 Nhân vật] [🎬 Prompts] [🎬 Video]                       │
│  ── 📋 Nhật ký:  [📋 Lịch sử hệ thống] [📋 Copy Log] [🗑️ Xóa Log]       │
│  ── 👥 Nhân vật: [🖼️ Tạo Ảnh][✅ Tạo lại ảnh đã chọn][➕ Thêm nhân vật] │
│                  grid card nhân vật (checkbox + ảnh + prompt)           │
│  ── 🎬 Prompts:  [🔗 Map ảnh tự động]                                   │
│                  BẢNG CẢNH (1 dòng = 1 cảnh, prompt sửa được)           │
│  ── 🎬 Video:    [🎬 Tạo Video][⏹️ Dừng Video][🔄 Tạo lại video lỗi]     │
│                  [☑️ Tạo lại đã chọn][🔗 Nối Video][🗑️ Xóa hết Video]   │
│                  Tổng: 0 | Xong: 0 | Đang chạy: 0 | Lỗi: 0 | Chờ: 0     │
│                  grid thẻ video (JobCard)                               │
└─────────────────────────────────────────────────────────────────────────┘
```

**Điều khiển:**

| Nhãn | Loại | Giá trị / tập chọn | Mặc định (khoá config) | Ghi chú |
|---|---|---|---|---|
| `📁 Dự án` | LabeledSelect (purple) | danh sách thư mục dự án | `default_project` (`config.json → CURRENT_PROJECT`, danh sách `PROJECTS`) | Kèm `➕ Tạo` (`BtnCreate` #10b981) và `🗑 Xóa` (`BtnDelete` #ef4444). Tên dự án bị `re.sub(r'[^\w\s-]','')`. |
| `📂 Xem kết quả` | GhostButton | — | — | `startfile(project_dir)`; web → mở ResultsDrawer. |
| `🚀 CHẠY` | PrimaryButton (gradient `#10b981→#059669`, 100×34) | — | — | `_on_run_clicked`. |
| `⏹️ DỪNG` | StopButton (gradient `#ef4444→#991b1b`, 90×34) | — | — | Xác nhận: `Bạn có chắc muốn DỪNG quy trình đang chạy?\n\nDữ liệu đã hoàn thành sẽ được giữ lại.` |
| `📐 Tỷ lệ video` | LabeledSelect | `Dọc 9:16`→`9:16` · `Ngang 16:9`→`16:9` | `9:16` (`VIDEO_ASPECT_RATIO`) | |
| `⏱ Thời lượng` | LabeledSelect | `4s`→4 · `6s`→6 · `8s`→8 · **`10s`→10 chỉ khi model = `OMNI Flash`** | `8` (`VIDEO_DURATION_SECONDS`) | `_update_duration_options()` phụ thuộc model. |
| `🤖 Model` | LabeledSelect | `Veo 3.1 - Fast` · `Veo 3.1 - Quality` · `Veo 3.1 - Lite [Lower Priority]` · `OMNI Flash` | `Veo 3.1 - Lite [Lower Priority]` (`IDEA_MODEL`, fallback `VEO_MODEL`) | `_ensure_model_allowed()`: Lite[Lower Priority] **chỉ cho `TYPE_ACCOUNT == ULTRA`**, ngược lại cảnh báo `Model không hỗ trợ` / `Model Lower Priority chỉ hỗ trợ tài khoản ULTRA.` rồi ép về `Veo 3.1 - Fast`. |
| `⚙️ Cài đặt` | GhostButton | — | — | Trong exe mở modal `⚙️ Cấu hình Idea to Video` chứa toàn bộ hàng dưới; bản web đặt inline ở Card B (giữ nguyên nhãn). |
| `🎬 Số cảnh` | Field số nguyên | `QIntValidator(1, 500)` | `1` (`IDEA_SCENE_COUNT`; hằng fallback trong code là `5`) | Nhập tay, không phải combo. `min(scene_count, 50)` khi dựng UI thẻ. |
| `🎨 Phong cách` | LabeledSelect + `✏️`/`➕`/`🗑` | 19 mục `name` từ `data_general/video_styles.json` (`Người Que (Stick Man)`, `Hoạt Hình 3D Pixar`, `Anime Nhật Bản (Ghibli)`, `Người Thật Điện Ảnh`, `Quay Thật Hollywood`, `Hoạt Hình 2D`, `Hoạt Hình 3D Cartoon`, `CGI 3D Siêu Thực`, `Phim Tài Liệu`, `Sinh Tồn Sử Thi`, `Noir Đen Trắng`, `Pixel Art 8-bit`, `Cổ Điển Retro`, `MV Âm Nhạc`, `CGI Siêu Thực`, `Góc Nhìn Thứ Nhất`, `Camera Giám Sát`, `Phim Thể Nghiệm`, `Sân Khấu Kịch`) | `3d_Pixar` (`IDEA_STYLE`) | Nạp qua `listVideoStyles()`. Tooltip nút: `Sửa phong cách` / `Thêm phong cách mới` / `Xóa phong cách`. Editor: `Tên phong cách:` (placeholder `VD: My_Style`), `Nội dung mô tả (visual style):` (placeholder `Mô tả chi tiết phong cách hình ảnh...`), `💾 Lưu`. |
| `🗣️ Ngôn Ngữ Thoại` | LabeledSelect | `Tiếng Việt (vi-VN)` · `English (en-US)` · `中文 (zh-CN)` | `Tiếng Việt (vi-VN)` (`IDEA_DIALOGUE_LANGUAGE`) | |
| `🗣️ Kiểu thoại` | LabeledSelect | `🎭 Lời thoại nhân vật`→`dialogue` · `📖 Chỉ lời dẫn dắt câu chuyện`→`narration` · `🔇 Không thoại` | `🎭 Lời thoại nhân vật` (`IDEA_DIALOGUE_MODE`; giá trị máy `IDEA_DIALOGUE_MODE_VAL = "dialogue"`) | Chọn `🔇 Không thoại` ⇒ set `IDEA_NO_DIALOGUE=true` (mặc định `false`). |
| `🎶 Nhạc nền` | LabeledSelect | `🎶 Có nhạc nền` · `🚫 Không nhạc nền` | `🎶 Có nhạc nền` (`IDEA_BG_MUSIC`) | Worker nhận `bg_music_disabled`. Nguồn nhạc: `data_general/nhac_nen/*.MP3`. |
| `⚙️ Backend / Model` | LabeledSelect | `🌐 Chrome Gemini (Trình duyệt)`→`chrome` · `⚡ API — gemini-3.5-flash` · `⚡ API — gemini-3.6-flash` · `⚡ API — gemini-3.5-flash-lite` · `🧪 API — gemini-3-flash-preview` · `⚡ API — gemini-2.5-flash` · `💡 API — gemini-3.1-flash-lite` | `⚡ API — gemini-3.6-flash` (`IDEA_BACKEND` hiện lưu `🌐 Chrome Gemini`; khoá model `idea_gemini_model` mặc định `gemini-3.6-flash`, **không có trong config.json**) | Bản local **bỏ nhánh Chrome**, chỉ dùng provider LLM đã cấu hình qua `getLlmProviders()/setLlmConfig()`. |
| `🎙️ Đồng Bộ Giọng Nói` + ` (VIP)` | Checkbox + Select + `▶ Nghe thử` | `.wav` trong `data_general/Voice/` | tắt (`VOICE_SYNC_ENABLED=false`, `VOICE_SYNC_ID=""`) | Trong exe chặn bằng license (`Bạn hãy nâng cấp tài khoản VIP.`) → **bỏ chặn, bật thẳng**. |
| `✅ Xác nhận` / `❌ Hủy` | Buttons | — | — | Chỉ tồn tại nếu giữ dạng modal. |
| Bảng cảng (tab `🎬 Prompts`) | PromptTable, editable | 1 dòng/cảnh: `#`, ảnh tham chiếu (click đổi), prompt (`👉 Bấm vào đây để chỉnh sửa`) | sinh từ `step3_prompts_file.json` | Double-click mở dialog `Sửa Prompt Cảnh <n>`. `🔗 Map ảnh tự động` = `_on_auto_map_images`. |
| `🖼️ Tạo Ảnh` / `✅ Tạo lại ảnh đã chọn` / `➕ Thêm nhân vật` | Buttons (#7c3aed / #0f766e / #0f4c75) | — | — | Tab `👥 Nhân vật`. |
| `🎬 Tạo Video` / `⏹️ Dừng Video` / `🔄 Tạo lại video lỗi` / `☑️ Tạo lại đã chọn` / `🔗 Nối Video` / `🗑️ Xóa hết Video` | Buttons | — | — | Tab `🎬 Video`. |
| Bộ đếm | StatusChip ×5 | `Tổng: 0` · `Xong: 0` (#10b981) · `Đang chạy: 0` (#3b82f6) · `Lỗi: 0` (#ef4444) · `Chờ: 0` (#f59e0b) | 0 | `_update_video_counters`. |

**Luồng:**
1. User dán ý tưởng vào `idea_editor` → bấm `🚀 CHẠY`. Rỗng ⇒ cảnh báo `Thiếu nội dung` / `Vui lòng nhập kịch bản / ý tưởng.`
2. FE gọi `ensureBoardProject(boardId)` để có `projectId` Flow (nếu chưa) — chỉ cần khi tới bước sinh ảnh/video.
3. FE gọi **`POST /api/idea/expand`** *(MỚI)* với `{projectId, idea, sceneCount, style, language, dialogueMode, noDialogue, bgMusic, keepChars, resumeMode}`. BE chạy pipeline 7 bước, stream log qua SSE:
   `⏳ Step 1: Tạo nhân vật và bối cảnh...` → `⏳ Step 1.5: Tạo prompt ảnh tham chiếu nhân vật...` → `⏳ Step 2: Tạo kịch bản và đối thoại...` → `⏳ Step 3: Tạo prompts chi tiết...` → `⏳ Step 4: Tạo ảnh tham chiếu nhân vật...` → `⏳ Step 5: Map ảnh nhân vật vào cảnh...` → `⏳ Step 6: Lưu nhân vật để tái sử dụng...` → `⏳ Step 7: Format prompts từ step3...`
   Artefact ghi ra: `step1_char_file.json`, `step1_5_char_ref_prompts.json`, `step2_shot_file.json`, `step3_prompts_file.json`, `step4_ref_images.json`, `step5_scene_image_map.json`, `characters.json`.
4. **Resume gate** — trước khi chạy, `GET /api/idea/{project}/progress` *(MỚI)* trả cờ `step1_done / step2_done / step2_partial / step3_done / step4_done / resume_from`. Nếu có dữ liệu cũ hiện modal `Phát hiện dữ liệu cũ` với nội dung:
   `Dự án đã có dữ liệu từ lần chạy trước:` + list `✅/⚠️/⬜ Bước 1 — Nhân vật & Bối cảnh`, `Bước 2 — Kịch bản`, `Bước 1.5 — Prompt ảnh nhân vật`, `Bước 3 — Prompt chi tiết`, `Bước 4 — Ảnh tham chiếu`, rồi `▶ Sẽ tiếp tục từ: <bước>` và `Bạn muốn VIẾT TIẾP từ chỗ dừng hay TẠO MỚI từ đầu?`. Nhánh TẠO MỚI hỏi tiếp `Giữ nhân vật cũ?` (`- [Yes]: Giữ nhân vật, xóa kịch bản/prompt cũ.` / `- [No]: Xóa tất cả và tạo lại từ đầu.`).
5. Ảnh nhân vật: mỗi nhân vật → `createRequest({type:'gen_image', params:{prompt, project_id, model:'Nano Banana 2', quality:'1k', output_count:1}})`, poll `getRequest(id)`, hiển thị bằng `mediaUrl(mediaId)`.
6. User sửa bảng cảnh (`🎬 Prompts`), có thể `🔗 Map ảnh tự động`.
7. Bấm `🎬 Tạo Video`: mỗi dòng bảng →
   * model ≠ `OMNI Flash` ⇒ `createRequest({type:'gen_video', params:{prompt, project_id, aspect_ratio, paygate_tier, start_media_id?, video_quality}})`
   * model = `OMNI Flash` ⇒ `createRequest({type:'gen_video_omni', params:{prompt, project_id, aspect_ratio, duration_s, start_media_ids}})`
   Poll `getRequest(id)` cho tới `done|failed|canceled|timeout`; nhãn trạng thái đúng bộ mine: `⏳ Đang chờ...` (#64748b) → `⬆️ Upload ảnh...` (#f59e0b) → `🔄 Đang tạo...` (#3b82f6) → `📨 Đã gửi, đang xử lý...` (#f59e0b) → `⬇️ Đang tải...` (#10b981) → `✨ Đang upscale...` (#8b5cf6) → `✅ Hoàn thành!\n▶ Bấm để xem` (#10b981) / `❌ Thất bại` (#ef4444).
8. `🔗 Nối Video` → `POST /api/postprod/concat {clips[], output, width, height, fps}`; nếu `🎶 Có nhạc nền` thì nối tiếp `POST /api/postprod/bgm`.
9. `⏹️ DỪNG` → `cancelActivity(id)` cho mọi request đang chạy + `POST /api/idea/{project}/stop` *(MỚI)*.

**Kết quả:** Tab `🎬 Video` render grid `JobCard` (thumbnail lấy bằng ffmpeg `-ss 0.5 -vframes 1 -vf scale=400:-1`, file `<name>.thumb.jpg`), viền `#10b981` khi xong; click mở player. Bộ đếm `Tổng / Xong / Đang chạy / Lỗi / Chờ` cập nhật realtime. `📂 Xem kết quả` mở `ResultsDrawer` trỏ vào `VIDEO_OUTPUT_DIR` (`D:/TOOL_VIDEO/VIDEO_OUT`).

**Cạm bẫy:**
- **`⏱ Thời lượng` chỉ có tác dụng khi model = `OMNI Flash`.** `gen_video()` không nhận `duration`. Với 3 model Veo 3.1 phải **disable** select thời lượng (hoặc hiện tooltip `Thời lượng chỉ áp dụng cho OMNI Flash`) — nếu để bật, user chọn 4s nhưng vẫn nhận clip 8s và mất credit.
- **`IDEA_MODEL` mặc định trong config.json là `Veo 3.1 - Lite [Lower Priority]` — tài khoản Pro (`PAYGATE_TIER_ONE`) KHÔNG có lane 0-credit này.** Phải chạy `_ensure_model_allowed` **ngay khi mount tab**, không đợi lúc bấm CHẠY, nếu không mọi request cảnh đều 4xx sau khi đã trừ token.
- `MULTI_VIDEO = 4` ⇒ nếu tái dùng đường sinh video mặc định thì **1 cảnh = 4 clip = 4 lần trừ credit**. Idea to Video sinh theo cảnh nên phải ép `output_count = 1` (`OUTPUT_COUNT` = 1) và bỏ qua `MULTI_VIDEO`.
- `IDEA_STYLE` lưu slug `3d_Pixar` nhưng combo hiển thị `name` tiếng Việt (`Hoạt Hình 3D Pixar`). Nếu so khớp thẳng bằng chuỗi thì combo rơi về index 0 (`Người Que (Stick Man)`) và **toàn bộ video sẽ ra phong cách người que**. Phải map slug↔name hai chiều.
- `🎬 Số cảnh` cho tới 500 nhưng UI chỉ dựng tối đa 50 thẻ (`min(scene_count, 50)`); >50 cảnh sẽ sinh video mà không có thẻ tương ứng để retry.
- Nhánh `🌐 Chrome Gemini (Trình duyệt)` trong exe điều khiển Chrome thật; bản local **không có** — phải ẩn mục này khỏi combo backend, đừng để user chọn rồi treo.
- Resume gate đọc file trên đĩa; nếu FE bỏ qua `GET /api/idea/{project}/progress` thì lần chạy thứ hai sẽ **ghi đè `characters.json`** và mất toàn bộ nhân vật đã sinh ảnh.

---

### Phân tích video — Phân tích & lấy prompt video

**Mục đích:** Nạp video mẫu (link FB/YouTube/TikTok hoặc file local), cho LLM thị giác phân tích và trả về bộ prompt VEO 3 tái tạo lại video đó (prompt ảnh, prompt video, lời thoại, thumbnail, tiêu đề).

**Bố cục:**
```
┌ ToolHeader ─────────────────────────────────────────────────────────────┐
│ [🎬] Phân Tích Video & Lấy Prompt VEO 3                                  │
│      — Tải video mẫu, AI phân tích và tạo prompt tái tạo video.         │
└─────────────────────────────────────────────────────────────────────────┘
┌ Card A ─────────────────────────────────────────────────────────────────┐
│ [+ Tạo Dự Án mới] [dự án ▾ purple] [📂 Mở Folder] [🗑 Xóa]              │
└─────────────────────────────────────────────────────────────────────────┘
┌ Card B ─────────────────────────────────────────────────────────────────┐
│ 🎬 Video (mỗi dòng 1 link):     [📥 Import Excel][📊 Xem kết quả]        │
│                                  [📂 Chọn file][✕ Xóa]                  │
│ ┌ txt_video_input ──────────────────────────────────────────────────┐   │
│ │ Dán link Facebook / YouTube / TikTok hoặc đường dẫn file video... │   │
│ └───────────────────────────────────────────────────────────────────┘   │
│ 💡 Hỗ trợ: Link Facebook Reels/Watch, YouTube, TikTok, hoặc file video…  │
│ [✕ Xóa Excel]  (chỉ hiện khi đang ở chế độ Import Excel)                │
└─────────────────────────────────────────────────────────────────────────┘
┌ ✨ Tuỳ chỉnh nâng cao (Tuỳ chọn) — CfgGroup ────────────────────────────┐
│ [Ngôn ngữ viết prompt ▾][Số luồng ▴▾][Ảnh tham chiếu ▾][☑ 🔒 Khóa số…]  │
│ [📹 Loại phân tích ▾][🤖 Gemini Model ▾][Thời lượng][Giọng đọc ▾][+][x] │
│ [🎨 Phong cách ▾][Thêm phong cách][Xóa phong cách]                      │
│ [Retry lỗi 503: ▴▾ lần][☑ Tự động đổi model khi hết retry 503]          │
│ [Model đổi sang sau khi hết retry ▾]                                    │
│ (khi Ảnh tham chiếu = CÓ) [📷 Chọn ảnh nhân vật] Chưa chọn ảnh nào [✕]  │
│ ✨ Yêu cầu tùy chỉnh (Ưu tiên cao nhất - ghi đè các cài đặt khác…)      │
│ ┌ txt_custom_instruction ───────────────────────────────────────────┐   │
│ └───────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
        [ 🔍  Phân Tích & Lấy Prompt ]        [ ⛔  Dừng ]
┌ 📊  Kết quả Phân tích ──────────────────────────────────────────────────┐
│ [🖼 Copy Prompt Ảnh][🎬 Copy Prompt Video][📋 Sao chép][📊 Xem kết quả] │
│ ResultEditor: Prompt Ảnh | Prompt Video | Lời Thoại | Prompt Thumbnail  │
│              | Tiêu Đề            (mỗi khối sửa được)                   │
└─────────────────────────────────────────────────────────────────────────┘
```

**Điều khiển:**

| Nhãn | Loại | Giá trị / tập chọn | Mặc định (khoá config) | Ghi chú |
|---|---|---|---|---|
| `+ Tạo Dự Án mới` | PrimaryButton (gradient `#f59e0b→#d97706`, chữ đen) | — | — | Dự án riêng của tab, thư mục `clone_projects/<name>`. |
| dự án | LabeledSelect (purple `#5b21b6→#7c3aed`) | thư mục con của `clone_projects/` | `default_project` (`CURRENT_PROJECT`) | Auto-save mỗi 10 s (`_auto_save_timer`). |
| `📂 Mở Folder` / `🗑 Xóa` | GhostButton / StopButton | — | — | |
| `🎬 Video (mỗi dòng 1 link):` | Textarea | 1 link/dòng | rỗng | Placeholder mine nguyên: `Dán link Facebook / YouTube / TikTok hoặc đường dẫn file video local...\nMỗi dòng 1 link. Ví dụ:\nhttps://www.youtube.com/shorts/abc123\nhttps://www.tiktok.com/@user/video/456\nD:\Videos\my_video.mp4` |
| `📥 Import Excel` | PrimaryButton (#16a34a) | `.xlsx` | — | Mẫu: `data_general/excel_mau_batch.xlsx`. Khi bật hiện `_lbl_import_file` + `✕ Xóa Excel` (tooltip `Hủy Import Excel`). |
| `📂 Chọn file` | FilePickerButton (#6366f1→#8b5cf6) | `.mp4 .avi .mov .mkv .webm` | — | |
| `✕ Xóa` | GhostButton đỏ | — | — | |
| `📊 Xem kết quả` | GhostButton (#7c3aed) | — | — | Mở CSV/XLSX kết quả. |
| `Ngôn ngữ viết prompt` | LabeledSelect | `Tiếng Việt`→`vi` · `English`→`en` | `Tiếng Việt` (không có trong config.json) | |
| `Số luồng` | SpinBox | 1–11 | `1` (không có trong config.json) | Số worker song song (`_fill_worker_slots`); mỗi luồng lấy 1 API key round-robin. |
| `Ảnh tham chiếu` | LabeledSelect | `KHÔNG`→`no` · `CÓ`→`yes` | `KHÔNG` | `CÓ` mở `_ref_image_container`. |
| `📷 Chọn ảnh nhân vật` | FilePickerButton | ảnh | `Chưa chọn ảnh nào` | Kèm `✕ Xóa tất cả`. **Tối đa 3 ảnh tham chiếu** (giới hạn chung của tool). |
| `🔒 Khóa số cảnh theo thời gian` | Checkbox | — | **bật** | Tooltip mine nguyên: `Bật: Số cảnh = ceil(thời lượng / 8s), cố định theo thời lượng video.\nTắt: AI tự quyết định số cảnh dựa trên nội dung video.` |
| `📹 Loại phân tích` | LabeledSelect (label #f59e0b) | `📹 Video thường` → `system_prompts/standard_mode.txt` · `📹 Video thường TEXT` → `text_mode.txt` · `🧍 Video Người Que` → `stick_figure_mode.txt` · `🏥 Video sức khỏe` → `health_mode.txt` | `📹 Video thường` (khoá node `analysis_mode`, **không có trong config.json**) | ⚠️ Trong exe combo này nằm ở **inspector của node `Phân Tích Video` trong Workflow**, tab standalone luôn nạp cứng `standard_mode.txt`. Bản replica đưa lên tab (chủ ý). Log tương ứng: `🧩 Chế độ phân tích: VIDEO THƯỜNG WORKFLOW (...)`, `🧍 Chế độ phân tích: VIDEO NGƯỜI QUE (stick figure)`, `🏥 Chế độ phân tích: VIDEO SỨC KHỎE`. |
| `🤖 Gemini Model` | LabeledSelect | `⚡ API — gemini-3.5-flash` · `⚡ API — gemini-3.6-flash` · `⚡ API — gemini-3.5-flash-lite` · `🧪 API — gemini-3-flash-preview` · `⚡ API — gemini-2.5-flash` · `💡 API — gemini-3.1-flash-lite` · `🌐 Chrome Gemini (trình duyệt)`→`chrome_gemini` | `gemini-3.6-flash` (không có trong config.json) | Bản local: nạp từ `getLlmProviders()`, **ẩn mục Chrome**. |
| `Thời lượng` | Field | giây (rỗng = theo video gốc) | rỗng | `target_duration`; text `Original video length` khi bỏ trống. |
| `Giọng đọc` | LabeledSelect + `Thêm` / `Xóa` | 16 `title` từ `data_general/voice_styles.json` + `🗣️ Không chọn` + `🗣️ Giọng tùy chỉnh (Nhập prompt riêng)` | `🗣️ Không chọn` | Chọn giọng tùy chỉnh ⇒ hiện `txt_custom_voice` (placeholder `Nhập mô tả chi tiết giọng đọc của bạn...`). Tooltip nút: `Thêm giọng đọc` / `Xóa giọng đọc`. |
| `🎨 Phong cách` | LabeledSelect + `Thêm` / `Xóa` | 19 mục `video_styles.json` | (rỗng = giữ style video gốc) | Tooltip: `Thêm phong cách` / `Xóa phong cách`. |
| `Retry lỗi 503:` | SpinBox + suffix ` lần` | 0–10 | `3` (khoá node `retry_503_count`) | Tooltip: `Số lần thử lại thêm sau lần gọi đầu tiên khi Gemini trả lỗi 503`. |
| `Tự động đổi model khi hết retry 503` | Checkbox | — | **bật** (`auto_switch_model_503`) | |
| `Model đổi sang sau khi hết retry` | LabeledSelect | như `🤖 Gemini Model` | `⚡ API — gemini-3.5-flash-lite` (`fallback_gemini_model`) | |
| `✨ Yêu cầu tùy chỉnh (Ưu tiên cao nhất - ghi đè các cài đặt khác nếu xung đột)` | Textarea (nền amber `rgba(245,158,11,.08)`) | tự do | rỗng | Placeholder mine nguyên: `Ví dụ: Giữ nguyên kịch bản nhưng thay nhân vật nữ thành nam...\nHoặc: Chuyển bối cảnh sang không gian vũ trụ...\nĐể trống nếu không có yêu cầu đặc biệt.` |
| `🔍  Phân Tích & Lấy Prompt` | PrimaryButton (gradient 4 chặng `#00f2fe→#7c3aed→#f59e0b→#00f2fe`, cao 48) | — | — | |
| `⛔  Dừng` | StopButton (`#dc2626→#991b1b`, cao 120px width) | — | — | |
| `🖼 Copy Prompt Ảnh` / `🎬 Copy Prompt Video` / `📋 Sao chép` | GhostButtons | — | — | Toast: `✅  Đã sao chép` / `✅  Đã sao chép Social Media Content!` |

**Luồng:**
1. User dán N link (hoặc chọn file / Import Excel) → bấm `🔍  Phân Tích & Lấy Prompt`. Không có Gemini key ⇒ chặn với thông báo `Tính năng Phân tích video cần Gemini API Key để hoạt động.` (kiểm tra bằng `getLlmConfig()`).
2. Mỗi dòng → **`POST /api/analyze/ingest`** *(MỚI)* `{source}`. BE nhận diện `facebook|youtube|tiktok|local`; TikTok thử `https://www.tikwm.com/api/` trước, fallback `yt-dlp` (`-f best[height<=480]... --merge-output-format mp4 --no-playlist`), sau đó remux H.264 (`libx264 -preset fast -crf 23 -c:a aac`) nếu codec khác. Trả `{sourceId, path, durationSeconds, width, height, hasAudio}`.
3. **`POST /api/analyze/frames`** *(MỚI)* `{sourceId, everyNSeconds|frameCount, sheetGrid}` → BE tách frame bằng ffmpeg rồi ghép thành **contact sheet** (lưới ảnh), upload từng sheet qua `uploadImage()` → `{sheets:[{mediaId, fromSec, toSec}], frameCount}`.
   *Lý do:* provider Gemini CLI **không nhận file mp4**, chỉ nhận ảnh. Bản exe gốc gửi base64 nguyên video (API) hoặc attach file vào Chrome — cả hai đều không dùng lại được.
4. `createRequest({type:'analyze_video', params:{sourceId, sheetMediaIds, mode, language, style, voice, customVoicePrompt, customInstruction, lockSceneCount, targetDuration, refMediaIds, characterNames, geminiModel, retry503Count, autoSwitchModel503, fallbackGeminiModel}})` *(request type MỚI cho worker)*.
   BE nạp template theo `mode`, thay các placeholder mine được: `__REF_PRE_INSTRUCTION__`, `__LANG_INSTRUCTION__`, `__CUSTOM_TEXT__`, `__DUR_TEXT__`, `__STYLE_TEXT__`, `__VOICE_TEXT__`, `__CHAR_NAMES_TEXT__`, `__SCENE_COUNT_HEADER__`, `__CHAR_IDS__` (mặc định `CHAR_1, CHAR_2`), `__DIALOGUE_RULE__`, `__IMAGE_EXAMPLE__`, `__VIDEO_EXAMPLE__`, `__OUTPUT_IMAGE_EXAMPLE__`, `__OUTPUT_VIDEO_EXAMPLE__`, `__VOICE_EXAMPLE__`, `__OUTPUT_FORMAT_RULE__`, `__REF_BLOCK__`, `__LANG_NAME__`, `__DURATION_TEXT__`.
5. Poll `getRequest(id)`. Log tiến trình: `🎬 Bắt đầu phân tích video:` → `🔍 Đang phân tích qua API Gemini...` → `🎉 Đã lấy kết quả thành công.` / `🎉 Phân tích hoàn tất! Tìm thấy <n> cảnh`. Lỗi 503 ⇒ `🔁 Lỗi 503: retry thêm <n>` rồi `🔁 Đổi model Gemini Prompt sang <model>`.
6. Kết quả JSON parse đệ quy (`_find_scenes_recursive`, chấp nhận khoá `scenes|scene|script|prompts|kich_ban|kịch bản` và item khoá `image_prompt|veo_prompt|video_prompt|imageprompt|veoprompt|scene_number|scenenumber|visual_prompt|image_description|video_description`) → đổ vào `ResultEditor` 5 khối: `Prompt Ảnh`, `Prompt Video`, `Lời Thoại`, `Prompt Thumbnail`, `Tiêu Đề`.
7. Lưu `full_data_gemini.txt` + `data.json` trong thư mục dự án; `📊 Xem kết quả` gọi **`GET /api/analyze/{projectId}/export?format=csv|xlsx`** *(MỚI)*.
8. `⛔  Dừng` → `cancelActivity(id)` cho mọi worker; log `⛔ Đã dừng theo yêu cầu.`

**Kết quả:** Card `📊  Kết quả Phân tích` với 5 khối text **sửa được tại chỗ**; chỉnh xong tự lưu vào `data.json` dự án. Ba nút copy đổ thẳng vào clipboard. Batch nhiều link ⇒ mỗi link là 1 hàng `JobList` với trạng thái, khi Import Excel thì ghi ngược kết quả vào chính file Excel (`📝 Đã ghi kết quả vào Excel (hàng <n>)`).

**Cạm bẫy:**
- **`gen_video()` không có ở tab này** — tab chỉ sinh *text*. Không render Card A row2 (`Tỷ lệ / Thời lượng / Model`) ở đây; nếu render, user sẽ tưởng tab tạo video và nhấn nhầm.
- **Không attach mp4 cho LLM.** Bắt buộc đi qua contact sheet; nếu ai đó “tối ưu” bằng cách gửi thẳng file thì provider CLI trả lỗi im lặng và kết quả ra rỗng nhưng request vẫn `done`.
- Contact sheet mất **âm thanh** ⇒ `Lời Thoại` sẽ bị bịa nếu không kèm transcript. Phải chạy STT (`POST /api/postprod/stt`, xem tab Tạo phụ đề) và nhét transcript vào `__VOICE_TEXT__`, nếu không `narration_voice` và `dialogue_sequence` là hallucination thuần túy.
- `🔒 Khóa số cảnh theo thời gian` bật ⇒ số cảnh = `ceil(duration/8)`. Với video 3 phút = 23 cảnh; nhân `MULTI_VIDEO=4` ở bước sinh video sau đó = **92 lần trừ credit**. Hiện cảnh báo số cảnh dự kiến ngay dưới checkbox.
- `Số luồng` > số API key khả dụng ⇒ key bị dùng lại đồng thời và Gemini trả 429; giới hạn `min(threads, len(keys))`.
- `📹 Loại phân tích` không được lưu ở `config.json` — nếu không tự persist vào state dự án thì mỗi lần mở lại tab đều rơi về `standard_mode` và user không hiểu vì sao video người que ra thành người thật.
- Nhánh `🌐 Chrome Gemini (trình duyệt)` phải bị ẩn (bản local không điều khiển Chrome).

---

### Tạo phụ đề (tab_create_sub) — Tạo phụ đề & xóa watermark video

**Mục đích:** Nạp loạt video, sinh/nạp phụ đề rồi burn vào video theo 7 kiểu chữ ASS, kèm 39 preset chữ nghệ thuật và preview trước/sau ngay trên canvas.

**Bố cục:** (splitter ngang, trái = cấu hình cuộn, phải = preview)
```
┌ ToolHeader ─────────────────────────────────────────────────────────────┐
│ [📝] Tạo phụ đề            Tạo phụ đề & xóa watermark video              │
└─────────────────────────────────────────────────────────────────────────┘
┌ TRÁI (scroll) ───────────────────┐ ┌ PHẢI ────────────────────────────┐
│ 🎥 Nguồn Video Xử Lý (#10b981)   │ │ 🔍 Xem Thử Trực Quan & Căn Chỉnh │
│ [Chọn File Video][Chọn Thư Mục]  │ │  (#00f2fe)                       │
│ [Xóa Hết]                        │ │ ┌[Trước Xử Lý (Xem Thử)]────────┐│
│ ┌[🎥 Video Gốc][✅ Video Kết Quả]│ │ │[Sau Xử Lý (Kết Quả)]          ││
│ │ list_videos / list_processed   │ │ │  canvas 1920×1080 hoặc 9:16   ││
│ └────────────────────────────────┘ │ │  (kéo phụ đề/logo để đặt vị trí)│
│ ⚙️ Chức Năng Thực Hiện (#3b82f6)  │ │ └───────────────────────────────┘│
│  ☑ Tạo Phụ Đề    ☐ Xóa Logo      │ │ [──────▮────────] 00:00 / 00:00 │
│ 🎨 Phong Cách Phụ Đề (#ec4899)   │ └──────────────────────────────────┘
│  Phông chữ: [▾]    Cỡ chữ: [24]  │
│  Màu chữ: [▾] Màu mờ: [▾] Màu viền:[▾]
│  Kiểu chữ: [▾]                   │
│  Căn lề dọc: [──▮────] 150 px    │
│  Độ dày viền: [2.5] Bóng đổ:[0.0]│
│  Chữ nghệ thuật: [▾]             │
│ ✂️ Xóa Logo & Watermark (#f59e0b)│
│  Chế độ xóa:[▾] Tỉ lệ khung hình:[▾]
│  Ảnh đè:[………][Chọn]  |  Tỉ lệ zoom:[107 %]
│  [W 115][H 52]                   │
│  Vị trí nhanh: [↖ Trái Trên][↗ Phải Trên][↙ Trái Dưới][↘ Phải Dưới]
│ 📂 Thư Mục Lưu Kết Quả (#a855f7) │
│  [………][Chọn]                     │
│ 📝 Tiến Trình & Nhật Ký (#cbd5e1)│
│  <log>  [████████░░]             │
│  [🚀 Bắt Đầu Xử Lý] [🛑 Dừng]    │
└──────────────────────────────────┘
```

**Điều khiển:**

| Nhãn | Loại | Giá trị / tập chọn | Mặc định (khoá config) | Ghi chú |
|---|---|---|---|---|
| `Chọn File Video` | FilePickerButton (Warning, h=30) | filter mine nguyên: `Video Files (*.mp4 *.mov *.mkv *.webm *.avi);;All Files (*)`, tiêu đề `Chọn các file video cần xử lý` | — | Log `Đã thêm <n> file video vào danh sách.` |
| `Chọn Thư Mục` | FilePickerButton | quét `*.mp4 *.mov *.mkv *.webm *.avi`, tiêu đề `Chọn thư mục chứa video` | — | Log `Đã quét và thêm <n> video từ thư mục.` |
| `Xóa Hết` | GhostButton (DangerSoft) | — | — | Log `Đã dọn sạch danh sách video.` |
| `🎥 Video Gốc` / `✅ Video Kết Quả` | inner tabs (`tab_original` / `tab_processed`) | — | `🎥 Video Gốc` | Click item ⇒ nạp preview. |
| `Tạo Phụ Đề` | Checkbox | — | **bật** | Tắt ⇒ ẩn nhóm `🎨 Phong Cách Phụ Đề`. |
| `Xóa Logo` | Checkbox | — | tắt | Bật ⇒ hiện nhóm `✂️ Xóa Logo & Watermark`. |
| `Phông chữ:` | LabeledSelect (render bằng chính font đó, `maxVisibleItems` giới hạn) | `Arial`, `Trebuchet MS`, `Impact`, `Segoe UI`, `Tahoma` + toàn bộ họ font từ `CapCut_Fonts/` (41 file `.ttf/.otf`) | `Arial` (`SUB_FONT_FAMILY`) | Nạp qua `GET /api/postprod/status → .fonts`. |
| `Cỡ chữ:` | SpinBox (w=80) | 8 – 120 | `24` (`SUB_FONT_SIZE`) | |
| `Màu chữ:` | LabeledSelect có icon màu | `Trắng`→`&H00FFFFFF` · `Xám`→`&H00808080` · `Vàng`→`&H0000FFFF` · `Xanh Lá`→`&H0000FF00` · `Xanh Dương`→`&H00FFFF00` · `Đỏ`→`&H000000FF` · `Hồng`→`&H00FF00FF` · `Cam`→`&H000055FF` · `Đen`→`&H00000000` | `&H00FFFFFF` (`SUB_PRIMARY_COLOR`) | Bảng màu icon: `#ffffff #808080 #facc15 #22c55e #3b82f6 #ef4444 #ec4899 #f97316 #94a3b8`. |
| ` Màu mờ:` | LabeledSelect (cùng tập) | như trên | `&H00808080` (khoá code `sub_inactive_color`, **không có trong config.json**) | Chỉ có nghĩa với `Tô sáng từ (Word Highlight)` và `Karaoke chạy chữ`. |
| ` Màu viền:` | LabeledSelect (cùng tập) | như trên | `&H00000000` (`SUB_OUTLINE_COLOR`) | |
| `Kiểu chữ:` | LabeledSelect | **7 kiểu, nhãn↔giá trị byte-exact:**<br>`Chuẩn SRT (Tĩnh)`→`Standard SRT`<br>`Chữ nhảy (Bounce Pop)`→`Bounce Pop ASS`<br>`Thu phóng Shorts (Zoom Pop)`→`Zoom Pop ASS`<br>`Xoay nghiêng nảy (Tilt Bounce)`→`Tilt Bounce ASS`<br>`Tô sáng từ (Word Highlight)`→`Word Highlight ASS`<br>`Phát sáng Neon (Neon Glow)`→`Neon Glow ASS`<br>`Karaoke chạy chữ`→`Karaoke ASS` | `Standard SRT` (`SUB_STYLE_TYPE`) | Tag ASS mine được:<br>`Standard SRT` = `{\fad(50,50)}`<br>`Bounce Pop ASS` = `{\fscx60\fscy60\t(0,150,\fscx100\fscy100)\fad(100,100)}`<br>`Zoom Pop ASS` = `{\fscx30\fscy30\t(0,120,\fscx125\fscy125)\t(120,220,\fscx100\fscy100)\fad(80,80)}`<br>`Tilt Bounce ASS` = `{\frz-5\fscx50\fscy50\t(0,120,\fscx115\fscy115\frz3)\t(120,240,\fscx100\fscy100\frz0)\fad(100,100)}`<br>`Neon Glow ASS` = `{\blur4\fad(120,120)}`<br>`Word Highlight ASS` dùng `{\c…}` + `\fscx115\fscy115` + `{\r}`<br>`Karaoke ASS` dùng `{\k…}` / `{\kf…}` |
| ` Căn lề dọc:` | Slider + nhãn `150 px` (#00f2fe) | 10 – 600 | `150` (`SUB_MARGIN_V`) | |
| `Độ dày viền:` | DoubleSpinBox | 0.0 – 15.0, bước 0.5 | `2.5` (`SUB_OUTLINE_WIDTH`) | |
| ` Bóng đổ:` | DoubleSpinBox | 0.0 – 15.0 | `0.0` (`SUB_SHADOW_VAL`) | |
| ` Chữ nghệ thuật:` | LabeledSelect (item render bằng `TextArtDelegate`, xem trước gradient) | **39 preset:** `None`, `Sunset Glow`, `Neon Pink`, `Neon Blue`, `Neon Green`, `Neon Orange`, `Neon Red`, `Neon Yellow`, `Neon Purple`, `Gold Luxury`, `Cyberpunk`, `Retro 3D`, `Bubble Gum`, `Art Orange`, `Purple Magic`, `Silver Metallic`, `Sweet Candy`, `Ice Frost`, `Fire Flame`, `Forest Nature`, `Double Outline`, `Comic Book`, `Graffiti`, `Pink Cyan`, `Yellow White`, `Red Orange`, `Luminous Green`, `Classic Retro`, `Soft Pink Candy`, `Tiffany Blue`, `Ocean Wave`, `Luxury Gold Foil`, `Cyberpunk Purple`, `Black Gold`, `White Blue Glow`, `Lemon Yellow`, `Vintage Brown`, `Pop Art Pink`, `Cotton Candy` | `None` (khoá code `sub_art_effect`, **không có trong config.json**) | Preset ≠ `None` ⇒ **không burn bằng ASS nữa** mà render PNG từng dòng (PicTex: `LinearGradient` + `Shadow` + `text_stroke`) rồi overlay. Log: `🎨 Đang vẽ Phụ đề PNG trực tiếp...` |
| `Chế độ xóa:` | LabeledSelect | `Làm Mờ` · `Crop/Zoom` · `Chèn Logo` | `Làm Mờ` (map `WM_MODE`; giá trị `Không xóa` = tắt) | Xem tab `Xóa watermark` bên dưới cho chi tiết. |
| ` Tỉ lệ khung hình:` | LabeledSelect | `16:9` · `9:16` | `16:9` | Chỉ đổi khung preview + chọn cặp toạ độ LAND/PORT. |
| `Ảnh đè:` | Field + `Chọn` (w=60) | đường dẫn PNG/JPG | rỗng (`LOGO_PATH`) | Placeholder `Đường dẫn file logo PNG/JPG...`. Chỉ hiện khi `Chế độ xóa: = Chèn Logo`. |
| `Tỉ lệ zoom:` | SpinBox + suffix ` %` (w=90) | 100 – 200 | `107` (khoá `watermark_zoom_ratio`, **không có trong config.json**; chia 100 ⇒ `1.07`) | Chỉ hiện khi `Crop/Zoom`. |
| W / H logo | 2 SpinBox | 10 – 2000 | `115` (`LOGO_W`) / `52` (`LOGO_H`) | |
| `Vị trí nhanh:` | 4 GhostButton | `↖ Trái Trên`→`tl` · `↗ Phải Trên`→`tr` · `↙ Trái Dưới`→`bl` · `↘ Phải Dưới`→`br` | — | Ghi vào `LOGO_X_LAND/Y_LAND` (1805/1028) hoặc `LOGO_X_PORT/Y_PORT` (965/1868) tuỳ tỉ lệ đang xem. |
| `📂 Thư Mục Lưu Kết Quả` | Field + `Chọn` | — | rỗng (`SUB_VIDEO_OUTPUT_PATH`; nguồn `SUB_VIDEO_SOURCE_PATH`) | Placeholder `Đường dẫn lưu thư mục video kết quả...` |
| `🚀 Bắt Đầu Xử Lý` | PrimaryButton (Accent, h=38) | — | — | |
| `🛑 Dừng` | StopButton (Danger, h=38) | — | — | Disabled khi rảnh. |
| preview tabs | inner tabs | `Trước Xử Lý (Xem Thử)` (`tab_before`) · `Sau Xử Lý (Kết Quả)` (`tab_after`) | `Trước Xử Lý (Xem Thử)` | Canvas kéo-thả chữ/logo, đồng bộ ngược về slider/spin. Text mẫu: `Đây là phụ đề mẫu!` |
| thanh thời gian | Slider + `00:00 / 00:00` | — | — | Click canvas = play/pause. |
| **`Nguồn phụ đề`** *(BỔ SUNG)* | Radio/Segmented | `Nhập file SRT` · `Từ lời dẫn đã tạo` · `Bóc băng từ video` | `Bóc băng từ video` | Bản exe chỉ có 1 nguồn (CapCut STT). Xem "Cạm bẫy". |
| **`Ngôn ngữ bóc băng`** *(BỔ SUNG)* | LabeledSelect | `vi-VN` · `en-US` · `zh-CN` | `vi-VN` | Tương ứng trường `language` của body STT. |
| **`Dịch phụ đề`** *(BỔ SUNG)* | Checkbox + LabeledSelect | bật/tắt + ngôn ngữ đích | tắt | Ánh xạ đúng 2 trường mine được trong body STT gốc: `use_translation` (bool) và `translation_language` (mã ngôn ngữ). Trong exe **không có control UI**, chúng chỉ là field của request `POST /lv/v1/common_task/new` (`stt_new_body` = `audio_vid, audio_md5, duration_ms, language, translation_language, use_translation, preserve_auto_caption_segments`). |

**Luồng:**
1. `Chọn File Video` / `Chọn Thư Mục` ⇒ danh sách vào `🎥 Video Gốc`. Click 1 item ⇒ `POST /api/postprod/preview-frame` *(MỚI)* lấy frame đại diện, canvas `Trước Xử Lý (Xem Thử)` vẽ khung + phụ đề mẫu + ROI logo.
2. Chỉnh style — mọi thay đổi gọi `trigger_realtime_preview_sync()` (debounce ~150 ms), **chỉ vẽ lại phía client**, không gọi BE.
3. Bấm `🚀 Bắt Đầu Xử Lý`. Với từng video, theo `Nguồn phụ đề`:
   * `Nhập file SRT` → dùng file người dùng chọn.
   * `Từ lời dẫn đã tạo` → lấy `.srt` sinh kèm khi chạy `POST /api/postprod/narrate` (cần BE trả thêm `srtPath`).
   * `Bóc băng từ video` → **`POST /api/postprod/stt`** *(MỚI)* `{video, language, useTranslation, translationLanguage}`; BE tách audio bằng ffmpeg rồi gọi Gemini audio STT với key của user, trả `{srtPath, txtPath, segments[]}`. Log: `⏳ Đang xử lý bóc băng giọng nói (...)` → `✅ Bóc băng thành công! Đang thực hiện...`
4. Burn:
   * `Chữ nghệ thuật: = None` ⇒ **`POST /api/postprod/subtitles-styled`** *(MỚI)* `{video, srt, output, font, size, primaryColor, outlineColor, inactiveColor, outlineWidth, shadow, marginV, styleType, playResX, playResY}`. BE dựng file `.ass` (header mine nguyên: `[Script Info] / Title: Auto Subtitles / ScriptType: v4.00+ / PlayResX / PlayResY / Timer: 100.0000`, dòng `Style: Default,<font>,<size>,<primary>,&H00000000,<outline>,&H00000000,-1,0,0,0,100,100,0,0,1,<outlineW>,<shadow>,8,20,20,<marginV>,1`) rồi `ffmpeg -vf "ass='<path>':fontsdir='<CapCut_Fonts>'"`. Log `🎬 Đang chạy FFmpeg để burn phụ đề ASS...`
   * `Chữ nghệ thuật ≠ None` ⇒ đường PicTex: render PNG từng dòng + `-filter_complex_script` + `-map [outv] -map 0:a? -c:a copy`.
   *(Endpoint `POST /api/postprod/subtitles` hiện có KHÔNG đủ: thiếu `styleType`, `inactiveColor`, `artEffect`, và `outlineWidth` của nó là `int` trong khi tool cần `float 2.5`.)*
5. Nếu `Xóa Logo` bật ⇒ chạy tiếp bước watermark (xem tab kế) trên chính file vừa burn.
6. File kết quả đẩy vào `✅ Video Kết Quả`; tiến trình đẩy `progress_bar` + log; kết thúc log `🎉 Tạo phụ đề thành công! Video: <path>` / `🎉 Burn phụ đề thành công!`

**Kết quả:** Tab `✅ Video Kết Quả` liệt kê file đã xử lý (click ⇒ canvas `Sau Xử Lý (Kết Quả)` phát bản đã burn). Log dạng `[%H:%M:%S] …` trong `📝 Tiến Trình & Nhật Ký`, progress 0–100.

**Cạm bẫy:**
- **CapCut STT đã bị gỡ.** Toàn bộ đường `capcut_stt`, `upload_audio_file`, `/lv/v1/common_task/new|query`, `make_sign_header` phụ thuộc API riêng của CapCut. Thay bằng Gemini audio STT ⇒ **timing từ đây kém chính xác hơn CapCut ở mức từ (word-level)**. Hệ quả trực tiếp: 2 kiểu `Tô sáng từ (Word Highlight)` và `Karaoke chạy chữ` **cần timestamp theo từ**; nếu STT chỉ trả theo câu, hai kiểu này sẽ highlight cả câu một lúc. Phải hoặc (a) yêu cầu provider trả word-level timing, hoặc (b) disable 2 kiểu đó khi nguồn là STT câu-level và hiện tooltip lý do.
- `Chữ nghệ thuật ≠ None` **vô hiệu hoá hiệu ứng của `Kiểu chữ:`** (đường PicTex vẽ PNG tĩnh, không có tag `\t(...)`). Hai control này xung đột — phải khoá chéo hoặc cảnh báo, nếu không user chọn `Karaoke chạy chữ` + `Neon Pink` rồi nhận phụ đề đứng yên.
- `Màu mờ:` chỉ có tác dụng với `Word Highlight ASS` / `Karaoke ASS`; ở 5 kiểu còn lại nó bị bỏ qua hoàn toàn — disable để tránh hiểu nhầm.
- `Căn lề dọc:` là **MarginV của ASS**, tính theo `PlayResY`. Nếu BE không set `PlayResX/PlayResY` bằng đúng kích thước video thì 150 px trên video 1080p và trên video 4K ra vị trí hoàn toàn khác nhau. Bắt buộc probe kích thước trước khi dựng `.ass`.
- Font: `ass` filter chỉ thấy font qua `fontsdir`. Nếu FE gửi tên họ font mà file không nằm trong `CapCut_Fonts/`, ffmpeg **im lặng fallback sang font mặc định** — kết quả sai mà không có lỗi. Chỉ cho chọn tên có trong `GET /api/postprod/status → .fonts`.
- `Độ dày viền` là `float` (2.5); endpoint `/api/postprod/subtitles` hiện khai báo `outlineWidth: int` ⇒ 2.5 bị ép về 2. Phải dùng endpoint mới.
- Tab này **không** có Card A row2 (`Tỷ lệ / Thời lượng / Model`) — không có gọi Flow, không trừ credit. Render row2 ở đây là sai contract.
- Preview canvas kéo-thả chỉ đúng khi tỉ lệ canvas khớp tỉ lệ video thật. Video 1:1 hoặc 4:5 (có trong hệ thống: `4:5`) không có mục trong ` Tỉ lệ khung hình:` ⇒ vị trí logo/sub đặt bằng mắt sẽ lệch.

---

### Xóa watermark (tab_remove_watermark) — Xóa logo & watermark khỏi video

**Mục đích:** Xoá/che logo (VEO3, Gemini, hoặc watermark bất kỳ) trên loạt video bằng ffmpeg `delogo` / crop-zoom / overlay logo riêng, hoặc bằng engine AI MI-GAN cho trường hợp nền phức tạp.

**Bố cục:** (splitter ngang; objectName `RemoveWatermarkTab`)
```
┌ ToolHeader ─────────────────────────────────────────────────────────────┐
│ [📝] Xóa watermark          Xóa logo & watermark khỏi video              │
└─────────────────────────────────────────────────────────────────────────┘
┌ TRÁI ────────────────────────────┐ ┌ PHẢI ─ 🔍 Xem Thử & Căn Chỉnh Vị Trí┐
│ 📂 Nguồn Video (#10b981)         │ │  (#a855f7)                         │
│ [Chọn File Video][Chọn Thư Mục]  │ │ Vị trí nhanh:                      │
│ [Xóa Hết]                        │ │ [↖ Trên trái][↗ Trên phải]         │
│ ┌[🎥 Video Gốc][✅ Video Đã Xóa] │ │ [↙ Dưới trái][↘ Dưới phải]  [16:9▾]│
│ │  list_videos / list_processed  │ │ ┌ LogoPreviewCanvas ─────────────┐ │
│ └────────────────────────────────┘ │ │  1920×1080, ROI kéo/thu phóng  │ │
│ ⚙️ Cấu Hình Xử Lý (#00f2fe)      │ │  nhãn "LÀM MỜ" / "LOGO" /       │ │
│  Chế độ xóa: [▾]                 │ │  "ZOOM: 107%"                   │ │
│  [W 115] [H 52]                  │ │  (chưa nạp: "Chưa nạp video mẫu │ │
│  Ảnh logo/watermark: [……][Chọn]  │ │   (Click video trong danh sách  │ │
│  Thư mục lưu: [……………][Chọn]      │ │    để xem thử)")                │ │
│ 📝 Nhật Ký Hoạt Động (#cbd5e1)   │ │ └────────────────────────────────┘ │
│  <log> [██████░░░░]              │ │ Tỉ lệ zoom video (%): [107 %]     │
│  [🚀 Xóa logo VEO3] [🛑 Dừng]    │ │ [────▮─────] 00:00 / 00:00        │
└──────────────────────────────────┘ └───────────────────────────────────┘
```

**Điều khiển:**

| Nhãn | Loại | Giá trị / tập chọn | Mặc định (khoá config) | Ghi chú |
|---|---|---|---|---|
| `Chọn File Video` | FilePickerButton (Warning, h=30) | `Video Files (*.mp4 *.mov *.mkv *.webm *.avi);;All Files (*)`, tiêu đề `Chọn các file video cần xử lý` | — | Log `Đã thêm <n> file video vào danh sách.` |
| `Chọn Thư Mục` | FilePickerButton | quét `*.mp4 *.mov *.mkv *.webm *.avi`, tiêu đề `Chọn thư mục chứa video` | — | Log `Đã quét thư mục và thêm <n> video.` |
| `Xóa Hết` | GhostButton (DangerSoft) | — | — | Log `Đã làm sạch danh sách video.` |
| `🎥 Video Gốc` | inner tab (`tab_original`) | — | active | Tooltip mine nguyên: `Click chọn video gốc để xem thử căn chỉnh logo` |
| `✅ Video Đã Xóa` | inner tab (`tab_processed`) | — | — | Tooltip: `Click chọn video đã xử lý để xem lại kết quả` |
| `Chế độ xóa:` | LabeledSelect (`SettingsCombo`) | `Làm Mờ` → ffmpeg `delogo` · `Crop/Zoom` → `crop` · `Chèn Logo` → `overlay` | `Làm Mờ` (khoá `watermark_mode`; khoá gốc trong config.json là `WM_MODE` mặc định `Không xóa`) | Bản đồ mine nguyên trong `remove_watermark.py`: `{"Làm Mờ":"delogo", "Crop":"crop", "Chèn Logo":"overlay"}` — **lưu ý nhãn combo là `Crop/Zoom` nhưng khoá map là `Crop`**. |
| W logo | SpinBox | 10 – 2000 | `115` (`LOGO_W`) | = `w` của `delogo` / bề rộng overlay. |
| H logo | SpinBox | 10 – 2000 | `52` (`LOGO_H`) | = `h` của `delogo`. |
| `Ảnh logo/watermark:` | Field + `Chọn` (w=60) | PNG/JPG | rỗng (`LOGO_PATH`) | Placeholder `Chọn đường dẫn hình ảnh PNG/JPG...`. Bắt buộc cho `Chèn Logo`, nếu thiếu ⇒ lỗi `Chèn Logo nhưng chưa chọn file`. |
| `Thư mục lưu:` | Field + `Chọn` | — | rỗng (`video_output_dir` / `VIDEO_OUTPUT_DIR` = `D:/TOOL_VIDEO/VIDEO_OUT`) | Placeholder `Mặc định lưu cùng thư mục video gốc...` |
| `Vị trí nhanh:` | 4 GhostButton | `↖ Trên trái`→`tl` · `↗ Trên phải`→`tr` · `↙ Dưới trái`→`bl` · `↘ Dưới phải`→`br` | — | ⚠️ Nhãn **khác** với tab Tạo phụ đề (ở đó là `↖ Trái Trên`…). Giữ nguyên cả hai bộ, đừng thống nhất. |
| tỉ lệ preview | LabeledSelect (w=85) | `16:9`→`16:9` · `9:16`→`9:16` | `16:9` (khoá `watermark_preview_aspect`) | `16:9` dùng `LOGO_X_LAND=1805` / `LOGO_Y_LAND=1028`; `9:16` dùng `LOGO_X_PORT=965` / `LOGO_Y_PORT=1868`. |
| canvas | LogoPreviewCanvas, kéo + resize | video 1920×1080 (hoặc 1080×1920), ROI mặc định `x=1805 y=1028 w=115 h=52` | như trên | Nền `#090d16`, khung `#1f2937`, ROI nét đứt `#00f2fe`. Nhãn trong ROI theo mode: `LÀM MỜ` (#10b981), `LOGO` (#f97316), `ZOOM: <n>%` (#3b82f6). Trống ⇒ `Chưa nạp video mẫu\n(Click video trong danh sách để xem thử)`. Click canvas = play/pause; kéo ô góc phải-dưới để đổi kích thước. |
| `Tỉ lệ zoom video (%):` | SpinBox + suffix `%` (w=80) | 100 – 200 | `107` (`watermark_zoom_ratio`, chia 100 ⇒ `1.07`) | Chỉ hiện khi `Crop/Zoom`. |
| thanh thời gian | Slider + `00:00 / 00:00` | — | — | |
| `🚀 Xóa logo VEO3` | PrimaryButton (Accent, h=38) | — | — | |
| `🛑 Dừng` | StopButton (Danger, h=38) | — | — | Disabled khi rảnh. |
| **`Engine`** *(BỔ SUNG, có trong backend exe nhưng chưa lên tab này)* | Segmented | `ffmpeg (nhanh)` · `AI MI-GAN (chậm, GPU→CPU)` | `ffmpeg (nhanh)` | Nhánh AI = `third_party/gemini_video_watermark/GeminiWatermarkTool-Video.exe` + `migan_pipeline_v2.onnx` (27 MB, CPU fallback). Trong exe nó được gọi từ node hậu kỳ (`auto_remove_gemini_video_watermark`), không từ tab. |

**Luồng:**
1. Nạp video → click 1 item ⇒ `POST /api/postprod/preview-frame` *(MỚI)* `{video, atSeconds}` trả 1 ảnh JPEG; canvas vẽ frame + ROI.
2. Kéo/thu ROI hoặc bấm `Vị trí nhanh:` ⇒ cập nhật `logo_x/logo_y/logo_w/logo_h` (canvas ↔ spinbox hai chiều, `on_canvas_coords_changed` / `on_spin_logo_dims_changed`).
3. Bấm `🚀 Xóa logo VEO3`. Với từng video, theo `Chế độ xóa:`:
   * **`Làm Mờ`** → **`POST /api/postprod/delogo`** *(MỚI)* `{video, output, x, y, w, h}`. BE: `ffmpeg -y -i <src> -vf "delogo=x=<x>:y=<y>:w=<w>:h=<h>" -c:v libx264 -crf 18 -preset fast -c:a copy <dst>`.
   * **`Crop/Zoom`** → **`POST /api/postprod/crop-zoom`** *(MỚI)* `{video, output, zoomRatio}`. BE: `-vf "scale=<W*z>:<H*z>,crop=<W>:<H>:<x>:<y>,scale=<W>:<H>" -c:v libx264 -crf 18 -preset fast -c:a copy` (chuỗi `scale=…,crop=…,scale=…` mine nguyên).
   * **`Chèn Logo`** → tái dùng **`POST /api/postprod/logo`** (đã có) `{video, logo, output, x, y, width, height}`. BE gốc dùng `-filter_complex "[0:v]scale=<W>:<H>[bg];[1:v]scale=<lw>:<lh>[logo];[bg][logo]overlay=<x>:<y>"`.
   * **`AI MI-GAN`** → **`POST /api/postprod/watermark-ai`** *(MỚI)* `{video, output, mark, variant, sigma}`. BE chia video thành chunk an toàn bằng ffmpeg (`_split_video`, log `Đã chia thành <n> đoạn an toàn (tối đa <k> frame/đoạn).`) rồi với mỗi chunk gọi:
     `GeminiWatermarkTool-Video.exe <in> <out> --verbose --veo --mark diamond --variant auto|ai --sigma 20`
     Sau đó nối lại (`concat -safe 0`, file `clean_chunks.txt` / `clean_video_only.mp4`) và mux lại audio gốc (`-map 1:a:0? -c copy`). Frame runtime bỏ qua được vá bằng MI-GAN CPU (`migan_pipeline_v2.onnx`).
4. Poll tiến trình; log realtime `[%H:%M:%S] …`, ví dụ `🧹 Bắt đầu xóa logo Gemini: ưu tiên GPU...`, `Đang xóa logo đoạn <i>/<n> bằng GPU...`, `CPU fallback`, `MI-GAN vá <n> frame.`, `🎉 Che mặt thành công! File: …`.
5. File xong đẩy sang `✅ Video Đã Xóa`; cấu hình được `save_settings_to_config` (khoá `video_output_dir`, `logo_path`, `logo_x_land`, `logo_y_land`, `watermark_zoom_ratio`, `watermark_mode`, `watermark_preview_aspect`).

**Kết quả:** Danh sách `✅ Video Đã Xóa` + preview lại được ngay trong canvas. `📝 Nhật Ký Hoạt Động` + `progress_bar`. Kết quả AI trả thêm `{changed, elapsedMs, usedGpu, usedCpu, chunkCount, patchedFrameCount, message}` để hiển thị dòng tổng kết kiểu `Đã xóa logo bằng GPU dò vùng + CPU phục hồi thích ứng theo frame; <n> đoạn dò, làm sạch <m> frame…`.

**Cạm bẫy:**
- **Cờ CLI của `GeminiWatermarkTool-Video.exe` KHÔNG phải `--input --output --model --gpu --chunk` như brief ghi.** Mine trực tiếp từ binary: bộ cờ thật là `--input --output --region --mark --variant --veo --veo-alpha --sigma --radius --strength --threshold --grow --backend --force --force-small --force-large --fallback-region --snap --snap-min-size --snap-max-size --snap-threshold --denoise --tune --psnr --ssim --legacy --no-legacy --v3 --prop --towards --remove --banner --no-banner --quiet --verbose --version --help`. Lệnh mà tool gốc thực sự dựng là `<exe> <in> <out> --verbose --veo --mark diamond --variant auto|ai --sigma 20`. **Không có `--chunk`** — việc chia đoạn do phía gọi làm bằng ffmpeg. Nếu build theo brief thì process chết ngay với exit code khác 0.
- **`WM_MODE` trong `config.json` mặc định là `Không xóa`** trong khi combo UI chỉ có 3 mục (`Làm Mờ` / `Crop/Zoom` / `Chèn Logo`). Nếu load thẳng giá trị config vào combo thì `findText` trả −1 và combo rơi về index 0 = `Làm Mờ`, tức **bật xoá logo cho người vốn đã tắt nó**. Phải xử lý `Không xóa` như trạng thái "tắt tính năng" riêng.
- Nhãn combo là `Crop/Zoom` nhưng bảng ánh xạ backend dùng khoá `Crop`. Gửi thẳng `currentText` sang BE ⇒ `KeyError` → im lặng bỏ qua xử lý và trả về file gốc không đổi.
- `Crop/Zoom` **phóng to toàn khung** để đẩy watermark ra ngoài mép ⇒ mất 7 % rìa hình ở mọi cạnh và giảm độ nét. Với video dọc đã crop sẵn thì thường cắt mất mặt/chữ. Hiện preview so sánh trước/sau trước khi chạy hàng loạt.
- Toạ độ mặc định `(1805, 1028)` / `(965, 1868)` là **toạ độ tuyệt đối cho khung 1920×1080 / 1080×1920**. Video 720p hoặc 4K sẽ che nhầm chỗ. BE bắt buộc probe `WxH` và scale toạ độ theo tỉ lệ (`ref_w`/`ref_h` có sẵn trong chữ ký `post_process_video`), đừng dùng số thô.
- `delogo` yêu cầu ROI **nằm trọn trong khung và cách mép ≥ 1 px**; ROI chạm mép (rất hay gặp với logo góc dưới-phải) làm ffmpeg fail. Clamp `x+w ≤ W-1`, `y+h ≤ H-1`.
- Nhánh AI có thể mất tiếng: pipeline gốc kiểm tra `Kiểm tra đầu ra thất bại: video bị mất âm thanh.` và `… <n> frame nguồn nhưng <m> frame đầu ra.` — giữ nguyên 2 kiểm tra này, nếu bỏ thì sản phẩm câm mà không ai biết.
- Tab này **không** gọi Flow, không có project, không có `Tỷ lệ / Thời lượng / Model` — không render Card A row2.
- `Ảnh logo/watermark:` ở chế độ `Chèn Logo` chỉ được nhận **logo của chính người dùng**. Không cung cấp asset logo bên thứ ba trong app.


# IMAGE TOOLS

## Nhóm ẢNH & UPSCALE — build contract

> Mọi nhãn tiếng Việt dưới đây là **byte-exact** trích từ `RUN_VEO_3_ULTRA_PROMAX.exe` (Nuitka const-pool). Khoảng trắng đôi sau emoji (vd `✏️  Nhập prompt`) là **cố ý** — giữ nguyên. Mặc định đều dẫn khoá `data_general/config.json`, `data_general/affiliate_state.json`, `data_general/upscale_config.json`.

### Token bổ sung mined được (ngoài bộ đã chốt)
`emerald #10b981` (nhãn khối tạo ảnh) · `violet #8b5cf6` (nhãn topbar Affiliate) · `lavender #c4b5fd` (Tư thế) · `cyan #67e8f9` (Bối cảnh) · `orange #fb923c / #f97316` (khối video Affiliate) · `rose #f43f5e` (checkbox chọn sản phẩm) · `amber #fbbf24` (tuỳ chỉnh prompt) · `neon #00f2fe` (UP SCALE IMAGE 4K) · `#f59e0b` (KẾT QUẢ AI) · `#2dd4bf` (dòng trạng thái upscale) · `#020617` (nền drop-zone) · chiều cao control topbar **38px** (`_top_common_ctrl_h`).

### Shared TopBar ở chế độ ẢNH (khác chế độ video)
Ba tab tạo ảnh dùng chung dải điều khiển trên cùng, nhưng **nhãn đổi theo chế độ**:

| Video mode (đã chốt) | Image mode (mined) | Widget |
|---|---|---|
| `📏 Tỷ lệ` | `📐 Tỉ lệ` | `combo_aspect` |
| `⏱ Thời lượng` | *(ẩn)* | `combo_duration` |
| `🎬 Model` | `🎨 Model` | `combo_image_model` |
| — | `🖼 Quality` | `combo_image_quality` |
| `📂 Thư mục lưu Video` | `📂 Lưu ảnh` | `out_dir` (`CfgInput`) |
| `TẠO VIDEO` | `TẠO ẢNH` | `btn_start` |

CTA có 3 trạng thái văn bản: `TẠO ẢNH` → `THÊM {n} VÀO HÀNG CHỜ` (khi platform đang bận) → `DỪNG ĐANG CHẠY` (khi chính nó đang chạy). Nút đỏ riêng `DỪNG` (`objectName=DangerSoft`) gọi stop-all.
`Chọn dự án` + `combo_project` (`CfgCombo`) có 2 hàng sentinel: `➕ Tạo Dự Án Mới` (`__ACTION_NEW__`) và `🗑 Xóa Dự Án` (`__ACTION_DELETE__`); tên dự án thật hiển thị với icon `📁`.
Dialog tạo: tiêu đề `Tạo Dự Án Mới`, nhãn `Nhập tên dự án mới`, hint `Dữ liệu sẽ được lưu theo cấu trúc: thư mục gốc / tên dự án / image, video, thumbnail`, placeholder `Ví dụ: QuangCao_TraSua_03`, nút `Xác nhận` / `Hủy`.
Dialog xoá: `Xóa dự án` / `Chọn dự án muốn xóa` / `Tích chọn một hoặc nhiều dự án trong danh sách bên dưới.` / cảnh báo `⚠ Xóa sẽ xóa hết video và ảnh đã tạo, không thể khôi phục.` / kết quả `Đã xóa {n} dự án.`

### JobList — sự thật về bản gốc
Bản gốc **không** đặt JobList trong tab tạo ảnh. Bảng job nằm ở mục sidebar riêng `Activity Log / Nhật ký hoạt động` (`StatusPanel`), 7 cột: `Chọn | STT | Video | Trạng thái | Mode | Prompt | Link Ảnh`; toolbar `🔗 Nối video`, `🔄 Tạo lại`, `⚠️ Tạo lại video lỗi`, `🔄 Tạo lại Hủy`, `✂️ Cắt ảnh cuối`, `🗑️ Xóa kết quả`, `⏸️ DỪNG LẠI`, `📁 XEM KẾT QUẢ`. Popup sau khi dispatch: `Khởi động thành công` / `Đã khởi động chế độ tạo ảnh từ {n} prompt` / `Bạn có thể chuyển sang tab Nhật ký để xem tiến trình tạo video/ảnh`.
**Quyết định cho bản replica:** vẫn render `JobList` cuối mỗi tab tạo ảnh, lọc `getActivityList({type})` theo mode của tab, và giữ Activity Log là view đầy đủ. Mode label byte-exact: `VEO3 - Tạo ảnh từ prompt`, `VEO3 - Tạo ảnh từ ảnh tham chiếu`.

---

### Text to Image — Tạo ảnh từ prompt

**Mục đích:** nhập hàng loạt prompt (mỗi dòng = 1 ảnh), bắn `gen_image` theo lô, kết quả rơi vào Activity Log.

**Bố cục:**
```
┌ TopBar ─────────────────────────────────────────────────────────────────┐
│ Chọn dự án[▼] 📁 Xem kết quả | 📐 Tỉ lệ[▼] 🎨 Model[▼] 🖼 Quality[▼]     │
│ 📂 Lưu ảnh[………]                              [ TẠO ẢNH ] [ DỪNG ]       │
└─────────────────────────────────────────────────────────────────────────┘
┌ ToolHeader ─────────────────────────────────────────────────────────────┐
│ 🎨  Text to Image        Tạo ảnh từ prompt                              │
└─────────────────────────────────────────────────────────────────────────┘
┌ Card A · SectionBoard · h=60 ───────────────────────────────────────────┐
│  Model Tạo ảnh [🍌 Nano Banana pro ▼]   Chất lượng ảnh [1K ▼]           │
└─────────────────────────────────────────────────────────────────────────┘
┌ Card B · SubTabContainer ───────────────────────────────────────────────┐
│ ✏️  Nhập prompt (mỗi dòng là 1 prompt)                                   │
│ 💡 Mỗi dòng = 1 ảnh. Dòng trống sẽ bị bỏ qua.                            │
│ ┌── gutter ─┬──────────────────────────────────────────────────────────┐│
│ │ 001 │ a cinematic portrait of …                                      ││
│ │ 002 │ …                                                              ││
│ └───────────┴──────────────────────────────────────────────────────────┘│
│ [📄 Import prompt.txt]  [📄 batch1.txt ×] [📄 batch2.txt ×]             │
└─────────────────────────────────────────────────────────────────────────┘
┌ JobList ────────────────────────────────────────────────────────────────┐
```

**Điều khiển:**

| Nhãn | Loại | Giá trị / tập chọn | Mặc định (khoá config) | Ghi chú |
|---|---|---|---|---|
| `Chọn dự án` | select (purple) | tên dự án + `➕ Tạo Dự Án Mới` + `🗑 Xóa Dự Án` | `CURRENT_PROJECT` = `"default_project"`; danh sách `PROJECTS` | map sang `ensureBoardProject/getBoardProject`; `projectId` phải hợp lệ nếu không worker trả `invalid_project_id` |
| `📁 Xem kết quả` | GhostButton | — | — | mở `VIDEO_OUTPUT_DIR/<project>/image`; web không mở được Explorer → thay bằng `ResultsDrawer` |
| `📐 Tỉ lệ` | select (purple) | `Dọc 9:16`→`IMAGE_ASPECT_RATIO_PORTRAIT`, `Ngang 16:9`→`IMAGE_ASPECT_RATIO_LANDSCAPE` | `VIDEO_ASPECT_RATIO` = `"9:16"` → Dọc | API còn nhận `IMAGE_ASPECT_RATIO_SQUARE` nhưng combo gốc **không** có; đừng tự thêm |
| `🎨 Model` (topbar) | select (purple) | đồng bộ 1-1 với `Model Tạo ảnh` | `CREATE_IMAGE_MODEL` | hai combo cùng nguồn `MODEL_OPTIONS`, thay đổi ở đâu cũng ghi config |
| `🖼 Quality` (topbar) | select (purple) | đồng bộ với `Chất lượng ảnh` | `CREATE_IMAGE_QUALITY` | |
| `Model Tạo ảnh` | LabeledSelect (`CreateImageCombo`, nhãn `#10b981` w700 13px) | `🍌 Nano Banana pro`→`Nano Banana pro`; `🍌 Nano Banana 2`→`Nano Banana 2`; `🍌 Nano Banana LITE`→`Nano Banana LITE` | `CREATE_IMAGE_MODEL` = `"Nano Banana 2"` | key nội bộ: pro→`GEM_PIX_2`, 2→`NARWHAL`, LITE→`HARBOR_SEAL`, (`Nano Banana`→`GEM_PIX` chỉ còn trong config-loader, không lên UI) |
| `Chất lượng ảnh` | LabeledSelect | `1K`→`1k`; `2K`→`2k`; `4K`→`4k`; `🌟 4K Siêu nét GPU`→`4k_x4plus` | `CREATE_IMAGE_QUALITY` = `"1k"` | **không** phải tham số API — là bước hậu-xử-lý cục bộ, xem Cạm bẫy |
| `📂 Lưu ảnh` | FilePickerButton (`CfgInput`) | thư mục | `VIDEO_OUTPUT_DIR` = `"D:/TOOL_VIDEO/VIDEO_OUT"` | ảnh lưu `<dir>/<project>/image` |
| Ô prompt | PromptEditor (multiline + gutter STT) | 1 dòng = 1 prompt; placeholder `Nhập một prompt cho mỗi dòng...` | rỗng | gutter vẽ `prompt_id` ổn định theo dòng (`_rebuild_id_map`), nền `#1a1c23`, vạch `#2d2f39`, chữ `#9ca3af` |
| `📄 Import prompt.txt` | GhostButton | file `.txt` | — | dialog `Chọn file prompt.txt`, filter `Text Files (*.txt);;All Files (*.*)`, đọc UTF-8, **append** vào cuối; mỗi file thành chip `📄 {tên}` nền `rgba(16,185,129,0.12)` chữ `#10b981`; bấm `×` gỡ đúng các dòng file đó đã thêm |
| `TẠO ẢNH` | PrimaryButton (`#2563eb`) | — | — | 3 trạng thái văn bản như trên |
| `DỪNG` | StopButton (`#dc2626`) | — | — | xác nhận `Xác nhận dừng` / `Bạn có chắc muốn DỪNG TẤT CẢ các quy trình đang chạy?\nDữ liệu đang xử lý sẽ được giữ lại nhưng quy trình sẽ bị ngắt.` |
| *(ẩn, từ config)* `OUTPUT_COUNT` | — | 1–4 | `OUTPUT_COUNT` = `1` | số biến thể / prompt → `variant_count`; backend chặn cứng `MAX_VARIANT_COUNT = 4` |
| *(ẩn, từ config)* `SEED_MODE` / `SEED_VALUE` | — | `Random` \| `Fixed` / int | `SEED_MODE` = `"Random"`, `SEED_VALUE` = `9797` | **backend hiện không nhận seed** — xem Cạm bẫy |
| *(ẩn, từ config)* `MULTI_VIDEO` | — | int | `MULTI_VIDEO` = `4` | dùng làm `max_in_flight` cho lô ảnh, không phải số ảnh |

**Luồng:**
1. User gõ N dòng prompt (hoặc `📄 Import prompt.txt`). Dòng trắng bị loại.
2. Bấm `TẠO ẢNH`. Nếu list rỗng → modal `Không có prompt` / `Hãy nhập ít nhất một prompt ở tab Tạo Ảnh Từ Prompt.` và dừng.
3. `ensureBoardProject(boardId)` → lấy `projectId`. Nếu dự án chưa tồn tại, `createRequest({type:'create_project', params:{name}})` trước.
4. Nếu đang có job VEO3 chạy: hỏi `Xác nhận thêm hàng chờ` / `Bạn muốn thêm {n} prompt vào hàng chờ không ?` (`Có`/`Không`), CTA đọc `THÊM {n} VÀO HÀNG CHỜ`.
5. Với mỗi prompt → `createRequest({ type:'gen_image', params:{ prompt, project_id, aspect_ratio, image_model, variant_count: OUTPUT_COUNT, paygate_tier } })`. **Không** truyền `node_id`. Client giới hạn số request in-flight = `MULTI_VIDEO` (4), phần còn lại nằm hàng chờ Zustand.
6. Poll `getRequest(id)` cho tới `done|failed|timeout|canceled`. Trạng thái nội bộ bản gốc: `PENDING → ACTIVE → (UPSCALING) → SUCCESSFUL|FAILED|TIMEOUT`.
7. `done` → response có `media_ids` + `media_entries`. Hiển thị bằng `mediaUrl(mediaId)`; poll `getMediaStatus(mediaId)` nếu `available=false`.
8. Nếu `Chất lượng ảnh ≠ 1k` → chạy bước upscale cục bộ (xem Cạm bẫy #2): cần endpoint mới `POST /api/media/{media_id}/upscale`. Bản gốc đổi tên file `…_1k.jpg` → `…_{quality}.jpg` và log `✅ Đã đổi tên → {tên}`.
9. Toast `Khởi động thành công` / `Đã khởi động chế độ tạo ảnh từ {n} prompt`.

**Kết quả:** thumbnail grid trong `JobList` (một `JobCard`/prompt, N ô ảnh nếu `OUTPUT_COUNT>1`); click mở `ResultsDrawer` xem full + tải. Log dòng cấu hình đầu run, copy y nguyên: `⚙️  Cấu hình: output_count={n}, timeout_ảnh={t}s, token_timeout={t}s, wait_between={w}s, max_in_flight={m}, quality={q}` (hai dấu cách sau ⚙️).

**Cạm bẫy:**
1. **`SEED_MODE`/`SEED_VALUE` không thể tôn trọng được.** `flow_sdk.gen_image()` tự sinh seed `(ts + i*9973) % 1_000_000` và **không có tham số `seed`**. Muốn `SEED_MODE="Fixed"` hoạt động phải thêm `seed` vào signature + vào `_handle_gen_image`. Cho tới lúc đó: **ẩn hoàn toàn** control seed, đừng render một ô mà backend bỏ qua.
2. **`Chất lượng ảnh` 2K/4K/4K-GPU không phải tham số API.** Trong exe, `maybe_upscale_image_bytes()` chạy **100% cục bộ**: `4k_x4plus` → `realesrgan-x4plus` (log `🌟 Upscale AI x4Plus GPU (Người thật)...`), còn `2k`/`4k` → `upscayl-lite-4x` (log `🧩 Đang Upscale AI ({q})...`), luôn `-s 4`. Ba tuỳ chọn 2K/4K/4K-GPU **chỉ khác nhau ở tên model + nhãn file**, không khác kích thước đầu ra — cả ba đều là ×4. Module `API_upscale_image` (Flow online `UPSAMPLE_IMAGE_RESOLUTION_2K/4K`) vẫn nằm trong binary nhưng **không còn được gọi** ở 4.6.1.3. Trong replica: giữ 4 mục nhưng nói rõ 2K/4K/4K-GPU chỉ đổi model, hoặc rút gọn còn `1K` + `4K (Lite)` + `🌟 4K Siêu nét GPU`.
3. **Model LITE chưa map ở backend.** `flow_sdk.IMAGE_MODELS` chỉ có `NANO_BANANA_PRO→GEM_PIX_2` và `NANO_BANANA_2→NARWHAL`; `resolve_image_model()` **âm thầm rơi về Pro** khi gặp key lạ. Chọn `🍌 Nano Banana LITE` hôm nay sẽ chạy Pro và **tính tiền như Pro**. Phải thêm `NANO_BANANA_LITE→HARBOR_SEAL` (và `NANO_BANANA→GEM_PIX`) trước khi bật mục LITE.
4. **Gating VIP/model của combo Chất lượng.** Bản gốc: 2k/4k/4k_x4plus chỉ hợp lệ với `nano banana 2` / `nano banana pro` (và `Imagen 4` trong code), nếu không → `Không hỗ trợ chất lượng` / `Chất lượng ảnh 2k, 4k chỉ hỗ trợ ở model 🍌 Nano Banana 2 và 🍌 Nano Banana pro.` rồi ép về `1k`. Thêm gate license `Thông báo bản quyền` / `Chất lượng ảnh 2k, 4k chỉ áp dụng cho tài khoản mua tool vĩnh viễn (VIP).` và lúc khởi động `⚠️ Tài khoản thường chỉ dùng chất lượng 1K. Đã tự động đưa về 1K.` — **bản local đã gỡ license server nên bỏ toàn bộ gate VIP**, chỉ giữ gate theo model.
5. **`OUTPUT_COUNT` bị kẹp im lặng ở 4.** `n = max(1, min(variant_count, 4))`. Nếu user set `OUTPUT_COUNT: 8` trong config.json, UI phải kẹp và hiển thị 4, đừng hứa 8.
6. `MULTI_VIDEO=4` là **độ song song**, không phải số ảnh. Mỗi request `gen_image` với `variant_count=k` tính **k lượt credit**; 10 prompt × 4 biến thể = 40 lượt trong một cú bấm.
7. Chữ hoa/thường của nhãn model **khác nhau giữa hai tab**: tab này là `🍌 Nano Banana pro` (p thường), tab Affiliate là `🍌 Nano Banana Pro` (P hoa). Giữ nguyên cả hai, đừng "sửa cho nhất quán".

---

### Image to Image — Tạo ảnh từ ảnh tham chiếu

**Mục đích:** cùng một danh sách prompt, nhưng **toàn bộ** ảnh tham chiếu (tối đa 15, mỗi ảnh có tên) được nhồi vào **mọi** prompt như `IMAGE_INPUT_TYPE_REFERENCE`.

**Bố cục:**
```
┌ TopBar (giống Text to Image, nhãn 📐 Tỉ lệ / 🎨 Model / 🖼 Quality) ─────┐
┌ ToolHeader ── 📸  Image to Image   Tạo ảnh từ ảnh tham chiếu ───────────┐
┌ Card A · SectionBoard · h=60 ── Model Tạo ảnh[▼]  Chất lượng ảnh[▼] ────┐
┌ Card B ── 2 cột ────────────────────────────────────────────────────────┐
│ TRÁI (prompt)                    │ PHẢI (thư viện tham chiếu)           │
│ ✏️  Prompt tạo ảnh từ ảnh tham   │ [📂 Chọn ảnh tham chiếu]             │
│    chiếu                         │ Tối đa 15 ảnh tham chiếu             │
│ 💡 Mỗi dòng là một prompt. Toàn  │ ┌──────┐┌──────┐┌──────┐            │
│    bộ ảnh bên phải sẽ được dùng  │ │ thumb││ thumb││  ☁️   │            │
│    làm tham chiếu cho mỗi prompt.│ │[tên_]││[tên_]││ Kéo   │            │
│ ┌ gutter ┬──────────────────────┐│ │  ✕   ││  ✕   ││ thả…  │            │
│ │ 001 │ …                       ││ └──────┘└──────┘└──────┘            │
│ └────────┴──────────────────────┘│                                      │
│ [📄 Import prompt.txt]           │                                      │
└─────────────────────────────────────────────────────────────────────────┘
┌ JobList ────────────────────────────────────────────────────────────────┐
```

**Điều khiển:**

| Nhãn | Loại | Giá trị / tập chọn | Mặc định (khoá config) | Ghi chú |
|---|---|---|---|---|
| TopBar + Card A | — | y hệt Text to Image | `CREATE_IMAGE_MODEL`, `CREATE_IMAGE_QUALITY`, `VIDEO_ASPECT_RATIO`, `CURRENT_PROJECT`, `VIDEO_OUTPUT_DIR` | **cùng một `CreateImageTab`** — Card A dùng chung, `sub_tabs` (`QTabWidget#CreateImageSubTabs`, tabBar ẩn) chỉ đổi phần thân. Sidebar là thứ chuyển tab, không phải tab bar |
| Tiêu đề khối trái | SectionHeader | `✏️  Prompt tạo ảnh từ ảnh tham chiếu` | — | ghi đè nhãn của `CreateImageFromPromptTab` |
| Hint | text | `💡 Mỗi dòng là một prompt. Toàn bộ ảnh bên phải sẽ được dùng làm tham chiếu cho mỗi prompt.` | — | |
| Ô prompt | PromptEditor | 1 dòng = 1 prompt | rỗng | placeholder `Nhập một prompt cho mỗi dòng...` |
| `📂 Chọn ảnh tham chiếu` | FilePickerButton | multi-select ảnh | — | dialog `Chọn ảnh nhân vật`, filter `Images (*.png *.jpg *.jpeg *.webp *.bmp);;All Files (*.*)` |
| DropZone rỗng | EmptyState | `☁️`<br>`Kéo thả ảnh tham chiếu vào đây`<br>`hoặc nhấp để chọn ảnh` | — | `objectName=DropArea`, viền `2px dashed #334155`, nền `#020617`; hover đổi `2px dashed #fbbf24` + nền `rgba(251,191,36,0.1)` |
| Giới hạn | text | `Tối đa 15 ảnh tham chiếu` | 15 | vượt → modal `Đủ số lượng` / `Đã đủ số lượng nhân vật tối đa (15 ảnh).`, phần dư bị cắt |
| Ô tên / ảnh | Field trong `MediaPicker` card | text tự do | rỗng | **bắt buộc**; card có nút `✕` xoá |
| `TẠO ẢNH` | PrimaryButton | — | — | |

**Luồng:**
1. User tải ảnh tham chiếu (≤15) và **đặt tên cho từng ảnh**.
2. User gõ N prompt.
3. Bấm `TẠO ẢNH` → 3 lớp validate, đúng thứ tự bản gốc:
   - prompt rỗng → `Không có prompt` / `Hãy nhập ít nhất một prompt ở tab Tạo Ảnh Từ Ảnh Tham Chiếu.`
   - không có ảnh → `Thiếu ảnh tham chiếu` / `Hãy chọn ảnh tham chiếu và điền tên nhân vật cho từng ảnh.`
   - có ảnh chưa đặt tên → `Thiếu tên nhân vật` / `Các ảnh tham chiếu sau chưa được đặt tên:\n{danh sách}`
4. `ensureBoardProject(boardId)` → `projectId`.
5. Upload từng ảnh: `uploadImage(file, projectId)` (không `nodeId`) → `mediaId`. Cache theo hash để không upload lại giữa các lần chạy.
6. Với mỗi prompt → `createRequest({ type:'gen_image', params:{ prompt, project_id, aspect_ratio, image_model, variant_count: OUTPUT_COUNT, ref_media_ids: [tất cả mediaId], paygate_tier } })`.
7. Poll `getRequest(id)`; `mediaUrl(mediaId)` để render.
8. Hậu-xử-lý upscale nếu `Chất lượng ảnh ≠ 1k` (endpoint mới, giống Text to Image).
9. Mode label ghi vào Activity Log: `VEO3 - Tạo ảnh từ ảnh tham chiếu`.

**Kết quả:** giống Text to Image; thêm hàng chip tên ảnh tham chiếu trên mỗi `JobCard` để nhìn ra lô nào dùng bộ ref nào.

**Cạm bẫy:**
1. **Tên ảnh tham chiếu không đi vào payload.** Flow chỉ nhận `imageInputs: [{name: <mediaId>, imageInputType: "IMAGE_INPUT_TYPE_REFERENCE"}]` — `name` **là mediaId**, không phải nhãn người dùng. Bản gốc nhồi tên vào **thân prompt** dưới dạng câu: `"{tên} là nhân vật/ảnh trong … có mediaId là {mediaId}. "` ghép trước prompt. Nếu replica bỏ bước ghép chuỗi này thì tên nhân vật hoàn toàn vô nghĩa và model sẽ trộn lẫn các ref. **Bắt buộc ghép.**
2. **15 ảnh × mọi prompt là bẫy chi phí và chất lượng.** Bản gốc không giới hạn số ref/prompt ở tab này (khác Đồng bộ nhân vật vốn cap 3). Nhồi 15 ref vào một prompt gần như chắc chắn làm loãng điều kiện. Nên cảnh báo mềm khi >5 ref, không chặn.
3. **Đây không phải `edit_image`.** Nhiều người nhầm "Image to Image" = refine. Bản gốc gọi `batchGenerateImages` với `imageInputs`, **không** có `source_media_id`. Nếu ai đó định tuyến tab này sang `edit_image` thì hành vi lệch hoàn toàn (edit yêu cầu `source_media_id`, worker trả `missing_source_media_id`).
4. Ảnh upload đi qua `POST /api/upload`, vốn **chỉ nhận ảnh** trong `ALLOWED_UPLOAD_MIMES` và cap `MAX_UPLOAD_BYTES`. `.bmp` nằm trong filter của bản gốc nhưng có thể không nằm trong allow-list backend → kiểm tra và hoặc mở rộng allow-list hoặc bỏ `.bmp` khỏi filter.
5. Toàn bộ cạm bẫy #1–#7 của Text to Image (seed, quality, LITE, kẹp 4, credit) áp dụng y nguyên.

---

### Affiliate Sản Phẩm — Tạo ảnh quảng cáo sản phẩm

**Mục đích:** Virtual Try-On 2 bước — **Bước 1** tách trang phục/sản phẩm khỏi ảnh sản phẩm lên nền trắng, **Bước 2** ghép lên ảnh KOL theo tư thế + bối cảnh đã chọn — rồi (tuỳ chọn) nhờ Gemini viết kịch bản và đẩy sang tab tạo video. **Không có scraping Shopee**: ảnh do user upload.

**Bố cục:**
```
┌ AffiliateTopBar (gradient tím, radius 12) ──────────────────────────────┐
│ 📦 Loại Sản Phẩm[▼] 📁 Dự án[▼] [📁 Xem kết quả] 📐 Tỉ lệ[▼]            │
│ 🎨 Model[▼] 💎 Chất lượng[▼]                                            │
└─────────────────────────────────────────────────────────────────────────┘
┌ ImagesFrame — SẢN PHẨM 1 (khối chính, không xoá được) ──────────────────┐
│ ☑ 📦 SẢN PHẨM 1              ☐ ✏️ Tùy Chỉnh Prompt Nâng Cao             │
│ ┌───────────┬───────────┬───────────────┬───────────────┐              │
│ │📸 Ảnh KOL │👗 Ảnh Sản │🤍 Ảnh Nền     │✅ Ảnh Đầu Ra  │              │
│ │(Cố Định)  │  Phẩm     │   Trắng       │               │              │
│ │Click để   │Click để   │Click để upload│Chưa có        │              │
│ │chọn ảnh   │chọn ảnh   │[🔄 Tạo lại    │(sau Bước 2)   │              │
│ │KOL        │sản phẩm   │  nền trắng]   │[🔄 Tạo lại    │              │
│ │           │           │               │  ảnh đầu ra]  │              │
│ └───────────┴───────────┴───────────────┴───────────────┘              │
│ 🕺 Tư thế [P01 · Đứng thẳng tự tin ▼] [➕ Thêm][🗑 Xóa]                 │
│ 🏞 Bối cảnh[S19 · Khu phố Hàn Quốc  ▼] [➕ Thêm][🗑 Xóa]                │
│ ┌ (hiện khi tick Tùy Chỉnh Prompt Nâng Cao) ─────────────────────────┐  │
│ │ 💡 Nhập yêu cầu bổ sung cho Bước 2. Tư thế & bối cảnh đang chọn… │  │
│ │ [VD: Standing in a modern coffee shop, …                        ] │  │
│ └────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
┌ VideoConfigFrame (viền cam) ────────────────────────────────────────────┐
│ [❌ Không tạo video ▼] [❌ Không thoại ▼] [🇻🇳 Tiếng Việt ▼]              │
│ [⚡ gemini-3.6-flash ▼]                                                 │
│ ☐ 📝 Có Thông tin SP   ☐ 👗 Chỉ thay trang phục                        │
│ ☐ 🔄 Chỉ dùng 1 prompt chuyển động                                      │
│ [VD: Kem chống nắng ABC SPF50+ …                                     ]  │
│ 🎯 Yêu cầu tùy chỉnh cho prompt video (Tuyệt đối tuân theo):            │
│ [Nhập yêu cầu bắt buộc cho Gemini tại đây (Dùng chung cho cả dự án). ]  │
└─────────────────────────────────────────────────────────────────────────┘
┌ ExtraBlock_2 · 📦 Sản phẩm #2 …  [✕ Gỡ hàng]  (lặp lại 4 card + combo) ─┐
┌ BtnActionFrame ─────────────────────────────────────────────────────────┐
│ [➕ THÊM SẢN PHẨM MỚI][🗑 XÓA ĐÃ CHỌN][🎨 TẠO ẢNH][🧠 VIẾT PROMPT]      │
│ [🎥 TẠO VIDEO][🚀 COMBO][🛑 DỪNG][🧹]                                   │
│ [🗑 XÓA TẤT CẢ CÁC HÀNG SẢN PHẨM PHỤ]                                   │
└─────────────────────────────────────────────────────────────────────────┘
┌ QSplitter dọc ──────────────────────────────────────────────────────────┐
│ 📝 Kịch bản video (Kéo thanh ngang bên dưới để mở rộng):                │
│ [Mỗi dòng = 1 cảnh video...                                          ]  │
│ ─────────────────────── (kéo) ──────────────────────────────────────    │
│ [log …]                                                                 │
└─────────────────────────────────────────────────────────────────────────┘
```

**Điều khiển:**

| Nhãn | Loại | Giá trị / tập chọn | Mặc định (khoá config) | Ghi chú |
|---|---|---|---|---|
| `📦 Loại Sản Phẩm` | select (violet `#8b5cf6`) | `👗 Thời Trang`→`thoi_trang`; `💄 Mỹ Phẩm`→`my_pham`; `💊 Thực phẩm chức năng`→`thuc_pham_chuc_nang`; `🏠 Đồ Gia Dụng`→`gia_dung`; `📦 Đồ vật khác`→`do_vat_khac` | `affiliate_state.json:category_index` = `0` → Thời Trang | đổi category **thay toàn bộ** bộ 30 tư thế + 30 bối cảnh **và** template prompt Bước 2 |
| `📁 Dự án` | select | tên dự án | `affiliate_state.json:project_index` = `-1` (chưa chọn) | chưa chọn mà bấm chạy → `⚠️ Chưa chọn dự án` |
| `📁 Xem kết quả` | GhostButton | — | — | `⚠️ Thư mục không tồn tại: {p}` nếu chưa có |
| `📐 Tỉ lệ` | select | `Dọc 9:16`→`IMAGE_ASPECT_RATIO_PORTRAIT`; `Ngang 16:9`→`IMAGE_ASPECT_RATIO_LANDSCAPE` | `affiliate_state.json:aspect_ratio` = `"IMAGE_ASPECT_RATIO_PORTRAIT"` | lưu **giá trị enum đầy đủ**, không phải `"9:16"` |
| `🎨 Model` | select | `🍌 Nano Banana 2`; `🍌 Nano Banana Pro`; `🍌 Nano Banana LITE` | `affiliate_state.json:model_index` = `0` → Nano Banana 2 | thứ tự **khác** tab Text to Image (2 đứng đầu); "Pro" viết hoa P |
| `💎 Chất lượng` | select | `1K`→`1k`; `2K`→`2k`; `4K`→`4k`; `🌟 4K Siêu nét GPU`→`4k_x4plus` | `affiliate_state.json:quality_index` = `0` → 1K | vẫn là upscale cục bộ sau Bước 2 |
| `📦 SẢN PHẨM 1` | Checkbox (`#f43f5e`, 13px w800) | on/off | on | chỉ hàng được tick mới chạy |
| `✏️ Tùy Chỉnh Prompt Nâng Cao` | Checkbox (`#fbbf24`) | on/off | `custom_prompt_checked` = `false` | mở ô `custom_prompt_text` (mặc định `""`) |
| `📸 Ảnh KOL (Cố Định)` | MediaPicker | 1 ảnh | `kol_image` = `""` | placeholder `Click để chọn ảnh KOL`; **dùng chung cho mọi hàng** trừ khi hàng phụ tự set |
| `👗 Ảnh Sản Phẩm` | MediaPicker | 1 ảnh | `product_image` = `""` | placeholder `Click để chọn ảnh sản phẩm` |
| `🤍 Ảnh Nền Trắng` | MediaPicker (read-only + overlay) | kết quả Bước 1 | `extracted_image` = `""` | placeholder `Click để upload` — user **được phép** tự nạp để bỏ qua Bước 1; overlay `🔄 Tạo lại nền trắng` |
| `✅ Ảnh Đầu Ra` | MediaPicker (read-only + overlay) | kết quả Bước 2 | `result_image` = `""` | placeholder `Chưa có (sau Bước 2)`; overlay `🔄 Tạo lại ảnh đầu ra` |
| `🕺 Tư thế` | select (`#c4b5fd`, `maxVisibleItems` lớn) | 30 mục `{id} · {label}` theo category, id `P01…P30` | `pose_index` = `0` → `P01 · Đứng thẳng tự tin` | value là **prompt tiếng Anh** thay cho `{POSE}` |
| `➕ Thêm` / `🗑 Xóa` (tư thế) | GhostButton | — | — | dialog `Thêm Tư Thế` / `Tên tư thế (VD: Ngồi thiền):` / `Mô tả prompt tiếng Anh:`; xoá mặc định → `Không thể xóa` / `Tư thế "{x}" là mặc định của hệ thống.` / `Chỉ có thể xóa các tư thế do bạn tự thêm.`; còn 1 mục → `⚠️ Không thể xóa — phải có ít nhất 1 tư thế` |
| `🏞 Bối cảnh` | select (`#67e8f9`) | 30 mục `S01…S30` theo category | `scene_index` = `18` → `S19 · Khu phố Hàn Quốc` | value thay cho `{SCENE}` |
| `➕ Thêm` / `🗑 Xóa` (bối cảnh) | GhostButton | — | — | `Thêm Bối Cảnh` / `Tên bối cảnh (VD: Cửa hàng mỹ phẩm):`; guard `⚠️ Không thể xóa — phải có ít nhất 1 bối cảnh` |
| ô prompt bổ sung | textarea (`#fef3c7` trên `rgba(15,23,42,.95)`, Consolas) | tự do | `custom_prompt_text` = `""` | hint đầy đủ: `💡 Nhập yêu cầu bổ sung cho Bước 2. Tư thế & bối cảnh đang chọn vẫn được giữ. Nếu bạn mô tả tư thế hoặc bối cảnh mới ở đây, nó sẽ ghi đè lên tư thế/bối cảnh cũ.`; placeholder `VD: Standing in a modern coffee shop, one hand holding a coffee cup, looking at camera with a warm smile, full body shot. Soft warm ambient lighting, bokeh background.` |
| số cảnh video | select (`#fb923c`) | `❌ Không tạo video`(0), `🎬 1 Cảnh` … `🎬 10 Cảnh` | `auto_video_count` = `0` | xem Cạm bẫy #3 — nhánh `🧠 VIẾT PROMPT` chỉ chấp nhận 1–4 |
| kiểu thoại | select | `❌ Không thoại`(0), `👤 Người mẫu nói`(1), `🎙️ Lời giới thiệu ngoài`(2) | `vo_type` = `0` | |
| ngôn ngữ thoại | select | `🇻🇳 Tiếng Việt`→`vi`; `🇺🇸 Tiếng Anh`→`en` | `vo_lang` = `"vi"` | ẩn khi `vo_type == 0` |
| model Gemini | select (viền `#10b981`) | `⚡ gemini-3.5-flash`, `⚡ gemini-3.6-flash`, `⚡ gemini-3.5-flash-lite`, `🧪 gemini-3-flash-preview`, `⚡ gemini-2.5-flash`, `💡 gemini-3.1-flash-lite`, `🌐 Chrome Gemini (Trình duyệt)`→`chrome-gemini` | `gemini_model` = `"gemini-3.6-flash"` | `chrome-gemini` là đường lái Chrome thật (không có API key) — replica local **không** hỗ trợ, ẩn mục này và đi qua `getLlmConfig()/setLlmConfig()` |
| `📝 Có Thông tin SP` | Checkbox (`#fb923c`) | on/off | `product_intro_checked` = `false` | mở ô `product_intro_text` (`""`), placeholder `VD: Kem chống nắng ABC SPF50+ PA+++, bảo vệ da 12h, không gây nhờn rít, phù hợp mọi loại da...` |
| `👗 Chỉ thay trang phục` | Checkbox | on/off | `only_outfit_checked` = `false` | **vô hiệu hoá cả Tư thế lẫn Bối cảnh** và thay bằng hai câu khoá cứng (xem Luồng bước 4) |
| `🔄 Chỉ dùng 1 prompt chuyển động` | Checkbox (`#f472b6`) | on/off | `one_motion_checked` = `false` | mọi cảnh dùng chung một English motion prompt; log `Chế độ 1 Prompt chuyển động: Đã đồng bộ English prompt` |
| `🎯 Yêu cầu tùy chỉnh cho prompt video (Tuyệt đối tuân theo):` | textarea (`#00f2fe`) | tự do | `video_custom_requirement` = `""` | nhãn phụ `Nhập yêu cầu bắt buộc cho Gemini tại đây (Dùng chung cho cả dự án).`, placeholder `VD: Tuyệt đối không được dùng lồng tiếng, chỉ tập trung vào hành động xoay người, người mẫu phải cười tươi...`; ghép vào system prompt dưới header `GLOBAL CRITICAL REQUIREMENT (MUST FOLLOW ABOVE ALL ELSE):` |
| `➕ THÊM SẢN PHẨM MỚI` | PrimaryButton tím (`#8b5cf6→#a78bfa`) | — | — | tạo `ExtraBlock_{n}`; toast `➕ Đã thêm sản phẩm #{n}`; card đổi nhãn `📸 Ảnh KOL (#{n})` / `👗 Sản phẩm (#{n})` / `🤍 Nền trắng (#{n})` / `✅ Kết quả (#{n})`, overlay rút gọn `🔄 Nền trắng` / `🔄 Chạy` / `🎥 Video`, checkbox `✏️  Tùy Chỉnh Prompt`, placeholder `Nhập prompt tùy chỉnh cho sản phẩm này...` và `Nhập mô tả thêm cho sản phẩm này...` |
| `🗑 XÓA ĐÃ CHỌN` | GhostButton đỏ | — | — | `⚠️ Chưa chọn hàng nào để xóa`; xác nhận `Xác nhận` / `Xóa {n} hàng đã chọn?` |
| `🎨 TẠO ẢNH` | Button cyan (`#0891b2→#0e7490`) | — | — | chạy Bước 1+2 cho các hàng đã tick |
| `🧠 VIẾT PROMPT` | Button tím (`#7c3aed→#6d28d9`) | — | — | gọi Gemini sinh kịch bản |
| `🎥 TẠO VIDEO` | Button cam (`#ea580c→#c2410c`) | — | — | đẩy sang tab Image to Video và tự chạy |
| `🚀 COMBO` | PrimaryButton xanh lá (`#10b981→#059669`, 2px `#34d399`) | — | — | ảnh → prompt → video liên hoàn |
| `🛑 DỪNG` | StopButton | — | — | |
| `🧹` | GhostButton icon-only | — | — | refresh/khôi phục trạng thái |
| `🗑 XÓA TẤT CẢ CÁC HÀNG SẢN PHẨM PHỤ` | GhostButton mờ | — | — | xác nhận `Bạn có chắc chắn muốn xóa sạch trạng thái hiện tại?` |
| `📝 Kịch bản video (…)` | textarea (`#ffd8a8`, Consolas) | 1 dòng = 1 cảnh | `video_prompts` = `[]` | placeholder `Mỗi dòng = 1 cảnh video...`; user sửa tay được, log `♻️ Sử dụng kịch bản video hiện có từ giao diện.` |

**Bộ preset (mined đầy đủ — 5 category × 30 tư thế × 30 bối cảnh):** hằng `DEFAULT_CATEGORY_DATA` trong module `affiliate_default_data`, cấu trúc `{category: {poses: {P01:{label, description}}, scenes: {S01:{label, description}}}}`. `description` là prompt tiếng Anh, thay trực tiếp vào `{POSE}` / `{SCENE}`. Ví dụ `thoi_trang`: `P01 Đứng thẳng tự tin` · `P03 Bước đi tự nhiên` · `P12 Catwalk động` · `P22 Đứng khoanh tay` · `P29 Quay người dance`; `S01 Phòng khách Bắc Âu` · `S11 Phố đêm neon` · `S19 Khu phố Hàn Quốc` (mặc định) · `S28 Bậc thang terrazzo` · `S29 Studio nền pastel`. `my_pham` có bộ pose riêng (`P01 Cầm sản phẩm cạnh mặt`, `P04 Cầm son/son môi`, `P10 Xịt nước hoa`, `P19 Nhắm mắt thư giãn`…). Preset user tự thêm lưu ở `data_general/affiliate_poses_scenes.json` (tách khỏi `affiliate_state.json`). Ngoài ra còn một bộ `prompt_mau.py` gồm 65 bối cảnh mô tả tiếng Việt (`38. Quán cafe bàn gỗ ấm áp` … `65. Cafe vintage nhẹ nhàng`) — đó là template ảnh ốp lưng riêng, **không** thuộc tab này, đừng trộn vào.

**Luồng:**
1. User chọn `📦 Loại Sản Phẩm`, `📁 Dự án`, nạp `📸 Ảnh KOL` + `👗 Ảnh Sản Phẩm`, chọn tư thế/bối cảnh, tick hàng cần chạy.
2. Bấm `🎨 TẠO ẢNH` → validate: chưa tick → `⚠️ Chưa chọn sản phẩm nào để chạy (Tick vào checkbox …)`; thiếu ảnh → `❌ Sản phẩm #{n} thiếu ảnh KOL hoặc sản phẩm`; không có cấu hình → `❌ Không có cấu hình hợp lệ để chạy`. Log mở đầu `🚀 Bắt đầu workflow Affiliate Virtual Try-On`.
3. **Bước 1 — tách nền trắng** (bỏ qua nếu `🤍 Ảnh Nền Trắng` đã có: `✅ Đã có ảnh trang phục nền trắng, bỏ qua Bước 1`). Log `📋 Bước 1: Tách trang phục từ ảnh sản phẩm...`
   - `uploadImage(productFile, projectId)` → `mediaId_product`
   - `createRequest({type:'gen_image', params:{ prompt: PROMPT_STEP1_EXTRACT + " Product là nhân vật/ảnh trong … có mediaId là {mediaId_product}. ", project_id, aspect_ratio, image_model, variant_count:1, ref_media_ids:[mediaId_product], paygate_tier }})`
   - Retry tối đa 2 lần (`🔄 Thử lại bước 1 (lần {i}/2)...`); thất bại → `❌ Bước 1 thất bại — không tách được trang phục (đã thử 2 lần)`. Thành công → `✅ Bước 1 hoàn tất: {path}`, log `✅ Ảnh sản phẩm nền trắng: {path}`, output_prefix `Product`.
4. **Bước 2 — ghép KOL + trang phục.** Log `📋 Bước 2: Kết hợp KOL + trang phục...`
   - `uploadImage(kolFile, projectId)` → `mediaId_kol`; upload lại ảnh nền trắng → `mediaId_white`
   - Dựng prompt từ `PROMPT_STEP2_TEMPLATE[category]`, thay `{POSE}` / `{SCENE}`:
     - bình thường → `description` của tư thế/bối cảnh đang chọn
     - `👗 Chỉ thay trang phục` bật → `{POSE}` = `The model in the reference image maintains her exact original posture and body orientation. NO change to her pose.`; `{SCENE}` = `The background must be EXACTLY the same as in the reference image. NO change to the scene environment.`; đồng thời **disable** hai combo
     - `✏️ Tùy Chỉnh Prompt Nâng Cao` bật → nối thêm khối `ADDITIONAL USER INSTRUCTIONS (if these describe a pose or scene, they OVERRIDE the pose/scene described above):` + `custom_prompt_text`
   - Nối `char_parts`: `"MyModel là nhân vật/ảnh trong … có mediaId là {mediaId_kol}. Product là nhân vật/ảnh trong … có mediaId là {mediaId_white}. "`
   - `createRequest({type:'gen_image', params:{ prompt, project_id, aspect_ratio, image_model, variant_count:1, ref_media_ids:[mediaId_kol, mediaId_white], paygate_tier }})`, output_prefix `MyModel`, thư mục `affiliate_result`
   - Thất bại → `❌ Bước 2 thất bại`; thành công → `✅ Bước 2 hoàn tất: {path}`, `✅ Ảnh đầu ra: {path}`
5. Nếu `💎 Chất lượng ∈ {2k, 4k, 4k_x4plus}` → log `🧩 Đang thực hiện upscale ảnh kết quả lên {q}` rồi upscale cục bộ, đổi tên `…_1k.jpg` → `…_{q}.jpg`, log `✅ Đã đổi tên → {tên}`. Kết thúc `🎉 Workflow hoàn tất!`. Chạy nhiều hàng: `🎉 Job {i} hoàn tất!`.
6. `🧠 VIẾT PROMPT` → validate `⚠️ Chưa chọn sản phẩm nào để viết kịch bản.`, `❌ Các sản phẩm được chọn chưa có ảnh kết quả. Hãy 'Tạo ảnh' trước.`, `❌ Vui lòng chọn số cảnh video (1, 2, 3 hoặc 4).` Gửi ảnh kết quả + `product_intro_text` + `vo_type`/`vo_lang` + `video_custom_requirement` cho Gemini (endpoint mới `POST /api/affiliate/script`), nhận JSON array N chuỗi → đổ vào `📝 Kịch bản video`. Log `⏳ Pipeline tạo ảnh đã xong, đang chờ Gemini hoàn tất kịch bản...` → `✅ Đã hoàn thành viết kịch bản cho các sản phẩm.`
7. `🎥 TẠO VIDEO` → validate `❌ Không tìm thấy kịch bản video. Hãy 'Viết Prompt' trước.` Upload ảnh kết quả lên Flow (log `⬆️ Upload ảnh đầu ra lên VEO3...` → `✅ Upload OK → mediaID: {id}`), rồi mỗi cảnh: `createRequest({type:'gen_video', params:{ prompt, project_id, start_media_id, aspect_ratio:'VIDEO_ASPECT_RATIO_PORTRAIT', paygate_tier, video_quality }})`. Chuyển sang tab Image to Video, toast `Đã nạp {n} yêu cầu video sang trang tạo video...` hoặc `✨ Đã nạp kịch bản vào tab Video. Bạn có thể kiểm tra và bấm 'Bắt đầu' thủ công.`
8. `🚀 COMBO` = bước 2→7 liên hoàn, không dừng giữa chừng.
9. Mọi thay đổi control ghi ngay xuống `affiliate_state.json` (`_save_affiliate_state`); lỗi → `Error saving affiliate state:` / `Error loading affiliate state:`; khôi phục → `✨ Đã khôi phục trạng thái cũ. Bạn có thể tiếp tục tạo video.`

**Kết quả:** Ảnh Bước 1 và Bước 2 hiện ngay trong đúng card của hàng (spinner overlay khi đang chạy). Kịch bản đổ vào ô `📝 Kịch bản video`. Log realtime ở pane dưới với timestamp `[HH:MM:SS]`. Video sinh ra hiển thị ở tab Image to Video / Activity Log, tiêu đề `Product {n} - Scene {m}`.

**Cạm bẫy:**
1. **Không có `edit_image` ở đây.** Cả hai bước đều là `gen_image` + `ref_media_ids`. Ai đó thấy chữ "ghép/thay trang phục" mà route sang `edit_image` sẽ hỏng: `edit_image` cần `source_media_id` và giữ khung ảnh gốc, còn VTON cần dựng lại toàn bộ khung theo `{SCENE}`.
2. **Tên `MyModel` / `Product` chỉ tồn tại trong text prompt.** Template Bước 2 nói `"reference image MyModel"` và `"reference image Product"`, nhưng payload chỉ có mediaId. Bắt buộc ghép câu `"{tên} là nhân vật/ảnh trong … có mediaId là {mediaId}. "` **và giữ đúng thứ tự** `ref_media_ids = [kol, white_bg]`. Đảo thứ tự → model mặc trang phục lên ảnh sản phẩm.
3. **Combo cảnh cho 10, Gemini chỉ nhận 4.** `combo_auto_video` liệt kê `🎬 1 Cảnh` … `🎬 10 Cảnh` nhưng nhánh `🧠 VIẾT PROMPT` chặn cứng: `❌ Vui lòng chọn số cảnh video (1, 2, 3 hoặc 4).` Chọn 5–10 rồi bấm `VIẾT PROMPT` là ngõ cụt im lặng. Trong replica: hoặc cắt combo còn 1–4, hoặc disable `🧠 VIẾT PROMPT` kèm tooltip khi >4. **Đừng** để nguyên cả hai.
4. **`scene_index: 18` là 0-based** → mục thứ 19 (`S19 Khu phố Hàn Quốc`), không phải `S18`. Off-by-one ở đây đổi hẳn bối cảnh mặc định.
5. **Đổi `📦 Loại Sản Phẩm` làm `pose_index`/`scene_index` trỏ sai.** Mỗi category có bộ 30 riêng; nếu giữ nguyên index cũ thì tư thế "Xịt nước hoa" của mỹ phẩm biến thành "Catwalk động" của thời trang mà không báo. Bản gốc reset về 0; replica phải làm giống hoặc map theo id.
6. **`👗 Chỉ thay trang phục` phải thực sự disable combo.** Nếu chỉ bỏ qua giá trị mà vẫn để combo sáng, user tưởng bối cảnh có tác dụng — nhưng prompt đã khoá `NO change to the scene environment.` Đây là nguồn bug báo cáo kinh điển.
7. **`🌐 Chrome Gemini (Trình duyệt)` không chạy được ở bản local.** Nó lái Chrome thật qua CDP, gõ prompt vào `div[role="textbox"]` của gemini.google.com. Bản replica không có lớp đó → **ẩn mục này**, đừng để một lựa chọn luôn thất bại.
8. **`💎 Chất lượng` ở đây và `🖼 Quality` ở tab tạo ảnh là hai state khác nhau** (`affiliate_state.json:quality_index` vs `config.json:CREATE_IMAGE_QUALITY`). Đừng gộp store.
9. **PII trong `branding_state.json`.** `Tên:` / `SĐT:` ở header lấy từ đó — ship **rỗng** (`Tên: N/A` / `SĐT: N/A`), user tự điền.
10. Bước 1 chỉ retry **2 lần** rồi bỏ; Bước 2 **không** retry ở tầng workflow. Nếu 403/reCAPTCHA thì bản gốc bỏ inline-retry để queue worker khởi động lại từ đầu — replica phải để `RETRY_WITH_ERROR` (=`3`) ở tầng request, không tự thêm vòng lặp riêng.

---

### UP SCALE IMAGE 4K — Nâng cấp chất lượng ảnh

**Mục đích:** phóng to ảnh cục bộ bằng RealESRGAN/Upscayl (một ảnh hoặc cả thư mục), hoàn toàn offline, không tốn credit Flow.

**Bố cục:**
```
┌ ToolHeader ── 🌟  UP SCALE IMAGE 4K   Nâng cấp chất lượng ảnh ──────────┐
┌ #ControlPanel (nền #0f172a, viền 2px #1e293b, radius 15, h≈75) ─────────┐
│ [Nâng cấp 4K ▼] [⚡ Lite (Nhanh) ▼]  ☐ 🔥 Chế độ CPU                   │
│                        [ 🚀 CHẠY UPSCALE ]  [ 🛑 DỪNG ]                 │
└─────────────────────────────────────────────────────────────────────────┘
┌ #OutputPanel (radius 10) ───────────────────────────────────────────────┐
│ 📂 Thư mục lưu:  Mặc định (Lưu cùng thư mục ảnh gốc)                    │
│                  [Chọn thư mục lưu...] [Xóa]                            │
└─────────────────────────────────────────────────────────────────────────┘
┌──────────────── 📸 ẢNH GỐC ────┬──────────── ✨ KẾT QUẢ AI ────────────┐
│ ┌ #DropArea ─────────────────┐ │ ┌ #ResultPreview ──────────────────┐  │
│ │      Kéo thả ảnh vào đây   │ │ │   BẤM CHẠY UPSCALE ĐỂ XEM        │  │
│ │      hoặc Click để chọn    │ │ │                                  │  │
│ └────────────────────────────┘ │ └──────────────────────────────────┘  │
│ [📂 CHỌN ẢNH...][📁 CHỌN THƯ  │ [📁 MỞ THƯ MỤC]                       │
│  MỤC...][🔍 XEM ẢNH GỐC]      │                                        │
└────────────────────────────────┴────────────────────────────────────────┘
[▓▓▓▓▓▓▓░░░░░░░░░░░]  Trạng thái: Sẵn sàng
```

**Điều khiển:**

| Nhãn | Loại | Giá trị / tập chọn | Mặc định (khoá config) | Ghi chú |
|---|---|---|---|---|
| *(không nhãn)* `cb_quality` | select (viền `#00f2fe`, nền `#0f172a`, chữ `#00f2fe` w900) | `Nâng cấp 2K`, `Nâng cấp 4K` | `data_general/upscale_config.json:quality` = `1` → `Nâng cấp 4K` | quyết định `-s`; xem Cạm bẫy #1 |
| *(không nhãn)* `cb_model` | select | `⚡ Lite (Nhanh)`→`upscayl-lite-4x`; `🌟 x4Plus (Người thật - GPU)`→`realesrgan-x4plus` | `upscale_config.json:model` = `0` → Lite | tooltip: `x4Plus: model Real-ESRGAN chuyên người thật, chạy GPU Vulkan`; nạp thật từ `GET /api/postprod/status .upscaleModels` |
| `🔥 Chế độ CPU` | Checkbox (`#f472b6` w800 12px, `objectName=CpuCheck`) | on/off | `upscale_config.json:cpu` | ép `-g -1`; bị **tự tắt** khi chọn x4Plus |
| `🚀 CHẠY UPSCALE` | PrimaryButton (`objectName=UpscaleBtn`, viền `2px #ff0080`, 15px w900, padding 12/40) | — | — | **disable khi `status.upscaleAvailable === false`**, tooltip nêu lý do |
| `🛑 DỪNG` | StopButton (`objectName=StopBtn`) | — | — | ẩn cho tới khi chạy; dừng → `Đã dừng.` / `🛑 Đã dừng tiến trình upscale thư mục.` |
| `📂 Thư mục lưu:` | nhãn (`#94a3b8` w700 12px) + giá trị (`#3b82f6` w600) | đường dẫn | `upscale_config.json:output_folder` = rỗng → `Mặc định (Lưu cùng thư mục ảnh gốc)` | khi đã chọn, giá trị đổi màu `#10b981`; dialog `Chọn thư mục lưu ảnh upscale` |
| `Chọn thư mục lưu...` / `Xóa` | GhostButton (`SecondaryBtn`, 11px, `Xóa` màu `#ef4444`) | — | — | |
| `📸 ẢNH GỐC` | SectionHeader (`SectionTitleOrig`, `#94a3b8` w800 13px) | — | — | |
| DropArea | EmptyState | `Kéo thả ảnh vào đây`<br>`hoặc Click để chọn` | — | drag-over → viền `2px dashed #fbbf24`, nền `rgba(251,191,36,0.1)`, chữ `#fbbf24` |
| `📂 CHỌN ẢNH...` | FilePickerButton | 1 ảnh | — | dialog `Chọn ảnh gốc`, filter `Images (*.png *.jpg *.jpeg *.webp *.bmp)`; sau khi chọn nhãn thành `📂 Đã chọn: {tên}` |
| `📁 CHỌN THƯ MỤC...` | FilePickerButton | thư mục | — | dialog `Chọn thư mục chứa ảnh gốc`; quét `.jpeg .jpg .png .webp .bmp`; rỗng → `❌ Không tìm thấy ảnh nào trong thư mục!` + modal `Thông báo` / `Không tìm thấy ảnh hợp lệ (.png, .jpg, .jpeg, .webp, .bmp) trong thư mục đã chọn!`; có → `📁 Đã chọn thư mục: {p} (Tìm thấy {n} ảnh)` |
| `🔍 XEM ẢNH GỐC` | GhostButton | — | disabled | mở ảnh full |
| `✨ KẾT QUẢ AI` | SectionHeader (`SectionTitleRes`, `#f59e0b` w800 13px) | — | — | |
| ResultPreview | ClickableLabel (`#ResultPreview`, viền `2px #f59e0b`, nền `#020617`) | — | `BẤM CHẠY UPSCALE ĐỂ XEM` | click → xem full; xong hiện `✅ {msg} (Click vào ảnh kết quả để xem full)` |
| `📁 MỞ THƯ MỤC` | GhostButton | — | — | web không mở Explorer → `ResultsDrawer` |
| ProgressBar | ProgressBar (chunk gradient `#3b82f6→#2dd4bf`, radius 10) | 0–100 | 0 | |
| dòng trạng thái | StatusChip (`#2dd4bf` w700 13px) | — | `Trạng thái: Sẵn sàng` | |

**Luồng:**
1. User thả/chọn ảnh (hoặc thư mục). Preview hiện ngay.
2. Chọn `Nâng cấp 2K`/`4K`, model, CPU. Ràng buộc tự động, hiển thị đúng câu bản gốc: chọn 2K → ép model về Lite, status `2K chỉ dùng Lite (nhanh)`; chọn x4Plus → bỏ tick CPU, status `Model x4Plus luôn dùng GPU Vulkan`.
3. Bấm `🚀 CHẠY UPSCALE`. Chưa chọn gì → `Vui lòng chọn ảnh trước!` / `Vui lòng chọn thư mục chứa ảnh trước!`.
4. `GET /api/postprod/status` → nếu `upscaleAvailable === false` thì không gửi request; hiện thông báo engine thiếu (dùng nguyên văn bản gốc): `Không tìm thấy thư mục UpscaleEngine.\nHãy đặt folder UpscaleEngine cùng thư mục với file chạy tool.\nĐã tìm tại: {p}` / `Thiếu thư mục models trong UpscaleEngine.\nCần có: {p}\nHãy copy đầy đủ folder UpscaleEngine bao gồm cả thư mục models bên trong.`
5. Đưa file lên server: **cần endpoint mới** `POST /api/files/stage` (route `/api/postprod/upscale` chỉ nhận đường dẫn server-side, `_existing()` giới hạn trong `STORAGE_DIR` + `ASSET_ROOT`; `/api/upload` hiện có thì chỉ nhận ảnh **và** đẩy lên Flow — sai mục đích).
6. `POST /api/postprod/upscale` với `{ source, output, scale, model, kind: "image" }`. Log `🚀 Đang khởi chạy AI Engine [{x4Plus GPU (Người thật)|Lite}]...`
7. Chế độ thư mục: chạy tuần tự, log `🚀 [{i}/{n}] Đang upscale: {tên}...` và `[{i}/{n}] Đang upscale ({pct}%): {tên}`; lỗi giữa chừng → hỏi `Lỗi xử lý ảnh` / `Lỗi khi upscale ảnh '{tên}':\n{err}\n\nBạn có muốn tiếp tục xử lý các ảnh tiếp theo trong thư mục không?` (`Yes`/`No`); `No` → `❌ Đã dừng do lỗi.` Xong → `🎉 Đã hoàn thành upscale toàn bộ {n} ảnh trong thư mục!`
8. Kết quả ghi ra `output_folder` hoặc cạnh ảnh gốc, **luôn JPEG** (`Images (*.jpg)`, save dialog `Lưu kết quả`, xong → `Thành công` / `Đã lưu ảnh!`).

**Kết quả:** ảnh sau khi upscale hiện ở panel `✨ KẾT QUẢ AI` (click xem full), progress + `Trạng thái: …` cập nhật realtime. Chế độ thư mục thêm `JobList` một dòng/ảnh.

**Cạm bẫy:**
1. **`Nâng cấp 2K` với model 4× là ảnh HỎNG.** Cả `realesrgan-x4plus` lẫn `upscayl-lite-4x` đều là mạng cố định ×4. `realesrgan-ncnn-vulkan -s 2` vẫn **exit 0** và ghi ra ảnh đúng kích thước nhưng pixel hỏng — `agent/flowboard/services/upscale.py` đo được **4.5 dB PSNR** ở `-s 2` so với **26 dB** ở `-s 4`. Bản gốc "ép 2K dùng Lite" **không** cứu được gì vì Lite cũng là 4×. **Trong replica: luôn chạy `scale=4` rồi hạ về đích bằng `targetHeight`** (`/api/postprod/upscale` đã có tham số này cho video; cần mở cho `kind:"image"`). Đổi nhãn thành `Nâng cấp 2K (×4 rồi hạ về 1440p)` hoặc bỏ hẳn mục 2K.
2. **`upscaleModels` chỉ có đúng 2 tên.** `UpscaleEngine/models/` = `realesrgan-x4plus.param` + `upscayl-lite-4x.param`. Đừng hardcode combo — đọc từ `status.upscaleModels`, và nếu tên nào biến mất thì bỏ khỏi combo thay vì để chạy rồi lỗi.
3. **Route upscale hiện tại là ĐỒNG BỘ.** `POST /api/postprod/upscale` chạy trong threadpool nhưng **giữ kết nối HTTP tới khi xong** — một ảnh 4K mất hàng chục giây, cả thư mục mất hàng chục phút, và **không có cách huỷ**. Nút `🛑 DỪNG` sẽ là nút giả. Cần biến upscale thành job có id + `GET`/`cancel` (xem `newEndpoints`), hoặc thêm `upscale` làm `type` của `createRequest` để tái dùng máy queue/poll/cancel sẵn có.
4. **Giới hạn 10 ảnh/ngày là license gate của tác giả** (`data_general/upscale_usage.json`, `Bạn đã đạt giới hạn 10 ảnh upscale/ngày trên bản miễn phí.` / `Bạn chỉ còn {n} lượt upscale miễn phí hôm nay. Hệ thống sẽ xử lý {n} ảnh đầu tiên.`). Bản local đã gỡ license server → **bỏ hoàn toàn**, đừng port sang.
5. **`🔥 Chế độ CPU` là cứu cánh, không phải tuỳ chọn màu mè.** Máy không có Vulkan sẽ crash im lặng; thông điệp bản gốc `Engine crash không có log (exit code: {n}).\nNguyên nhân có thể:\n• Máy thiếu Visual C++ Redistributable 2015-2022.\n• GPU không hỗ trợ Vulkan — hãy bật Chế độ CPU.\n• File engine bị hỏng — hãy copy lại folder UpscaleEngine.` Giữ nguyên câu này trong `EmptyState` lỗi — nó là chẩn đoán thật.
6. **Đầu ra luôn JPEG** kể cả nguồn PNG có alpha → alpha bị flatten. Nếu cần giữ PNG phải đổi đuôi `dst`, backend đã hỗ trợ ("Output format follows `dst`'s extension").
7. Vẫn tồn tại một đường **Flow online** rẻ hơn/đẹp hơn cho ảnh AI (`UPSAMPLE_IMAGE_RESOLUTION_2K/4K`, `tool=PINHOLE`, cần `mediaId + projectId + reCAPTCHA`) — module `API_upscale_image` còn trong binary nhưng **4.6.1.3 không gọi nữa**. Đừng quảng cáo tính năng đó nếu không tự nối lại; nếu nối thì phải nói rõ **tốn credit**.

---

### Upscale Video — Nâng Cấp Video (Local Engine)

> **Sự thật cần biết trước:** `qt_ui.tab_upscale_video` **có trong binary nhưng KHÔNG có mục sidebar** ở v4.6.1.3. Nó nằm trong bảng module (`qt_ui.tab_upscale_video`, `API_upscale_video`) nhưng `_rebuild_sidebar` không mount. Đây là tab ngủ đông. Ta dựng nó thành tab thật — đó chính là lý do build bản local.

**Mục đích:** phóng to video cục bộ bằng Real-ESRGAN: tách frame → upscale từng frame → ghép lại, **giữ nguyên audio gốc**.

**Bố cục:**
```
┌ ToolHeader ─────────────────────────────────────────────────────────────┐
│ ✨ Nâng Cấp Video (Local Engine)                          (24px, w900)  │
│ Sử dụng Real-ESRGAN để phóng to video lên 4K trực tiếp trên máy của bạn. │
└─────────────────────────────────────────────────────────────────────────┘
┌ #Card (rgba(30,41,59,.5), viền rgba(255,255,255,.1), radius 16, pad 20) ┐
│ ┌ #SelectBtn (dashed #334155, padding 40, 16px) ───────────────────────┐│
│ │        📁 Kéo thả hoặc Click để chọn Video (.mp4)                    ││
│ └──────────────────────────────────────────────────────────────────────┘│
│ Chưa chọn video                                        (#64748b, 13px)  │
│                                                                          │
│ Độ phân giải: [Lên 4K (4X - Chậm) ▼]   Mô hình AI: [Người thực 4X ▼]     │
│                                                                          │
│           [ 🚀 BẮT ĐẦU NÂNG CẤP ]        [ ⏹ DỪNG ]                     │
│ [▓▓▓▓▓▓░░░░░░░░░░░░░]                                                    │
│ Trạng thái: Sẵn sàng                                                     │
└─────────────────────────────────────────────────────────────────────────┘
```

**Điều khiển:**

| Nhãn | Loại | Giá trị / tập chọn | Mặc định (khoá config) | Ghi chú |
|---|---|---|---|---|
| `📁 Kéo thả hoặc Click để chọn Video (.mp4)` | FilePickerButton (`#SelectBtn`, viền `2px dashed #334155`, hover `#38bdf8`) | 1 video | — | dialog `Chọn Video`, filter `Video Files (*.mp4 *.mov *.avi)`; drop cũng nhận |
| `lbl_file_info` | text (`#64748b` 13px) | — | `Chưa chọn video` | sau khi chọn → `📍 File: {tên}` |
| `Độ phân giải:` | LabeledSelect | `Lên 1080p (2X - Nhanh)`, `Lên 4K (4X - Chậm)` | index 1 → `Lên 4K (4X - Chậm)` (không có khoá config — tab chưa được mount nên không persist) | ánh xạ `scale` |
| `Mô hình AI:` | LabeledSelect | `Người thực 2X (X2Plus)`→`realesrgan-x2plus`; `Người thực 4X (X4Plus)`→`realesrgan-x4plus` | `realesrgan-x4plus` (default của `LocalVideoUpscaleWorker`) | **`realesrgan-x2plus` KHÔNG tồn tại trong `UpscaleEngine/models/`** — xem Cạm bẫy #1 |
| `🚀 BẮT ĐẦU NÂNG CẤP` | PrimaryButton (`#RunBtn`, gradient `#3b82f6→#8b5cf6`, 16px bold, pad 15; disabled `#334155`/`#64748b`) | — | — | gate trên `status.upscaleAvailable` **và** `status.ffmpegAvailable` |
| `⏹ DỪNG` | StopButton (`#dc2626`, hover `#ef4444`) | — | — | ẩn cho tới khi chạy; bấm → `⏹ Đang ngắt GPU Video...` → `Đã dừng video upscale.` |
| ProgressBar | ProgressBar | 0–100 | 0 | |
| dòng trạng thái | StatusChip | — | `Trạng thái: Sẵn sàng` | các mốc: `🎞 Đang tách khung hình từ video...` → `🚀 Bắt đầu khâu AI Upscale (GPU)...` → `🚀 AI Upscale: Đang xử lý ảnh {i}/{n} ({pct}%)...` → `🎬 Đang ghép lại thành video 4K...` → `✅ Hoàn thành!` |
| *(dialog lưu)* | — | `Lưu Video kết quả`, tên gợi ý `video_upscaled.mp4`, filter `Video Files (*.mp4)` | — | web không có Save dialog → tải về từ `ResultsDrawer` |

**Luồng:**
1. `GET /api/postprod/status` → gate nút trên `upscaleAvailable && ffmpegAvailable`; điền `Mô hình AI` từ `status.upscaleModels`.
2. User thả/chọn video. Hiện `📍 File: {tên}` + (nên có) độ phân giải/thời lượng nguồn — cần endpoint probe mới.
3. Chọn độ phân giải đích + model. Bấm `🚀 BẮT ĐẦU NÂNG CẤP`.
4. Stage file lên server (`POST /api/files/stage`, endpoint mới — `/api/upload` chỉ nhận ảnh).
5. `POST /api/postprod/upscale` với `{ source, output, scale: 4, model, kind: "video", targetHeight: 1080 | 2160 }`.
6. Backend (`upscale.upscale_video`) làm đúng như bản gốc: nổ frame PNG → upscale cả thư mục trong **một** lần gọi engine → ghép lại ở đúng fps nguồn → **mux lại audio gốc**. Bản gốc dùng ffmpeg: tách `-qscale:v 2 frame_%06d.jpg`, đọc fps qua `ffprobe -select_streams v:0 -show_entries stream=r_frame_rate -of csv=p=0`, ghép `-y -r {fps} -i frames_out/frame_%06d.jpg -i {src} -map 0:v:0 -map 1:a:0? -c:v libx264 -pix_fmt yuv420p -crf 18 -c:a copy`.
7. Tiến độ: engine in `[{i}/{n}]` cho mỗi frame → ánh xạ thành phần trăm.
8. Xong → `Nâng cấp Video thành công!`, modal `Xong!` / `Video đã được nâng cấp thành công tại: {path}`. Lỗi → `❌ Lỗi: {msg}` + modal `Lỗi`.
9. Lỗi engine/ffmpeg dùng nguyên văn: `Không tìm thấy Engine Upscale tại: {p}`, `Không tìm thấy file ffmpeg.exe trong thư mục gốc.`, `Lỗi Real-ESRGAN: {err}`, `Lỗi hệ thống: {err}`, `Đã dừng quy trình.`

**Kết quả:** video kết quả trong `ResultsDrawer` với `<video controls>` + nút tải; hiện cạnh nhau kích thước nguồn/đích và thời lượng.

**Cạm bẫy:**
1. **`Người thực 2X (X2Plus)` là mục CHẾT.** Combo trỏ `realesrgan-x2plus`, nhưng `UpscaleEngine/models/` chỉ có `realesrgan-x4plus` và `upscayl-lite-4x`. Chọn nó → `-n realesrgan-x2plus` → engine không tìm thấy `.param` → hỏng. Trong replica: **dựng combo từ `status.upscaleModels`**, không hardcode. Nếu vẫn muốn nhãn "2X" thì nó phải là *x4plus + targetHeight=1080*, chứ không phải một model khác.
2. **`Lên 1080p (2X)` cũng là bẫy `-s 2` y như tab ảnh** (mục #1 của UP SCALE IMAGE 4K). Ép `scale=4` + `targetHeight=1080`; `/api/postprod/upscale` đã có `targetHeight` chính vì lý do này.
3. **Route đồng bộ = không thể huỷ, và một video 30s ở 4K là hàng chục phút.** Đây là lý do mạnh nhất để chuyển upscale sang job có id + poll + cancel. `⏹ DỪNG` không có nghĩa nếu request vẫn treo.
4. **Đường Flow online tồn tại và cho AI footage thì tốt hơn hẳn** — `POST /v1/video:batchAsyncGenerateVideoUpsampleVideo`, `videoModelKey ∈ {veo_3_1_upsampler_1080p, veo_3_1_upsampler_4k}`, poll `batchCheckAsyncVideoGenerationStatus`, tải qua `media.getMediaUrlRedirect?name=`. Nhưng: **tốn credit**, cần reCAPTCHA (`reCAPTCHA token cho Video Upscale`), và payload đóng cứng `userPaygateTier` mặc định `PAYGATE_TIER_TWO` — tài khoản **Pro = `PAYGATE_TIER_ONE` nhiều khả năng bị từ chối** (`Google từ chối yêu cầu upscale video`). Bản gốc còn có `upscale_local_video_experimental` (upload mp4 local lên Flow rồi upscale online). **flowboard hiện KHÔNG có đường này** (`flow_sdk` không có `upsample`, worker không có request type).
   → Trình bày 3 lựa chọn **trung thực** trong UI:
   - **Local RealESRGAN** (mặc định): miễn phí, offline, chậm, tốt cho người thật/live-action, kém với artifact nén.
   - **Flow online (video AI)**: nhanh hơn và sạch hơn cho footage do Veo sinh ra vì upsampler cùng họ model, **nhưng tốn credit** và **có thể chỉ chạy trên Ultra**. Chưa nối — cần request type mới.
   - **Không upscale**: với clip 720p đăng mạng xã hội, upscale thường không đáng thời gian.
5. **Audio.** Bất kỳ đường tắt nào (ffmpeg `-c copy` sai map, hoặc chỉ ghép frame) sẽ **im tiếng**. `upscale.py` ghi rõ: "a video path that quietly dropped the audio track would be worse than no upscale at all." Phải test một clip có narrate.
6. **Đĩa.** Tách frame một clip 8s 1080p ≈ 200 PNG; ở 4K, `frames_out` phồng lên hàng GB. Cần cảnh báo dung lượng trống trước khi chạy và dọn thư mục tạm kể cả khi lỗi.
7. Tab này chưa từng chạy thật trong bản đóng gói (không có nav entry) → **không có "hành vi gốc" để đối chiếu**. Coi phần UI là mock trung thực theo mã, còn hành vi thì tin `agent/flowboard/services/upscale.py` (đã đo PSNR, đã xử lý audio) hơn là tin worker của exe.


# SYSTEM + 5 pill trên

## Nguồn dữ liệu cho nhóm này

Mọi nhãn dưới đây **mined byte-exact** từ blob hằng số Nuitka trong `RUN_VEO_3_ULTRA_PROMAX.exe`. Blob giữ nguyên thứ tự khai báo của từng module `qt_ui\*.py`, nên có thể dựng lại gần như 1:1 cây widget. Offset các module (bản dựng 4.6.1.3):

| Module | Blob offset (đầu → cuối) | Class |
|---|---|---|
| `qt_ui/status_help_view.py` | 319 870 331 → 319 874 935 | `build_status_help_view` |
| `qt_ui/tab_cut_merge_video.py` | 320 000 414 → 320 013 537 | `CutMergeVideoTab`, `VideoCutWorker`, `VideoMergeWorker`, `SimpleVideoPreviewCanvas` |
| `qt_ui/tab_donate.py` | 320 015 296 → 320 020 589 | `DonateTab`, `QRDownloadThread` |
| `qt_ui/tab_grok_settings.py` | 320 102 492 → 320 109 998 | `GrokSettingsTab` |
| `qt_ui/tab_settings.py` | ~320 199 400 → 320 228 967 | `SettingsTab`, `_GetTokenWorker`, `_AutoLoginWorker` |
| `qt_ui/tab_video_clone.py` | 320 257 629 → 320 300 053 | `VideoCloneTab`, `_BatchItemWorker` |
| `qt_ui/tab_workflow.py` | 320 300 053 → 320 796 782 | `WorkflowTab`, `WorkflowGalleryTab` |
| `qt_ui/ui.py` | 320 796 782 → 320 943 761 | `MainWindow`, `_SidebarDelegate`, `TokenRefreshDialog` |

Lệnh tái lập (chạy trong `d:\TOOL_VIDEO\TOOL`):

```
python -X utf8 -c "import re,io,sys; sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8',errors='replace'); d=open('RUN_VEO_3_ULTRA_PROMAX.exe','rb').read(); seg=d[A:B]; pat=rb'(?:[\x20-\x7e]|[\xc2-\xdf][\x80-\xbf]|\xe0[\xa0-\xbf][\x80-\xbf]|[\xe1-\xef][\x80-\xbf][\x80-\xbf]|\xf0[\x90-\xbf][\x80-\xbf][\x80-\xbf]){3,220}'; s=[]; [s.append(t) for m in re.finditer(pat,seg) if (t:=m.group().decode('utf-8','ignore').strip()) and t not in s]; [print('|',t) for t in s]"
```

> **Sửa một fact đã cho:** nhãn tỷ lệ ở top-card là **`📏 Tỷ lệ`** — chuỗi `✏️ Tỷ lệ` **không tồn tại** trong exe (`count == 0`). Tương tự nhãn thời lượng top-card là `⏱ Thời lượng` (không có VS16); biến thể `⏱️ Thời lượng` chỉ xuất hiện trong card cấu hình Idea-to-Video.

---

## A. Cấu trúc điều hướng đã mined (nền chung cho 5 pill)

`MainWindow` dựng 5 nút top-nav (`objectName = "PlatformBtn"`, thuộc tính động `active`), một `QSplitter` ngang chứa `SidebarContainer` (232 px, `QListWidget#Sidebar` + `_SidebarDelegate` vẽ tay), và `QStackedWidget#ContentArea`.

**Bảng platform → sidebar (mined nguyên văn, gồm phụ đề tiếng Việt + màu accent):**

| Platform | Nhóm | Tiêu đề (EN) | Phụ đề (VI) | Icon key | Accent |
|---|---|---|---|---|---|
| `VEO3` | `VIDEO TOOLS` | `Text to Video` | `Tạo video từ prompt` | `edit` | `#7c3aed` |
| | | `Image to Video` | `Tạo video từ ảnh` | `image` | `#0ea5e9` |
| | | `Video Start-End` | `Làm video đầu - cuối` | `layers` | `#6366f1` |
| | | `Character Sync` | `Đồng nhất nhân vật` | `sync` | `#f59e0b` |
| | | `Idea to Video` | `Biến ý tưởng thành video` | `idea` | `#10b981` |
| | | `Phân tích video` | `Phân tích & lấy prompt video` | — | `#f43f5e` |
| | | `Phụ Đề & Xóa Logo` | `Tạo phụ đề & xóa watermark video` | `video` | `#ec4899` |
| | `IMAGE TOOLS` | `Text to Image` | `Tạo ảnh từ prompt` | `brush` | — |
| | | `Image to Image` | `Tạo ảnh từ ảnh tham chiếu` | — | `#f97316` |
| | | `Affiliate Sản Phẩm` | `Tạo ảnh quảng cáo sản phẩm` | `🛍️` | — |
| | | `UP SCALE IMAGE 4K` | `Nâng cấp chất lượng ảnh` | — | `#00f2fe` |
| | `SYSTEM` | `Settings` | `Cài đặt hệ thống` | `settings` | `#64748b` |
| | | `Cut & Merge Video` | `Cắt/Ghép video nhanh` | — | `#8b5cf6` |
| | | `User Guide` | `Hướng dẫn sử dụng` | `help-circle` | `#0891b2` |
| `GROK` | — | `Text to Video` | `Tạo Video Từ Prompt` | — | — |
| | | `Image to Video`, `Text to Image`, `Image to Image` | (dùng lại phụ đề VEO3) | — | — |
| | | `Settings` | `Cài đặt GROK` | — | — |
| `LOGS` | — | `Activity Log` | `Nhật ký hoạt động` | `list` | — |
| `WORKFLOW` | — | `Workflow Editor` | `Kết nối và chạy workflow` | — | — |
| `DONATE` | — | `Support Author` | `Ủng hộ phát triển tool` | `heart` | — |

> Mined được **14 mục sidebar VEO3**, không phải 16. Chênh lệch đến từ cách đếm: `ImageToVideoTab` chứa 2 sub-tab (`Tạo Video Từ Ảnh` / `Tạo video từ Ảnh Đầu - Ảnh Cuối`) nhưng chiếm 2 dòng sidebar riêng; `CreateImageTab` cũng chứa 2 sub-tab; `UpscaleVideoTab` và `RemoveWatermarkTab` tồn tại dưới dạng class nhưng **không có dòng sidebar riêng** (được nhúng trong `Phụ Đề & Xóa Logo` / `UP SCALE IMAGE 4K`). Dùng bảng trên làm chuẩn, đừng bịa thêm 2 dòng cho đủ 16.

**Header hàng trên (`SectionBoard#mode_header`) — mined:** `lbl_mode_icon` (nền `#334155`, chữ `#ffffff`, bo 6px) + `lbl_mode_title` (`color:#f8fafc; font-size:14px; font-weight:900`) + chip `Tên:` / `Tên: N/A` + chip `SĐT:` / `SĐT: N/A` + nút ` Zalo` (icon `icons/zalo.png`, nền `#0f172a`, chữ `#dbeafe`) + `_BellNotifyButton` (tooltip `Có bản cập nhật mới!`, badge `#ef4444`, chuông `#c9a227` khi có, `#6b7280` khi không).

**Top-card dùng chung cho mọi tab sinh nội dung — mined:**

| Nhãn | Loại | Tập chọn (label → data) | Mặc định (khoá config) |
|---|---|---|---|
| `Chọn dự án` | `QComboBox#CfgCombo` | từ `PROJECTS` | `CURRENT_PROJECT` = `default_project` |
| `📁 Xem kết quả` | `QPushButton#TopAction` | — | — |
| `📏 Tỷ lệ` | combo | `Dọc 9:16` → `9:16`; `Ngang 16:9` → `16:9` | `VIDEO_ASPECT_RATIO` = `9:16` |
| `⏱ Thời lượng` | combo | 4s/6s/8s (OMNI + 10s) | `VIDEO_DURATION_SECONDS` = `8` |
| `🎬 Model` | combo | Veo 3.1 - Fast / - Lite / - Lite [Lower Priority] / - Quality | `VEO_MODEL` = `Veo 3.1 - Lite [Lower Priority]` |
| `🎨 Model` | combo | `MODEL_OPTIONS` | `CREATE_IMAGE_MODEL` = `Nano Banana 2` |
| `🖼 Quality` | combo | `QUALITY_OPTIONS` | `CREATE_IMAGE_QUALITY` = `1k` |
| `📂 Thư mục lưu Video` | `_ClickPickLineEdit#CfgInput` | picker thư mục | `VIDEO_OUTPUT_DIR` |

**Footer (`QWidget#FooterBar`, `background-color:#0b0e14`) — mined:** pill `VPN : OFF` (`btn_status_vpn`) và pill `Profile_CAPTCHA` (`btn_status_profile`), cả hai `objectName="FooterPillBtn"`. Style ba trạng thái: mặc định `bg rgba(30,41,59,.5) / border rgba(255,255,255,.08) / color #94a3b8`; `[active="true"]` → `rgba(16,185,129,.15) / rgba(16,185,129,.4) / #10b981`; `[active="error"]` → `rgba(239,68,68,.15) / rgba(239,68,68,.45) / #ef4444`. Bên trái footer có `QPushButton#FooterAiChatBtn` nhãn `✦ AI Agent`, tooltip `Mở Workflow và bật/ẩn AI Agent`.

---

### Settings — Cài đặt hệ thống

**Mục đích:** Một trang chứa toàn bộ tham số vận hành của tool (nhịp gửi request, retry, dọn dữ liệu), thư mục media, tài khoản Google Flow + Gemini API key, và vòng đời các Chrome profile; mọi thay đổi auto-save và phát tín hiệu `configSaved` để các tab khác nạp lại.

**Bố cục:**

```
┌ ToolHeader: [⚙ magenta] Settings · Cài đặt hệ thống ─────────────────────────┐
├─ QGroupBox "⚙️ App Settings"  (title color #10b981, font-weight 800) ────────┤
│  ┌ QGridLayout 2 cột × 5 hàng (int_edit width 60, AlignCenter) ────────────┐ │
│  │ MULTI_VIDEO:      [ 4] │ CLEAR_DATA:          [ 5]                     │ │
│  │ WAIT_GEN_VIDEO:   [ 7] │ CLEAR_DATA_WAIT:     [ 4]                     │ │
│  │ WAIT_GEN_IMAGE:   [ 7] │ WAIT_RESEND_VIDEO:   [10]                     │ │
│  │ RETRY_WITH_ERROR: [ 3] │ TOKEN_REFRESH (phút):[55]                     │ │
│  │ CLEAR_DATA_IMAGE: [11] │                                               │ │
│  └───────────────────────────────────────────────────────────────────────┘ │
│  Run Captcha: [ V2 ▾]   ☑ Bỏ qua lỗi AUDIO                                  │
│  ☐ Luồng Tạo ảnh chỉ lấy ảnh được gọi tên trong prompt để tạo ảnh           │
│  Thư mục lưu media: [D:/TOOL_VIDEO/VIDEO_OUT............] [Chọn]            │
│  [💬 Mở Chrome Login ChatGPT]        [🗑️ Xóa Chrome GPT]                    │
├─ QGroupBox "👤 Tài khoản VEO3"  (QFormLayout) ──────────────────────────────┤
│  Tài Khoản: [USER..................]                                        │
│  Mật Khẩu:  [PASS••••••••] [👁]                                             │
│  Loại TK: --        Credit: --        [Kiểm tra Tài khoản]                  │
│  <lbl_credit_status>                                                        │
│  [ 🔑  AUTO Login TK Veo3 ]   (gradient 5 chặng)                            │
│  Gemini API Keys (mỗi dòng 1 key):                                          │
│  ┌ QPlainTextEdit#gemini_api_keys ─────────────────────────────────────┐    │
│  └──────────────────────────────────────────────────────────────────────┘    │
│  <b>Profile 1 (Tài khoản chính):</b>                                        │
│    [Mở Profile 1] [🌐 Mở Gemini] [Xóa Profile 1]                            │
│  <b>Profile TOKEN (Chuyên lấy token):</b>                                   │
│    ☐ Fix Recaptcha(403) / TK 0 credit                                       │
│    [Mở Profile TOKEN] [Xóa Profile TOKEN]                                   │
├─ AiProvidersSection  (mount component sẵn có) ──────────────────────────────┤
│                                          [ Lưu cài đặt ]  #Accent           │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Điều khiển:**

| Nhãn | Loại | Giá trị / tập chọn | Mặc định (khoá config) | Ghi chú |
|---|---|---|---|---|
| `MULTI_VIDEO:` | int_edit (QLineEdit + QIntValidator, w=60, AlignCenter) | ≥ 1 | `4` (`MULTI_VIDEO`) | 1 lần bấm = 4 clip = **4 lần trừ credit**. Hiện cảnh báo inline khi > 1. |
| `WAIT_GEN_VIDEO:` | int_edit | giây | `7` (`WAIT_GEN_VIDEO`) | Giãn cách giữa 2 request tạo video. |
| `WAIT_GEN_IMAGE:` | int_edit | giây | `7` (`WAIT_GEN_IMAGE`) | Giãn cách giữa 2 request tạo ảnh. |
| `RETRY_WITH_ERROR:` | int_edit | số lần | `3` (`RETRY_WITH_ERROR`) | Số lần thử lại khi request lỗi. |
| `CLEAR_DATA_IMAGE:` | int_edit | số job | `11` (`CLEAR_DATA_IMAGE`) | **Chỉ khoá này viết UPPER, không có alias thường.** |
| `CLEAR_DATA:` | int_edit | số job | `5` (`CLEAR_DATA`) | Sau N job thì dọn cache Chrome. |
| `CLEAR_DATA_WAIT:` | int_edit | giây | `4` (`CLEAR_DATA_WAIT`) | Chờ sau khi dọn. |
| `WAIT_RESEND_VIDEO:` | int_edit | giây | `10` (`WAIT_RESEND_VIDEO`) | Chờ trước khi gửi lại video lỗi. |
| `TOKEN_REFRESH (phút):` | int_edit | phút | `55` (`TOKEN_REFRESH_INTERVAL` / `token_refresh_interval`) | Google access_token hết hạn ~60′. |
| `Run Captcha:` | `QComboBox#SettingsCombo` | `V1`→`v1`, `V2`→`v2`, `V3`→`v3` | `V2` (`RUN_CAPTCHA` / `run_captcha`) | `_on_run_captcha_changed` reset `captcha_service`. |
| `Bỏ qua lỗi AUDIO` | `QCheckBox#SettingsCheckBox` | bool | `true` (`SKIP_AUDIO_ERROR` / `skip_audio_error`) | |
| `Luồng Tạo ảnh chỉ lấy ảnh được gọi tên trong prompt để tạo ảnh` | QCheckBox (chữ `#67e8f9`, indicator `#22d3ee`, checked `#0891b2`) | bool | `false` (`WORKFLOW_IMAGE_ONLY_NAMED_REFERENCES` / `workflow_image_only_named_references`) | Tooltip byte-exact: `Chỉ lọc khi toàn bộ ảnh tham chiếu nối vào Generate Image đều đến trực tiếp từ Upload Media và đã đặt Tên nhân vật. Nếu có một ảnh chưa đặt tên, workflow sẽ upload toàn bộ ảnh như cũ.` |
| `Thư mục lưu media:` | QLineEdit + `Chọn` | đường dẫn | `D:/TOOL_VIDEO/VIDEO_OUT` (`VIDEO_OUTPUT_DIR` / `video_output_dir`, ghi thêm alias `media_root_dir`) | Dialog title `Chọn thư mục gốc lưu media`; placeholder `Chọn thư mục gốc lưu media...` |
| `💬 Mở Chrome Login ChatGPT` | QPushButton, gradient `#10a37f → #0e8f6f` | — | — | Mở profile Chrome riêng cho ChatGPT. |
| `🗑️ Xóa Chrome GPT` | QPushButton, gradient `#ef4444 → #dc2626` | — | — | Xác nhận: `Bạn chắc chắn muốn xóa Profile ChatGPT?` |
| `Tài Khoản:` | QLineEdit (placeholder `USER`) | email | `""` (`veo3_user`) | **CREDENTIAL — xem quy tắc mask bên dưới.** |
| `Mật Khẩu:` | QLineEdit `EchoMode.Password` + nút mắt | text | `""` (`veo3_pass`) | **CREDENTIAL.** Có `_pw_hide_timer` (single-shot) tự ẩn lại sau khi bấm mắt. |
| `Loại TK: --` | QLabel `color:#3b82f6; font-weight:800; font-size:14px` | `ULTRA` / `PRO` / thường | `account1.TYPE_ACCOUNT` = `ULTRA` | Chỉ hiển thị. |
| `Credit: --` | QLabel `color:#10b981; font-weight:800; font-size:14px` | số | — (nạp qua `load_credits_from_config`) | |
| `Kiểm tra Tài khoản` | QPushButton (`#1e293b` / chữ `#10b981` / viền `#334155`) | — | — | Tooltip `Kiểm tra thông tin tài khoản và Credit`. |
| `🔑  AUTO Login TK Veo3` | QPushButton gradient 5 chặng `#f59e0b→#ef4444→#a855f7→#3b82f6→#10b981` | — | — | **Hai dấu cách sau 🔑** — giữ nguyên. |
| `Gemini API Keys (mỗi dòng 1 key):` | QPlainTextEdit | 1 key/dòng | file `data_general/gemini_api_key.txt` | **CREDENTIAL.** |
| `Fix Recaptcha(403) / TK 0 credit` | QCheckBox (viền `#f59e0b`, checked `#3b82f6`) | bool | `false` (`FIX_403_RECAPTCHA`) | Bật → hiện popup hướng dẫn (bên dưới) và đồng bộ `account1.folder_user_data_get_captcha`. |
| `Mở Profile 1` / `Xóa Profile 1` | QPushButton `#Warning` / `#Danger` | — | `account1.folder_user_data_get_captcha` | |
| `🌐 Mở Gemini` | QPushButton `#PrimaryButton` | — | mở `https://gemini.google.com/app` trong profile | |
| `Mở Profile TOKEN` / `Xóa Profile TOKEN` | QPushButton | — | `account1_token.folder_user_data_get_captcha`, `account1_token.URL_GEN_TOKEN` | |
| `Lưu cài đặt` | QPushButton `#Accent` | — | — | Ngoài auto-save; phát `configSaved`. |
| *(AI Providers)* | mount `AiProvidersSection` | claude / gemini / openai | `GET /api/llm/config` | Component sẵn có — **không viết lại**. Nó tự gọi `getLlmProviders`, `getLlmConfig`, `testLlmProvider`, `setLlmConfig`. |

**Bảng phân nhóm TOÀN BỘ khoá `data_general/config.json` (65 khoá) + chủ sở hữu UI:**

| Nhóm | Khoá | Mặc định | Tab sở hữu |
|---|---|---|---|
| **Chung** | `MULTI_VIDEO` | `4` | Settings |
| | `OUTPUT_COUNT` | `1` | Text to Video (top-card) |
| | `SEED_MODE` / `SEED_VALUE` | `Random` / `9797` | Text to Video |
| | `AUTO_UPSCALE` | `true` | top-card / UP SCALE |
| | `DOWNLOAD_MODE` | `"720"` | **không có control** — `SettingsTab._save` ghi cứng `"720"` |
| | `WM_MODE` | `"Không xóa"` | Phụ Đề & Xóa Logo |
| | `VIDEO_OUTPUT_DIR` / `video_output_dir` | `D:/TOOL_VIDEO/VIDEO_OUT` | Settings |
| | `offscreen_chrome` | `true` | không có control (runtime) |
| | `current_project` / `CURRENT_PROJECT` | `default_project` | top-card |
| | `projects` / `PROJECTS` | `["default_project"]` | top-card |
| | `WORKFLOW_IMAGE_ONLY_NAMED_REFERENCES` / lowercase | `false` | Settings |
| | `SKIP_AUDIO_ERROR` / `skip_audio_error` | `true` | Settings |
| **Thời gian chờ / retry** | `WAIT_GEN_VIDEO`, `WAIT_GEN_IMAGE` | `7`, `7` | Settings |
| | `RETRY_WITH_ERROR` | `3` | Settings |
| | `CLEAR_DATA`, `CLEAR_DATA_WAIT`, `CLEAR_DATA_IMAGE` | `5`, `4`, `11` | Settings |
| | `WAIT_RESEND_VIDEO` | `10` | Settings |
| | `TOKEN_REFRESH_INTERVAL` / `token_refresh_interval` | `55` | Settings |
| **Token / Tài khoản 🔒** | `account1.sessionId` | `""` | **MASK + REJECT WRITE** |
| | `account1.projectId` | `""` | **MASK + REJECT WRITE** |
| | `account1.access_token` | `""` | **MASK + REJECT WRITE** |
| | `account1.cookie` | `""` | **MASK + REJECT WRITE** |
| | `account1.TYPE_ACCOUNT` | `ULTRA` | read-only (hiển thị) |
| | `account1.folder_user_data_get_captcha` | `…\chrome_user_data\PROFILE_1` | Settings (đường dẫn, không phải secret) |
| | `account1.URL_GEN_TOKEN` | `https://labs.google/fx/vi/tools/flow` | Settings |
| | `account1_token.folder_user_data_get_captcha` / `.URL_GEN_TOKEN` | `""` | Settings |
| | `veo3_user` / `veo3_pass` | `""` | **MASK + REJECT WRITE** |
| | `FIX_403_RECAPTCHA` | `false` | Settings |
| | `RUN_CAPTCHA` / `run_captcha` | `v2` | Settings |
| | `TOKEN_OPTION` | `Option 2` | không có control trong SettingsTab |
| **Phụ đề `SUB_*`** | `SUB_FONT_FAMILY` | `Arial` | Phụ Đề & Xóa Logo |
| | `SUB_FONT_SIZE` | `24` | ″ |
| | `SUB_PRIMARY_COLOR` | `&H00FFFFFF` | ″ |
| | `SUB_OUTLINE_COLOR` | `&H00000000` | ″ |
| | `SUB_STYLE_TYPE` | `Standard SRT` | ″ |
| | `SUB_MARGIN_V` | `150` | ″ |
| | `SUB_OUTLINE_WIDTH` | `2.5` | ″ |
| | `SUB_SHADOW_VAL` | `0.0` | ″ |
| | `SUB_VIDEO_SOURCE_PATH` / `SUB_VIDEO_OUTPUT_PATH` | `""` | ″ |
| **Logo `LOGO_*`** | `LOGO_PATH` | `""` | Phụ Đề & Xóa Logo |
| | `LOGO_W` / `LOGO_H` | `115` / `52` | ″ |
| | `LOGO_X_LAND` / `LOGO_Y_LAND` | `1805` / `1028` | ″ (16:9) |
| | `LOGO_X_PORT` / `LOGO_Y_PORT` | `965` / `1868` | ″ (9:16) |
| **AI Providers** | *(không nằm trong config.json)* | — | `AiProvidersSection` → `secrets.json` backend |
| **Giọng đọc** | `VOICE_SYNC_ENABLED` | `false` | Character Sync / Idea to Video |
| | `VOICE_SYNC_ID` | `""` | ″ |
| **Idea to Video** | `IDEA_SCENE_COUNT` | `1` | Idea to Video |
| | `IDEA_STYLE` | `3d_Pixar` | ″ |
| | `IDEA_DIALOGUE_LANGUAGE` | `Tiếng Việt (vi-VN)` | ″ |
| | `IDEA_DIALOGUE_MODE` / `_VAL` | `🎭 Lời thoại nhân vật` / `dialogue` | ″ |
| | `IDEA_NO_DIALOGUE` | `false` | ″ |
| | `IDEA_BACKEND` | `🌐 Chrome Gemini` | ″ |
| | `IDEA_BG_MUSIC` | `🎶 Có nhạc nền` | ″ |
| | `IDEA_MODEL` | `Veo 3.1 - Lite [Lower Priority]` | ″ |
| **Video (top-card)** | `VIDEO_ASPECT_RATIO` | `9:16` | top-card |
| | `VIDEO_DURATION_SECONDS` | `8` | top-card |
| | `VEO_MODEL` | `Veo 3.1 - Lite [Lower Priority]` | top-card |
| | `VIDEO_RESOLUTION` | `480p` | top-card |
| | `CREATE_IMAGE_MODEL` / `CREATE_IMAGE_QUALITY` | `Nano Banana 2` / `1k` | top-card ảnh |
| **Grok 🔒** | `GROK_ACCOUNT_TYPE` | `SUPER` | Cài đặt GROK |
| | `GROK_VIDEO_LENGTH_SECONDS` | `6` | ″ |
| | `GROK_VIDEO_RESOLUTION` | `480p` | ″ |
| | `GROK_MULTI_VIDEO` | `5` | ″ |
| | `grok_account.type_account` / `.TYPE_ACCOUNT` | `SUPER` | ″ |

**QUY TẮC BẢO MẬT (bắt buộc, không thương lượng):**

Định nghĩa `CREDENTIAL_KEYS` ở backend (regex, không phải whitelist tay):

```
^(veo3_user|veo3_pass)$
^account1(_token)?\.(sessionId|projectId|access_token|cookie)$
.*_token$   |   ^token.*   |   ^license.*   |   ^grok_account\..*
gemini_api_key   |   gemini_api_keys
```

- **Đọc (`GET /api/settings/config`)**: mọi khoá khớp → giá trị bị thay bằng `null`, kèm khoá song song `"<key>__configured": true|false`. Không bao giờ trả về 4 ký tự cuối, không trả về độ dài, không trả về hash. Nếu file `gemini_api_key.txt` có N dòng → trả `{"geminiKeyCount": N}`, không trả nội dung.
- **Ghi (`PUT /api/settings/config`)**: nếu body chứa bất kỳ khoá nào khớp → **HTTP 400** `{"detail":"credential keys are not writable through this endpoint"}`. Không âm thầm bỏ qua — âm thầm bỏ qua sẽ khiến user tưởng đã lưu.
- Gemini key đi qua endpoint chuyên dụng riêng `PUT /api/settings/gemini-key` (body `{apiKey: string|null}`, `null` = xoá), theo đúng khuôn `setLlmApiKey` đã có.
- `veo3_user` / `veo3_pass` **không có endpoint ghi nào cả**. Xem "Cạm bẫy".

**Luồng:**

1. Mount → `GET /api/settings/config` **(chưa tồn tại)** → nạp toàn bộ ô số, combo, checkbox, đường dẫn; ô credential hiển thị placeholder `••••••••` khi `__configured === true`, rỗng khi `false`.
2. Song song: `GET /api/postprod/status` để biết `assetRoot` có tồn tại không, ffmpeg/upscale có sẵn không → hiển thị strip cảnh báo nếu thiếu.
3. `AiProvidersSection` tự chạy vòng đời riêng (`getLlmProviders` → `getLlmConfig` → user chọn card → `testLlmProvider` → `setLlmConfig`).
4. Mọi widget đổi giá trị → debounce 600 ms (bản gốc dùng `QTimer` single-shot qua `_schedule_auto_save`) → `PUT /api/settings/config` với **chỉ delta**. Server trả bản config đã merge; store Zustand thay thế toàn bộ.
5. `Lưu cài đặt` → flush debounce ngay lập tức → toast `Đã lưu App Settings.` (title `Thông báo`) hoặc `Không lưu được cấu hình: <lỗi>` (title `Lỗi`).
6. `Kiểm tra Tài khoản` → `GET /api/settings/credits` **(chưa tồn tại)** → cập nhật `Loại TK:` + `Credit:` + `lbl_credit_status`. Chuỗi trạng thái mined: `Đang check...`, `Đang kiểm tra...`, `🔍 Đang kiểm tra trạng thái tài khoản và credit VEO3...`, `Chờ login`, `Chưa có token`, `✅ Kiểm tra tài khoản thành công: Loại TK = …`, `❌ Kiểm tra tài khoản thất bại (Mã: …)`, `Chỉ cập nhật lại sau khi auto login thành công.`
7. `🔑  AUTO Login TK Veo3` → **thay thế hoàn toàn**: không hỏi mật khẩu, gọi `POST /api/settings/profiles/PROFILE_1/open` **(chưa tồn tại)** → mở Chrome persistent-profile tới `https://labs.google/fx/vi/tools/flow`, hiện modal chờ với log stream. Khi extension bridge báo có session (`getAuthMe`) → đóng modal, refresh credits.
8. `Mở Profile 1` / `Mở Profile TOKEN` / `💬 Mở Chrome Login ChatGPT` → cùng endpoint, khác `profile` param.
9. `Xóa Profile …` → `DELETE /api/settings/profiles/{name}` **(chưa tồn tại)** sau confirm dialog. Chuỗi mined: `Bạn chắc chắn muốn xóa Profile 1?`, `Bạn chắc chắn muốn xóa Profile TOKEN?\n(Chrome đang chạy với profile này có thể bị tắt)`, `Bạn chắc chắn muốn xóa Profile ChatGPT?`; thành công → `Đã xóa Profile 1.` / `Đã xóa Profile TOKEN thành công.` / `Đã xóa Profile ChatGPT thành công.`
10. Bật `Fix Recaptcha(403) / TK 0 credit` → mở dialog `Hướng dẫn Fix Captcha` với thân bài byte-exact:
    ```
    Khi bạn chọn chức năng Fix Captcha này cần làm đúng các bước sau:
    B1: Bấm nút mở profile TOKEN
    B2: bấm vào 'Create with Google Flow' Sau đó đăng nhập bằng 1 mail chính chủ. không phải gmail đi mua
    B3: Bấm vào Nút Tạo Dự Án Mới
    B4: Quay lại popup của tool. Bấm Lưu LINK project, sau đó tắt trình duyệt và sử dụng bình thường
    ```
    Nút `Ok` / `Cancel`; bấm Cancel → checkbox revert (bản gốc `blockSignals` rồi `setChecked(False)`).

**Kết quả:** Không có JobList. Phản hồi là: toast lưu, hai chip `Loại TK:` / `Credit:` cập nhật tại chỗ, dòng `lbl_credit_status` màu `#cbd5e1`, và modal Chrome-profile với vùng log `background:#1e1e1e; color:#dcdcdc; border:1px solid #333` (nguyên style mined), có nút `Dừng`.

**Cạm bẫy:**

- **Khoá trùng hoa/thường.** `config.json` chứa cả `RUN_CAPTCHA` lẫn `run_captcha`, `SKIP_AUDIO_ERROR` lẫn `skip_audio_error`, `TOKEN_REFRESH_INTERVAL` lẫn `token_refresh_interval`, `VIDEO_OUTPUT_DIR` lẫn `video_output_dir`, `CURRENT_PROJECT` lẫn `current_project`, `PROJECTS` lẫn `projects`, `WORKFLOW_IMAGE_ONLY_NAMED_REFERENCES` lẫn lowercase. **Ghi thiếu một vế = giá trị cũ vẫn được đọc và người dùng tưởng đã đổi.** Backend phải ghi **cả hai vế** khi khoá gốc có alias. `CLEAR_DATA_IMAGE` là ngoại lệ duy nhất — chỉ có bản UPPER.
- **`veo3_pass` là mật khẩu Google thật.** Bản gốc lưu plaintext trong `config.json` và dùng nó để tự động gõ vào form đăng nhập Google — đó là con đường nhanh nhất tới khoá tài khoản vì "đăng nhập bất thường". Bản replica **không được có ô này ở dạng ghi được**. Giữ nhãn `Tài Khoản:` / `Mật Khẩu:` chỉ ở chế độ hiển thị-đã-cấu-hình (read-only, luôn `••••`), và thay `🔑  AUTO Login TK Veo3` bằng luồng Chrome profile bền vững. Nếu sản phẩm quyết định bỏ hẳn hai ô này thì phải nói rõ trong UI, đừng để ô trống nhìn như đang chờ nhập.
- **`DOWNLOAD_MODE` bị ghi cứng.** `SettingsTab._save` ghi `download_mode = "720"` mà **không có control nào**. Nếu ta phơi selector chất lượng tải xuống ở tab khác thì Settings sẽ âm thầm ghi đè về `720` mỗi lần lưu. Hoặc bỏ khoá này khỏi payload của Settings, hoặc thêm control thật.
- **`MULTI_VIDEO` = 4 nghĩa là mỗi cú bấm trừ 4 credit.** Không được để nó là ô số trần trụi. Phải có dòng hậu quả ngay dưới ô: `1 lần chạy = N clip = N lần trừ credit`.
- **`TOKEN_REFRESH (phút)` = 55 nhưng token Google sống ~60 phút.** Cho phép nhập > 60 sẽ khiến job dài chết giữa chừng với lỗi 401 khó truy. Clamp max 59 và nói lý do.
- **`Run Captcha` V1/V2/V3 không có nghĩa nào lộ ra ngoài.** Bản gốc dùng nó để chọn service giải captcha. Bản local đã bỏ license server và có thể không có service nào → nếu không nối được, ẩn hẳn control thay vì để dropdown chết.
- **`Chọn thư mục gốc lưu media`** — trình duyệt **không** mở được file-picker thư mục trả về đường dẫn tuyệt đối. Phải là ô text + nút `Chọn` gọi endpoint backend mở native dialog, hoặc chấp nhận nhập tay và validate qua backend. Đừng dùng `<input type="file" webkitdirectory>` — nó trả tên file chứ không trả path.
- `AiProvidersSection` áp **một provider cho cả 3 feature** (auto_prompt / vision / planner) theo thiết kế đã chốt. Đừng "sửa" thành 3 dropdown riêng: backend vẫn hỗ trợ granular nhưng UI cố tình ràng buộc lại.
- Endpoint credits gốc gọi `https://aisandbox-pa.googleapis.com/v1/credits?key=[redacted]` với API key nhúng cứng trong exe. **Không copy key đó.** Credits phải lấy qua phiên đăng nhập của chính người dùng (extension bridge), nếu không lấy được thì hiện `Chưa có token` chứ không dùng key của tác giả.

---

### Cut & Merge Video — Cắt/Ghép video nhanh

**Mục đích:** Hai thao tác ffmpeg thuần local, không đụng Flow, không tốn credit: chẻ một video dài thành nhiều đoạn đều nhau, và nối nhiều clip ngắn thành một file. Bản replica mở rộng thêm 2 card hậu kỳ (nhạc nền, lồng tiếng) vốn có backend sẵn.

**Bố cục:**

```
┌ ToolHeader: [✂ magenta] Cut & Merge Video · Cắt/Ghép video nhanh ───────────┐
├──────────────── QSplitter ngang (sizes ~ 62 / 38) ──────────────────────────┤
│ TRÁI  QTabWidget#main_tabs                    │ PHẢI                        │
│ ┌───────────────┬────────────────┐            │ ┌ 🔍 Xem Thử Trực Quan ───┐ │
│ │🎬 Cắt Video Dài│🔗 Nối Video Ngắn│            │ │  <canvas #090d16>       │ │
│ ├───────────────┴────────────────┤            │ │  Chưa nạp video mẫu     │ │
│ │ ▸ 🎥 Chọn Video Muốn Cắt        │            │ │  (Click chọn video      │ │
│ │   [đường dẫn...      ][Chọn File]│           │ │   trong danh sách để    │ │
│ │ ▸ ⚙️ Cấu Hình Cắt Video         │            │ │   xem thử)              │ │
│ │   Độ dài muốn cắt (giây): [30 s]│            │ └─────────────────────────┘ │
│ │   Thư mục lưu: [...][Chọn Thư Mục]│          │ ──────●────────────         │
│ │ ▸ 📦 Danh Sách Video Sau Khi Cắt│            │            00:00 / 00:00    │
│ │   [ list ]                      │            │                             │
│ └────────────────────────────────┘            │                             │
│ ┌ 📊 Nhật Ký & Điều Khiển ────────┐            │                             │
│ │ [ log ]                         │            │                             │
│ │ [▓▓▓▓░░░░ progress]             │            │                             │
│ │ [🚀 Bắt Đầu Xử Lý] [🛑 Dừng]    │            │                             │
│ └────────────────────────────────┘            │                             │
├────────── MỞ RỘNG (không có trong exe gốc, backend đã sẵn) ─────────────────┤
│ ┌ 🎶 Nhạc nền ─────────────────┐ ┌ 🗣️ Giọng đọc ─────────────────────────┐ │
│ │ Track ▾ (9 file nhac_nen)   │ │ Giọng ▾ (30) · Persona ▾ (16)         │ │
│ │ 🔊 Âm lượng video gốc: ──●── │ │ Kịch bản [textarea]                    │ │
│ │ 🔊 Âm lượng Voice:     ──●── │ │ [Nghe thử giọng mẫu] [Tạo lồng tiếng]  │ │
│ └─────────────────────────────┘ └────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────────┘
```

Tab `🔗 Nối Video Ngắn` thay panel trái bằng:

```
┌ 🔗 Danh Sách Video Muốn Ghép ──────────────────────────────────┐
│ [Thêm Video][Xóa][Xóa Hết]  [▲ Lên][▼ Xuống]                  │
│ ┌ QListWidget (tooltip: Sắp xếp danh sách video theo thứ tự  │
│ │  từ trên xuống dưới để ghép nối) ─────────────────────────┐ │
│ └──────────────────────────────────────────────────────────┘ │
├ ⚙️ Cấu Hình Ghép Video ────────────────────────────────────────┤
│ File kết quả: [....................] [Chọn Nơi Lưu]           │
└───────────────────────────────────────────────────────────────┘
```

**Điều khiển:**

| Nhãn | Loại | Giá trị / tập chọn | Mặc định (khoá config) | Ghi chú |
|---|---|---|---|---|
| `🎬 Cắt Video Dài` / `🔗 Nối Video Ngắn` | Tab (Zustand `cutMergeTab`) | `cut` / `merge` | `cut` | Không dùng router. |
| `🎥 Chọn Video Muốn Cắt` | SectionHeader (QGroupBox) | — | — | |
| *(ô đường dẫn nguồn)* | QLineEdit **readOnly** | path | `""` (`cut_video_source_path`) | Placeholder `Đường dẫn file video gốc...` |
| `Chọn File` | GhostButton `#Warning` | — | — | Dialog `Chọn Video Muốn Cắt`, filter `Video Files (*.mp4 *.avi *.mkv *.mov)` |
| `⚙️ Cấu Hình Cắt Video` | SectionHeader | — | — | |
| `Độ dài muốn cắt (giây):` | QSpinBox, suffix ` s`, width 80 | ≥ 1 | `cut_video_duration` — **không có trong config.json đã ship**; giá trị số không mine được từ blob chuỗi | Xem "Câu hỏi mở". |
| `Thư mục lưu:` | QLineEdit + button | path | `""` (`cut_video_output_dir`) | Placeholder `Mặc định lưu cùng thư mục video gốc...`; button `Chọn Thư Mục`, dialog `Chọn Thư Mục Lưu Video Cắt` |
| `📦 Danh Sách Video Sau Khi Cắt` | List kết quả | — | — | Tooltip `Click đúp vào tệp để xem thử trên trình phát bên phải` |
| `🔗 Danh Sách Video Muốn Ghép` | SectionHeader | — | — | |
| `Thêm Video` | GhostButton | — | — | Dialog `Chọn File Video Để Ghép` (multi-select) |
| `Xóa` | GhostButton `#DangerSoft` | — | — | Xoá dòng đang chọn |
| `Xóa Hết` | GhostButton `#DangerSoft` | — | — | Log `🗑️ Đã xóa sạch danh sách video ghép.` |
| `▲ Lên` / `▼ Xuống` | GhostButton | — | — | Đổi thứ tự nối |
| `⚙️ Cấu Hình Ghép Video` | SectionHeader | — | — | |
| `File kết quả:` | QLineEdit + button | path `.mp4` | `""` (`merge_video_output_path`) | Placeholder `Đường dẫn file video kết quả sau khi ghép (.mp4)...`; button `Chọn Nơi Lưu`, dialog `Chọn Nơi Lưu Video Ghép`, filter `Video Files (*.mp4)`, tên gợi ý `video_ghep_ket_qua.mp4` |
| `📊 Nhật Ký & Điều Khiển` | SectionHeader | — | — | |
| `🚀 Bắt Đầu Xử Lý` | PrimaryButton `#Accent` | — | — | Chạy tác vụ của tab đang mở |
| `🛑 Dừng` | StopButton `#Danger` | — | — | Ban đầu `setEnabled(False)` |
| `🔍 Xem Thử Trực Quan` | SectionHeader | — | — | |
| *(thanh thời gian)* | QSlider + QLabel | — | `00:00 / 00:00` | Format `%02d:%02d` |
| **MỞ RỘNG** `🎶 Nhạc nền` | SectionHeader | — | — | Nhãn mined từ node workflow, không phải từ `CutMergeVideoTab` |
| `🎵 Chọn file nhạc nền...` | LabeledSelect + FilePickerButton | 9 track trong `nhac_nen/` + file ngoài | `Không nhạc nền` | Danh sách lấy từ `GET /api/postprod/status → bgm[]`: `1. Nhạc.MP3`, `2. chuẩn.MP3`, `3. đạo lý.MP3`, `4. đạo lý.MP3`, `5. buồn bản quyền.MP3`, `6. tâm trạng.MP3`, `7. tình cảm buòn.MP3`, `8. tâm trạng.MP3`, `9. tâm trạng.MP3` |
| `🔊 Âm lượng video gốc:` | Slider 0–1 | 0.0 – 1.0 | `1.0` (`origVolume` của `POST /api/postprod/bgm`) | |
| `🔊 Âm lượng Voice:` | Slider 0–1 | 0.0 – 1.0 | `0.3` (`bgmVolume`) | Nhãn gốc dành cho voice; với BGM dùng `bgmVolume` |
| **MỞ RỘNG** `🗣️ Giọng đọc` | SectionHeader | — | — | |
| `Chọn giọng nói` | LabeledSelect | 30 voice Gemini (khớp `voice/*.wav`) | `Kore` (`tts.DEFAULT_VOICE`) | `Zephyr, Puck, Charon, Kore, Fenrir, Leda, Orus, Aoede, Callirrhoe, Autonoe, Enceladus, Iapetus, Umbriel, Algieba, Despina, Erinome, Algenib, Rasalgethi, Laomedeia, Achernar, Alnilam, Schedar, Gacrux, Pulcherrima, Achird, Zubenelgenubi, Vindemiatrix, Sadachbia, Sadaltager, Sulafat` |
| *(persona)* | LabeledSelect | 16 mục `title` trong `voice_styles.json` | `Không chọn giọng đọc` | vd `Giọng Nam Miền Bắc Trầm Triết Lý`, `Giọng Thiền Sư Miền Bắc Tĩnh Tại`, … |
| `Giọng tùy chỉnh (Nhập prompt riêng)` | option cuối + textarea | free text | — | Map sang `persona` free-text của `/narrate` |
| `Nghe thử giọng mẫu` | GhostButton | — | — | Phát `voice/{name}.wav` — **cần endpoint mới** |
| `Thời gian cộng thêm:` | NumberField (giây) | ≥ 0 | `0` | Cho `fit-narration` |

**Luồng:**

*Cắt:*
1. `Chọn File` → path nguồn (backend native dialog hoặc upload). Load preview.
2. `🚀 Bắt Đầu Xử Lý` → validate: rỗng → `Cảnh báo` / `Vui lòng chọn video nguồn muốn cắt.`
3. `POST /api/postprod/cut` **(CHƯA TỒN TẠI — xem newEndpoints)** với `{source, segmentSeconds, outputDir}`.
4. Backend `ffprobe` lấy thời lượng → `numSegments = ceil(duration / segmentSeconds)` → mỗi đoạn chạy
   `ffmpeg -y -ss {t:.3f} -t {d:.3f} -i <src> -c:v libx264 -crf 18 -preset fast -c:a copy <out>`;
   nếu `returncode != 0` → fallback `-c copy` (log `⚠️ Cắt có mã hóa lại lỗi. Đang thử bằng chế độ copy stream nhanh...`).
5. Log stream (SSE hoặc poll) hiển thị byte-exact: `🎬 Bắt đầu phân tích video: …`, `⏱️ Thời lượng video: {d:.2f} giây.`, `📏 Cấu hình cắt mỗi đoạn: …`, `📦 Dự kiến cắt thành N đoạn video ngắn.`, `⏳ Đang cắt đoạn i/N: (từ {a:.1f}s đến {b:.1f}s)...`, `🎉 Hoàn thành! Đã cắt video gốc thành N video ngắn thành công.`
6. Hoàn tất → dialog `Thành công` / `Đã hoàn thành cắt video thành N đoạn ngắn.`; danh sách `📦 Danh Sách Video Sau Khi Cắt` được điền.

*Ghép:*
1. `Thêm Video` (multi-select) → list; `▲ Lên` / `▼ Xuống` sắp thứ tự.
2. Validate: `< 1` → `Vui lòng thêm các tệp video ngắn để ghép.`; `< 2` → `Cần có ít nhất 2 video để thực hiện ghép nối.`; thiếu output → `Vui lòng chọn đường dẫn tệp tin kết quả sau khi ghép.`
3. `POST /api/postprod/concat` **(đã có)** với `{clips: string[], output: string, width, height, fps}`.
   Backend hiện tại **luôn normalize** mọi clip (`postprod.normalize` → codec/size/fps chung) rồi mới `concat`. Đây là hành vi tốt hơn bản gốc (bản gốc thử `-f concat -safe 0 -c copy` trước rồi mới fallback `filter_complex concat … -crf 20 -c:a aac`).
4. Kết quả: `🎉 Ghép nối thành công! Video kết quả: <path>` → dialog `Thành công` / `Ghép nối video thành công!\nĐã lưu: <path>`; lỗi → `Lỗi` / `Ghép nối video thất bại.`

*Nhạc nền (mở rộng):*
1. `GET /api/postprod/status` → `bgm[]` (tên file) + `ffmpegAvailable`. Nếu `ffmpegAvailable === false` → **ẩn cả tab**, hiện `EmptyState` giải thích, không hiện nút chạy.
2. Chọn track + 2 slider → `POST /api/postprod/bgm` `{video, output, track, bgmVolume, origVolume, fadeIn, fadeOut}`.
3. Backend `assets.bgm_path(track)` resolve trong `nhac_nen/`; nếu không khớp → coi là đường dẫn tuyệt đối và validate qua `_existing`.
4. **Suno**: chưa có provider, chưa có key → **không render card Suno**. Chỉ hiện khi `postprod/status` báo có key (trường mới `sunoKeyAvailable`, chưa tồn tại).

*Lồng tiếng (mở rộng):*
1. `GET /api/postprod/status` → `ttsKeyAvailable`, `voices[]`. Nếu `ttsKeyAvailable === false` → card ở trạng thái disabled với link tới tab Settings (`Gemini API Keys (mỗi dòng 1 key):`).
2. `POST /api/postprod/narrate` `{text, voice, persona, output?}` → trả `{path, voice, durationSeconds}`.
3. Ghép narration vào video: `POST /api/postprod/fit-narration` `{video, audio, output}` (cắt video khớp độ dài narration).

**Kết quả:** Không dùng `JobList` chung — tab này chạy đồng bộ, dài, và có preview. Hiển thị: `ProgressBar` xác định (%) + vùng log monospace (`Consolas`) + danh sách file kết quả có thể click để nạp vào `SimpleVideoPreviewCanvas` bên phải. Canvas rỗng vẽ text `Chưa nạp video mẫu` + dòng 2 `(Click chọn video trong danh sách để xem thử)` trên nền `#090d16`, viền `#1f2937`, chữ `#4b5563`.

**Cạm bẫy:**

- **`POST /api/postprod/cut` chưa tồn tại.** Toàn bộ nửa "Cắt" của tab này không có backend. Đây là blocker cứng, không phải nice-to-have.
- **Không có route phục vụ file.** `postprod` ghi kết quả vào `storage/renders/` nhưng `main.py` **không mount StaticFiles nào**. `<video src>` sẽ 404 với mọi `path` trả về. Preview, nghe thử nhạc nền, và nghe thử `voice/*.wav` đều chết cho tới khi có `GET /api/files`.
- **`_existing()` chặn input ngoài `STORAGE_DIR` và `ASSET_ROOT`.** Người dùng chọn video ở `D:\Videos\` → mọi endpoint postprod trả **400** `"… is outside the allowed folders"`. Hoặc UI phải upload file vào storage trước, hoặc phải set `FLOWBOARD_INPUT_ROOTS`. Nếu UI không xử lý, user sẽ thấy lỗi 400 khó hiểu sau khi chọn file thành công.
- **`concat` luôn re-encode.** Backend hiện tại normalize mọi clip → chậm hơn nhiều so với stream-copy của bản gốc với các clip cùng codec. Với 20 clip 8s thì khác biệt là vài giây so với vài phút. Cần progress thật, không phải spinner vô định.
- **Tên file nhạc nền có dấu tiếng Việt và dấu cách** (`3. đạo lý.MP3`). Phải encode khi đưa vào URL; và **hai cặp trùng tên hiển thị** (`3.`/`4. đạo lý`, `6.`/`8.`/`9. tâm trạng`) → dropdown phải hiện cả số thứ tự, nếu chỉ hiện phần chữ thì 3 mục sẽ trông y hệt nhau.
- **`🔊 Âm lượng Voice:` là nhãn mượn.** Trong exe nó thuộc node voice của Workflow, không thuộc tab này. Với card BGM, nhãn đúng ngữ nghĩa là volume của nhạc nền. Nếu dùng lại nhãn gốc thì người dùng sẽ chỉnh nhầm. Khuyến nghị: giữ `🔊 Âm lượng video gốc:` cho `origVolume`, và đặt nhãn mới rõ ràng cho `bgmVolume` thay vì mượn `🔊 Âm lượng Voice:`.
- **`Độ dài muốn cắt (giây)` không có giá trị mặc định mine được.** Hằng số nguyên nằm trong blob nhị phân của Nuitka, không nằm trong blob chuỗi. Đừng bịa một con số rồi ghi "mặc định theo config" — khoá `cut_video_duration` cũng không có trong `config.json` đã ship.
- **4 khoá config của tab này (`cut_video_source_path`, `cut_video_output_dir`, `cut_video_duration`, `merge_video_output_path`) chỉ tồn tại sau lần lưu đầu tiên.** Loader phải chịu được `undefined` cho cả bốn.

---

### Phân tích video — Phân tích & lấy prompt video

> Tab này chính là **Video Clone** (`qt_ui/tab_video_clone.py`, class `VideoCloneTab`). Nhãn sidebar byte-exact là `Phân tích video`, phụ đề `Phân tích & lấy prompt video`. Chuỗi `Video Clone` **không xuất hiện** trong sidebar.

**Mục đích:** Dán link mạng xã hội (hoặc chọn file local) → tải video về → Gemini phân tích → sinh ra bộ prompt ảnh + prompt video Veo 3 + kịch bản + nhân vật + nội dung social, để tái tạo lại video đó.

**Bố cục:**

```
┌ ToolHeader ─────────────────────────────────────────────────────────────────┐
│ [🎞 magenta]  Phân Tích Video & Lấy Prompt VEO 3                            │
│              — Tải video mẫu, AI phân tích và tạo prompt tái tạo video.     │
│                        [+ Tạo Dự Án mới] [dự án ▾] [📂 Mở Folder] [🗑 Xóa]  │
├─────────────────────────────────────────────────────────────────────────────┤
│ ⚠️  BANNER RỦI RO TÀI KHOẢN  (bắt buộc, không đóng được ở lần đầu)          │
├─ Card A: nguồn video ───────────────────────────────────────────────────────┤
│ 🎬 Video (mỗi dòng 1 link):   [📥 Import Excel][📊 Xem kết quả]              │
│                               [📂 Chọn file][✕ Xóa]                         │
│ ┌ textarea ──────────────────────────────────────────────────────────────┐ │
│ │ Dán link Facebook / YouTube / TikTok hoặc đường dẫn file video local... │ │
│ │ Mỗi dòng 1 link. Ví dụ:                                                │ │
│ │ https://www.youtube.com/shorts/abc123                                  │ │
│ │ https://www.tiktok.com/@user/video/456                                 │ │
│ │ D:\Videos\my_video.mp4                                                 │ │
│ └────────────────────────────────────────────────────────────────────────┘ │
│ 💡 Hỗ trợ: Link Facebook Reels/Watch, YouTube, TikTok, hoặc file video      │
│    trên máy (.mp4, .avi, .mov, .mkv, .webm)                                 │
│ [🔒 Chế độ Import Excel — Bấm nút ✖ để hủy và nhập link thủ công][✕ Xóa Excel]│
├─ Card B: ✨ Tuỳ chỉnh nâng cao (Tuỳ chọn)   (QFrame#CfgGroup) ──────────────┤
│ Ngôn ngữ viết prompt [Tiếng Việt ▾]   Số luồng [1]   Ảnh tham chiếu [KHÔNG ▾]│
│ ☐ 🔒 Khóa số cảnh theo thời gian                                            │
│ 🤖 Gemini Model [⚡ API — gemini-3.6-flash ▾]      [ thời lượng ]           │
│ Giọng đọc [.......▾][Thêm][Xóa]    🎨 Phong cách [.......▾][Thêm][Xóa]      │
│ (khi Giọng = tùy chỉnh) [Nhập mô tả chi tiết giọng đọc của bạn...]          │
│ (khi Ảnh tham chiếu = CÓ)                                                   │
│   [📷 Chọn ảnh nhân vật]  Chưa chọn ảnh nào   [✕ Xóa tất cả]                │
│ ✨ Yêu cầu tùy chỉnh (Ưu tiên cao nhất - ghi đè các cài đặt khác nếu xung đột)│
│ ┌ textarea ──────────────────────────────────────────────────────────────┐ │
│ └────────────────────────────────────────────────────────────────────────┘ │
│              [ 🔍  Phân Tích & Lấy Prompt ]        [ ⛔  Dừng ]             │
├─ 📊  Kết quả Phân tích  (#ResultFrame) ─────────────────────────────────────┤
│                                   [🖼 Copy Prompt Ảnh][🎬 Copy Prompt Video] │
│ 👤 Nhân vật & 📝 Kịch bản   [readonly]                                      │
│ 🖼  Image Prompts            [readonly]                                     │
│ 🎬  Video Prompts (Veo 3)    [readonly]                                     │
├─ 📱  Social Media Content  (#SocialFrame)   [📋 Sao chép] ──────────────────┤
├─ <status> ▓▓▓▓░░░ <error> ─────────────────────────────────────────────────┤
├─ 📋 Log tiến trình  (#LogPanel, Consolas, viền rgba(0,242,254,.15)) ────────┤
└─────────────────────────────────────────────────────────────────────────────┘
```

**Điều khiển:**

| Nhãn | Loại | Giá trị / tập chọn | Mặc định (khoá config) | Ghi chú |
|---|---|---|---|---|
| `+ Tạo Dự Án mới` | PrimaryButton | — | — | Dialog `Tạo Dự Án Mới` / `Nhập tên dự án mới` / hint `Dữ liệu sẽ được lưu theo cấu trúc: thư mục gốc / tên dự án / data, video_goc` / placeholder `Ví dụ: QuangCao_TraSua_03` |
| *(dropdown dự án)* | `_NoScrollComboBox` (`FocusPolicy.StrongFocus`) | thư mục con của `downloads/clone_projects/` | `default_project` (`data_clone.last_project`) | Không cuộn bằng bánh xe — cố ý |
| `📂 Mở Folder` | GhostButton | — | — | |
| `🗑 Xóa` | GhostButton `#Danger` | — | — | Confirm `Xóa Dự Án` / `Bạn có chắc chắn muốn xóa dự án "<tên>"?\nToàn bộ dữ liệu (prompts, video gốc) sẽ bị xóa.` |
| `🎬 Video (mỗi dòng 1 link):` | Label | — | — | |
| *(textarea link)* | QPlainTextEdit | 1 link/dòng | `""` (`data_clone.video_links`) | Placeholder 5 dòng byte-exact (xem sketch) |
| `📥 Import Excel` | GhostButton | `.xlsx` / `.csv` | — | Dialog `Chọn file Excel hoặc CSV`; mẫu ở `data_general/excel_mau_batch.xlsx` |
| `📊 Xem kết quả` | GhostButton | — | — | Mở file CSV/Excel kết quả |
| `📂 Chọn file` | FilePickerButton (multi) | `.mp4 .avi .mov .mkv .webm` | — | Dialog `Chọn Video`, filter `Video Files (*.mp4 *.avi *.mov *.mkv *.webm);;All Files (*)` |
| `✕ Xóa` | GhostButton | — | — | Xoá textarea |
| `✕ Xóa Excel` | GhostButton | — | — | Tooltip `Hủy Import Excel`; chỉ hiện khi đang ở chế độ Excel |
| `Ngôn ngữ viết prompt` | LabeledSelect | `Tiếng Việt`→`vi`, `English`→`en` | `Tiếng Việt` (`data_clone.language` = `"vi"`) | |
| `Số luồng` | NumberField | ≥ 1 | `1` (`data_clone.threads`) | Song song hoá `_spawn_worker` |
| `Ảnh tham chiếu` | LabeledSelect | `KHÔNG`→`no`, `CÓ`→`yes` | `KHÔNG` (`data_clone.ref_image_enabled` = `"no"`) | `CÓ` → hiện `_ref_image_container` |
| `🔒 Khóa số cảnh theo thời gian` | Checkbox | bool | `false` (`data_clone.lock_scene`) | Tooltip byte-exact 2 dòng: `Bật: Số cảnh = ceil(thời lượng / 8s), cố định theo thời lượng video.` / `Tắt: AI tự quyết định số cảnh dựa trên nội dung video.` |
| `🤖 Gemini Model` | LabeledSelect | `⚡ API — gemini-3.5-flash`→`gemini-3.5-flash`; `⚡ API — gemini-3.6-flash`→`gemini-3.6-flash`; `⚡ API — gemini-3.5-flash-lite`→`gemini-3.5-flash-lite`; `🧪 API — gemini-3-flash-preview`→`gemini-3-flash-preview`; `⚡ API — gemini-2.5-flash`→`gemini-2.5-flash`; `💡 API — gemini-3.1-flash-lite`→`gemini-3.1-flash-lite`; `🌐 Chrome Gemini (trình duyệt)`→`chrome_gemini` | `⚡ API — gemini-3.6-flash` (`data_clone.gemini_model` = `"gemini-3.6-flash"`) | `chrome_gemini` = chạy qua Chrome profile, **không cần API key** |
| *(thời lượng mục tiêu)* | QLineEdit | giây, để trống = auto | `""` (`data_clone.duration`) | |
| `Giọng đọc` | LabeledSelect + `Thêm` + `Xóa` | thư viện `style_voice_manager` + `Không chọn giọng đọc` + `Giọng tùy chỉnh (Nhập prompt riêng)`→`CUSTOM` | index `0` (`data_clone.voice_index`) | Tooltip nút: `Thêm giọng đọc` / `Xóa giọng đọc` |
| *(mô tả giọng tùy chỉnh)* | Textarea | free text | `""` | Placeholder `Nhập mô tả chi tiết giọng đọc của bạn...`; chỉ hiện khi chọn `CUSTOM` |
| `🎨 Phong cách` | LabeledSelect + `Thêm` + `Xóa` | `video_styles.json` + `Không chọn` | index `0` (`data_clone.style_index`) | Tooltip: `Thêm phong cách` / `Xóa phong cách` |
| `📷 Chọn ảnh nhân vật` | FilePickerButton (multi) | ảnh | `[]` (`data_clone.ref_images`) | Dialog `Chọn ảnh nhân vật tham chiếu`; sau mỗi ảnh hỏi `Tên nhân vật` / `Nhập tên nhân vật trong ảnh:` → `data_clone.ref_image_names` |
| `Chưa chọn ảnh nào` | Label (wordWrap) | — | — | Đổi thành `📷 N ảnh: …` sau khi chọn |
| `✕ Xóa tất cả` | GhostButton | — | — | Log `📷 Đã xóa tất cả ảnh tham chiếu` |
| `✨ Yêu cầu tùy chỉnh (Ưu tiên cao nhất - ghi đè các cài đặt khác nếu xung đột)` | Textarea (viền `rgba(245,158,11,.3)`) | free text | `""` (`data_clone.custom_instruction`) | Placeholder 3 dòng: `Ví dụ: Giữ nguyên kịch bản nhưng thay nhân vật nữ thành nam...` / `Hoặc: Chuyển bối cảnh sang không gian vũ trụ...` / `Để trống nếu không có yêu cầu đặc biệt.` |
| `🔍  Phber Tích & Lấy Prompt` → **`🔍  Phân Tích & Lấy Prompt`** | PrimaryButton (viền tím `rgba(124,58,237,.5)`) | — | — | **Hai dấu cách sau 🔍** |
| `⛔  Dừng` | StopButton (viền đỏ `rgba(239,68,68,.5)`) | — | — | **Hai dấu cách sau ⛔** |
| `🖼 Copy Prompt Ảnh` / `🎬 Copy Prompt Video` | GhostButton | — | — | Toast `✅  Đã sao chép Prompt Ảnh` (hai dấu cách sau ✅) |
| `📋 Sao chép` | GhostButton | — | — | Toast `✅  Đã sao chép Social Media Content!` |

**Luồng:**

1. Mount → `GET /api/videoclone/projects` **(chưa tồn tại)** → nạp dropdown; `GET /api/videoclone/projects/{name}/data` → nạp 13 khoá `data_clone.json`.
2. Người dùng dán link / chọn file / import Excel. Mỗi thay đổi → debounce → `PUT /api/videoclone/projects/{name}/data` **(chưa tồn tại)** ghi lại `data_clone.json` (bản gốc dùng `_auto_save_timer` + `_save_data`).
3. Bấm `🔍  Phân Tích & Lấy Prompt`:
   - Nếu `gemini_model !== "chrome_gemini"` và chưa có key → dialog `Thiếu Gemini API Key`, thân bài byte-exact:
     ```
     Bạn chưa cấu hình Gemini API Key.
     Tính năng Phân tích video cần Gemini API Key để hoạt động.
     Bấm vào link bên dưới để lấy API Key, sau đó điền key vào phần "Gemini API Keys" trong tab Cài đặt.
     Hoặc chọn "🌐 Chrome Gemini" để phân tích qua trình duyệt.
     ```
   - Rỗng → `Vui lòng nhập link video hoặc chọn file video local.` / `Không có link/path nào hợp lệ.`
   - Đang chạy → `Đang xử lý. Vui lòng chờ...`
4. `POST /api/videoclone/analyze` **(chưa tồn tại)** với body = 13 khoá + danh sách link → server tạo hàng đợi, trả `{jobId, total}`. Log `📦 Bắt đầu xử lý N video`.
5. Mỗi item, backend:
   a. `_detect_source_type(url)` → `facebook` (`facebook.com`, `fb.watch`, `fb.com`) / `youtube` (`youtube.com`, `youtu.be`) / `tiktok` (`tiktok.com`) / `unknown` (coi là path local).
   b. **TikTok**: gọi `https://www.tikwm.com/api/` (`{url, hd:1}`) lấy `data.hdplay || data.play`, tải bằng `requests` stream với `User-Agent` Chrome + `Referer: https://www.tikwm.com/`. Nếu fail → log `⚠️ TikTok API thất bại: …` rồi `🔄 API thất bại, thử dùng yt-dlp...`
   c. **yt-dlp** (`yt-dlp.exe` bundled cạnh exe): `-o vid_%(id)s.%(ext)s -f "best[height<=480][ext=mp4]/best[height<=480]/best" --merge-output-format mp4 --postprocessor-args "Merger+ffmpeg_o:-c:v copy -c:a aac -b:a 192k" --no-playlist --progress --windows-filenames --write-info-json --user-agent <Chrome 131> --ffmpeg-location <ffmpeg.exe>`; Facebook thêm `--referer https://www.facebook.com/`; nếu có `cookies.txt` / `cookies_yt.txt` / `cookies_tiktok.txt` thì thêm `--cookies`. Timeout 5 phút → `❌ Tải video quá thời gian (>5 phút)`.
   d. `_remux_to_h264`: `ffprobe` codec; nếu đã `h264`/`avc` → `✅ Video đã ở codec H.264, không cần convert`; nếu không → `🔄 Đang convert video sang H.264 (tương thích mọi player)...` rồi `ffmpeg -i src -c:v libx264 -preset fast -crf 23 -c:a aac -y`.
   e. `check_disk_space` trước khi tải → `❌ Lỗi: Không còn bộ nhớ để lưu trữ video/ảnh`.
   f. Phân tích: `chrome_gemini` → `🌐 Đang phân tích qua Chrome Gemini...`; API → `🔍 Đang phân tích qua API Gemini...`. Xoay vòng nhiều key (`_get_next_api_key`); gặp 429 → `bị 429 (Quota Exceeded). Chờ 3s thử key khác...`
   g. Lưu raw ra `full_data_gemini.txt`, ghi backup `.txt`, cập nhật ô CSV/Excel nếu đang ở chế độ Import.
6. Frontend poll `GET /api/videoclone/jobs/{jobId}` **(chưa tồn tại)** → cập nhật `ProgressBar` + log.
7. Kết thúc: `✅ Hoàn tất tất cả N video`, hoặc `⚠️ Hoàn tất N video, bỏ qua M`, hoặc `❌ Thất bại tất cả (N lỗi)`. Dừng giữa chừng → confirm `Xác nhận dừng` / `Bạn có chắc muốn DỪNG quy trình phân tích đang chạy?\nDữ liệu đã hoàn thành sẽ được giữ lại.` → `⛔ Đang dừng phân tích...` → `⛔ Đã dừng! Hoàn tất N video, bỏ qua M`.

**Kết quả:** Card `📊  Kết quả Phân tích` (hai dấu cách) với 3 vùng readonly + card `📱  Social Media Content`. Định dạng text byte-exact:

```
══════════ NHÂN VẬT ══════════
<mô tả nhân vật>
     🎙 Giọng: <voice>
══════════ KỊCH BẢN (CHIA CẢNH) ══════════
━━━ SCENE 1 ━━━
<prompt>
```

Ngoài ra: `💾 Đã ghi tạm kết quả vào: <path>`, `📝 Đã ghi kết quả vào Excel (hàng N)`, và nút tải ZIP (`Lưu file ZIP` → `✅  Đã lưu ZIP: <path>`).

**Cạm bẫy:**

- **RỦI RO TÀI KHOẢN — banner bắt buộc.** Tab này (i) tải nội dung có bản quyền từ FB/YT/TikTok bằng `yt-dlp`, (ii) tuỳ chọn nạp `cookies.txt` / `cookies_yt.txt` / `cookies_tiktok.txt` — tức là **cookie phiên đăng nhập thật của người dùng** — vào một binary bên thứ ba, và (iii) ở chế độ `🌐 Chrome Gemini (trình duyệt)` thì tự động hoá phiên Gemini đăng nhập của người dùng. Cả ba đều là hành vi bị nền tảng đánh dấu. Banner phải nói rõ: **dùng cookie đăng nhập để tải video có thể khiến tài khoản mạng xã hội bị hạn chế hoặc khoá; nội dung tải về có thể có bản quyền; chế độ Chrome Gemini tự động hoá phiên Google của bạn.** Banner **không được đóng vĩnh viễn** — dismiss chỉ trong session, và phải hiện lại nếu người dùng bật `cookies`.
- **Toàn bộ backend của tab này chưa tồn tại.** Không có route download, không có route analyze, không có project store. Đây là tab tốn công nhất trong nhóm.
- **`voice_index` / `style_index` là chỉ số nguyên vào một danh sách động.** Thêm/xoá một giọng ở giữa danh sách sẽ khiến mọi dự án cũ trỏ sai giọng **mà không báo lỗi**. Phải chuyển sang lưu **id/tên** thay vì index, và migrate `data_clone.json` cũ khi đọc. Đây là lỗi im lặng sinh ra output sai — đúng loại nguy hiểm nhất.
- **`🔒 Khóa số cảnh theo thời gian` + `duration` để trống** → `ceil(duration/8)` chia cho `undefined`. Nếu bật khoá mà không có thời lượng thì phải lấy thời lượng thật từ `ffprobe` sau khi tải, không được đoán.
- **`Số luồng` > 1 nhân số lần gọi Gemini lên đồng thời.** Với một API key duy nhất, `threads = 4` gần như chắc chắn dính 429 và tự xoay key — nếu chỉ có 1 key thì nó chỉ chờ 3s rồi thử lại chính key đó. Clamp theo số key có sẵn, hoặc cảnh báo inline.
- **`gemini-3.6-flash` (mặc định) và `gemini-3.5-flash-lite` v.v. là tên model do tác giả đặt trong exe**, không phải danh mục model chính thức. Nếu Google đổi/bỏ tên, dropdown sẽ hỏng lặng lẽ. Phải lấy danh sách model từ backend (`GET /api/llm/providers`) chứ đừng hardcode 7 mục này.
- **`tikwm.com` là API bên thứ ba không có SLA.** Nếu nó chết, mọi link TikTok rơi xuống `yt-dlp` mà không báo cho người dùng biết chất lượng có thể khác. Log rõ đường nào đã dùng.
- **Ảnh tham chiếu tối đa 3** theo luật chung của tool (`reference images max 3`). Bản gốc **không chặn** ở tab này. Bản replica nên chặn ở 3 và nói lý do, không âm thầm gửi 10 ảnh rồi để model bỏ bớt.
- **Excel writeback ghi đè file đang mở.** Bản gốc phải `📎 File Excel đang mở, đang thử đóng...` rồi kill process Excel. Bản web **không làm được điều đó** — phải xuất file mới (`<tên>_ketqua.xlsx`) thay vì ghi đè, và nói rõ trong UI.

---

### User Guide — Hướng dẫn sử dụng

**Mục đích:** Hiển thị nguyên văn `data_general/huong_dan_su_dung_tool.md` (tài liệu do chính tác giả viết) dưới dạng các thẻ nhóm cuộn được — không phải markdown renderer đầy đủ, mà là một parser 2 luật rất hẹp.

**Bố cục:**

```
┌ ToolHeader: [❓ magenta] User Guide · Hướng dẫn sử dụng ─────────────────────┐
│                                                                             │
│            CÁC BƯỚC HƯỚNG DẪN SỬ DỤNG VEO TOOL      (AlignCenter, đậm)      │
│                                                                             │
├ QScrollArea#HelpBody (NoFrame, widgetResizable) ────────────────────────────┤
│  ┌ Card ────────────────────────────────────────────────────────────────┐  │
│  │ 1) Tạo Video Từ Prompt                                                │  │
│  │ • Bước 1: Chọn tab Text to Video.                                     │  │
│  │ • Bước 2: Nhập hàng loạt prompt, mỗi dòng một prompt.                 │  │
│  │ • Bước 3: Bấm BẮT ĐẦU TẠO VIDEO TỪ PROMPT để chạy theo danh sách...   │  │
│  │ • LƯU Ý: Có thể sửa từng prompt trước khi chạy để tránh lỗi nội dung. │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│  ┌ Card: 2) Tạo Video từ Ảnh ───────────────────────────────────────────┐  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│  … 3) Tạo Video từ Ảnh Đầu / Ảnh Cuối                                       │
│  … 4) Tạo Video từ Ý Tưởng                                                  │
│  … 5) Đồng bộ Nhân vật                                                      │
│  … 6) Tạo Ảnh                                                               │
│  … 7) Cài đặt                                                               │
│                                            (addStretch cuối)                │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Điều khiển:**

| Nhãn | Loại | Giá trị / tập chọn | Mặc định (khoá config) | Ghi chú |
|---|---|---|---|---|
| `CÁC BƯỚC HƯỚNG DẪN SỬ DỤNG VEO TOOL` | Tiêu đề trang | — | hằng số trong code | `AlignCenter`, font đậm |
| *(thẻ nhóm)* | Card, tự sinh | 1 thẻ / 1 header khớp `^\s*(\d+\)\s*.+)$` | 7 thẻ với file đang ship | Tiêu đề thẻ giữ nguyên cả số `n)` |
| *(dòng gạch đầu)* | Bullet | khớp `^\s*[-*•]\s*(.+)$` | — | Glyph hiển thị là `•`, `setWordWrap(True)` |
| *(fallback khi file sai định dạng)* | Card lỗi | tiêu đề `1) Hướng Dẫn Sử Dụng`; 2 dòng: `Nội dung file hướng dẫn chưa đúng định dạng.` và `Vui lòng chỉnh lại file: <đường dẫn tuyệt đối>` | — | Xảy ra khi không tìm thấy header nào |
| *(không có control nào khác)* | — | — | — | Tab thuần đọc: không nút, không input, không lưu |

**Luồng:**

1. Mount tab → `GET /api/guide` **(chưa tồn tại)** → server đọc `<ASSET_ROOT>/data_general/huong_dan_su_dung_tool.md` (`encoding="utf-8"`).
2. Nếu file không tồn tại → server tự tạo nó từ `DEFAULT_GUIDE_TEXT` (bản gốc: `HELP_GUIDE_FILE.parent.mkdir(parents=True, exist_ok=True)` rồi `write_text(DEFAULT_GUIDE_TEXT, encoding="utf-8")`) rồi trả nội dung vừa ghi. `DEFAULT_GUIDE_TEXT` chính là 7 mục hiện đang có trong file.
3. Server trả `{ groups: [{ title: string, lines: string[] }] , path: string }` — parse ở **backend**, không ở frontend, để logic trùng khớp bản gốc và để đường dẫn trong thông báo lỗi là đường dẫn thật của máy.
4. Parser (byte-exact với bản gốc):
   - `header_re = ^\s*(\d+\)\s*.+)$` → mở nhóm mới, tiêu đề = `group(1)`.
   - `bullet_re = ^\s*[-*•]\s*(.+)$` → thêm `group(1)` vào nhóm đang mở.
   - Dòng không khớp luật nào → **bỏ qua hoàn toàn** (bản gốc không có nhánh xử lý dòng thường).
   - Hết file → `flush_group()`.
   - `groups` rỗng → trả nhóm fallback ở trên.
5. Frontend render danh sách `Card` + `SectionHeader` + list bullet. Không có state, không có API nào khác.

**Kết quả:** Danh sách thẻ tĩnh trong vùng cuộn `#HelpBody`. Không có JobList, không có toast, không có drawer.

**Cạm bẫy:**

- **Parser bỏ im lặng mọi dòng không phải header/bullet.** Nếu người dùng sửa file và viết một đoạn văn xuôi giữa các bullet, đoạn đó **biến mất khỏi UI mà không báo gì**. Đây đúng là kiểu "silently produce wrong output". Khuyến nghị: giữ luật parse để trung thành, nhưng thêm một dòng đếm ở cuối trang — `Đã bỏ qua N dòng không đúng định dạng` — và link mở file. Đừng đổi sang markdown renderer đầy đủ: nội dung sẽ khác bản gốc.
- **Nội dung file mô tả tên tab của bản Qt, không phải tên sidebar của bản replica.** Ví dụ nó nói `Chọn tab Ý tưởng to Video` (sidebar thật: `Idea to Video`), `Chọn tab Đồng bộ nhân vật` (sidebar thật: `Character Sync`), `Chọn tab Tạo Ảnh` (sidebar thật: `Text to Image` / `Image to Image`). Người dùng sẽ tìm không ra. **Không sửa file** (nó là tài liệu của tác giả và người dùng có thể sửa) — thay vào đó hiển thị một strip ghi chú ánh xạ ở đầu trang, hoặc để backend gắn thêm `matchedTab` cho mỗi nhóm nếu tiêu đề khớp một tab đã biết.
- **Mục `7) Cài đặt` hướng dẫn nhập mật khẩu Google.** Nguyên văn: `Bước 2: Nhập Tài khoan VEO3 và API Keys của Gemini vào form.` và `Bước 4: Bấm Auto Login TK VEO3 để tool tự động đăng nhập lấy thông tin để tạo video.` Bản replica **không làm điều đó** (dùng Chrome profile bền vững). Phải chèn callout ngay dưới mục 7 nói rõ khác biệt, nếu không người dùng sẽ đi tìm ô mật khẩu không tồn tại — hoặc tệ hơn, đi tìm cách nhập nó.
- Ghi chú chính tả trong file gốc: `Tài khoan` (thiếu dấu), `LƯU Ý` viết hoa. **Giữ nguyên** — đây là nội dung của người dùng, không phải copy của ta.
- Có sẵn chuỗi lỗi mined cho trường hợp không mở được file: title `Hướng dẫn`, body `Không mở được file hướng dẫn: <lỗi>`.

---

### 🌟 VEO3 — Pill nền tảng chính

**Mục đích:** Nền tảng mặc định; chứa toàn bộ cây sidebar VEO3 (14 mục, 3 nhóm) và là platform duy nhất render top-card dự án/tỷ lệ/model.

**Bố cục:**

```
┌ TopBar 52px ─────────────────────────────────────────────────────────────┐
│ [🌟 VEO3]* [⚡ GROK] [📊 NHẬT KÝ] [🔗 WORKFLOW] [💝 ỦNG HỘ TÁC GIẢ]      │
└──────────────────────────────────────────────────────────────────────────┘
┌ Sidebar 232px ───────┬─ main ──────────────────────────────────────────┐
│ VIDEO TOOLS          │ ┌ SectionBoard#mode_header ────────────────────┐ │
│  ▸ Text to Video     │ │ [icon] Text to Video   Tên: … SĐT: … [ Zalo] │ │
│  ▸ Image to Video    │ │                                        [🔔]  │ │
│  ▸ Video Start-End   │ └──────────────────────────────────────────────┘ │
│  ▸ Character Sync    │ ┌ top_action_area (scroll ngang, ẩn scroll dọc)┐ │
│  ▸ Idea to Video     │ │ Chọn dự án [▾][+][🗑]        [📁 Xem kết quả]│ │
│  ▸ Phân tích video   │ │ 📏 Tỷ lệ[▾] ⏱ Thời lượng[▾] 🎬 Model[▾]     │ │
│  ▸ Phụ Đề & Xóa Logo │ │ 📂 Thư mục lưu Video [.....................] │ │
│ IMAGE TOOLS          │ └──────────────────────────────────────────────┘ │
│  ▸ Text to Image     │ ┌ <nội dung tab> ─────────────────────────────┐ │
│  ▸ Image to Image    │ └──────────────────────────────────────────────┘ │
│  ▸ Affiliate Sản Phẩm│                                                  │
│  ▸ UP SCALE IMAGE 4K │                                                  │
│ SYSTEM               │                                                  │
│  ▸ Settings          │                                                  │
│  ▸ Cut & Merge Video │                                                  │
│  ▸ User Guide        │                                                  │
└──────────────────────┴──────────────────────────────────────────────────┘
┌ StatusBar 30px ────────────────────── [VPN : OFF] [Profile_CAPTCHA] ────┐
└──────────────────────────────────────────────────────────────────────────┘
```

**Điều khiển:**

| Nhãn | Loại | Giá trị / tập chọn | Mặc định (khoá config) | Ghi chú |
|---|---|---|---|---|
| `🌟 VEO3` | Pill `#PlatformBtn` | active / inactive | **active khi khởi động** | `setProperty("active", …)`; gradient nav-active `#4f46e5→#7c3aed` |
| *(sidebar)* | List + custom delegate | 14 mục / 3 header nhóm | `Text to Video` | Delegate vẽ: emoji (`ROLE_EMOJI`), nền icon (`ROLE_ICON_BG` = accent màu), tiêu đề `#f4f8ff` (active) / `#e2e8f0`, phụ đề `#afbddf` (active) / `#8290a8` (`ROLE_VN_SUB`) |
| `Tên:` / `Tên: N/A` | Chip | text | **ship RỖNG** (`branding_state.json`) | PII của tác giả — không copy |
| `SĐT:` / `SĐT: N/A` | Chip | text | **ship RỖNG** | PII |
| ` Zalo` | Button (icon `icons/zalo.png`) | URL nhóm | **ship RỖNG** | Lỗi mined: `Chưa cấu hình link nhóm Zalo.` — dùng chính chuỗi này khi rỗng |
| 🔔 | `_BellNotifyButton` | badge có/không | không badge | Tooltip `Có bản cập nhật mới!` |
| `VPN : OFF` | FooterPillBtn | `OFF` / `ON` | `OFF` | `active="true"` → xanh `#10b981` |
| `Profile_CAPTCHA` | FooterPillBtn | — | — | Mở trình quản lý profile |

**Luồng:**
1. Khởi động → `VEO3` active, sidebar chọn dòng 0 (`Text to Video`), `content_stack` hiện `TextToVideoTab`.
2. Đổi pill → `_switch_platform(name)`: lưu dòng sidebar hiện tại vào `_platform_saved_row[platform]`, lọc `_visible_platform_items(platform)`, khôi phục dòng đã lưu của platform mới.
3. Đổi mục sidebar → cập nhật `lbl_mode_icon` + `lbl_mode_title`, `content_stack.setCurrentWidget(...)`, và **hiện/ẩn** các cụm top-card theo loại tab (video → `📏/⏱/🎬`; ảnh → `🎨/🖼`; GROK → cụm GROK riêng).
4. Chip `Tên:` / `SĐT:` → `_refresh_owner_badges()` đọc branding; rỗng → `N/A`.

**Kết quả:** Không có output riêng — đây là khung. Mọi tab con render bên trong `ContentArea`.

**Cạm bẫy:**
- **`branding_state.json` chứa tên + số điện thoại thật của tác giả.** Ship rỗng, hiển thị `Tên: N/A` / `SĐT: N/A`, và cho người dùng tự điền trong Settings. Không commit file đó.
- `_is_platform_busy` chặn đổi platform khi có workflow đang chạy — chuỗi mined: `Bạn có thể chuyển sang tab Nhật ký để xem tiến trình tạo video/ảnh`. Replica dùng Zustand: nếu có job `running`, đổi tab **được phép** nhưng phải hiện chip đang chạy trên pill `📊 NHẬT KÝ`.
- Sidebar dùng delegate vẽ tay với 3 vai trò dữ liệu. Đừng thay bằng list mặc định — mất phụ đề tiếng Việt và dải màu accent, tức mất phần lớn nhận dạng thị giác.

---

### ⚡ GROK — Pill nền tảng phụ (chưa sẵn sàng)

**Mục đích:** Nền tảng thứ hai chạy qua `grok.com` bằng một Chrome profile **hoàn toàn tách biệt** (`chrome_user_data_grok/`) do `patchright` điều khiển. Trong bản 4.6.1.3 chính exe tự tuyên bố chức năng chưa hoàn thiện.

**Bố cục:**

```
┌ main (khi chọn ⚡ GROK) ─────────────────────────────────────────────────┐
│ ┌ EmptyState (chiếm toàn khung) ────────────────────────────────────────┐│
│ │            ⚡                                                          ││
│ │  Chức năng GROK đang phát triển, chưa sẵn sàng.                        ││
│ │                                                                        ││
│ │  Bản replica chưa nối tới grok.com. Bạn vẫn có thể chuẩn bị            ││
│ │  hồ sơ đăng nhập bên dưới để dùng khi tính năng sẵn sàng.              ││
│ │                                                                        ││
│ │  ┌ QGroupBox "Cài đặt GROK" ────────────────────────────────────────┐ ││
│ │  │ ┌ "Thiết lập video GROK"  (QGridLayout) ──────────────────────┐  │ ││
│ │  │ │ Loại tài khoản:  [SUPER ▾]                                  │  │ ││
│ │  │ │ Thời gian video: [6 giây ▾]                                 │  │ ││
│ │  │ │ Chất lượng video:[480 ▾]                                    │  │ ││
│ │  │ │ MULTI VIDEO:     [5 ▾]                                      │  │ ││
│ │  │ └─────────────────────────────────────────────────────────────┘  │ ││
│ │  │ [Mở Chrome để đăng nhập GROK]  [Xóa Profile GROK]                │ ││
│ │  │ <b>Hướng Dẫn:</b> Bước 1 … Bước 4 …                              │ ││
│ │  │                                     [ Lưu cài đặt GROK ]         │ ││
│ │  └───────────────────────────────────────────────────────────────────┘ ││
│ └───────────────────────────────────────────────────────────────────────┘│
└──────────────────────────────────────────────────────────────────────────┘
```

**Điều khiển:**

| Nhãn | Loại | Giá trị / tập chọn | Mặc định (khoá config) | Ghi chú |
|---|---|---|---|---|
| `⚡ GROK` | Pill `#PlatformBtn` | active/inactive | inactive | |
| `Chức năng GROK đang phát triển, chưa sẵn sàng.` | EmptyState title | — | — | **Chuỗi của chính tác giả** — dùng nguyên văn, đừng dịch lại |
| `Cài đặt GROK` | QGroupBox | — | — | |
| `Thiết lập video GROK` | SectionHeader | — | — | |
| `Loại tài khoản:` | LabeledSelect | `SUPER` → `SUPER`; `NORMAL` → `NORMAL` (chấp nhận `ULTRA` khi đọc, upper-case rồi map) | `SUPER` (`GROK_ACCOUNT_TYPE`, `grok_account.TYPE_ACCOUNT`) | Chọn `NORMAL` → ép `6 giây` + `480` và disable 2 combo kia |
| `Thời gian video:` | LabeledSelect | `6 giây` → `6`; `10 giây` → `10` | `6 giây` (`GROK_VIDEO_LENGTH_SECONDS` = `6`) | `10 giây` chỉ khả dụng với `SUPER` |
| `Chất lượng video:` | LabeledSelect | `480` → `480p`; `720` → `720p` | `480` (`GROK_VIDEO_RESOLUTION` = `480p`) | |
| `MULTI VIDEO:` | LabeledSelect | số | `5` (`GROK_MULTI_VIDEO`) | 1 lần = 5 clip |
| `Mở Chrome để đăng nhập GROK` | GhostButton `#Warning` | — | — | Mở `https://grok.com/` trong profile riêng |
| `Xóa Profile GROK` | GhostButton `#Danger` | — | — | Confirm `Xác nhận` / `Bạn chắc chắn muốn xóa profile GROK?`; kết quả `Đã xóa profile GROK.` / `Không xóa được profile GROK: …` / `Profile GROK không tồn tại.` |
| `Lưu cài đặt GROK` | PrimaryButton `#Accent` | — | — | Toast `Thông báo` / `Đã lưu cài đặt GROK.`; lỗi `Lỗi` / `Không lưu được cài đặt GROK: …` |
| *(khối hướng dẫn)* | HTML block, `setWordWrap` | — | — | Byte-exact bên dưới |

Khối hướng dẫn nguyên văn:

```
<b>Hướng Dẫn:</b><br/>Bước 1: Bấm nút <b>Mở Chrome để đăng nhập GROK</b>, sau đó nhập tài khoản và mật khẩu để đăng nhập GROK trực tiếp trên web.<br/>Bước 2: Chọn <b>Loại Tài khoản</b> đã đăng nhập. Tài khoản thường chọn <b>NORMAL</b>, tài khoản SUPER chọn <b>SUPER</b> (chọn sai sẽ không chạy được).<br/>Bước 3: Cấu hình thời gian video <b>6s hoặc 10s</b> (tài khoản <b>SUPER</b> mới chọn được 10s).<br/>Bước 4: Chọn chất lượng video <b>480 hoặc 720</b> (tool đã tự upscale lên 720 mức cao nhất của GROK rồi).
```

Popup profile (mined): title `GROK Profile`; nội dung `Chrome GROK đang mở để đăng nhập.` + `CDP: <host>:<port>` + `Profile: <dir>` + `Sau khi login xong bạn có thể để mở, hoặc bấm 'Tắt Chrome GROK'.`; nút `Tắt Chrome GROK` và `Đóng`; trạng thái `Trạng thái: Chrome GROK đang chạy` / `Trạng thái: Đã tắt Chrome GROK`; lỗi `Không mở được Chrome GROK: …` / `Không tắt được Chrome GROK: …`.

**45 khoá `GROK_*` mined từ exe** (nằm ở `data_general/grok_config.json`, không phải `config.json`):

`GROK_ACCOUNT_TYPE`, `GROK_API_C`, `GROK_API_I`, `GROK_API_Create_image`, `GROK_API_Image_to_image`, `GROK_ASSETS_BASE`, `GROK_AUTO_REFRESH_HEADERS`, `GROK_BASE`, `GROK_CACHE_PATH`, `GROK_CDP_HOST`, `GROK_CDP_PORT`, `GROK_CHROME_LANGUAGE`, `GROK_CHROME_USER_DATA_ROOT`, `GROK_CLOSE_CHROME_ON_SESSION_END`, `GROK_CONFIG_PATH`, `GROK_COUNT`, `GROK_CREATE_IMAGE`, `GROK_DOWNLOAD_DIR`, `GROK_DOWNLOAD_TIMEOUT_MS`, `GROK_ERROR`, `GROK_FORCE_CLEAN_START`, `GROK_IMAGE_DOWNLOAD_TIMEOUT_MS`, `GROK_IMAGE_STREAM_TIMEOUT`, `GROK_IMAGE_TO_IMAGE`, `GROK_IMAGE_TO_IMAGE_TIMEOUT`, `GROK_IMAGE_TO_VIDEO`, `GROK_JOB_HARD_TIMEOUT_SECONDS`, `GROK_MULTI_VIDEO`, `GROK_PROFILE_NAME`, `GROK_PROMPT`, `GROK_PROMPTS`, `GROK_REQUEST_LOG_PATH`, `GROK_RESTART_PROFILE_ON_SESSION_START`, `GROK_RUNTIME_CONFIG_PATH`, `GROK_STREAM_TIMEOUT`, `GROK_TEXT_TO_VIDEO`, `GROK_UPLOAD_429_BACKOFF_SECONDS`, `GROK_UPLOAD_MAX_ATTEMPTS`, `GROK_UPLOAD_MIN_INTERVAL_SECONDS`, `GROK_URL`, `GROK_USER_DATA_ROOT`, `GROK_VIDEO_ASPECT_RATIO`, `GROK_VIDEO_LENGTH`, `GROK_VIDEO_LENGTH_SECONDS`, `GROK_VIDEO_RESOLUTION`, `GROK_WORKFLOW_CHROME_EXTRA_ARGS`.

Trong `data_general/config.json` chỉ có 5 khoá: `GROK_VIDEO_LENGTH_SECONDS`=6, `GROK_VIDEO_RESOLUTION`="480p", `GROK_MULTI_VIDEO`=5, `GROK_ACCOUNT_TYPE`="SUPER", `grok_account`={type_account:"SUPER", TYPE_ACCOUNT:"SUPER"}.

**Luồng:**
1. Chọn pill `⚡ GROK` → hiện `EmptyState` với thông điệp nguyên văn của tác giả **ngay lập tức**, không thử gọi API, không spinner.
2. Bên dưới `EmptyState` render form `Cài đặt GROK` ở trạng thái **editable** — người dùng vẫn lưu được cấu hình và mở được profile.
3. `Mở Chrome để đăng nhập GROK` → `POST /api/settings/profiles/GROK/open` **(chưa tồn tại)** với `url: "https://grok.com/"`.
4. `Lưu cài đặt GROK` → `PUT /api/settings/grok-config` **(chưa tồn tại)** ghi `grok_config.json`.
5. Không có nút chạy nào. 4 sidebar tab GROK (`Text to Video`, `Image to Video`, `Text to Image`, `Image to Image`) đều render cùng `EmptyState`.

**Kết quả:** Trạng thái không-khả-dụng trung thực + form cấu hình đã lưu được. Không JobList, không kết quả.

**Cạm bẫy:**
- **Đừng ngụy trang thành "coming soon" chung chung.** Exe nói cụ thể: `Chức năng GROK đang phát triển, chưa sẵn sàng.` Dùng đúng câu đó và ghi rõ đây là câu của tác giả bản gốc, không phải giới hạn do ta thêm vào.
- **`GROK_ACCOUNT_TYPE` mặc định là `SUPER` nhưng phần lớn người dùng có tài khoản `NORMAL`.** Chọn sai → chuỗi mined `(chọn sai sẽ không chạy được)`. Đặt mặc định UI là `NORMAL` cho an toàn, **hoặc** giữ `SUPER` theo config và hiện cảnh báo — nhưng phải chọn một và ghi rõ, đừng để lệch âm thầm.
- **Profile GROK phải tách hoàn toàn khỏi profile VEO3.** Dùng chung `chrome_user_data/` sẽ trộn cookie Google và x.com trong cùng một profile — vừa hỏng đăng nhập vừa tăng rủi ro flag. Bắt buộc `chrome_user_data_grok/`, và `GROK_CDP_PORT` phải khác cổng CDP của VEO3 (nếu không, quy tắc process-management sẽ "tắt chủ cũ" nhầm Chrome của VEO3).
- Khi `Loại tài khoản = NORMAL`, `_apply_account_constraints` **ép** `6 giây` + `480` và disable 2 combo. Replica phải làm y hệt, kèm dòng giải thích `NORMAL: chỉ 480p + 6 giây và sẽ tự upscale khi video 480p.` (chuỗi mined) — nếu chỉ disable mà không giải thích, người dùng tưởng UI hỏng.
- `patchright` là fork của Playwright dùng để né phát hiện bot. Bản replica local nên nói thẳng điều đó trong ghi chú kỹ thuật thay vì giấu.

---

### 📊 NHẬT KÝ — Trang nhật ký hoạt động toàn màn hình

**Mục đích:** Xem toàn bộ lịch sử request của tool ở dạng bảng full-page thay vì dropdown chuông, kèm dải hiển thị credit còn lại và các thao tác hàng loạt trên kết quả.

**Bố cục:**

```
┌ main (khi chọn 📊 NHẬT KÝ) ──────────────────────────────────────────────┐
│ ┌ Credit strip ─────────────────────────────────────────────────────────┐│
│ │ Loại TK: ULTRA   Credit: 1,240   [Kiểm tra Tài khoản]   cập nhật 3′   ││
│ └───────────────────────────────────────────────────────────────────────┘│
│ ┌ Toolbar ──────────────────────────────────────────────────────────────┐│
│ │ Lọc: [tất cả ▾]  [⏸️ DỪNG LẠI] [📁 XEM KẾT QUẢ]                       ││
│ │                            [📋 Copy nhật ký] [🗑️ Xóa nhật ký]         ││
│ └───────────────────────────────────────────────────────────────────────┘│
│ ┌ Bảng ─────────────────────────────────────────────────────────────────┐│
│ │ ☑ Chọn │ Prompt / node        │ Link Ảnh │ Trạng thái │ 🎬 Video │ ⋯  ││
│ │ ☑      │ Prompt 1  [Sửa]      │ [Copy]   │ ĐANG TẠO   │ ▶ Video  │    ││
│ │ ☐      │ (chưa có)            │ …        │ HOÀN THÀNH │ ▶ Bấm để…│    ││
│ └───────────────────────────────────────────────────────────────────────┘│
│ ⏳ Đang chờ 3 video hoàn thành...                                        │
│ [🔗 Nối video] [🔄 Tạo lại] [✂️ Cắt ảnh cuối] [🗑️ Xóa kết quả]           │
└──────────────────────────────────────────────────────────────────────────┘
```

**Điều khiển:**

| Nhãn | Loại | Giá trị / tập chọn | Mặc định (khoá config) | Ghi chú |
|---|---|---|---|---|
| `📊 NHẬT KÝ` | Pill `#PlatformBtn` | active/inactive | inactive | Badge số job đang chạy |
| `Loại TK:` / `Credit:` | Chip | text | `--` / `--` | Nạp từ `SettingsTab.load_credits_from_config` tương đương |
| `Kiểm tra Tài khoản` | GhostButton | — | — | Cùng endpoint với Settings |
| `Chọn` | Cột checkbox + `_SelectAllHeader` | bool | `false` | Header có nút chọn-tất-cả vẽ tay (`#16a34a` khi checked) |
| `Trạng thái` | Cột | `SẴN SÀNG` · `ĐANG LẤY TOKEN` · `ĐÃ GỬI REQUEST` · `ĐANG CHỜ` · `ĐANG TẠO` · `ĐANG TẢI` · `ĐANG UPSCALE` · `HOÀN THÀNH` · `LỖI` · `HỦY` | `SẴN SÀNG` | Map sang `RequestDTO.status`: `queued→ĐANG CHỜ`, `running→ĐANG TẠO`, `done→HOÀN THÀNH`, `failed→LỖI`, `canceled→HỦY`, `timeout→LỖI` |
| `Link Ảnh` | Cột | URL/path + `Copy` | — | Copy → toast `📋 Đã copy link ảnh: …` |
| `Sửa` | Nút inline trong ô prompt | — | — | Mở dialog `Sửa Prompt` / `Prompt hiện tại / chỉnh sửa:` / `Xác nhận` / `Hủy` |
| `(chưa có)` | Placeholder ô rỗng | — | — | |
| `🎬 Video` / `▶ Video` / `▶ Bấm để xem` | Cột kết quả | — | — | |
| `⏸️ DỪNG LẠI` | StopButton | — | — | |
| `📁 XEM KẾT QUẢ` | GhostButton | — | — | |
| `📋 Copy nhật ký` | GhostButton | — | — | Toast `📋 Đã copy nhật ký vào clipboard` |
| `🗑️ Xóa nhật ký` | GhostButton | — | — | |
| `🔗 Nối video` | GhostButton | — | — | Gọi `/api/postprod/concat` với các dòng đã tick |
| `🔄 Tạo lại` | GhostButton | — | — | Trạng thái phụ mined: `🔄 Tạo lại Hủy`, `⚠️ Tạo lại video lỗi` |
| `✂️ Cắt ảnh cuối` | GhostButton | — | — | Lấy frame cuối làm ảnh đầu cho clip kế |
| `🗑️ Xóa kết quả` | GhostButton `#Danger` | — | — | Confirm `Bạn có chắc muốn xóa N kết quả đã chọn?\nDòng đã chọn: …`; chặn khi đang chạy: `Không thể xóa` / `Workflow đang chạy. Vui lòng đợi hoàn thành hoặc dừng trước khi xóa kết quả.`; chưa tick: `Chưa chọn` / `Hãy tích chọn các dòng cần xóa kết quả.` |
| `⏳ Đang chờ N video hoàn thành...` | Dòng trạng thái | — | — | Ghép từ `⏳ Đang chờ` + N + ` video hoàn thành...` |

**Luồng:**
1. Mount → `getActivityList({limit: 50})` (**đã có**, `GET /api/activity`) → `{items, next_before_id}`. Mỗi item: `{id, type, status, node_id, node_short_id, created_at, finished_at, duration_ms}`.
2. Tái sử dụng **nguyên** `useActivityFeed` + `ActivityRow` + `ActivityIcon` + `activity-meta.ts` từ `frontend/src/components/activity/`. Chỉ viết mới phần layout full-page và toolbar — **không fork logic feed**.
3. Cuộn tới cuối → `getActivityList({beforeId: next_before_id})` (cursor pagination, không offset).
4. Click một dòng → `getActivityDetail(id)` → `ActivityDetailModal` (đã có).
5. `⏸️ DỪNG LẠI` → `cancelActivity(id)` cho mọi dòng `queued`/`running` đã tick.
6. Dải credit → `GET /api/settings/credits` **(chưa tồn tại)**, refresh nền mỗi 60′ (bản gốc log `[CHECK_60P] 🔄 Bắt đầu refresh credit và tài khoản định kỳ...`).
7. `📁 XEM KẾT QUẢ` → mở thư mục render qua backend.

**Kết quả:** Bảng đầy trang, cuộn vô hạn theo cursor. Hàng đang chạy có nền `#141824` (chẵn/lẻ `#0f121d`/`#141824`), hàng đang chọn viền `#10b981`, hover `rgba(255,255,255,0.08)`.

**Cạm bẫy:**
- **`GET /api/activity` trả `status` tiếng Anh (`queued|running|done|failed|canceled|timeout`) còn UI gốc hiển thị tiếng Việt viết hoa.** Bảng ánh xạ ở trên là hợp đồng — `timeout` **phải** map thành `LỖI` chứ không tạo nhãn mới, nếu không có 2 nhãn cho cùng một kết cục.
- **Không có endpoint credits.** Nếu strip credit không nối được, hiển thị `Credit: --` + `Chưa có token` chứ đừng hiện `0` — `0` credit và "chưa biết" là hai trạng thái khác nhau và nhầm lẫn sẽ khiến người dùng ngừng chạy job không cần thiết.
- **`getActivityList` không có filter theo platform.** Cột `type` chỉ có `proxy | create_project | gen_image | gen_video | gen_video_omni | edit_image`. Dropdown `Lọc:` phải dựng từ tập đó, đừng bịa filter `VEO3 / GROK`.
- **`🔗 Nối video` / `✂️ Cắt ảnh cuối` cần đường dẫn file cục bộ**, nhưng `ActivityDetail` trả `media_id` chứ không trả path. Cần `GET /api/activity/{id}` mở rộng thêm `outputPath`, nếu không hai nút này không nối được vào `/api/postprod/*`.
- **`🗑️ Xóa nhật ký` mơ hồ.** Trong bản gốc nó xoá *vùng log text*, khác hẳn `🗑️ Xóa kết quả` (xoá file kết quả). Đặt cạnh nhau trong web sẽ bị nhầm là "xoá lịch sử". Ghi tooltip phân biệt rõ, hoặc đổi vị trí để không đứng cạnh nhau.

---

### 🔗 WORKFLOW — Trình soạn thảo canvas node

**Mục đích:** Canvas React Flow để nối các node thành pipeline (ảnh → prompt → video → hậu kỳ). **Phase này chỉ spec khung + danh sách template**; 25 loại node là phase sau.

**Bố cục:**

```
┌ Toolbar ─────────────────────────────────────────────────────────────────┐
│ Dự án: [▾]  [💾 Lưu] [▶ Chạy] [RUN Lỗi ⚠️] [⏹ Dừng] [📁 Kết quả]        │
│ [Workflow Mẫu ▾] [Bật/Ẩn Preview] [Quản lý Batch]        [✦ AI Agent]   │
└──────────────────────────────────────────────────────────────────────────┘
┌ THƯ VIỆN NODE ──┬─ Canvas ─────────────────────┬─ Properties ───────────┐
│ « (thu gọn)     │                              │  (WorkflowProperties   │
│ ▸ text_prompt   │        ⬡ ── ⬡ ── ⬡           │   Panel)               │
│ ▸ prompt_list   │             │                │                        │
│ ▸ upload_media  │             ⬡                │                        │
│ ▸ gemini_prompt │                              │                        │
│ ▸ analyze_video │              [MiniMap]       │                        │
│ ▸ gen_image     │                              │                        │
│ ▸ gen_video     ├──────────────────────────────┤                        │
│ ▸ create_char.. │ 📦 0 node  🔗 0 kết nối  ⚠ 0 chưa nối                 │
│ ▸ create_voice  ├──────────────────────────────┤                        │
│ ▸ merge_video   │ 📜 NHẬT KÝ   [📋 Copy][🗑 Xóa]│                        │
│ ▸ edit_video    │ ┌ log (Consolas) ──────────┐ │                        │
│                 │ └──────────────────────────┘ │                        │
└─────────────────┴──────────────────────────────┴────────────────────────┘
```

**Điều khiển:**

| Nhãn | Loại | Giá trị / tập chọn | Mặc định (khoá config) | Ghi chú |
|---|---|---|---|---|
| `🔗 WORKFLOW` | Pill `#PlatformBtn` | active/inactive | **ẩn** trừ khi `WORKFLOW_ENABLED` hoặc `--dev-mode` trong argv | `setVisible(...)` — mined |
| `Dự án:` | LabeledSelect | thư mục trong `WORKFLOWS_DIR` | `default_workflow` (`Workflows/default_workflow.json`) | |
| `💾 Lưu` | GhostButton | — | — | AI action `save_workflow` |
| `▶ Chạy` | PrimaryButton | — | — | AI action `run_workflow` |
| `RUN Lỗi ⚠️` | GhostButton | — | — | AI action `run_errors_only` |
| `⏹ Dừng` | StopButton | — | — | AI action `stop_workflow`; log `🛑 Đang dừng workflow...`, `✅ Đã dừng workflow` |
| `📁 Kết quả` | GhostButton | — | — | AI action `open_results_folder` |
| `Workflow Mẫu` | Menu (`QMenu`, nền `#0f172a`, selected `#4f46e5`) | 4 mục | — | `📋 Quản lý & Chọn Mẫu...` · `➕ Lưu thành Mẫu mới...` · `📥 Import từ file JSON...` · `📤 Export ra file JSON...` |
| `Bật/Ẩn Preview` | ToggleButton | bool | bật | Tooltip `Ẩn/hiện ảnh và thumbnail trên Canvas; đường dẫn kết quả vẫn được giữ.` |
| `Quản lý Batch` | GhostButton | — | — | Dock `WorkflowBatchQueueDock`; action: `show_batch_queue`, `hide_batch_queue`, `add_batch_projects`, `start_batch_queue`, `pause_batch_queue`, `clear_batch_queue` |
| `✦ AI Agent` | Button `#FooterAiChatBtn` | — | ẩn | Tooltip `Mở Workflow và bật/ẩn AI Agent`; lỗi `Không thể bật cửa sổ AI Agent:` |
| `THƯ VIỆN NODE` | Sidebar trái | — | mở | Tooltip thu gọn: `« Thư viện node: thu gọn/mở rộng thư viện node bên trái.` |
| `📦 0 node` / `🔗 0 kết nối` / `⚠ 0 chưa nối` | Status bar | số | 0/0/0 | |
| `📜 NHẬT KÝ` | Panel log (`#LogContainer`) | — | mở | `📋 Copy` (→ `📋 Đã copy nhật ký vào clipboard`), `🗑 Xóa`; khi đóng → nút `📝 Nhật Ký` |
| *(MiniMap)* | Toggle | bool | tắt | Tooltip `Bật / Tắt Mini Map định vị và di chuyển Node trên Canvas` |

**9 template hệ thống** (`data_general/system_workflows.json` + `data_general/system_workflows/*.json`):

| # | `name` (hiển thị) | `file` | `description` | Kích thước |
|---|---|---|---|---|
| 1 | `Sao chép video Ngoại Nè con` | `sao_chep_video_ngoai_ne_con.json` | `Kịch bản mẫu sao chép video ngoại nè con` | 7 KB |
| 2 | `Thời trang trước gương` | `thoi_trang_truoc_guong_1.json` | `Kịch bản mẫu thời trang trước gương` | 13 KB |
| 3 | `Sao chép người que NEW 1706` | `sao_chep_nguoi_que_new_1706.json` | `Kịch bản mẫu sao chép video Người Que NEW 1706` | 61 KB |
| 4 | `Người que NEW` | `nguoi_que_new.json` | `Kịch bản mẫu video Người Que NEW mới nhất` | 62 KB |
| 5 | `Người Que Tạo Từ Ảnh` | `nguoi_que_tao_tu_anh.json` | `Workflow hệ thống tạo video người que từ ảnh tham chiếu` | 60 KB |
| 6 | `Giới thiệu đồ nội thất` | `gioi_thieu_do_noi_that.json` | `Kịch bản mẫu giới thiệu đồ nội thất chuyên nghiệp` | 19 KB |
| 7 | `Tạp Hóa` | `tap_hoa.json` | `Kịch bản mẫu Tạp Hóa hệ thống` | 73 KB |
| 8 | `Thời Trang Nam 3 cảnh` | `thoi_trang_nam_3_canh.json` | `Kịch bản mẫu Thời Trang Nam 3 cảnh hệ thống` | 30 KB |
| 9 | `Thời Trang Nữ Cầm Điện thoại` | `thoi_trang_nu_cam_dien_thoai.json` | `Kịch bản mẫu Thời Trang Nữ Cầm Điện thoại hệ thống` | 27 KB |

Dialog quản lý mẫu chia 2 nhóm: `Mẫu hệ thống` / `Mẫu cá nhân`, thao tác `Dùng Mẫu (Tạo Bản Sao)`, `Sửa trực tiếp Mẫu`, `Đổi Tên Mẫu`, `Xóa Mẫu`, `Lưu thành Mẫu mới` (`Nhập tên Workflow Mẫu mới:`).

**11 node cốt lõi** (mined nguyên văn từ system prompt của AI Agent — dùng làm mục lục `THƯ VIỆN NODE` phase này): `text_prompt`, `prompt_list`, `upload_media`, `gemini_prompt`, `analyze_video`, `gen_image`, `gen_video`, `create_character`, `create_voice`, `merge_video`, `edit_video`.

Bố cục canvas chuẩn (mined): `input/source → gemini hoặc analyze → prompt_list → gen_image/gen_video → voice/merge/edit/output`; hàng trên = input + nhánh ảnh, hàng dưới = video, hàng cuối = hậu kỳ.

**Luồng:**
1. Chọn pill → `GET /api/workflow/templates` **(chưa tồn tại)** → 9 mẫu hệ thống + mẫu cá nhân.
2. `Workflow Mẫu → 📋 Quản lý & Chọn Mẫu...` → modal 2 nhóm → `Dùng Mẫu (Tạo Bản Sao)` tạo dự án mới và nạp graph.
3. Canvas nạp graph JSON vào React Flow; `📦/🔗/⚠` cập nhật theo `nodes.length`, `edges.length`, số node có port bắt buộc chưa nối.
4. `▶ Chạy` → dispatch tuần tự các node qua `createRequest({type, node_id, params})` (**đã có**) và poll `getRequest(id)`.
5. Log stream vào panel `📜 NHẬT KÝ`.

**Kết quả:** Canvas + panel thuộc tính bên phải + panel log dưới + dock batch. Kết quả từng node hiện dưới dạng thumbnail trên node (tắt được bằng `Bật/Ẩn Preview`).

**Cạm bẫy:**
- **Pill này ẩn mặc định trong bản gốc** (`WORKFLOW_ENABLED` hoặc `--dev-mode`). Quyết định của bản replica: hiện hay ẩn? Nếu hiện mà 25 node chưa xong thì phải là `EmptyState` trung thực, không phải canvas trống nhìn như đã hoạt động.
- **`gen_video` không được prompt-only.** Luật mined nguyên văn: *`gen_video` không được prompt-only: phải có ảnh start/end hoặc character/component/media.* Validator phải chặn trước khi chạy, nếu không Flow trả lỗi mơ hồ sau khi đã tốn thời gian.
- **`prompt_list` là cầu bắt buộc** giữa `gemini_prompt`/`analyze_video` và node tạo. Nối tắt sẽ chạy nhưng ra kết quả sai.
- **Giới hạn theo tier khác nhau giữa hai đường:** LOWER chỉ 4/6/8s và tối đa 3 character/cảnh; OMNI cho 4/6/8/10s và tối đa 10 character/cảnh. UI node phải đọc tier hiện tại chứ đừng hiện cả 4 tuỳ chọn duration cho mọi model.
- 9 file template là graph của **engine Qt**, schema node/port của chúng gần như chắc chắn khác schema React Flow của ta. Cần một lớp chuyển đổi, và nếu chuyển đổi thất bại thì phải báo rõ template nào hỏng — đừng nạp một phần rồi để canvas thiếu node.

---

### 💝 ỦNG HỘ TÁC GIẢ — Trang tĩnh

**Mục đích:** Trang cảm ơn + thông tin chuyển khoản để người dùng ủng hộ tác giả bản gốc.

**Bố cục:**

```
┌ #DonateRoot (background:#0b0e14, AlignCenter) ───────────────────────────┐
│  ┌ #donateCard  (gradient #161b26→#0f121d, viền rgba(0,242,254,.25),   ┐ │
│  │               bo 16px, max-width ~560) ───────────────────────────── │ │
│  │  💝 Sự đồng hành của bạn là niềm cổ vũ vô giá!    (chữ #fbbf24)      │ │
│  │  Hãy tiếp thêm năng lượng để tác giả duy trì và phát triển           │ │
│  │  công cụ hữu ích này!                                                │ │
│  │  Sự ủng hộ từ bạn là nền tảng để tối ưu và nâng cấp tính năng        │ │
│  │  mỗi ngày.                                                           │ │
│  │  ─────────────────────── <hr> ───────────────────────                │ │
│  │  ┌ #qrBgFrame ──────┐   ┌ #detailsFrame ───────────────────────┐    │ │
│  │  │   [ QR image ]   │   │ NGÂN HÀNG      <để trống>            │    │ │
│  │  │  Đang tải QR...  │   │ SỐ TÀI KHOẢN   <để trống>  [📋 Copy] │    │ │
│  │  └──────────────────┘   │ CHỦ TÀI KHOẢN  <để trống>            │    │ │
│  │  Quét mã QR bằng App     └──────────────────────────────────────┘    │ │
│  │  Ngân hàng                                                           │ │
│  │  💡 Lời nhắn chuyển khoản tùy ý. Xin chân thành cảm ơn sự tin        │ │
│  │     tưởng và đồng hành của bạn!                                      │ │
│  └──────────────────────────────────────────────────────────────────────┘ │
│           (hạt bay lơ lửng — QTimer animation, WA_TransparentForMouse)    │
└──────────────────────────────────────────────────────────────────────────┘
```

**Điều khiển:**

| Nhãn | Loại | Giá trị / tập chọn | Mặc định (khoá config) | Ghi chú |
|---|---|---|---|---|
| `💝 ỦNG HỘ TÁC GIẢ` | Pill `#PlatformBtn` | active/inactive | inactive | |
| `💝 Sự đồng hành của bạn là niềm cổ vũ vô giá!` | Tiêu đề (`<span style='font-size:32px'>💝</span>` + text, `color:#fbbf24`) | — | — | Font `Segoe UI` Bold |
| `Hãy tiếp thêm năng lượng để tác giả duy trì và phát triển công cụ hữu ích này!` | Đoạn văn | — | — | |
| `Sự ủng hộ từ bạn là nền tảng để tối ưu và nâng cấp tính năng mỗi ngày.` | Đoạn văn | — | — | |
| *(ảnh QR)* | `<img>` trong `#qrBgFrame` | ảnh cục bộ | `icons/qr_donate_only.png` | Fallback text `Đang tải QR...` |
| `Quét mã QR bằng App Ngân hàng` | Caption | — | — | |
| `NGÂN HÀNG` | Label + value | text | **RỖNG — người dùng tự điền** | Bản gốc nhúng ngân hàng của tác giả |
| `SỐ TÀI KHOẢN` | Label + value (font `Consolas`) | text | **RỖNG — [redacted]** | Bản gốc nhúng số tài khoản thật |
| `📋 Copy` | GhostButton (viền `rgba(34,211,238,.25)`) | — | — | Đổi nhãn thành `✓ Đã chép` trong ~1.5s rồi `reset_copy_btn` |
| `CHỦ TÀI KHOẢN` | Label + value | text | **RỖNG — [redacted]** | Bản gốc nhúng tên thật của tác giả |
| `💡 Lời nhắn chuyển khoản tùy ý. Xin chân thành cảm ơn sự tin tưởng và đồng hành của bạn!` | Ghi chú (`setWordWrap`) | — | — | |

**Luồng:**
1. Chọn pill → render tĩnh, không API.
2. Ảnh QR: đọc `icons/qr_donate_only.png` từ asset root (`GET /api/files` **chưa tồn tại**). Không có → hiện `Đang tải QR...` rồi `EmptyState`.
3. `📋 Copy` → `navigator.clipboard.writeText(<số tài khoản>)` → nhãn đổi `✓ Đã chép`, `setTimeout` 1.5s trả về `📋 Copy`.
4. Hiệu ứng hạt: bản gốc dùng `QTimer` + `update_particles` với `random.uniform` cho tốc độ/biên độ lắc và `WA_TransparentForMouseEvents`. Replica dùng CSS animation `pointer-events:none`, và **tôn trọng `prefers-reduced-motion`** (bản Qt không có lựa chọn đó).

**Kết quả:** Một card tĩnh. Không job, không kết quả.

**Cạm bẫy:**
- **Bản gốc nhúng cứng số tài khoản, tên chủ tài khoản, ngân hàng và một URL VietQR chứa cả số tài khoản lẫn tên tác giả.** Đây là dữ liệu cá nhân của một cá nhân xác định. **Không copy sang repo, không commit, không hiện mặc định.** Ship 3 ô rỗng + một ô cấu hình trong Settings để người dùng tự điền nếu muốn giữ trang này. Nếu người dùng không điền, trang hiện `EmptyState` giải thích, chứ không hiện thông tin của tác giả.
- **URL VietQR sinh động là một request ra ngoài mang theo số tài khoản.** Dùng ảnh cục bộ `icons/qr_donate_only.png` hoặc để trống; đừng gọi `img.vietqr.io` từ ứng dụng local.
- **Không có route phục vụ file** → `icons/qr_donate_only.png` sẽ 404. Cùng blocker với các tab khác.
- Hiệu ứng hạt trong bản gốc chạy `QTimer` liên tục kể cả khi tab ẩn. Trong web, dừng animation khi tab không hiển thị (`IntersectionObserver` hoặc `document.hidden`), nếu không nó đốt CPU nền suốt phiên.


---

## Endpoint mới cần dựng (tổng hợp từ mọi tab)

| Method | Path | Mục đích | Request | Response |
| --- | --- | --- | --- | --- |
| `POST` | `/api/requests  (type: "gen_video_text")` | Text to Video thật — Flow CÓ endpoint t2v (`https://aisandbox-pa.googleapis.com/v1/video:batchAsyncGenerateVideoText`, hằng `URL_GENERATE_TEXT_TO_VIDEO` trong exe) và exe có sẵn họ key `veo_3_1_t2v_fast*` / `veo_3_1_t2v_fast_4s\|6s` / `veo_3_1_t2v_lite*` / `veo_3_1_t2v_lite_4s\|6s[_low_priority]` / `abra_t2v_*`. flow_sdk.py hiện KHÔNG có VIDEO_T2V_URL, không có nhánh t2v trong VIDEO_MODEL_KEYS, và processor.py không đăng ký handler. Thêm cái này loại bỏ hoàn toàn chuỗi gen_image→gen_video và tiền ảnh đi kèm. | { type: "gen_video_text", params: { prompt: string, project_id: string, aspect_ratio: "VIDEO_ASPECT_RATIO_PORTRAIT"\|"VIDEO_ASPECT_RATIO_LANDSCAPE", paygate_tier: "PAYGATE_TIER_ONE"\|"PAYGATE_TIER_TWO", video_quality?: "fast"\|"lite"\|"quality"\|"lite_relaxed"\|"fast_relaxed", duration_s?: 4\|6\|8, scene_id?: string, output_count?: number } } | RequestDTO — result: { raw, operation_names: string[], workflows?: [{name, primary_media_id}], media_ids?: string[] }; error: "missing_prompt" \| "missing_project_id" \| "invalid_project_id" \| "paygate_tier_unknown" \| "no_video_model_for_tier_<t>_quality_<q>_aspect_<a>" \| "no_operations_in_response" |
| `PATCH` | `agent/flowboard/services/flow_sdk.py — thêm VIDEO_MODEL_KEYS_FL + resolve_video_model_fl()` | Video Start-End đang gửi model key NON-FL vào endpoint FL. gen_video() route đúng VIDEO_I2V_FL_URL khi có end_media_id, nhưng resolve_video_model() chỉ trả veo_3_1_i2v_lite / _s_fast[_portrait][_ultra] / _s[_portrait]. Exe dùng đúng 6 key FL: veo_3_1_i2v_s_fast_fl (LANDSCAPE_FL_NORMAL), veo_3_1_i2v_s_fast_ultra_fl (LANDSCAPE_FL_ULTRA), veo_3_1_i2v_s_fast_fl_ultra_relaxed, veo_3_1_i2v_s_fast_portrait_fl (PORTRAIT_FL_NORMAL), veo_3_1_i2v_s_fast_portrait_fl_ultra, veo_3_1_i2v_s_fast_portrait_fl_ultra_relaxed. Không có FL cho lite / lower-priority / quality / 4s / 6s. | resolve_video_model(paygate_tier, aspect_ratio, quality, is_start_end: bool=False) -> Optional[str]; khi is_start_end=True chỉ giải trong họ _s_fast*_fl và bỏ qua duration. | str model_key, hoặc None → gen_video trả {"error": "no_fl_video_model_for_tier_<t>_aspect_<a>"} để UI báo thay vì để Flow âm thầm bỏ endImage. |
| `PATCH` | `/api/requests  (type: "gen_video_omni") — thêm params `video_model_key`, `prompt_parts`, `voice_id`` | Character Sync. (1) gen_video_omni ép abra_r2v_* nên không chạm được 4 key Veo r2v mined (veo_3_1_r2v_fast_portrait[_ultra][_relaxed], veo_3_1_r2v_lite_low_priority) → chọn model Veo trên UI âm thầm chạy OMNI với biểu giá khác. (2) Bản gốc gửi structuredPrompt.parts xen kẽ text/reference (hàm _build_structured_prompt_parts), thay {Tên} TẠI CHỖ bằng part reference {mediaId, handle, fileName}; bản cục bộ gửi parts=[{text}] + referenceImages rời → mất liên kết tên↔vị trí. (3) voice_id có trong build_payload_generate_video_reference của exe nhưng không có ở SDK cục bộ. | { type: "gen_video_omni", params: { prompt: string, prompt_parts?: Array<{text: string} \| {reference: {mediaId: string, handle: string, fileName: string}}>, project_id: string, ref_media_ids: string[], duration_s: 4\|6\|8\|10, aspect_ratio, paygate_tier, video_model_key?: string, voice_id?: string, seed?: number } } | RequestDTO — result: { raw, operation_names, sync_failures?: [[media_id, reason]], workflows? }; error: "missing_ref_media_ids" \| "invalid_duration_s" \| "omni_aspect_unsupported_<a>" \| "sync_failed: <reason>" \| "too_many_refs_for_model_<key>_limit_<n>" (mới — 3 cho veo_3_1_r2v_*, 10 cho abra_r2v_*, theo reference_image_limit_for_model_key mined) |
| `GET` | `/api/activity?board_id=<id>&project_id=<flow_project_id>` | JobList của mỗi tab phải được scope theo dự án đang chọn (`Chọn dự án`). list_activity() hiện chỉ nhận limit / before_id / type — không có bộ lọc board hay project, nên bốn tab sẽ dùng chung một feed và người dùng thấy job của dự án khác lẫn vào. | query: limit?: number (1..200, default 50), before_id?: number, type?: string (CSV), board_id?: number, project_id?: string | { items: [{id, type, status, node_id, node_short_id, created_at, finished_at, duration_ms}], next_before_id: number \| null } |
| `POST` | `/api/activity/cancel-batch` | Nút `DỪNG` / `⏸️ DỪNG LẠI` phải huỷ cả loạt. Hiện chỉ có cancelActivity(id) đơn lẻ → N request HTTP song song, dễ để lọt một job vừa chuyển sang running giữa chừng và job đó vẫn trừ credit sau khi người dùng đã bấm dừng. | { request_ids: number[] }  hoặc  { board_id: number, statuses?: ("queued"\|"running")[] } | { canceled: number[], not_cancelable: [{id, status}] } |
| `POST` | `/api/idea/expand` | Chạy pipeline 7 bước Idea to Video (nhân vật → kịch bản → prompt cảnh → ảnh tham chiếu → map) bằng provider LLM đã cấu hình; stream log qua SSE. | {projectId: string, idea: string, sceneCount: int(1..500), style: string, language: 'vi-VN'\|'en-US'\|'zh-CN', dialogueMode: 'dialogue'\|'narration', noDialogue: bool, bgMusic: bool, keepChars: bool, resumeMode: 'new'\|'continue', geminiModel: string} | SSE events {step:'step1'\|'step1_5'\|'step2'\|'step3'\|'step4'\|'step5', log:string, partial?:object} rồi {characters:[{charId,name,description,refPrompt}], scenes:[{index, prompt, charIds[], backgroundId, dialogueSequence[]}], files:{step1,step1_5,step2,step3,step4,step5}} |
| `GET` | `/api/idea/{projectId}/progress` | Phát hiện dữ liệu chạy dở để hiện modal 'Phát hiện dữ liệu cũ' (VIẾT TIẾP / TẠO MỚI). | — | {hasData: bool, step1Done, step1_5Done, step2Done, step2Partial, step2Total, step3Done, step3Partial, step3Total, step4Done, resumeFrom: 'step1'\|'step1_5'\|'step2'\|'step3'\|'step4'\|'step5', info:{step1,step2,step3,step4}} |
| `POST` | `/api/idea/{projectId}/stop` | Dừng toàn bộ worker của một dự án Idea to Video, giữ nguyên dữ liệu đã hoàn thành. | {} | {stopped: int, kept: int} |
| `GET` | `/api/idea/{projectId}/scenes` | Nạp lại bảng cảnh sửa được sau khi reload trang. | — | {scenes:[{index, prompt, refMediaId?, status, requestId?, videoPath?}]} |
| `PATCH` | `/api/idea/{projectId}/scenes/{index}` | Lưu prompt cảnh đã sửa tay trong PromptTable / dialog 'Sửa Prompt Cảnh <n>'. | {prompt?: string, refMediaId?: string\|null} | {index:int, prompt:string, refMediaId:string\|null} |
| `POST` | `/api/video-styles` | Thêm/sửa phong cách (nút ➕ / ✏️ trong Idea to Video và Phân tích video). Đọc đã có listVideoStyles(). | {name: string, description: string} | {name:string, description:string} |
| `DELETE` | `/api/video-styles/{name}` | Xóa phong cách (nút 🗑, xác nhận 'Xóa phong cách <name>?'). | — | {deleted: bool} |
| `POST` | `/api/analyze/ingest` | Nạp video nguồn cho tab Phân tích video: nhận diện Facebook/YouTube/TikTok/local, tải về (tikwm API → fallback yt-dlp), remux H.264 nếu cần. | {source: string, projectId: string} | {sourceId: string, path: string, title: string, durationSeconds: float, width: int, height: int, hasAudio: bool, sourceType: 'facebook'\|'youtube'\|'tiktok'\|'local'} |
| `POST` | `/api/analyze/frames` | Tách frame rồi ghép thành contact sheet để gửi cho LLM thị giác (provider Gemini CLI không nhận mp4, chỉ nhận ảnh). | {sourceId: string, everyNSeconds?: float, maxFrames?: int, sheetCols?: int, sheetRows?: int} | {sheets:[{mediaId: string, fromSec: float, toSec: float, frameIndices:[int]}], frameCount: int, fps: float} |
| `POST` | `/api/requests (type: 'analyze_video')` | Loại request MỚI cho worker: chạy phân tích video theo 1 trong 4 template system_prompts và trả JSON cảnh + prompt VEO 3. | createRequest({type:'analyze_video', params:{sourceId, sheetMediaIds:[string], transcript?: string, mode:'standard'\|'text'\|'stick_figure'\|'health', language:'vi'\|'en', style?: string, voice?: string, customVoicePrompt?: string, customInstruction?: string, lockSceneCount: bool, targetDuration?: int, refMediaIds?:[string](<=3), characterNames?:[string], geminiModel: string, retry503Count: int, autoSwitchModel503: bool, fallbackGeminiModel: string}}) | RequestDTO; khi done: result = {scenes:[{sceneNumber, imagePrompt, veoPrompt, dialogueSequence:[string], narrationVoice}], thumbnailPrompt: string, title: string, vietnameseScript: string, rawText: string} |
| `GET` | `/api/analyze/{projectId}/export` | Nút '📊 Xem kết quả' — xuất kết quả phân tích ra CSV/XLSX (và ghi ngược vào file Excel đã Import). | query: format=csv\|xlsx | file stream (text/csv \| application/vnd.openxmlformats-officedocument.spreadsheetml.sheet) |
| `POST` | `/api/postprod/stt` | Bóc băng audio của video bằng Gemini audio STT (thay CapCut STT đã gỡ) và xuất SRT/TXT; hỗ trợ dịch. | {video: string, language: 'vi-VN'\|'en-US'\|'zh-CN', useTranslation: bool = false, translationLanguage?: string, wordLevel: bool = true} | {srtPath: string, txtPath: string, wordLevel: bool, segments:[{startMs:int, endMs:int, text:string, words?:[{startMs,endMs,text}]}]} |
| `POST` | `/api/postprod/subtitles-styled` | Burn phụ đề theo 7 kiểu ASS + 39 preset chữ nghệ thuật. Thay thế /api/postprod/subtitles (endpoint cũ thiếu styleType, inactiveColor, artEffect và ép outlineWidth về int). | {video: string, srt: string, output: string, font: string='Arial', size: int=24, primaryColor: string='&H00FFFFFF', outlineColor: string='&H00000000', inactiveColor: string='&H00808080', outlineWidth: float=2.5, shadow: float=0.0, marginV: int=150, styleType: 'Standard SRT'\|'Bounce Pop ASS'\|'Zoom Pop ASS'\|'Tilt Bounce ASS'\|'Word Highlight ASS'\|'Neon Glow ASS'\|'Karaoke ASS', artEffect: string='None'} | {path: string, engine: 'ass'\|'pictex', durationSeconds: float} |
| `GET` | `/api/postprod/art-presets` | Trả 39 preset chữ nghệ thuật kèm màu/gradient để FE render đúng thumbnail trong dropdown (TextArtDelegate). | — | {presets:[{name: string, colors:[string], stroke:{width:float,color:string}, shadow:{offset:[float,float], blurRadius:float, color:string}\|null}]} |
| `POST` | `/api/postprod/preview-frame` | Lấy 1 frame làm nền cho canvas xem thử (cả tab Tạo phụ đề và Xóa watermark). | {video: string, atSeconds: float = 0.5, maxWidth: int = 960} | {mediaUrl: string, width: int, height: int, durationSeconds: float} |
| `POST` | `/api/postprod/delogo` | Chế độ 'Làm Mờ' — ffmpeg delogo trên ROI, toạ độ được scale theo kích thước video thật. | {video: string, output: string, x: int, y: int, w: int, h: int, refWidth?: int=1920, refHeight?: int=1080} | {path: string, appliedRegion:{x:int,y:int,w:int,h:int}, durationSeconds: float} |
| `POST` | `/api/postprod/crop-zoom` | Chế độ 'Crop/Zoom' — phóng khung rồi crop về kích thước gốc để đẩy watermark ra ngoài mép. | {video: string, output: string, zoomRatio: float = 1.07, anchor?: 'center'\|'tl'\|'tr'\|'bl'\|'br'} | {path: string, croppedPixels:{left:int,top:int,right:int,bottom:int}, durationSeconds: float} |
| `POST` | `/api/postprod/watermark-ai` | Nhánh AI MI-GAN: chia chunk bằng ffmpeg, gọi GeminiWatermarkTool-Video.exe từng chunk (GPU, fallback CPU), vá frame bỏ qua bằng migan_pipeline_v2.onnx, nối lại và mux audio gốc. | {video: string, output: string, mark: 'diamond' = 'diamond', variant: 'auto'\|'ai' = 'auto', sigma: int = 20, maxFramesPerChunk?: int} | {path: string, changed: bool, elapsedMs: int, usedGpu: bool, usedCpu: bool, chunkCount: int, patchedFrameCount: int, message: string} |
| `GET` | `/api/postprod/watermark-ai/status` | Kiểm tra engine AI có sẵn không, để ẩn lựa chọn 'AI MI-GAN' khi thiếu exe/onnx thay vì để nút fail lúc bấm. | — | {executableAvailable: bool, miganModelAvailable: bool, gpuAvailable: bool, executablePath: string\|null} |
| `POST` | `/api/files/stage` | Nhận một file cục bộ do trình duyệt upload (ảnh HOẶC video) và lưu vào STORAGE_DIR/staging, trả về đường dẫn server-side hợp lệ cho /api/postprod/*. Bắt buộc cho cả hai tab Upscale: /api/postprod/upscale chỉ nhận path (_existing() giới hạn trong STORAGE_DIR + ASSET_ROOT) còn /api/upload hiện có thì chỉ nhận ảnh VÀ đẩy lên Flow (tốn round-trip, sai mục đích cho pipeline offline). | multipart/form-data: file: UploadFile (image/* hoặc video/mp4\|quicktime\|x-msvideo), kind: 'image'\|'video' | { path: string, name: string, bytes: number, mime: string, width?: number, height?: number, durationSeconds?: number } |
| `POST` | `/api/media/{media_id}/upscale` | Upscale một media ĐÃ SINH bằng mediaId thay vì đường dẫn. Cần cho CREATE_IMAGE_QUALITY = 2k\|4k\|4k_x4plus ở Text to Image / Image to Image / Affiliate: frontend chỉ có mediaId, không có đường dẫn cache trên đĩa (getMediaStatus chỉ trả {available, has_url, mime}). Backend tự resolve media_service.cached_path(), fetch_and_cache() nếu chưa có, chạy upscale.upscale_image, và trả file kết quả. | { quality: '2k'\|'4k'\|'4k_x4plus', model?: string, scale?: number (mặc định 4), targetHeight?: number, useCpu?: boolean } | { path: string, mediaId: string, model: string, scale: number, width: number, height: number } |
| `POST` | `/api/postprod/upscale/jobs` | Biến upscale thành job bất đồng bộ. POST /api/postprod/upscale hiện tại giữ kết nối HTTP tới khi render xong (hàng chục giây cho ảnh 4K, hàng chục phút cho video) và không có cách huỷ, nên nút '🛑 DỪNG' / '⏹ DỪNG' của cả hai tab Upscale là nút giả. Trả jobId ngay để UI poll tiến độ và huỷ. Thay thế được nếu ta thêm 'upscale' làm request type của createRequest (tái dùng queue/poll/cancel sẵn có). | { source: string, output: string, kind: 'image'\|'video', scale?: number, model?: string, targetHeight?: number, useCpu?: boolean } | { jobId: string, status: 'queued' } |
| `GET` | `/api/postprod/upscale/jobs/{job_id}` | Poll tiến độ một job upscale: phần trăm frame đã xử lý (engine in '[i/n]' mỗi ảnh xong), trạng thái, đường dẫn kết quả, và đuôi stderr khi lỗi để hiển thị đúng thông điệp chẩn đoán (thiếu VC++ Redist / GPU không hỗ trợ Vulkan / engine hỏng). | — | { jobId: string, status: 'queued'\|'running'\|'done'\|'failed'\|'canceled', progress: number, framesDone?: number, framesTotal?: number, path?: string, durationSeconds?: number, error?: string, stderrTail?: string[] } |
| `POST` | `/api/postprod/upscale/jobs/{job_id}/cancel` | Huỷ một job upscale đang chạy (terminate tiến trình engine + dọn thư mục frame tạm). Không có nó thì nút DỪNG chỉ là trang trí và thư mục frames_out của video 4K để lại hàng GB rác. | — | { jobId: string, status: 'canceled' } |
| `POST` | `/api/postprod/probe` | Trả metadata của một file đã stage (width, height, fps, duration, có audio hay không) để tab Upscale Video hiển thị kích thước nguồn, tính targetHeight đúng, ước lượng dung lượng đĩa cần cho frames_out, và cảnh báo trước khi tách frame một clip dài. | { source: string } | { width: number, height: number, fps: number, durationSeconds: number, hasAudio: boolean, codec: string, bytes: number } |
| `GET` | `/api/affiliate/presets` | Trả DEFAULT_CATEGORY_DATA đã mine từ exe: 5 category (thoi_trang, my_pham, thuc_pham_chuc_nang, gia_dung, do_vat_khac), mỗi category 30 tư thế P01..P30 và 30 bối cảnh S01..S30 với {label tiếng Việt, description prompt tiếng Anh}, kèm cờ isDefault để chặn xoá preset hệ thống. Gộp thêm preset user tự thêm (bản gốc lưu ở data_general/affiliate_poses_scenes.json). | — | { categories: { [key: string]: { label: string, poses: Array<{id: string, label: string, description: string, isDefault: boolean}>, scenes: Array<{id: string, label: string, description: string, isDefault: boolean}> } } } |
| `PUT` | `/api/affiliate/presets` | Ghi preset tư thế/bối cảnh do user tự thêm hoặc xoá, tương ứng nút '➕ Thêm' / '🗑 Xóa' của combo Tư thế và Bối cảnh. Phải từ chối xoá preset hệ thống (trả lỗi để UI hiện 'Tư thế "{x}" là mặc định của hệ thống.') và từ chối xoá mục cuối cùng ('⚠️ Không thể xóa — phải có ít nhất 1 tư thế'). | { category: string, kind: 'pose'\|'scene', op: 'add'\|'delete', id?: string, label?: string, description?: string } | { ok: true, poses: Array<{id,label,description,isDefault}>, scenes: Array<{id,label,description,isDefault}> } |
| `GET` | `/api/affiliate/state` | Đọc affiliate_state.json (kol_image, product_image, extracted_image, result_image, video_prompts, saved_media_id, auto_video_count, vo_type, vo_lang, product_intro_checked/text, category_index, project_index, pose_index, scene_index, quality_index, model_index, custom_prompt_checked/text, one_motion_checked, only_outfit_checked, gemini_model, aspect_ratio, video_custom_requirement, extra_blocks) để tab Affiliate khôi phục phiên làm việc ('✨ Đã khôi phục trạng thái cũ. Bạn có thể tiếp tục tạo video.'). | — | AffiliateState — đúng schema affiliate_state.json, extra_blocks là mảng cùng shape cho các hàng sản phẩm phụ |
| `PUT` | `/api/affiliate/state` | Ghi affiliate_state.json sau mỗi thay đổi control (bản gốc gọi _save_affiliate_state trên mọi signal). Lỗi ghi phải trả về để UI log 'Error saving affiliate state:'. | AffiliateState (partial merge) | { ok: true } |
| `POST` | `/api/affiliate/script` | Sinh N prompt cảnh video từ ảnh kết quả Bước 2 + thông tin sản phẩm + cấu hình thoại, cho nút '🧠 VIẾT PROMPT'. autoPrompt/autoPromptBatch hiện có đều gắn với node_id của board, còn tab Affiliate không có node nào. Backend ghép system prompt (gồm header 'GLOBAL CRITICAL REQUIREMENT (MUST FOLLOW ABOVE ALL ELSE):' + videoCustomRequirement) rồi gọi provider LLM đang cấu hình, trả JSON array chuỗi. | { resultMediaId: string, sceneCount: number (1-4), voType: 0\|1\|2, voLang: 'vi'\|'en', productIntro?: string, videoCustomRequirement?: string, oneMotion?: boolean, category: string, model?: string } | { prompts: string[], model: string, tokensUsed?: number } |
| `GET` | `/api/flow/image-models` | Trả bảng ánh xạ nhãn hiển thị → key model Flow để combo 'Model Tạo ảnh' / '🎨 Model' không hardcode. Cần thiết vì flow_sdk.IMAGE_MODELS hiện chỉ có NANO_BANANA_PRO→GEM_PIX_2 và NANO_BANANA_2→NARWHAL, còn resolve_image_model() ÂM THẦM rơi về Pro với key lạ — nghĩa là chọn 'Nano Banana LITE' hôm nay chạy Pro và tính tiền như Pro. Endpoint này (cùng việc bổ sung NANO_BANANA_LITE→HARBOR_SEAL và NANO_BANANA→GEM_PIX vào IMAGE_MODELS) làm cho sự khác biệt trở nên hiển thị được. | — | { models: Array<{ key: string, flowKey: string, label: string, supportsHighQuality: boolean }> } |
| `GET` | `/api/settings/config` | Đọc toàn bộ data_general/config.json cho tab Settings, với mọi khoá credential bị mask. | none | { config: Record<string, unknown>, masked: string[], flags: Record<string, boolean> } — mọi khoá khớp CREDENTIAL_KEYS có value=null và một khoá đồng hành "<key>__configured": boolean. Thêm geminiKeyCount: number (số dòng trong gemini_api_key.txt, không trả nội dung). |
| `PUT` | `/api/settings/config` | Ghi delta cấu hình; tự mirror sang alias chữ thường (RUN_CAPTCHA↔run_captcha, SKIP_AUDIO_ERROR↔skip_audio_error, TOKEN_REFRESH_INTERVAL↔token_refresh_interval, VIDEO_OUTPUT_DIR↔video_output_dir, CURRENT_PROJECT↔current_project, PROJECTS↔projects, WORKFLOW_IMAGE_ONLY_NAMED_REFERENCES↔lowercase). | { [key: string]: unknown } — chỉ các khoá thay đổi | { config: <giống GET, đã mask> } · 400 nếu body chứa bất kỳ khoá credential nào: { detail: "credential keys are not writable through this endpoint", keys: string[] } |
| `PUT` | `/api/settings/gemini-key` | Endpoint chuyên dụng duy nhất để đặt/xoá Gemini API key (ghi data_general/gemini_api_key.txt). Theo đúng khuôn setLlmApiKey đã có. | { apiKey: string \| null }  (null = xoá) | { configured: boolean, keyCount: number } — không bao giờ echo lại key |
| `GET` | `/api/settings/credits` | Loại tài khoản + credit còn lại của phiên Google Flow hiện tại, cho chip Settings và dải credit của trang NHẬT KÝ. | none | { accountType: string \| null, credits: number \| null, checkedAt: string \| null, state: "ok" \| "no_token" \| "login_required" \| "error", message: string \| null } — credits=null khi chưa biết (KHÁC với 0) |
| `GET` | `/api/settings/profiles` | Liệt kê các Chrome profile bền vững (PROFILE_1, PROFILE_VEO3_TOKEN, CHATGPT, GROK) và trạng thái chạy. | none | { profiles: { name: string, dir: string, exists: boolean, running: boolean, cdpHost: string \| null, cdpPort: number \| null }[] } |
| `POST` | `/api/settings/profiles/{name}/open` | Mở Chrome với profile bền vững đã đặt tên tới một URL. Thay thế hoàn toàn cho nút 'AUTO Login TK Veo3' của bản gốc — không nhận mật khẩu. | { url?: string }  (mặc định theo profile: PROFILE_1→https://labs.google/fx/vi/tools/flow, GROK→https://grok.com/, CHATGPT→https://chatgpt.com/) | { ok: true, pid: number, cdpHost: string, cdpPort: number, profileDir: string } |
| `POST` | `/api/settings/profiles/{name}/close` | Tắt nhẹ nhàng Chrome của profile đó để flush cookie/session (bản gốc: '🧹 Đang đóng Chrome nhẹ nhàng dưới nền để lưu session...'). | none | { ok: true, stopped: boolean } |
| `DELETE` | `/api/settings/profiles/{name}` | Xoá thư mục profile Chrome sau khi người dùng xác nhận. | none | { deleted: boolean, wasRunning: boolean } · 404 nếu profile không tồn tại |
| `GET` | `/api/settings/grok-config` | Đọc data_general/grok_config.json (45 khoá GROK_*) cho tab Cài đặt GROK. | none | { config: Record<string, unknown> } — mask grok_account.* |
| `PUT` | `/api/settings/grok-config` | Ghi grok_config.json + 5 khoá GROK_* trong config.json. | { GROK_ACCOUNT_TYPE?: "SUPER"\|"NORMAL", GROK_VIDEO_LENGTH_SECONDS?: 6\|10, GROK_VIDEO_RESOLUTION?: "480p"\|"720p", GROK_MULTI_VIDEO?: number } | { config: Record<string, unknown> } · 400 nếu NORMAL kèm length=10 hoặc resolution=720p |
| `POST` | `/api/postprod/cut` | Chẻ một video thành N đoạn đều nhau bằng ffmpeg — nửa 'Cắt' của tab Cut & Merge Video hiện KHÔNG có backend nào. | { source: string, segmentSeconds: number (>=1), outputDir?: string, reencode?: boolean (mặc định true) } | { segments: { path: string, startSeconds: number, endSeconds: number }[], sourceDurationSeconds: number, usedStreamCopyFallback: boolean } |
| `GET` | `/api/files` | Phục vụ một file cục bộ trong STORAGE_DIR hoặc ASSET_ROOT. KHÔNG có mount static nào trong main.py, nên hiện tại KHÔNG thể phát video render, nghe nhạc nền, nghe thử voice, hay hiện ảnh QR. | query: ?path=<absolute path>  (validate bằng đúng logic _existing() của postprod.py: resolve rồi kiểm tra containment) | FileResponse với Content-Type suy từ đuôi + hỗ trợ Range (bắt buộc để tua video) · 400 nếu path nằm ngoài các root cho phép |
| `GET` | `/api/assets/voice-sample/{voice}` | Trả file voice/{voice}.wav để nghe thử giọng ('Nghe thử giọng mẫu') mà không tốn một lần gọi TTS. | path param: tên voice thuộc tts.VOICES | audio/wav · 404 nếu asset root không có file mẫu đó |
| `GET` | `/api/assets/bgm/{name}` | Stream một track trong nhac_nen/ để nghe thử trước khi mix. Tên file có dấu tiếng Việt và dấu cách nên phải URL-encode. | path param: tên file đúng như trong postprod/status → bgm[] | audio/mpeg + hỗ trợ Range · 404 nếu không khớp |
| `GET` | `/api/guide` | Đọc và parse data_general/huong_dan_su_dung_tool.md theo đúng 2 luật regex của bản gốc; tự tạo file từ DEFAULT_GUIDE_TEXT nếu thiếu. | none | { path: string, groups: { title: string, lines: string[] }[], skippedLineCount: number, createdFromDefault: boolean } — groups rỗng thì trả nhóm fallback { title: "1) Hướng Dẫn Sử Dụng", lines: ["Nội dung file hướng dẫn chưa đúng định dạng.", "Vui lòng chỉnh lại file: <path>"] } |
| `GET` | `/api/videoclone/projects` | Liệt kê các dự án Video Clone (thư mục con của downloads/clone_projects/). | none | { projects: { name: string, dir: string, videoCount: number, analyzedCount: number }[], current: string } |
| `POST` | `/api/videoclone/projects` | Tạo dự án mới với cấu trúc <root>/<tên>/data + video_goc. | { name: string } | { name: string, dir: string } · 409 nếu trùng tên |
| `DELETE` | `/api/videoclone/projects/{name}` | Xoá dự án và toàn bộ prompt + video gốc. | none | { deleted: true, removedFiles: number } |
| `GET` | `/api/videoclone/projects/{name}/data` | Đọc data_clone.json (13 khoá) của dự án. | none | { last_project: string, video_links: string, language: "vi"\|"en", duration: string, voice_index: number, threads: number, ref_image_enabled: "yes"\|"no", ref_images: string[], ref_image_names: string[], gemini_model: string, lock_scene: boolean, style_index: number, custom_instruction: string } |
| `PUT` | `/api/videoclone/projects/{name}/data` | Ghi lại data_clone.json (auto-save có debounce). | delta của 13 khoá trên | { data: <đầy đủ 13 khoá> } |
| `POST` | `/api/videoclone/analyze` | Xếp hàng phân tích cho N link: tải video (tikwm → yt-dlp → remux H.264) rồi gọi Gemini sinh prompt/kịch bản. | { project: string, links: string[], language, duration, voiceDescription, style, customInstruction, refImages, refImageNames, threads, lockScene, geminiModel, useChromeGemini: boolean } | { jobId: string, total: number } · 400 { detail: "missing gemini api key" } khi geminiModel!=='chrome_gemini' và chưa có key |
| `GET` | `/api/videoclone/jobs/{jobId}` | Poll tiến độ + log + kết quả từng item. | none | { status: "running"\|"done"\|"stopped"\|"failed", done: number, total: number, skipped: number, errors: number, log: string[], items: { url: string, status: string, videoPath: string \| null, characters: string, script: string, imagePrompts: string[], videoPrompts: string[], social: string, error: string \| null }[] } |
| `POST` | `/api/videoclone/jobs/{jobId}/stop` | Dừng hàng đợi, giữ nguyên các item đã hoàn tất. | none | { stopped: true, completed: number, skipped: number } |
| `POST` | `/api/videoclone/import` | Nạp danh sách link từ .xlsx/.csv (mẫu: data_general/excel_mau_batch.xlsx) và trả về các dòng chưa phân tích. | multipart/form-data: file + project | { links: string[], alreadyAnalyzed: number, rowMap: Record<string, number>, isExcel: boolean } |
| `GET` | `/api/videoclone/styles` | Thư viện giọng đọc + phong cách dùng chung (voice_styles.json, video_styles.json + mục người dùng tự thêm). Trả kèm ID BỀN để thay cho voice_index/style_index dạng số. | none | { voices: { id: string, title: string, description: string, builtin: boolean }[], styles: { id: string, name: string, description: string, builtin: boolean }[] } |
| `POST` | `/api/videoclone/styles` | Thêm một giọng đọc hoặc phong cách do người dùng tự tạo. | { kind: "voice"\|"style", name: string, description: string } | { id: string, kind: string, name: string, description: string } |
| `DELETE` | `/api/videoclone/styles/{id}` | Xoá một giọng/phong cách người dùng tự tạo (không xoá được mục builtin). | none | { deleted: true } · 400 nếu builtin |
| `GET` | `/api/workflow/templates` | 9 template hệ thống trong data_general/system_workflows/ + template cá nhân, cho menu 'Workflow Mẫu'. | none | { system: { name: string, file: string, description: string, nodeCount: number, convertible: boolean }[], personal: { name: string, file: string, updatedAt: string }[] } |
| `GET` | `/api/workflow/templates/{file}` | Tải graph JSON của một template và chuyển sang schema React Flow. | none | { nodes: unknown[], edges: unknown[], warnings: string[] } — warnings liệt kê các node Qt không ánh xạ được, thay vì im lặng bỏ qua |
| `GET` | `/api/activity/{id}` | MỞ RỘNG endpoint đã có: thêm đường dẫn file kết quả để các nút '🔗 Nối video' / '✂️ Cắt ảnh cuối' của trang NHẬT KÝ nối được vào /api/postprod/*. | none | thêm vào response hiện tại: { outputPath: string \| null, outputPaths: string[], mediaIds: string[] } |
| `GET` | `/api/postprod/status` | MỞ RỘNG endpoint đã có: thêm cờ khả dụng của Suno để card Suno bị ẩn khi thiếu key (thay vì hiện một nút chắc chắn hỏng). | none | thêm vào CapabilityStatus: { sunoKeyAvailable: boolean } |


## Câu hỏi còn mở

- MULTI_VIDEO=4 nghĩa là gì? Bằng chứng mined mâu thuẫn: `_resolve_worker_max_in_flight` đọc MULTI_VIDEO (⇒ số job song song), còn payload `output_count` đến từ `_resolve_output_count` ← OUTPUT_COUNT=1; nhưng ImageToVideoWorkflow lại có nhánh `output_count = … MULTI_VIDEO … 1`. Log runtime in cả hai (`⚙️ Cấu hình chạy: MULTI_VIDEO=<n> | OUTPUT_COUNT=<m>`). Toàn bộ phần hiển thị chi phí credit trên UI phụ thuộc câu trả lời này — cần chốt trước khi viết cảnh báo credit.
- Tab Character Sync có được phép chạy 16:9 không? Kiểm kê key r2v trong exe chỉ có veo_3_1_r2v_fast_portrait, _fast_portrait_ultra, _fast_portrait_ultra_relaxed và _lite_low_priority — không có key landscape họ fast. Nếu chủ dự án xác nhận 16:9 không dùng được, hãy khoá `📏 Tỷ lệ` về `Dọc 9:16` trên tab đó; nếu có, cần một curl 16:9 thật để bổ sung key.
- Video Start-End: khi người dùng chọn `Veo 3.1 - Lite [Lower Priority]` (đúng default trong config.json) thì bản gốc fallback về Fast-FL hay báo lỗi? Không có key FL nào cho lite/lower-priority. Cần quyết định: (a) tự hạ về `Veo 3.1 - Fast` kèm toast, hay (b) disable các lựa chọn không có FL ngay trên combo.
- Text to Video đi nhánh nào? (A) thêm request type `gen_video_text` — đúng bản gốc, không tốn credit ảnh; hay (B) chuỗi gen_image→gen_video — chạy được ngay nhưng nhân đôi chi phí và đẩy toàn bộ retry/cancel/credit lên frontend. Đây là quyết định phạm vi, không phải kỹ thuật.
- `🧩 Chạy từ các thành phần` và `🎙️ Đồng Bộ Giọng Nói` có nằm trong phạm vi bản replica không? Cả hai không có đường backend cục bộ. Ẩn hẳn (gọn hơn) hay render disabled kèm tooltip (trung thành hơn với ảnh chụp bản gốc)?
- Bản gốc lưu ra `<VIDEO_OUTPUT_DIR>/<dự án>/{image,video,thumbnail}` và `📁 Xem kết quả` mở Explorer. Bản web không làm được. ResultsDrawer (dựng từ getActivityList + mediaUrl) có được coi là tương đương chấp nhận được, hay cần thêm endpoint tải file hàng loạt?
- `Chọn dự án` sẽ ánh xạ 1-1 sang Board của flowboard-local (listBoards / createBoard / deleteBoard + ensureBoardProject) — xác nhận cách hiểu này. Không có endpoint nào liệt kê project Flow cho UI (`/api/flow/projects` cố ý KHÔNG trả danh sách, chỉ trả trạng thái sync từng board).
- `audioFailurePreference`: nên theo exe (`RETURN_SILENCED_VIDEOS` khi SKIP_AUDIO_ERROR=true, mặc định) hay giữ hardcode `BLOCK_SILENCED_VIDEOS` của gen_video_omni? Chọn sai làm tỉ lệ 'failed' tăng vọt so với tool cũ trên cùng prompt.
- Bảng ảnh↔prompt hiện ghép thuần theo vị trí và cho phép `▲`/`▼` di chuyển từng cột riêng (ở Start-End dễ làm xáo cặp). Replica có được phép đổi thành 'di chuyển cả hàng' — tức cố ý khác bản gốc để tránh lỗi thầm lặng — hay phải giữ nguyên hành vi cũ?
- Sidebar 1:1 hay tách đôi? Exe chỉ có MỘT mục `Phụ Đề & Xóa Logo` (mount CreateSubTab, đã chứa cả nhóm `✂️ Xóa Logo & Watermark`); `RemoveWatermarkTab` không tìm thấy điểm mount. Spec này tách thành 2 tab theo yêu cầu — cần chốt: giữ 2 mục sidebar (lệch exe) hay gộp lại 1 mục với 2 checkbox `Tạo Phụ Đề` / `Xóa Logo` (đúng exe)?
- STT word-level: 2 kiểu phụ đề `Tô sáng từ (Word Highlight)` và `Karaoke chạy chữ` cần timestamp theo TỪ. Provider Gemini audio STT hiện có trả được word-level timing không? Nếu không, chốt hướng nào: (a) ép căn thời gian theo từ bằng heuristic độ dài ký tự, (b) disable 2 kiểu đó khi nguồn là STT, hay (c) dùng thư viện forced-alignment local?
- `📹 Loại phân tích` (4 mode) trong exe nằm ở inspector node Workflow chứ không phải tab standalone (tab luôn dùng `standard_mode.txt`). Có đưa combo này lên tab `Phân tích video` như spec đề xuất, hay giữ đúng exe và chỉ để nó trong node Workflow?
- `⏱ Thời lượng` ở tab Idea to Video: chốt hành vi khi model ≠ `OMNI Flash` — disable select kèm tooltip, hay ẩn hẳn? (Backend `gen_video()` không nhận `duration`; chỉ `gen_video_omni()` có `duration_s`.)
- `OUTPUT_COUNT`/`MULTI_VIDEO` cho Idea to Video: xác nhận ép `output_count = 1` mỗi cảnh (như spec giả định) thay vì dùng `MULTI_VIDEO = 4` — nếu không, 1 kịch bản 10 cảnh sẽ trừ 40 lượt credit.
- Chuẩn hoá `IDEA_STYLE`: config lưu slug `3d_Pixar` còn `video_styles.json` chỉ có `name` tiếng Việt (`Hoạt Hình 3D Pixar`). Chốt lưu theo slug (cần thêm trường `slug` vào video_styles.json) hay đổi sang lưu thẳng `name`?
- `WM_MODE = "Không xóa"` trong config.json không có trong tập chọn của combo. Chốt: thêm mục `Không xóa` vào combo (lệch exe), hay dùng một checkbox `Xóa Logo` riêng làm công tắc bật/tắt (đúng exe CreateSubTab)?
- Chế độ Chrome (`🌐 Chrome Gemini (Trình duyệt)` / `🌐 Chrome Gemini (trình duyệt)`) xuất hiện trong cả 2 combo backend. Xác nhận ẩn hoàn toàn khỏi UI bản local (bản local không điều khiển Chrome), hay giữ mục disabled kèm ghi chú?
- Engine AI MI-GAN: có đưa lên UI tab `Xóa watermark` như một lựa chọn `Engine` (spec đề xuất) không? Trong exe nó chỉ chạy từ node hậu kỳ qua cờ `auto_remove_gemini_video_watermark`, tab watermark thuần ffmpeg.
- Import Excel ở tab `Phân tích video`: có port sang bản web không? Nếu có thì cần thống nhất định dạng cột với `data_general/excel_mau_batch.xlsx` và cơ chế ghi ngược kết quả vào file (bản web không mở được Excel đang chạy trên máy user).
- gen_image không nhận `seed`: flow_sdk tự sinh `(ts + i*9973) % 1_000_000`. Ta (a) thêm tham số `seed` vào flow_sdk.gen_image + _handle_gen_image để SEED_MODE='Fixed'/SEED_VALUE=9797 có tác dụng, hay (b) ẩn hẳn khái niệm seed khỏi UI? Bản gốc CÓ dùng seed cố định khi SEED_MODE='Fixed' (resolve_seed_from_config), nên (b) là mất tính năng thật.
- CREATE_IMAGE_QUALITY 2k/4k/4k_x4plus trong exe chỉ khác nhau ở TÊN MODEL (upscayl-lite-4x vs realesrgan-x4plus), cả ba đều ×4 nên đầu ra cùng kích thước. Giữ nguyên 4 mục cho giống hệt bản gốc, hay rút còn 1K / 4K (Lite) / 🌟 4K Siêu nét GPU cho trung thực? Rút gọn làm lệch khỏi bản gốc nhưng bỏ được một lựa chọn vô nghĩa.
- IMAGE_MODELS thiếu HARBOR_SEAL (Nano Banana LITE) và GEM_PIX (Nano Banana). Bổ sung ngay, hay tạm ẩn mục LITE khỏi combo? Nếu để nguyên, người dùng chọn LITE sẽ chạy Pro và bị tính tiền Pro mà không hay biết — đây là rò rỉ chi phí im lặng.
- combo số cảnh của Affiliate cho tới 10 Cảnh nhưng nhánh '🧠 VIẾT PROMPT' chặn cứng ở 1–4. Cắt combo còn 1–4, hay giữ 10 và disable nút VIẾT PROMPT kèm tooltip khi >4? (Đường '🎥 TẠO VIDEO' trực tiếp có thể vẫn nhận >4 nếu kịch bản được nhập tay.)
- Tab Upscale Video KHÔNG có mục sidebar ở v4.6.1.3 (module bundled nhưng chưa mount). Xác nhận ta vẫn mount nó thành tab thật trong replica — và nếu có, nó đứng đâu trong sidebar: dưới VIDEO TOOLS cạnh 'Phụ Đề & Xóa Logo', hay dưới IMAGE TOOLS cạnh 'UP SCALE IMAGE 4K'?
- Có nối lại đường upscale ONLINE của Flow không? Ảnh: UPSAMPLE_IMAGE_RESOLUTION_2K/4K (module API_upscale_image còn trong binary, 4.6.1.3 đã ngừng gọi). Video: batchAsyncGenerateVideoUpsampleVideo với veo_3_1_upsampler_1080p|4k. Cả hai TỐN CREDIT và payload video đóng cứng userPaygateTier=PAYGATE_TIER_TWO — tài khoản Pro (PAYGATE_TIER_ONE) nhiều khả năng bị từ chối. Cần xác nhận tier thật của tài khoản người dùng trước khi hứa tính năng này.
- Nút '📁 Xem kết quả' / '📁 MỞ THƯ MỤC' của bản gốc mở Windows Explorer. Web không làm được. Thay bằng ResultsDrawer (đề xuất hiện tại), hay thêm một endpoint 'reveal in folder' chạy `explorer.exe` phía agent? Cái sau đúng bản gốc hơn nhưng là một primitive thực thi lệnh trên máy chủ.
- Bảng job: bản gốc đặt ở tab riêng 'Activity Log / Nhật ký hoạt động' (7 cột Chọn|STT|Video|Trạng thái|Mode|Prompt|Link Ảnh), KHÔNG nằm trong tab tạo ảnh. Spec này đề xuất JobList lọc theo mode ngay trong tab + giữ Activity Log đầy đủ. Cần chốt: giữ đề xuất, hay bám sát bản gốc (tab tạo ảnh không có job list nào)?
- /api/upload có ALLOWED_UPLOAD_MIMES và MAX_UPLOAD_BYTES — cần kiểm tra image/bmp có trong allow-list không, vì filter của bản gốc (Image to Image, Affiliate, Upscale ảnh) đều liệt kê *.bmp. Hoặc mở allow-list, hoặc bỏ .bmp khỏi filter để không hứa suông.
- Ảnh KOL của Affiliate là ảnh người thật của người dùng. Có ingest nó vào bảng references dùng chung (listReferences/createReference) để tái sử dụng giữa các dự án, hay giữ riêng trong affiliate_state.json vì tính nhạy cảm (ảnh chân dung)?
- Mặc định của `Độ dài muốn cắt (giây):` không mine được — hằng số nguyên nằm trong blob nhị phân của Nuitka, không nằm trong blob chuỗi, và khoá `cut_video_duration` KHÔNG có trong `data_general/config.json` đã ship. Cần chốt một giá trị (đề xuất 30 s) hoặc chạy exe một lần để đọc giá trị hiển thị. Đừng ghi 'mặc định theo config' vì khoá đó chưa tồn tại.
- Ô `Tài Khoản:` / `Mật Khẩu:` trong nhóm `👤 Tài khoản VEO3`: bỏ hẳn, hay giữ ở dạng read-only 'đã cấu hình'? Đây là quyết định sản phẩm có hệ quả bảo mật (bản gốc lưu plaintext mật khẩu Google và tự gõ vào form đăng nhập). Spec hiện đang giả định GIỮ NHÃN nhưng KHÔNG cho ghi. Cần xác nhận.
- Pill `🔗 WORKFLOW` trong bản gốc ẩn trừ khi `WORKFLOW_ENABLED` hoặc chạy với `--dev-mode`. Bản replica hiện luôn hay ẩn cho tới khi 25 node xong? Nếu hiện, nội dung của trạng thái trung gian là gì?
- Trang `💝 ỦNG HỘ TÁC GIẢ`: giữ trang với 3 ô rỗng để người dùng tự điền, hay bỏ hẳn pill này? Thông tin ngân hàng của tác giả bản gốc là dữ liệu cá nhân và không được ship. Nếu bỏ hẳn thì top-bar chỉ còn 4 pill và bố cục shell thay đổi.
- `voice_index` / `style_index` trong `data_clone.json` là chỉ số nguyên vào danh sách động — thêm/xoá một mục ở giữa sẽ khiến dự án cũ trỏ sai giọng mà không báo lỗi. Chốt: chuyển sang id bền (spec đã đề xuất `/api/videoclone/styles` trả `id`), và cần một migration đọc file cũ. Có chấp nhận phá vỡ tương thích với `data_clone.json` của bản gốc không?
- `postprod._existing()` chặn mọi input ngoài `STORAGE_DIR` và `ASSET_ROOT`. Người dùng chọn video ở `D:\Videos\` sẽ nhận 400. Hướng xử lý: (a) UI copy/upload file vào storage trước, (b) set `FLOWBOARD_INPUT_ROOTS` trong `run.bat`, hay (c) thêm một picker backend chỉ cho phép các thư mục đã đăng ký? Ảnh hưởng cả Cut & Merge lẫn Video Clone.
- Sidebar VEO3 mine được 14 mục chứ không phải 16 như brief nêu. Chênh lệch đến từ cách đếm sub-tab (`ImageToVideoTab` và `CreateImageTab` mỗi cái chứa 2 sub-tab; `UpscaleVideoTab` và `RemoveWatermarkTab` không có dòng sidebar riêng). Xác nhận dùng cây 14 mục đã mine, hay có nguồn khác cho con số 16?
- Chốt nhãn cho `bgmVolume` ở card mở rộng `🎶 Nhạc nền`: mượn nguyên `🔊 Âm lượng Voice:` (byte-exact nhưng sai ngữ nghĩa — đó là nhãn của node voice trong Workflow), hay đặt nhãn mới rõ nghĩa? Quy tắc 'không bịa nhãn' và 'không gây hiểu nhầm' xung đột ở đúng chỗ này.
