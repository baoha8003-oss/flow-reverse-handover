# Sổ thiếu / không làm — Flow đổi transport (18/09/2026)

## 📋 SỔ THIẾU / KHÔNG LÀM — một chỗ duy nhất (18/09 17:45)

> Chép nguyên văn từ kế hoạch `~/.claude/plans/wondrous-napping-nautilus.md` (mục cùng tên). Bản này là **danh sách đầy đủ** những gì sản phẩm chưa có hoặc cố ý không dựng, tính tới sau P13.
> Sau khi duyệt kế hoạch, bước đầu tiên của thực thi **chép nguyên văn** mục này ra
> `D:/TOOL_VIDEO/TOOL/plans/reports/gaps-260918-1729-flow-batch-migration.md` (plan mode chỉ cho sửa
> file kế hoạch). Trạng thái: **THIẾU** = muốn có mà chưa ai có payload · **CỐ Ý** = quyết định không
> dựng · **CHỜ PROBE** = một lần chạy 0-credit trả lời được · **NỢ CŨ** = việc từ pha trước, P13 không đổi.

### A. Năng lực Flow không có trên đường mới (`batchexecute`)

| # | Mục | Trạng thái | Vì sao | Người dùng thấy gì | Cách đóng |
| --- | --- | --- | --- | --- | --- |
| A1 | **Veo text-to-video** mọi lane (`veo_3_1_t2v_*`, kể cả `_lite_low_priority` 0 credit) | THIẾU / CHỜ PROBE | Không ai capture payload Veo t2v trên `flow.google.com`; chỉ có Omni `abra_t2v_<N>s` (YhhmEf) | Node t2v lane Veo → `notReady`: "chưa có trên đường mới, dùng OMNI (có tính credit)" | Probe (5) P13.10: gửi key Veo qua `YhhmEf`; nếu bị từ chối → capture theo `docs/flow-capture.md` |
| A2 | **Veo start+end (FL)** `veo_3_1_i2v_s_fast*_fl*` | THIẾU | Upstream: "payload's end-image slot was never captured" | `end_media_id` trên lane Veo → `unsupported_on_batch_start_end`; **Omni first+last** (`nprQif`) dùng thay | Capture Veo FL |
| A3 | **Veo r2v / Thành Phần** (`veo_3_1_r2v_*`, gồm 2 lane 0-credit) | THIẾU | Không capture; chỉ có Omni r2v `MZZa6b` (15–30 credit) | Cổng nhân vật + lane Veo → `unsupported_on_batch_veo_r2v`; lane omni chạy như cũ | Capture Veo r2v |
| A4 | **Character Entity** (`referenceEntities`, P7b) | THIẾU | Builder Omni r2v của upstream không có slot entity | UI đăng ký nhân vật vẫn còn; dispatch có entity → `unsupported_on_batch_reference_entities` (không lặng lẽ bỏ) | Capture một lần sinh video có entity trong Flow UI |
| A5 | **Edit Video / Motion Control** (`abra_edit`, upload-video chunk, P9c) | THIẾU | Cả upload lẫn edit đều là REST/tRPC đã chết | `motion_control` vẫn nằm ngoài palette; file exe nhập về `note` | Capture 2 RPC (upload video + edit) rồi mới dựng P9c |
| A6 | Lane **`fast_relaxed`** (0 credit) và **`quality`** | THIẾU | Chỉ 3 key Veo được nhận; key hậu tố `_relaxed`/`_s` bị từ chối | Chọn 2 lane này → `notReady` kèm lý do; **làn free duy nhất còn lại là `lite_relaxed`** | Capture (nếu Flow UI còn cho chọn) |
| A7 | **Số dư credit / tier** sống | THIẾU | Không có RPC credits; upstream cũng trả tier cấu hình | Tier = chọn trong Settings (chỉ nhãn); `/estimate` **không** so được với số dư; `_acceptance_pro` chốt 45 credit thành ước tính | Capture RPC credits (Flow UI có hiện số dư) |
| A8 | **Danh sách project** (`searchUserProjects`) | THIẾU | tRPC chết; không có RPC listing project | `/api/flow/projects` báo `exists_on_flow: null`; sync-up chỉ tạo cho board chưa có | Probe (0) P13.10 xem `Zzl0ze` với uuid lạ trả gì → dùng làm kiểm tồn tại |
| A9 | **Danh tính** (email/tên/ảnh) | THIẾU / CHỜ PROBE | Lấy qua `oauth2/v2/userinfo` bằng Bearer — Bearer không còn | AccountPanel bỏ email trừ khi `flow_probe` thấy khoá email trong `WIZ_global_data` | Probe (0) |
| A10 | **Upscale video** phía Flow | KHÔNG CẦN | Upstream cũng chưa port; bản này upscale offline (RealESRGAN/upscayl) | Không đổi | — |
| A11 | **Xuất ảnh 2K/4K** phía Flow (`SPrCad`) | CỐ Ý | Có ở upstream; bản này upscale offline — YAGNI | Không có nút | Port khi có nhu cầu (builder + 1 route) |
| A12 | **Omni 360p** | ĐÃ NỐI, THIẾU GIÁ | P13.6/13.7: chọn được trong Settings (`VIDEO_RESOLUTION`, enum đóng 360p/720p) và trên node (`resolution`), đi xuyỉn tại `node_settings.for_dispatch` → handler → `omni_model_key`. **Chưa đo giá 360p** | Mặc định 720p; ô 360p ghi "chưa đo giá" và estimate báo giá 720p | Đo giá 360p khi có credit dư |
| A12b | Canvas chưa có ô chọn làn / đọ dài / độ phân giải trên card video | CỐ Ý (đợi đủ bộ) | NodeCard chưa từng có editor cho làn lẫn đọ dài; thêm riêng một ô độ phân giải cạnh hai thứ không có là lệch hơn là đủ | Board canvas dùng mặc định Settings; workflow nhập vào giữ giá trị của nó | Dựng cả ba ô cùng lúc |
| A13 | Tạo project bằng `jHPbke` | CHỜ PROBE | Từ PR bị revert của upstream, họ ghi live-verified; bản này chưa tự chạy | Board mới tự tạo project; thất bại → dùng `FLOW_PROJECT_ID` dán tay | Probe (1) P13.10, 0 credit |
| A14 | Pro có làn `lite_low_priority` không | CHỜ PROBE | Bảng REST cũ nói Ultra-only; đường mới không gửi tier, Google tự quyết | Nếu Pro bị từ chối → `lite_relaxed` báo lỗi (không tính tiền) | Probe (4) P13.10, 0 credit |
| A15 | `fast` (`veo_3_1_i2v_s_fast_ultra`) trên Pro + giá | CHỜ PROBE | Key fast duy nhất mang tên Ultra | Pro chọn fast có thể bị từ chối; mặc định lane trống đổi thành `lite` | Chạy một clip fast khi user duyệt (có giá) |
| A16 | Giá thật Omni 4s vs bảng `OMNI_FLASH_CREDIT_COST` | CHỜ PROBE | Bảng là "informational", Google đổi giá | Estimate có thể lệch | Probe (6) P13.10 so credit trước/sau trong Flow UI |

