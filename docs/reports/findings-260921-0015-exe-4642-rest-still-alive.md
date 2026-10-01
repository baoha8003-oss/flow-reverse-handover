# Bản 4.6.4.2 của tác giả: REST CHƯA CHẾT — token lấy qua NextAuth session

**Ngày:** 21/09/2026 00:15 · **Nguồn:** đào chuỗi (mmap) từ `RUN_VEO_3_ULTRA_PROMAX.exe` bản
**4.6.4.2** (phát hành 19/09/2026, sha256 khớp manifest tác giả), giải nén trong hộp cách ly
`D:\TOOL_VIDEO\_sandbox-update\`. **Không** chạm `TOOL/`.

> ⚠️ Đây là **chuỗi trong binary**, chưa phải phép đo mạng sống. Bằng chứng gián tiếp mạnh (tác giả
> vẫn ship bản vá 2 ngày trước, tool chạy được cho người trả tiền), nhưng **điểm chốt phải verify
> live**: `labs.google/fx/api/auth/session` hôm nay còn trả token không. Test đó miễn phí (một lần
> đọc), cần trình duyệt đã đăng nhập của user.

## Kết luận một câu

P13 dựng trên niềm tin của flowkit: *"Google chuyển sang flow.google.com và NGỪNG phát Bearer token"*.
**Niềm tin đó sai một nửa.** Tác giả gốc **chưa bao giờ rời `labs.google/fx`** (frontend Next.js), và
lấy Bearer token bằng cách **gọi `/api/auth/session` NGAY TRONG TRANG** — không phải sniff header. REST
`aisandbox-pa.googleapis.com` (tên trường JSON rõ ràng) **vẫn sống** với token đó, và **cả bốn năng
lực ta thiếu đều là REST endpoint có sẵn ở đây**.

## Vì sao ta (và flowkit) tưởng REST chết — và hoà giải

- flowkit + P13 **sniff** header `Authorization: Bearer ya29` trên request đi ra. Trang mới không gắn
  header đó vào request Angular → sniff rỗng → kết luận "hết token".
- Tác giả **không sniff**. Họ chạy trong trang đã đăng nhập:
  ```js
  const r = await fetch(base + '/api/auth/session', {credentials: 'include'});
  return await r.json();   // { accessToken, user, session, ... }
  ```
  với `base = https://labs.google`. Token vẫn nằm trong session; chỉ cần **hỏi đúng endpoint**. Có
  retry: nếu JS fetch trả 401 → "Làm mới trang project..." rồi thử lại.
- Hai frontend khác nhau: **flow.google.com** (Angular, batchexecute) vs **labs.google/fx** (Next.js,
  NextAuth + REST). Ta migrate sang cái thứ nhất; tác giả ở cái thứ hai.

## Kiến trúc thật của 4.6.4.2 (lai)

**Auth:** `labs.google/fx/api/auth/csrf` → `/api/auth/signin/google` → `/api/auth/session` (trả
`accessToken`). Token + cookie bóc bằng `get_token_and_cookie_from_browser` (Playwright/patchright
trong Chrome profile của user).

**Sinh media = REST `aisandbox-pa` (Bearer):**

| Endpoint | Việc |
| --- | --- |
| `POST /v1/flow/entities` | **TẠO NHÂN VẬT** → trả `entityId`. Đây là mục #1 ta tưởng "không tồn tại" |
| `/v1/projects/{project_id}/flowMedia:batchGenerateImages` | sinh ảnh (Nano Banana / pro / 2 / LITE) |
| `/v1/video:batchAsyncGenerateVideoText` | Veo t2v |
| `/v1/video:batchAsyncGenerateVideoStartImage` | i2v |
| `/v1/video:batchAsyncGenerateVideoStartAndEndImage` | first+last |
| `/v1/video:batchAsyncGenerateVideoReferenceImages` | r2v / nhân vật (có `referenceEntities`) |
| `/v1/video:batchAsyncGenerateVideoEditVideo` | **Edit Video** (`abra_edit`) |
| `/v1/video:batchCheckAsyncVideoGenerationStatus` | poll |
| `/v1/flow/uploadImage`, `/v1:uploadUserImage` | upload ảnh |
| `/v1/flow:copyProjectMedia`, `/v1/flow:batchGenerateAudio`, `/v1/flowMedia`, `/v1/flowWorkflows` | phụ trợ |
| `GET /v1/credits?key=<FRONTEND_KEY>` | **số dư credit thật** (key công khai của frontend) |