### B. Cố ý không dựng (quyết định, không phải thiếu)

| # | Mục | Vì sao |
| --- | --- | --- |
| B1 | Telemetry giả `batchLog` / `FLOW_*_LATENCY` | Đánh lừa chống lạm dụng của Google; cái mất là tài khoản người dùng. Upstream còn mã nhưng no-op; bản này không port |
| B2 | Cầu cào audio TTS (elevenlabs/heygen/azure/chatgpt) | Thay bằng OpenAI TTS chính thức (P11) |
| B3 | API nội bộ CapCut/ByteDance (58 giọng) | Map tên giọng sang Gemini theo cao độ đo được (P11) |
| B4 | Nhân bản giọng (`Clone Voice`, `clone_segment_with_retry`) | Lái dịch vụ bên thứ ba bằng client giả trình duyệt |
| B5 | `FLOW_ALLOW_DEGRADED` (tự hạ lane) của upstream | User chốt 18/09: **từ chối, không thay**; hạ lane = chạy model khác thứ board xin |
| B6 | `resolve_video_model` gập key lạ về mặc định | Gập `…_ultra_relaxed` (0 credit) thành `…_ultra` **trả phí** |
| B7 | Passthrough wire-id model ảnh lạ | Key lạ không được coi là gì cả (luật tiền) |
| B8 | Multi-profile failover của upstream | Bản này một profile Chrome |
| B9 | Xoá project `QI2zvc` | Khó đảo, không ai gọi |
| B10 | License gate, Telegram, NordVPN, tự cập nhật từ GitHub tác giả | Ngoài phạm vi dùng cá nhân (mục D spec) |
| B11 | 36 dải màu preset chữ nghệ thuật | Đào từ binary không tin được; giữ màu phẳng + gọi tên preset |

### C. Nợ cũ, P13 không đổi

| # | Mục | Trạng thái |
| --- | --- | --- |
| C1 | **P9c** Edit Video | Chặn bởi A5 |
| C2 | **P12** Canvas Agent (8 graft) | Chưa bắt đầu; sau P13 |
| C3 | Chạy lại **ma trận D.9** (94 mục, tiêu chí "làm được từ UI") | Sau nghiệm thu P13.10 |
| C4 | Nghiệm thu P5 "1 ảnh gpt-image thật" | Hoãn từ 05/09 (chưa có khoá OpenAI trên máy) |
| C5 | Test symlink `safe_mine_path` | Chỉ chạy được trên POSIX/CI |
| C6 | `gemini_model`/`fallback_gemini_model` đổi model thật | `LLMProvider.run()` không có tham số model (giữ hợp đồng P1) |
| C7 | `batch_idx`/`preview_height` | Cố ý chỉ lưu (P8) |
| C8 | Extension trong trình duyệt của user vẫn là bản cũ (< 0.0.12) | Không probe nào chạy được trước khi reload v0.1.0 |

### D. Rủi ro ghi sẵn

- Golden envelope đỏ = Google đổi frontend → **capture lại**, không sửa golden.
- Token captcha **dùng một lần**; gửi lặp → `PUBLIC_ERROR_UNUSUAL_ACTIVITY`; nhịp gửi ≥ 1 s, ≤ 5 song song
  (bản này: `MULTI_VIDEO=4`, cooldown 7 s — trong ngưỡng).
- `NO_INJECTION_RESULT` hay gặp ở RPC đầu sau khi agent khởi động → retry có đếm.
- Listing `Zzl0ze` 17 MB: luôn gọi với `match`; WS `max_size` nâng lên 64 MiB cho đường dự phòng.

---

## 📌 Cập nhật sau P13.6 → P13.9 (18/09, cùng phiên)

Sổ này được chép ra lúc bắt đầu thực thi. Những gì **đã thay đổi** trong lúc làm — ghi ở đây thay vì
sửa các bảng trên, để còn đọc được cái gì là dự đoán lúc lập kế hoạch và cái gì là kết quả.

### Đóng được

| Mục | Kết quả |
| --- | --- |
| A2 (Veo first→last) | Không capture được, nhưng **Omni first+last (`nprQif`) đã dựng và có test** — năng lực này chạy được, chỉ không trên làn Veo. Tab Start-End nay có ô chọn độ dài thật (4 độ dài của Omni) thay vì 8s khoá cứng |
| A12 (Omni 360p) | Nối xuyên: Settings → node → `for_dispatch` → handler → `omni_model_key`. Còn thiếu **giá** 360p |
| A13 (`jHPbke` tạo project) | Dựng xong kèm dự phòng `FLOW_PROJECT_ID`, có test. **Vẫn chờ probe** để biết Flow có nhận thật |

### Phát hiện thêm trong lúc làm (không có trong bảng ban đầu)