**tRPC `labs.google/fx/api/trpc`:** `project.createProject`, `media.getMediaUrlRedirect`,
`general.submitBatchLog`. **Upload video:** `labs.google/fx/api/upload-video`
(`FLOW_VIDEO_UPLOAD_CHUNK_SIZE`, captcha `VIDEO_GENERATION`).

**batchexecute `flow.google.com`:** CHỈ `jHPbke` (tạo project) — phần rất nhỏ.

## Bốn năng lực thiếu — GIẢI QUYẾT HẾT bằng REST

1. **Tạo nhân vật:** `POST /v1/flow/entities`, `build_create_entity_rest_payload(project_id)`. Payload:
   `clientContext, projectId, tool:PINHOLE, imageBytes, isUserUploaded, isHidden, mimeType, fileName,
   entityType, characterInfo, entityInfo, entity{entityId,id,name}`. Lỗi:
   *"POST v1/flow/entities: response không có entityId"*.
2. **Edit/extend:** `/v1/video:batchAsyncGenerateVideoEditVideo` + `abra_edit` + upload chunk
   `labs.google/fx/api/upload-video` + `VIDEO_INPUT_TIMELINE_FPS`.
3. **Credits:** `GET /v1/credits?key=<FRONTEND_KEY>` (Referer `https://labs.google/`) → `PAYGATE_TIER_*`.
4. **Upscale video:** `veo_3_1_upsampler_1080p`, `veo_3_1_upsampler_4k`.

## Payload sinh video (tên trường thật, dùng ngay được)

`aspectRatio, seed(DEFAULT_SEED), textInput.structuredPrompt.parts[].text, videoModelKey, metadata.sceneId,
startImage.{mediaId,imageUsageType:IMAGE_USAGE_TYPE_ASSET}, endImage, referenceImages, referenceEntities,
referenceAudio, resolution(VIDEO_RESOLUTION_360P), outputSpec`. Guard:
*"reference_media_ids or reference_entities is required"* (một trong hai là đủ).

## Bảng model key đầy đủ (đào 110 chuỗi, lọc còn key thật)

t2v: `veo_3_1_t2v_{lite,fast}[_{4s,6s}][_portrait][_ultra][_relaxed|_low_priority]` ·
i2v: `veo_3_1_i2v_s_{lite,fast}...` + `veo_3_1_i2v_lite_low_priority` ·
r2v: `veo_3_1_r2v_lite_low_priority` (0 credit), `veo_3_1_r2v_fast_portrait[_ultra][_relaxed]` ·
edit: `abra_edit` · upscale: `veo_3_1_upsampler_{1080p,4k}` · omni: `abra_{t2v,i2v,r2v}_`,
`omni_flash`, `omni_storyboard`, `omni_asmr`.

## Ảnh hưởng tới flowboard-local (CHƯA quyết — cần user)

Nếu đường REST+session sống thật hôm nay, thì:

- **Đổi được cách lấy token:** extension chạy `fetch('/api/auth/session',{credentials:'include'})` trong
  MAIN world trên `labs.google/fx` → lấy Bearer, thay vì batchexecute-in-page. Đây là thay đổi nhỏ ở
  extension, không phải viết lại.
- **Bỏ được cả tầng payload vị trí:** REST dùng JSON có tên → xoá phần đoán ô của `flow_batch.py`.
- **Cả bốn capability mở ra** mà không cần capture gì (payload đã có ở trên).
- **Nhưng:** P13 (batchexecute) đã chạy thật, render clip thật 19/09. Không đập bỏ code chạy được vì
  chuỗi trong binary. Hai đường có thể **cùng sống**; REST tiện hơn vì payload có tên.

## Việc verify (miễn phí, chốt mọi thứ)

Trong trình duyệt đã đăng nhập Flow của user, tại tab `labs.google/fx/tools/flow`, Console chạy:
```js
fetch('/api/auth/session',{credentials:'include'}).then(r=>r.json()).then(d=>console.log(!!d.accessToken, Object.keys(d)))
```
- `true` + có `accessToken` → **REST path sống**, đây là đường đi tới. Chuyển chiến lược.
- `false`/rỗng → session endpoint đã đổi; giữ batchexecute (P13).

## Dọn dẹp

`D:\TOOL_VIDEO\_sandbox-update\` giữ tạm để đào thêm nếu cần; xoá sau khi chốt. Bản sao lưu
`_backup-260920\` (9 mẫu + Workflows + exe cũ) giữ tới khi xong đợt.