| # | Mục | Đã xử lý |
| --- | --- | --- |
| E1 | `VIDEO_RESOLUTION` mặc định là `480p` — giá trị Flow **không nhận** (chỉ 360p/720p). Không ai đọc khoá này nên nó vô hại, tới khi P13.6 nối nó vào dispatch | Mặc định đổi sang `720p`; thêm enum đóng; **`config.json` của exe bị kiểm** trước khi nhập (nó ship 480p) |
| E2 | `estimate` hỏi "khung hình đầu đã RESOLVE chưa" thay vì "có dây khung hình đầu chưa", nên mọi clip của storyboard bị chấm là text-to-video → **từ chối oan toàn bộ board mới** | Thêm `has_start_frame_wire()`; test bắt được trước khi chạy thật |
| E3 | `AccountPanel` gate mọi thứ (kể cả nút Cài đặt) trên `email`, mà email giờ luôn null → **không mở được Settings**, tức không chọn được gói | Panel đổi sang trạng thái cầu nối (extension / tab / ký được), nút Cài đặt luôn hiện |
| E4 | `_poll_video_dispatch` không nâng `model_key` lên result cho i2v → vòng review thấy "không có key" → coi là **không miễn phí** → không bao giờ tự lặp trên đúng làn 0 credit | Nâng ở một chỗ cho cả ba họ |
| E5 | `PUBLIC_ERROR_UNUSUAL_ACTIVITY` (token captcha dùng lại) không chứa chữ "captcha" lẫn mã HTTP → phân loại thành **terminal**, tức bỏ cả batch chỉ vì cần token mới | Thêm vào nhóm `captcha` |
| E6 | Luật "không bao giờ retry dispatch đã tạo operation" đúng nhưng **bỏ clip mắc kẹt**: batch Omni 4 biến thể timeout = 60 credit không ai lấy được | Thêm `poll_video`: RUN Lỗi **poll lại** thay vì dispatch lại. Chỉ operation chưa giải quyết; cái Google đã từ chối thì để nguyên |
| E7 | `IMAGE_MODELS` thiếu `HARBOR_SEAL` trong khi `flow_batch.IMAGE_MODEL_WIRE_IDS` đã cho phép → một model Flow chạy được mà **không có biệt danh nào chọn được** | Thêm `NANO_BANANA_2_LITE`; có test so hai bảng |
| E8 | `components/StatusBar.tsx` là bản chết (không ai mount) nhưng vẫn đọc `token_age_s` | Xoá file |

### Vẫn chờ (không đổi)

`A1` Veo t2v · `A3` Veo r2v · `A4` Character Entity · `A5` Edit Video · `A6` `fast_relaxed`/`quality` ·
`A7` số dư credit · `A8` danh sách project · `A9` danh tính · `A14`/`A15`/`A16` các probe giá ·
`C1`–`C8`. Cách đóng từng mục: `flowboard-local/docs/flow-capture.md` (viết ở P13.9).

**`C8` vẫn là thứ chặn mọi nghiệm thu thật:** extension trong Chrome của user phải được reload lên
**v0.1.0**. Trước đó không probe nào chạy được, và `/api/auth/scan` sẽ nói đúng điều đó.

---

## 🔬 NGHIỆM THU THẬT — P13.10, tài khoản Pro, 19/09/2026

Chạy trên tab `flow.google.com` đã đăng nhập, extension v0.1.0, agent code mới.
**Tiêu đúng trần đã duyệt: 45 credit OMNI** (15 + 30) + 1 ảnh.

### Đường nào chạy được

| Bước | RPC | Kết quả |
| --- | --- | --- |
| Ký lệnh trong trang | — | `flow_probe`: tab có, `at` token có, host `flow.google.com` |
| Tạo project | `jHPbke` | **CHẠY** — uuid thật, `created: true`, không cần `FLOW_PROJECT_ID` |
| Upload ảnh | `maseQ` | **CHẠY** — 1.1 MB PNG → media id |
| Sinh ảnh | `ogiZ0b` | **CHẠY** — URL ký trả ngay trong response; tải về JPEG 249 KB thật |
| Sinh video (references) | `MZZa6b` | **CHẠY** — 3 clip, mỗi clip **4.01 s · 720×1280 · h264+aac** |
| Poll ba tín hiệu | `jwpduf` → `Zzl0ze` → `as29s` | **CHẠY** — dispatch → xong trong 40–48 s mỗi clip |
| Fan-out + estimate | — | 2 dòng → 2 node → **đúng 2 request row**, estimate 30 credit khớp |
| Từ chối làn Veo r2v | — | `unsupported_on_batch_veo_r2v_lane_lite_relaxed`, **0 call tới Flow** |

### Câu hỏi đóng được

| Mục | Trước | Sau khi đo |
| --- | --- | --- |
| **A13** tạo project bằng `jHPbke` | CHỜ PROBE | **ĐÓNG** — chạy thật |
| **A14** Pro có làn `lite_low_priority` không | CHỜ PROBE | **ĐÓNG: KHÔNG.** `PUBLIC_ERROR_MODEL_ACCESS_DENIED`. Trên Pro **không có làn 0 credit nào** — tức vòng review không bao giờ tự lặp được trên gói này (hướng an toàn, nhưng phải biết) |
| **A1** Veo text-to-video | THIẾU: "không ai capture payload" | **SỬA KẾT LUẬN.** `YhhmEf` **hiểu** key Veo t2v — nó trả lỗi **GÓI**, không phải lỗi tham số. Veo t2v **chạy được** trên đường mới với tài khoản có key. Cái còn thiếu chỉ là **bảng key** `veo_3_1_t2v_*` (đã xoá cùng bảng REST) và mọi key gói này thử được đều **tốn tiền** |
| **A8** danh sách project | THIẾU | Vẫn thiếu. `Zzl0ze` với uuid lạ: không lỗi, cửa sổ rỗng → **không dùng làm phép kiểm tồn tại** được. `exists_on_flow: null` giữ nguyên là đúng |
| **A16** giá Omni 4s | CHỜ PROBE | Bảng nói 15; số dư không đọc được nên **chỉ đối chiếu được bằng mắt trong Flow UI** |

### Hai lỗi thật, tìm ra nhờ chạy live

**1. Mã lỗi của Flow không bao giờ tới tay người dùng.** Cái họ đọc là

```
RpcError: eb1hJf failed: [7, None, [['type.googleapis.com/google.rpc.ErrorInfo',
['PUBLIC_ERROR_MODEL_ACCESS_DENIED']]]]
```

Đây là lỗi một người dùng Pro gặp **nhiều nhất**, vì UI đánh dấu đúng làn đó là miễn phí và mời họ
chọn. Đã thêm `flow_batch.read_rpc_reason`, `_error_text` cho mã đi đầu chuỗi, và bản dịch tiếng Việt
trong `errorLabels.ts`. Cũng đặt tên hai mã gRPC: **7 = PERMISSION_DENIED, 8 = RESOURCE_EXHAUSTED** —
xác nhận `[8]` là transient.

**2. Bẫy phân loại lỗi, kích hoạt bởi chính lỗi số 1.** `PUBLIC_ERROR_` nằm trong
`_SELF_TERMINAL_PREFIXES` và được kiểm **trước** nhánh captcha. Ngay khi chuỗi lỗi bắt đầu bằng mã
sạch, `PUBLIC_ERROR_UNUSUAL_ACTIVITY` sẽ thành **terminal** — bỏ cả batch chỉ vì cần một token
captcha mới. Tiềm ẩn trước đó, sẽ nổ ngay khi sửa lỗi 1. Nay mã captcha được đọc trước, và đọc
**khớp chính xác** chứ không phải chuỗi con (nếu lỏng, giá trị người gọi đưa vào lọt được vào ngân
sách captcha).

### Việc mới mở ra (cần bạn quyết, tốn tiền để xác minh)

**Dựng lại bảng key Veo t2v.** Giờ đã biết `YhhmEf` nhận key Veo, nên `gen_video_text` có thể gửi
đúng làn board xin thay vì từ chối. Cần: (a) khôi phục tên key `veo_3_1_t2v_lite` / `_fast` / các biến
thể độ dài từ mã cũ; (b) **chạy thử ít nhất một key tốn tiền** để biết bảng đúng. Chưa làm — ngoài
phạm vi P13 và cần bạn duyệt chi.

---

## ✅ A1 ĐÓNG — VEO TEXT-TO-VIDEO ĐÃ DỰNG LẠI VÀ CHẠY THẬT (19/09, chiều)

User duyệt chi để xác minh. Kết quả: **A1 đóng hẳn**, và tốn ít hơn dự tính vì gói Pro từ chối phần
lớn key (từ chối là miễn phí).

### Đường đi của kết luận

| Mốc | Nội dung |
| --- | --- |
| P13.6 (sáng) | Từ chối mọi làn Veo t2v — tin rằng `YhhmEf` không có payload Veo |
| Probe (trưa) | `veo_3_1_t2v_lite_low_priority` → `PUBLIC_ERROR_MODEL_ACCESS_DENIED` = lỗi **GÓI** ⇒ RPC **có** phân tích key Veo |
| Đào binary | 16 key `veo_3_1_t2v_*` trong exe (bảng cũ không còn trong git lẫn cây làm việc) |
| Xác minh (chiều) | `veo_3_1_t2v_lite_4s` → **DENIED, miễn phí**; `veo_3_1_t2v_lite` → **clip thật 8,00 s · 1280×720 · h264+aac** |

**Đo trên gói Pro: 4s và 6s bị từ chối, 8s chạy.** Đúng bằng kết quả thời REST đã ghi trong plan
(`T2V_DENIED_DURATIONS`) — quyền theo tài khoản đi xuyên qua cuộc migration. Registry nay nói câu đó
ngay tại ô chọn độ dài.

### Đã dựng

- `flow_sdk.VEO_T2V_LANES` — 12 key, 4 làn × 3 độ dài, đào từ exe. Không gửi biến thể `_portrait`
  (aspect là ô riêng, đúng như họ i2v đã bỏ). Không có 10s — đó là của OMNI.
- `flow_sdk.resolve_t2v_plan()` — **từ chối, không gập**: làn không có key và độ dài không có key đều
  báo lỗi kèm danh sách cái có.
- `gen_video_text` gửi đúng làn board xin; OMNI vẫn là một làn bình thường bên cạnh.
- Registry + estimate theo bảng SDK (không chép lại), nên sửa một key là tới thẳng dropdown.

### An toàn tiền của thiết kế này

Key sai **không** tốn gì: Flow trả cùng lỗi gói cho tên lạ và tên không thuộc gói, và **không gập key
lạ về mặc định**. Nên một dòng sai trong bảng làm mất một vòng round-trip, không mất credit. Chỗ duy
nhất còn mơ hồ được ghi ngay tại chỗ: `fast` ở 8s — exe có **cả** `veo_3_1_t2v_fast` lẫn
`veo_3_1_t2v_fast_ultra`, và họ i2v lại dùng bản có `_ultra`. Đang gửi bản gốc cho nhất quán với
`_4s`/`_6s`; nếu Flow từ chối thì sửa đúng một dòng, và lần từ chối đó miễn phí.

### Chi phí thật của cả phiên xác minh

1 clip Veo t2v 8s (giá không công bố, không đọc được số dư) + 1 lần từ chối miễn phí. Cộng với buổi
sáng: 1 ảnh + 45 credit OMNI.

---

## 🗺 BẢN ĐỒ KEY ĐO BẰNG ORACLE MIỄN PHÍ (19/09, tối) — A2 đóng, A3 mở

User cấp full quyền để làm nốt. Kết quả **gần như không tốn gì**, vì tìm được cách hỏi Google mà
không sinh clip.

### Cách đo

Gửi một key mang hậu tố miễn phí (`_low_priority` / `_relaxed`) mà gói Pro chắc chắn không có, rồi
đọc lỗi. Hai câu trả lời phân biệt được:

| Trả lời | Nghĩa | Tốn |
| --- | --- | --- |
| `[7]` `PUBLIC_ERROR_MODEL_ACCESS_DENIED` | RPC **biết** tên, chỉ gói không có | 0 |
| `[5]` NOT_FOUND | RPC **không biết** tên | 0 |

Control dùng một tên bịa (`veo_3_1_definitely_not_a_model_zz`) để chốt hình dạng `[5]` trước. Không có
control này thì hai câu trả lời không phân biệt được và cả phép đo thành đoán.

### Kết quả (4 RPC, 17 key)

| Hậu tố | Đường batch | Ví dụ |
| --- | --- | --- |
| `_low_priority` | **BIẾT** | `i2v_s_lite_4s_low_priority`, `i2v_s_lite_6s_low_priority`, `r2v_lite_low_priority`, `t2v_lite_6s_low_priority` |
| `_relaxed` | **KHÔNG** | `i2v_s_fast_ultra_relaxed`, `t2v_fast_4s_relaxed`, `t2v_fast_6s_relaxed`, `t2v_fast_ultra_relaxed`, `r2v_fast_portrait_ultra_relaxed` |
| `portrait` | **KHÔNG** | `i2v_s_fast_portrait_ultra_relaxed` |
| `_fl` | **KHÔNG** | `i2v_s_fast_fl_ultra_relaxed` — trên **cả** `nprQif` lẫn `eb1hJf` |

### Đổi trạng thái

| Mục | Trước | Sau |
| --- | --- | --- |
| **A2** Veo first→last | THIẾU ("chưa ai capture") | **ĐÓNG HẲN** — `nprQif` không biết tên `_fl` nào, `eb1hJf` cũng vậy. Từ chối là đúng và vĩnh viễn, và nay là **đo** chứ không phải suy đoán |
| **A3** Veo r2v | THIẾU | **MỞ MỘT PHẦN** — đúng một key sống: `veo_3_1_r2v_lite_low_priority`, và nó **0 credit**. Đã dựng thành làn `lite_relaxed` trên cổng nhân vật |
| **A6** `fast_relaxed` | THIẾU | **XÁC NHẬN chết** — mọi tên `_relaxed` đều `[5]` |
| Veo i2v 4s/6s | không biết có | **CÓ** (`i2v_s_lite_4s/6s`), gói Pro không giữ. Chưa dựng — xem dưới |

### Lỗi của chính tôi, do đo mà ra

Bảng `VEO_T2V_LANES` dựng buổi chiều có **làn `fast_relaxed` với cả 3 tên sai**. Tôi dựng nó từ binary
mà không đọc lại luật đã viết ngay phía trên trong cùng file spec: *đường batch từ chối mọi hậu tố
`_portrait` / `_fl` / `_relaxed`*. Đã gỡ, và chuyển thành làn bị từ chối có nêu lý do. Không ai mất
tiền vì nó — key sai chỉ tốn một round-trip.

### Còn nợ, có chủ ý

- **`fast`@8s t2v**: `veo_3_1_t2v_fast` hay `veo_3_1_t2v_fast_ultra`? Không có oracle miễn phí cho họ
  `fast` (mọi biến thể free của nó đều `_relaxed` → đã chết). Phải chạy một clip **có tính tiền** để
  biết, và **harness chặn** đúng lệnh đó. Đang gửi bản gốc; sai thì Flow trả `[5]` có nêu tên và sửa
  đúng một dòng.
- **Veo i2v 4s/6s** (`veo_3_1_i2v_s_lite_4s/6s`): tên hợp lệ, gói này không có. Dựng được nhưng phải
  đổi `BATCH_VIDEO_LANES` từ phẳng sang lồng theo độ dài, và không có tài khoản nào ở đây kiểm được.
  Chưa làm — ghi lại để khi có tài khoản giữ key thì dựng.

### Chi phí

Vòng đo này: **0 credit** (mọi key đều bị từ chối vì gói). Cả ngày: 1 ảnh + 45 credit OMNI + 1 clip
Veo t2v 8s.

---

## Cập nhật 20/09 — review song song + vá P0/P1

Chi tiết đầy đủ: [`review-260920-1215-p13-parallel-audit.md`](review-260920-1215-p13-parallel-audit.md).
Chấm lại 94 mục D.9: [`d9/rescore-A.md`](d9/rescore-A.md) · [`d9/rescore-B.md`](d9/rescore-B.md) ·
[`d9/rescore-C.md`](d9/rescore-C.md).

**D.9 chấm lại:** built 29 → **31** · partial 32 → **33** · open 30 → **19** · cố ý 3 → **11**.

### Mục trong sổ này đã đổi trạng thái

| # | Trước | Nay |
| --- | --- | --- |
| A2 Veo first→last | THIẾU, "đo 19/09" | **ĐÓNG, và phép đo 19/09 đã được làm lại cho đúng.** Vòng cũ bị nhiễu: mọi tên `_fl` gửi đi cũng mang `_relaxed`. Đo lại 20/09 với control khác đúng một đoạn → `_fl` thật sự không được biết. Kết luận cũ đúng, nay có bằng chứng |
| A3 Veo r2v | MỞ | **VẪN MỞ, và làn đang chết trong production** — resolver chấp nhận key, `gen_video_omni` từ chối nó trước mọi RPC. Thứ tự vá bắt buộc ghi trong báo cáo; probe dứt điểm **bị harness chặn** |
| A6 `fast_relaxed` | chết | không đổi |

### Mục MỚI vào sổ

| # | Mục | Trạng thái |
| --- | --- | --- |
| A17 | `omni_flash_i2v_<N>s_first_last` — key **duy nhất** sau toàn bộ mặt Start-End | **CHƯA ĐO**, không có ngày capture ở builder, không có mục trong `flow-capture.md`. Không có oracle miễn phí (gói này CÓ Omni) → cần **một** lần chạy thật, rẻ nhất Omni 4s = **15 credit**. Chờ duyệt chi |
| A18 | `portrait` chưa được cô lập | Cùng nhiễu như `_fl`. Không chặn gì (không bảng nào có key `portrait`), nhưng lý do trong spec mạnh hơn bằng chứng cho phép |
| A19 | `[5]` NOT_FOUND là câu trả lời cho **hai** chuyện | Đo 20/09: Flow kiểm tên → gói → media. `[5]` = tên lạ **hoặc** media không tìm thấy. Nhãn tiếng Việt không được khẳng định một trong hai; một probe dựng trên media giả không bao giờ cô lập được tên |

### Nợ mới phát hiện, chưa làm (mức dưới P1)

- Vòng probe `AccountPanel` 8 giây bơm `executeScript` MAIN-world vào tab Flow; `reviveTabIfNeeded`
  biến một tab bị Chrome discard thành vòng lặp reload ~10,5 giây.
- `resolution` gửi cho t2v rồi bị bỏ ở hai tầng và hard-code 720p, trong khi hint Settings nói ngược.
- `models.py:290` gõ tay tuple làn startEnd + index `QUALITY_LABELS[key]` → **500** trên `/api/models`
  nếu thêm làn thiếu nhãn.
- `project_id` chỉ nằm trong RAM → re-poll sau khi khởi động lại agent là no-op 10 phút.
- `PUT /api/templates/mine/{file}` và `POST /api/batch/workflow` vẫn không có caller frontend.
- 4 khoá P8 "đường ống sống, công tắc chết"; 5 reader `node_settings` không có consumer production.

### Chốt lại 20/09 chiều (sau 2 probe user duyệt)

| # | Trạng thái cuối |
| --- | --- |
| A2 Veo first→last | **ĐÓNG** — `_fl` không được biết, cô lập bằng control khác đúng một đoạn |
| A3 Veo r2v | **ĐÓNG phần dựng** — `MZZa6b` đọc tên Veo từ `request[2]` (`[7]` vs `[5]` cho tên bogus cùng ô). Key nay luồn qua builder. **Giá CHƯA ĐO** và gói này không đo được → estimate báo chưa có giá |
| A17 first-last | **ĐÓNG** — chạy thật, tốn 15 credit |
| A18 `portrait` | **CÒN MỞ** — cần một tên `portrait` không mang `_relaxed`. Không chặn gì |
| A19 `[5]` mơ hồ | **ĐÓNG thành luật** — nhãn nói cả hai khả năng |

Chi phí: **15 credit**. Chi tiết: `review-260920-1215-p13-parallel-audit.md`.
