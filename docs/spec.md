# Flowboard Local — Mechanism Spec

Hợp đồng **cơ chế** của bản dựng lại. Bổ trợ, không thay thế:

- `docs/ui-spec.md` — nhãn UI byte-exact, layout từng tab.
- `docs/PLAN.md` — ý niệm canvas/node, kiến trúc mức cao.

Tài liệu này trả lời: *hệ thống nói chuyện với Google Flow bằng cách nào, và chỗ nào lấy cơ chế từ
exe gốc, chỗ nào là mới.*

Nguồn đối chiếu exe (`RUN_VEO_3_ULTRA_PROMAX.exe`, Nuitka, 338 MB):
`TOOL/plans/260830-veo3-dependency-audit/dependency-audit.md`,
`.../sources/user-guide.md`, `TOOL/data_general/config.json`.

---

## 1. Kiến trúc & cổng

| Thành phần | Cổng | Vai trò |
| --- | --- | --- |
| Agent FastAPI | `127.0.0.1:8101` | REST + worker hàng đợi + cache media |
| WebSocket | `127.0.0.1:9223` | Cầu nối extension ⇄ agent |
| Frontend Vite | `localhost:5173` | React UI |
| Extension MV3 | — | Ký + chạy RPC trong tab Flow, giải captcha |

Cổng **cố định**: extension biên dịch cứng `ws://127.0.0.1:9223` và callback `:8101`. Agent nhảy cổng
khác thì extension không bao giờ kết nối được (`run.bat` kiểm tra và từ chối chạy nếu cổng bận).

Trình duyệt là nơi giữ phiên đăng nhập; agent **không** lưu mật khẩu Google.

## 2. Auth — một host, ký ngay trong trang

Tháng 9/2026 Google chuyển Flow sang `flow.google.com` và **ngừng phát token `Bearer ya29…`**. Toàn bộ
`aisandbox-pa.googleapis.com` không còn xác thực được. Bằng chứng, không phải suy đoán: upstream
`crisng95/flowkit` v1.2.0 (commit `57b52e6`, 17/09/2026) xoá hẳn REST transport — *"REST transport
removed … net −1043 lines"* — và log của bản này cho `auth_seen: []` suốt cả phiên, tức **trang mới
không gửi header `Authorization` nào**.

**Mốc đồng bộ upstream: `d7977fd5` (20/09/2026).** Đối chiếu `57b52e6` → `d7977fd5` là **7 commit,
0 commit chạm transport**: `git diff --stat` trên `flow_batch.py`, `flow_client.py`, `flow_sdk.py`,
`omni_flash.py`, `extension/`, `docs/CAPTURE.md` trả về **rỗng**, và `flow_batch.py` của họ vẫn đúng
10 rpcid bản này đã biết. Bảy commit đó là tầng provider AI theo vai + bộ chấm video bằng CLI của
họ — **không có gì để map vào đây**. Hệ quả đáng ghi: các năng lực còn thiếu (`extend`/Edit Video,
`referenceEntities`, credits, listing project) **upstream cũng chưa capture**, nên chúng không phải
việc đồng bộ mà là việc capture — xem `docs/flow-capture.md`.

**Không còn credential nào tới được agent.** Trang tự ký lệnh của nó bằng cookie phiên cộng một token
CSRF `at` sống theo từng lần tải trang (`WIZ_global_data.SNlM0e`). Hai thứ đó không rời khỏi tab, nên:

- agent **không thể** gọi Flow một mình — không có chế độ headless, và điều đó là cấu trúc chứ không
  phải cấu hình;
- agent **không biết** danh tính: `/api/auth/me` trả null có chủ ý, gói Flow là thứ người dùng tự chọn
  trong Settings và chỉ là **nhãn giá**;
- số dư credit **đọc được** qua `nzlxg` (§2.2) — miễn phí, không captcha. Dòng này trước đây ghi
  ngược lại, đúng cho khoảng thời gian từ lúc chuyển transport tới P16.1, và mâu thuẫn với chính bảng
  RPC bên dưới kể từ đó.

| Đường | Auth | Dùng cho |
| --- | --- | --- |
| `flow.google.com/_/AiSandboxAngularFrontend/data/batchexecute` | cookie + `at` của chính trang | toàn bộ sinh ảnh/video, upload, poll, tạo project |
| `grok.com/rest/`, `shopee.vn/api/v4/` | cookie phiên của site đó | Grok, Shopee — **không bao giờ** thấy credential Google |

Đường thứ hai đi qua `flow_client.trpc_request` và allowlist **theo path** nằm trong
`extension/background.js` (`SESSION_CHANNELS`). Bản agent giữ bản sao thứ hai của allowlist đó đã bị
xoá cùng request kind `proxy`: hai bản sao của một biên giới an ninh là hai bản có thể lệch nhau, và
chỗ lệch chính là cái lỗ.

### 2.1 Envelope `batchexecute`

```text
agent  : f.req = [[[rpcid, "<payload dạng chuỗi JSON>", null, "generic"]]]
ext    : mint reCAPTCHA trong tab Flow → thay chỗ giữ "__CAPTCHA__" trong f.req
         → chrome.scripting.executeScript({world:'MAIN'}) trong tab flow.google.com:
           POST …/batchexecute?rpcids=<id>&source-path=…&bl=<cfb2h>&f.sid=<FdrFJe>&hl=…&_reqid=…&rt=c
           body: f.req=<envelope>&at=<SNlM0e>        credentials: include
         → trả {status, text}  (hoặc {status, matched, text} = cửa sổ 800 byte quanh `match`)
agent  : bỏ sentinel ")]}'" → đọc chunk ["wrb.fr", rpcid, "<payload>", …]; lỗi nằm ở ô [5]
```

Hai chi tiết đắt giá:

- **Hàm chạy trong MAIN world phải tự chứa.** Tham chiếu ra biến ngoài → Chrome trả
  `NO_INJECTION_RESULT`, và nó thường xảy ra ở RPC đầu tiên sau khi agent khởi động, nên có retry
  đếm được thay vì coi là lỗi.
- **Token captcha dùng một lần.** Gửi lại → `PUBLIC_ERROR_UNUSUAL_ACTIVITY`. Việc mint được serial hoá
  trong `injected.js` (`captchaMintTail`, chờ grecaptcha 22 s) vì hai lần mint song song thì một trong
  hai chắc chắn bị thay thế.

### 2.2 Bảng RPC

| rpcid | Việc | Ghi chú |
| --- | --- | --- |
| `ogiZ0b` | sinh ảnh | URL ký trả **ngay trong response**; một RPC mỗi biến thể; aspect ảnh 1 vuông/2 dọc/3 ngang/4 3:4/5 4:3 |
| `eb1hJf` | i2v — Veo **và** Omni first-frame | model là chuỗi trong ô; aspect video **1 dọc / 2 ngang** (khác bảng ảnh) |
| `nprQif` | Omni first+last | khả năng mới của bản này; Veo không có payload |
| `YhhmEf` | Omni text-to-video | trả media/workflow, poll qua `as29s` |
| `MZZa6b` | Omni references (Thành Phần) | refs dạng `[[null, mediaId], …]` |
| `jwpduf` | poll operation | `CAE` = xong; *"Media not found."* là **chẩn đoán**, không phải thất bại |
| `Zzl0ze` | listing media của project | ~17 MB → extension cắt cửa sổ 800 byte quanh `match`; **đây mới là nơi có mediaId** |
| `as29s` | mediaId → URL ký | poster `/image/` tới trước, `/video/` tới sau |
| `maseQ` | upload ảnh (base64 trong payload) | **có captcha** — đường REST cũ không cần |
| `jHPbke` | tạo project | thất bại → dùng `FLOW_PROJECT_ID` dán tay, và **báo ra** |
| `nzlxg` | số dư credit | **đọc, 0 credit, không captcha**; số dư cũng về miễn phí ở ô 1 của 4 reply khác |
| `C4BZMd` | tạo nhân vật (entity) | uuid project **trần**, không `projects/`, không client-context, không captcha |
| `rqZuUc` | tạo scene từ clip đã xong | trả scene id **và một CLONE**; clone là thứ lần nối đầu phải tham chiếu |
| `fZytfe` | nối tiếp clip (extend) | chỉ clip **Veo**; làn free mặc định; **giá chưa đo** |
| `p0UkFb` | upscale video 1080p | ô 0 = operation id, ô 4 = media id — **hai id khác nhau**; **giá chưa đo** |

`QI2zvc` (xoá project) **không port**: xoá là hành động khó đảo và không có ai gọi.

**Upscale 4K: TỪ CHỐI.** Hai capture độc lập không khớp ở ô tier (PR #47 ghi 3, PD-Auto-Footage
luôn gửi 2 và chỉ đổi model key), và 4K tốn 50 credit. Đoán ở đây là tiêu tiền của người dùng để
biết ai đúng. Tên model `veo_3_1_upsampler_4k` được ghi lại để một capture sau có cái đối chiếu,
**không** để gọi được.

### 2.3 `POST /api/auth/batch-probe` — đo bằng RPC không tính tiền

Có vài hành vi bản này đang phải **giả định** mà một cuộc gọi miễn phí trả lời được: project lạ thì Flow
báo lỗi hay trả listing rỗng, operation đã cũ còn tra được không, bản ghi media đã có URL video chưa.
Route này gửi **một** RPC như thế và báo lại *hình dạng* câu trả lời.

Hai luật làm nó an toàn để giữ lại, vì đây là endpoint localhost không cần đăng nhập:

1. **Allowlist ba RPC chỉ đọc** (`Zzl0ze` luôn kèm `match`, `jwpduf`, `as29s`) và **envelope dựng phía
   server**. Nới thêm một RPC sinh thì nó thành "tiêu credit của người này"; nhận `f.req` thô thì nó
   thành "gửi lệnh Flow bất kỳ dưới danh nghĩa người đang đăng nhập".
2. **Mô tả, không dội lại.** Cửa sổ listing chứa tiêu đề và **prompt** của media; dội ra là in prompt
   của người dùng vào console và activity log. URL ký chỉ báo có/không — một URL ký trong log là một lần
   tải mà bất kỳ ai giữ log đều làm được.

## 3. Vòng đời một request

```text
UI → POST /api/requests (type, params)
   → hàng đợi (SQLite) → worker (semaphore, cooldown 7s giữa các dispatch)
   → dispatch: một RPC mỗi ảnh/mỗi biến thể, mỗi lần một captcha riêng
        ├─ ảnh   → ogiZ0b  → URL ký có luôn trong response
        └─ video → eb1hJf / nprQif / YhhmEf / MZZa6b → operation id (hoặc workflow)
   → poll ba tín hiệu: jwpduf (trạng thái) → Zzl0ze (mediaId, luôn kèm match) → as29s (URL ký)
   → có URL `/video/` → tải → cache storage/media/<id>.<ext>
                      → đồng thời ghi VIDEO_OUTPUT_DIR/<project>/{video,image}/
   → UI phát qua /media/<id>
```

### 3.1 Ba tín hiệu, và chúng tới không đúng thứ tự

Không tín hiệu nào một mình đủ để tải clip về. Đây là phần đắt nhất của cả pha, vì mỗi cách hiểu sai
đều dẫn tới một clip đã trả tiền mà không lấy được:

| Tín hiệu | Nói gì | Bẫy |
| --- | --- | --- |
| `jwpduf` operation | tiến độ | có job **xong rồi mà không có trạng thái nào**; *"Media not found."* vẫn có thể giao clip 8 giây |
| `Zzl0ze` listing | **mediaId** | 17 MB và to dần theo từng lần sinh → không gọi kèm `match` là mất khoảng một nửa số lần tra |
| `as29s` media | URL ký | poster ảnh có trước clip → tải theo id không thôi là lưu về một bức ảnh tĩnh |

Nên một operation chỉ báo `SUCCESSFUL` **khi đã có URL `/video/`**. Mọi trạng thái ngắn hơn thế là
pending, và timeout thuộc về vòng lặp của caller.

Listing là **thẩm quyền** nhưng cũng là cuộc gọi đắt nhất, nên chỉ hỏi khi operation báo có chuyện,
khi poll không đọc được, hoặc **mỗi vòng thứ ba** (`LISTING_EVERY_NTH_ROUND`). Ba cache
(`_op_projects` / `_op_media` / `_op_polls`) có trần `OP_CACHE_LIMIT = 512` và **bị xoá cùng nhau** —
cùng khoá theo operation id, xoá lẻ một cái là để lại một mediaId không biết thuộc project nào.

### 3.2 Text-to-video trả workflow, không trả operation

`YhhmEf` đáp bằng một bản ghi media/workflow chứ không phải operation handle, nên nó được poll thẳng
qua `as29s`. `check_async()` nhận cả hai và gộp về chung một shape
(`{name, done, media_entries, status, error}`), nên worker không cần biết mình đang ở chế độ nào.

### 3.3 Lưu trữ

Mỗi media về đích ở **hai nơi** (`services/media.py`):

1. `storage/media/<media_id>.<ext>` — bản UI phát ngay.
2. `<VIDEO_OUTPUT_DIR>/<CURRENT_PROJECT>/{video|image}/<media_id>.<ext>` — bản người dùng mở bằng
   Explorer, đúng bố cục exe gốc.

Bản (2) ghi qua file tạm `.part` rồi `os.replace` để không bao giờ để lại file nửa vời, và **không bao
giờ ném lỗi**: ổ đầy không được phép biến một generation đã trả tiền thành thất bại.

Chỉ chấp nhận mime `image/*` hoặc `video/*`; URL chỉ nhận từ `flow-content.google`.

## 4. Bảng model key

Đường `batchexecute` nhận **đúng ba key Veo**. Mọi key có hậu tố `_portrait` / `_fl` / `_relaxed` bị
từ chối — aspect đã thành một ô riêng trong payload, nên nó không còn nằm trong tên key nữa. Tất cả ở
`services/flow_sdk.py`, và tập đóng của phía dây ở `services/flow_batch.py`.

| Làn | Key | Giá |
| --- | --- | --- |
| `lite_relaxed` | `veo_3_1_i2v_lite_low_priority` | **0 credit** — làn miễn phí duy nhất còn lại |
| `lite` | `veo_3_1_i2v_lite` | có tính tiền |
| `fast` | `veo_3_1_i2v_s_fast_ultra` | có tính tiền; tên mang chữ Ultra, **chưa đo trên Pro** |
| `omni` | `abra_i2v_<N>s` · `omni_flash_i2v_<N>s_first_last` · `abra_t2v_<N>s` · `abra_r2v_<N>s` | 15–30 credit theo độ dài |

**Text-to-video có bảng key Veo riêng** (`VEO_T2V_LANES`, 12 key đào từ exe). Đây là chỗ bản này
**sai rồi sửa trong cùng một ngày**, nên ghi lại cả hai:

- P13.6 từ chối mọi làn Veo t2v, tin rằng `YhhmEf` không có payload Veo. Từ chối là đúng **với niềm
  tin đó** — thay bằng OMNI là tính 15–30 credit cho làn mà tool đóng gói ghi là miễn phí.
- **Niềm tin đó chưa ai đo.** 19/09/2026 đo thật: `veo_3_1_t2v_lite_low_priority` trả
  `PUBLIC_ERROR_MODEL_ACCESS_DENIED` — lỗi **GÓI**, tức RPC có phân tích key Veo. Và
  `veo_3_1_t2v_lite` **sinh clip thật 8,00 s · 1280×720**.

| Làn t2v | 4s | 6s | 8s |
| --- | --- | --- | --- |
| `lite_relaxed` | `..._lite_4s_low_priority` | `..._lite_6s_low_priority` ✅ | `..._lite_low_priority` ✅ |
| `lite` | `..._lite_4s` ✅ | `..._lite_6s` | `..._lite` ✅ **sinh clip thật** |
| `fast` | `..._fast_4s` | `..._fast_6s` | `..._fast` ❓ chưa đo |

✅ = RPC xác nhận biết tên. Không gửi biến thể `_portrait`, và **làn `fast_relaxed` đã bị gỡ**: cả ba
tên của nó đều mang `_relaxed`. Không có key 10s — 10 giây là của OMNI.

**Đo trên gói Pro: 4s và 6s bị từ chối, 8s chạy** — đúng bằng kết quả thời REST, tức quyền theo tài
khoản đi xuyên qua cuộc migration. Registry vẫn chào 4s/6s kèm câu đó, vì không đọc được entitlement
và một lần bị từ chối thì không tốn gì.

### 4.2 Luật đặt tên của đường batch, đo trên 4 RPC

Cách đo: gửi một key **hậu tố miễn phí** (`_low_priority` / `_relaxed`) mà gói Pro chắc chắn không có,
rồi đọc lỗi. Hai câu trả lời khác hẳn nhau:

| Trả lời | Nghĩa | Tốn |
| --- | --- | --- |
| `[7]` `PUBLIC_ERROR_MODEL_ACCESS_DENIED` | RPC **biết** tên, chỉ gói không có | 0 |
| `[5]` NOT_FOUND | RPC **không biết** tên | 0 |

Nhờ vậy gần hết không gian tên đo được mà không sinh clip nào. Kết quả (19/09/2026):

| Hậu tố | Đường batch | Ví dụ |
| --- | --- | --- |
| `_low_priority` | **BIẾT** | `veo_3_1_i2v_s_lite_4s_low_priority`, `veo_3_1_r2v_lite_low_priority`, `veo_3_1_t2v_lite_6s_low_priority` |
| `_relaxed` | **KHÔNG** | `..._i2v_s_fast_ultra_relaxed`, `..._t2v_fast_4s_relaxed`, `..._r2v_fast_portrait_ultra_relaxed` |
| `portrait` | **KHÔNG** | `..._i2v_s_fast_portrait_ultra_relaxed` ⚠️ xem dưới |
| `_fl` | **KHÔNG** | đo lại 20/09 bằng control sạch — xem dưới |

#### ⚠️ Vòng 19/09 bị nhiễu; `_fl` đo lại 20/09, `portrait` thì chưa

Mọi tên dùng để kết luận `portrait` chết và `_fl` chết ở bảng trên **cũng mang `_relaxed`**, mà
`_relaxed` đã được chứng minh độc lập là không biết. Nên `[5]` của chúng được giải thích trọn vẹn bởi
`_relaxed`, và hai luật kia **chưa được cô lập**. Sai sót của bản ghi, không phải của mã: không bảng nào
có key `portrait`/`_fl` servable, nên hành vi từ chối vẫn đúng.

**`_fl` đã đo lại (20/09, 0 credit, `tools/probe-naming-rule.py`)** với control khác đúng một đoạn:

| | tên | media | trả về |
| --- | --- | --- | --- |
| A | bogus | thật | `[5]` |
| B | `veo_3_1_i2v_lite_low_priority` (ngoài gói) | **giả** | `[7]` MODEL_ACCESS_DENIED |
| C | `veo_3_1_i2v_lite_low_priority` (ngoài gói) | thật | `[7]` MODEL_ACCESS_DENIED |
| 1 | `veo_3_1_i2v_lite_**fl**_low_priority` | thật | **`[5]`** |

Test khác control C **đúng một đoạn `_fl`** → `_fl` thật sự không được biết. Kết luận cũ đúng, nay có
bằng chứng.

**`portrait` vẫn chưa được cô lập** — cần một tên `portrait` không mang `_relaxed` để chốt. Không chặn
gì: bảng hiện tại không có key `portrait` nào.

#### Thứ tự kiểm của Flow, và vì sao `[5]` mơ hồ (đo 20/09)

Control B ở trên trả `[7]` **về model** dù media là giả. Nên Flow kiểm theo thứ tự:

1. tên có tồn tại? → **`[5]`** nếu không
2. gói có quyền? → **`[7]`** nếu không
3. media có hợp lệ? → **`[5]`** nếu không

Hai hệ quả thực tế:

- **`[5]` là câu trả lời cho HAI chuyện khác nhau** (tên model lạ *và* media không tìm thấy), nên nhãn
  tiếng Việt cho nó **không được** khẳng định một trong hai. Một probe dựng trên media giả vì thế
  không bao giờ cô lập được tên — vòng đầu của `probe-naming-rule.py` thất bại đúng vì lý do này.
- Oracle dùng được là **entitlement**, không phải media hỏng: một tên ngoài gói không render được dù
  Flow biết nó.

Hai hệ quả về làn:

- **Veo khung-đầu-khung-cuối chết hẳn**: `nprQif` không biết tên `_fl` nào, `eb1hJf` cũng vậy. Từ chối
  trong sản phẩm là đúng và vĩnh viễn.
- **`omni_flash_i2v_<N>s_first_last` CHẠY ĐƯỢC — đo 20/09, tốn 15 credit.** Key **duy nhất** đứng sau
  toàn bộ mặt Start-End và trước đó chưa có phép đo nào (builder không ghi ngày capture, không có mục
  trong `flow-capture.md`). Không có oracle miễn phí cho nó: gói này **CÓ** Omni, nên tên đúng + media
  giả cũng trả `[5]` y như tên lạ. Phép cược tự giới hạn nên mới đáng đánh: tên lạ trả `[5]` và **không
  tốn gì**, chỉ nhánh "chạy được" mới tiêu tiền. Kết quả: `nprQif` nhận
  `omni_flash_i2v_4s_first_last` (dựng bằng `fb.omni_first_last_request` thật của sản phẩm) và tạo
  operation. Tab Start-End sống, key trong builder là đúng.
- **Veo r2v sống, đúng một key** — và **ô model đã xác nhận (20/09)**.
  `veo_3_1_r2v_lite_low_priority` đặt vào **đúng `request[2]`** mà
  `omni_reference_video_request` ghi → `MZZa6b` trả `[7] MODEL_ACCESS_DENIED`, trong khi một tên bogus
  ở **cùng ô, cùng RPC** trả `[5]`. Nên ô đó là ô RPC đọc tên Veo, và làn là thật. Ba tên còn lại mang
  `portrait` → `[5]`.
  **Giá thì CHƯA ĐO.** `_low_priority` thường là hàng đợi miễn phí trên đường này, nhưng đó là suy
  luận: key không nằm trong `ZERO_CREDIT_MODEL_KEYS`, không ai công bố giá, và `[7]` nghĩa là **gói
  hiện tại không có key** nên không chạy thử được để biết. `/estimate` vì vậy báo **chưa có giá**
  (`unpricedJobs`), không báo 0 — nhãn "miễn phí" là thứ cho phép vòng review tự chạy lại clip.

Bảng r2v (`VEO_R2V_LANES`): `lite_relaxed → veo_3_1_r2v_lite_low_priority`. Hết.

| Bảng | Trục | Ghi chú |
| --- | --- | --- |
| `IMAGE_MODELS` | nickname → wire id | `NANO_BANANA_PRO`→`GEM_PIX_2`, `NANO_BANANA_2`→`NARWHAL`, `NANO_BANANA_2_LITE`→`HARBOR_SEAL` |
| `BATCH_VIDEO_LANES` | làn → key | ba key trên; không còn trục tier lẫn trục aspect |
| `REFUSED_VIDEO_LANES` | làn → lý do | `fast_relaxed`, `quality` — vẫn chào ra, kèm lý do bị từ chối |
| `ZERO_CREDIT_MODEL_KEYS` | tập key miễn phí | **một** phần tử; đây là thứ vòng review đọc trước khi tự chạy lại |
| `omni_model_key(mode, duration, resolution)` | 4 mode × 4 độ dài × 2 độ phân giải | duration **là** phần của key |

### 4.0 Luật tiền: TỪ CHỐI, không thay thế

Đây là chỗ bản này cố ý khác upstream, và lý do đáng viết ra một lần:

`resolve_video_model` của upstream gập mọi key lạ về mặc định. Vì nó khớp theo **chuỗi con** `"ultra"`,
key `veo_3_1_i2v_s_fast_ultra_relaxed` — **0 credit** — gập vào `veo_3_1_i2v_s_fast_ultra`, **có tính
tiền**. Một làn xin miễn phí mà bị tính tiền là phép thay thế duy nhất bản này không bao giờ làm.

Nên `check_video_model()` **từ chối** key ngoài tập đóng, `resolve_video_plan()` từ chối làn không có,
và `model_substitutions` luôn rỗng. Khoá đó vẫn nằm trong hợp đồng trả về vì `jobs.ts` đọc nó để cảnh
báo — giữ kênh thì không phải sửa frontend nếu sau này có capture làm cho một phép thay thế trở nên
trung thực. Và nó được **ghim là rỗng**, vì một phép thay thế lặng lẽ xuất hiện ở đó chính là lỗi.

Các năng lực **chưa có payload** trên đường mới, đều từ chối có tên lý do:

| Năng lực | Mã lỗi | Thay bằng |
| --- | --- | --- |
| ~~Veo text-to-video~~ | — | **ĐÃ DỰNG LẠI** 19/09 sau khi đo; xem bảng §4 |
| Veo first→last | `unsupported_on_batch_veo_start_end` | Omni `nprQif` — **đo 20/09: Omni chạy thật**; tên `_fl` của Veo thì `[5]`, cô lập được ở §4.2 |
| Veo references ngoài `lite_relaxed` | `unsupported_on_batch_veo_r2v_<tên>` | `lite_relaxed` **chạy được** (ô model xác nhận 20/09); **giá chưa đo** → estimate báo chưa có giá, không báo 0 |
| Character Entity | `unsupported_on_batch_reference_entities` | — (ảnh tham chiếu vẫn chạy) |
| Danh sách project | `unsupported_on_batch_project_listing` | — |
| Edit Video (`abra_edit`) | — (node gỡ khỏi palette) | — |

Quy tắc còn giữ nguyên từ bản cũ:

- **Aspect lạ trả lỗi**, không đoán landscape. Đoán sai = video sai khung mà vẫn mất tiền.
- **Duration lạ trả lỗi**, không làm tròn. Với Omni, duration nằm trong key nên làm tròn = tính tiền
  một độ dài khác thứ được xin.
- Làn trống rơi về `lite`, **không** về `fast`: `fast` là key đắt nhất đường này có, và một board chưa
  từng chọn gì thì không nên bị tính giá cao nhất.

### 4.1 `GET /api/models` — một nguồn sự thật cho UI

Trước đây mỗi tab tự gõ danh sách model/tỉ lệ/thời lượng: **hơn 20 danh sách ở 10 file**, và chúng đã
lệch nhau. Ba chỗ lệch thật, đã sửa:

| Lệch | Hậu quả | Cách sửa |
| --- | --- | --- |
| Tỉ lệ **Vuông** lọt vào đường video | Không key video nào nhận Vuông → dispatch chắc chắn hỏng | Vuông chỉ còn ở `imageAspects` |
| Settings cho chọn 4–10s, `TextToVideoTab` khoá cứng 8s | Số người dùng chọn bị bỏ im lặng | Tab lấy thời lượng từ registry; Settings ghi rõ độ dài nào chỉ áp dụng cho OMNI |
| Backend fallback `fast`, frontend fallback `lite` | Cùng input, hai hoá đơn khác nhau | Một hàm `qualityFromLabel`, khớp **nhãn chính xác** — không dò chuỗi con |

Endpoint **không còn lọc theo tier**: payload mới không có ô `userPaygateTier`, nên tài khoản có làn
nào là câu trả lời của Google lúc dispatch, không phải thứ process này biết được. Tier chỉ còn là nhãn.

Làn đường này không có **vẫn được chào** — nhãn đó là hợp đồng UI của exe và người dùng sẽ đi tìm nó —
nhưng `note` đã đổi nghĩa, và đây là chỗ nói dối tốn tiền nhất nếu để nguyên: nó từng hứa *"sẽ chạy Veo
3.1 - Lite và CÓ tính credit"*. Lời hứa đó không còn được giữ — nay dispatch **báo lỗi, miễn phí** — nên
note phải nói "sẽ bị TỪ CHỐI". Ai đọc note cũ sẽ huỷ một lần chạy vốn không hề tính tiền họ.

`defaultValue` cố ý chọn lựa chọn **không có note**: không ai bị đặt mặc định vào làn sẽ bị từ chối.

`settingsOptions` chiếu cùng các bảng đó sang **không gian giá trị của Settings** (nhãn `"Veo 3.1 -
Lite"`, không phải token `lite`), vì đó là thứ `settings_store` kiểm. Bảng giá OMNI cũng chuyển hẳn về
đây; bản sao trong `store/settings.ts` đã xoá — bản sao cũ sẽ báo giá mà tài khoản không bị trừ.

### 4.3 Hai thao tác trên clip ĐÃ TRẢ TIỀN — `upscale_video`, `extend_video`

Cả hai đi qua `POST /api/requests` như mọi việc khác, nên không có route riêng, và cùng dùng vòng poll
`_poll_video_dispatch` sẵn có. Cái khác là **hậu quả khi sai**: một lần sinh sai thì mất lần sinh đó,
còn hai cái này có thể mất **bản gốc**.

**Không bao giờ ghi đè `mediaId`.** Upscale ghi vào `upscaledMediaId`, extend nối vào
`extensionMediaIds`. Bản gốc là thứ đã trả tiền, và một nút "nâng cấp" xoá nó là hỏng không đảo được.
Quyết định này nằm trong `lib/clipOps.ts` dưới dạng hàm thuần, có test và có đột biến.

**Hai id, không suy được từ nhau.** Upscale đánh địa chỉ **operation** ở ô 0 và **media** ở ô 4. Capture
cảnh báo dùng lẫn thì Flow *nhận rồi báo NOT_FOUND* — nhìn từ ngoài giống hệt thành công. Nên clip render
trước khi `_settle_generation_node` bắt đầu lưu `operationNames` bị **từ chối kèm lý do** ("chạy lại clip
để có id"), chứ không đoán id từ mediaId.

**Chuỗi nối.** Lần nối **đầu** tham chiếu **CLONE** mà `rqZuUc` tạo ra khi gói clip thành scene; lần sau
tham chiếu **operation id của lần nối trước**. Đổi operation id đó thành mediaId là request Flow nhận rồi
báo NOT_FOUND — đã tính tiền, không có clip. Vì thế `extensionOperationIds` được lưu **song song** với
`extensionMediaIds` và **được hydrate ở cả 3 map board**: mất nó sau một lần reload là chuỗi quay về
clone, render lại từ đầu clip, và Flow tính tiền.

**Clip Omni bị từ chối TRƯỚC khi ghi gì lên Flow.** Flow chỉ nối được clip Veo. Lời từ chối gọi tên clip
(`abra_i2v_8s`) và đến trước cả lệnh tạo scene — scene là lệnh miễn phí nhưng vẫn là một bản ghi thật
trong project của người dùng, tạo nó cho một clip không bao giờ nối được là để rác lại. Luật này có **một
định nghĩa** (`flow_sdk.is_omni_model_key`) dùng cho cả hai chỗ chặn.

**Làn:** extend mặc định `veo_3_1_extension_lite_low_priority` (free). Trả phí chỉ khi caller nói
`paid_lane` **đúng bằng `True`** — `params` tới qua HTTP không được kiểm, và chuỗi `"false"` là truthy
trong Python.

#### Giá: CHƯA ĐO, và cách nó sẽ được đo

Không mục nào dưới đây được coi là miễn phí:

| Thao tác | Giá | Vì sao chưa ghi |
| --- | --- | --- |
| upscale 1080p | **chưa đo** | không có giá công bố; bản này chưa chạy lần nào |
| extend làn free | **chưa đo** | capture của người khác ghi "tested 0 credit" — đó là phép đo trên máy họ |
| extend làn trả phí | **chưa đo** | — |
| upscale 4K | 50 credit (công bố) | nhưng payload bị từ chối, xem trên |

`ZERO_CREDIT_MODEL_KEYS` **không** nhận `veo_3_1_extension_lite_low_priority`. Bảng đó là thứ vòng review
dùng để tự chạy lại; ghi một key chưa tự đo vào đó là mở cửa cho tự động tiêu tiền.

Thay vào đó mỗi lần chạy **tự đo**: đọc số dư trước (miễn phí — `nzlxg`, hoặc đồng hồ nếu còn tươi), chạy,
rồi đọc lại (poll `jwpduf` mang số dư về không mất gì). Kết quả nằm trong `credits_spent_observed` +
`credits_note`, và tên trường nói rõ nó là **quan sát**: hiệu số phủ mọi thứ tài khoản tiêu trong cửa sổ
đó, nên nó chỉ là giá của thao tác khi lúc ấy không có render nào khác. Đo xong mới cập nhật bảng trên.

## 5. Nhịp, retry, circuit breaker

| Hằng số | Giá trị | Nguồn |
| --- | --- | --- |
| `VIDEO_POLL_INTERVAL_S` | 7.0 | exe `WAIT_GEN_VIDEO: 7` |
| `VIDEO_POLL_MAX_CYCLES` | 86 (≈10 phút) | chuỗi VN trong exe: "timeout sau 10 phút polling" |
| `API_COOLDOWN_S` | 7.0 | giãn cách giữa các **dispatch** |
| `MAX_CUMULATIVE_403` | 3 | breaker bảo vệ tài khoản |
| `BREAKER_PROBE_INTERVAL_S` | 60 | nửa mở, tự hồi phục |
| `CAPTCHA_MAX_ATTEMPTS` | 10 | captcha cần người giải, kiên nhẫn hơn |
| `FREE_MAX_RETRIES` | 20 | ngân sách riêng cho lỗi hạ tầng phía mình |
| `max_attempts` | `RETRY_WITH_ERROR` (mặc định 3) | exe `RETRY_WITH_ERROR: 3` |

### 5.1 Phân loại lỗi (`classify_error`)

Thứ tự phân loại **quan trọng**: lỗi do chính mình sinh ra (`_SELF_TERMINAL_PREFIXES`:
`PUBLIC_ERROR_`, `missing_`, `invalid_`, `no_*_model_for_tier_`…) được nhận diện **trước** mọi mẫu tìm
mã HTTP. Lý do: các lỗi này nhúng nguyên giá trị người gọi đưa vào (`invalid_duration_403`), endpoint
tạo request không cần đăng nhập, nên nếu không xử lý trước thì **ba POST rác đủ làm nổ breaker** và
dừng toàn bộ generation. Một request ta từ chối gửi đi không nói lên điều gì về tài khoản.

Các nhóm: `403` (nuôi breaker) · `captcha` · `free` (hạ tầng phía mình — không đốt ngân sách attempt)
· `counted` (429/5xx/transient) · `terminal` (lọc nội dung, input sai — không bao giờ retry).

Từ vựng lỗi của đường `batchexecute`:

| Lỗi | Nhóm | Vì sao |
| --- | --- | --- |
| `NO_AT_TOKEN`, `NO_FLOW_TAB`, `FLOW_TAB_DISCARDED`, `NO_INJECTION_RESULT` | `free` | trang không ký được; không tốn gì, và sửa ở trình duyệt |
| `PUBLIC_ERROR_UNUSUAL_ACTIVITY` | `captcha` | token captcha bị dùng lại. Nó **không** chứa chữ "captcha" lẫn mã HTTP, nên luật chung từng đọc nó thành lỗi lạ → terminal, tức bỏ cả batch chỉ vì cần một token mới |
| `flow_transient_retry: …` | `counted` | Flow đáp `[8]` khi phía sinh của nó từ chối vì tải. SDK gắn mã này ở chỗ duy nhất thấy được `detail`, thay vì để worker đoán qua chuỗi lỗi |
| `unsupported_on_batch_*` | `terminal` | retry không làm payload chưa capture xuất hiện |
| `NO_FLOW_PROJECT`, `create_project_failed` | `terminal` | cần người; retry hai mươi lần chỉ làm chậm thông báo đó |

### 5.2 Hai luật cứng của breaker

1. Breaker mở thì công việc **tạm dừng**, không bị huỷ. Từng có lỗi: khi mở, mọi thất bại bị coi là
   terminal nên mỗi lần probe lại giết một request thật — breaker ăn dần hàng đợi.
2. Probe chỉ bị tiêu khi thực sự có request được lấy ra, không tiêu ở nhịp rảnh.

### 5.3 Không retry một dispatch đã tạo operation — nhưng **poll lại** thì được

`retryable = cls in (...) and not op_created`. Dispatch đã tạo operation nghĩa là **credit đã trừ**;
retry là tạo bản thứ hai và trừ tiền lần nữa. Vì vậy mọi lỗi dispatch video đều mang theo tên các
operation đã tạo (`_dispatch_error`), để điều kiện này mang tính cấu trúc chứ không dựa vào chuỗi lỗi.

Đường mới **thêm một nửa còn thiếu**. Trước đây luật trên đúng nhưng để clip mắc kẹt: một batch Omni 4
biến thể bỏ dở ở timeout là 60 credit video không ai lấy được. Trên `batchexecute`, mediaId của một clip
đã xong **tìm lại được qua listing**, nên RUN Lỗi và chạy lại đều đi qua `_unresolved_video_operations`:
node nào còn operation chưa giải quyết thì tạo request `poll_video` (không captcha, không submit, không
credit) thay vì dispatch mới.

Phạm vi hẹp có chủ ý: chỉ poll lại operation **chưa** giải quyết. Operation Google đã từ chối vì lọc nội
dung là terminal của riêng nó — poll lại chỉ đổi một câu trả lời rõ ràng thành mười phút chờ để nhận
đúng câu đó.

## 6. Hợp đồng Settings

`services/settings_store.py` xếp lớp: `BASELINE` → `data_general/config.json` (chỉ đọc, exe sở hữu) →
override của người dùng trong bảng `appsetting`.

Hai tính chất an toàn:

- **Whitelist chứ không blacklist.** Chỉ khoá trong `SETTABLE` được đọc/ghi. `account1`,
  `account1_token`, `grok_account`, `*_token` **không** nằm trong tập đó nên không bao giờ lộ qua
  `GET /api/settings`. Khoá nhạy cảm thêm vào config.json sau này bị chặn mặc định.
- **Gộp hoa/thường.** config.json có cả `VIDEO_OUTPUT_DIR` lẫn `video_output_dir`; chuẩn hoá về UPPER.
- Ghi **all-or-nothing**: một giá trị sai → không ghi gì cả, trả 400.
- Đọc bằng `utf-8-sig`: exe có thể ghi lại file kèm BOM, `utf-8` thường sẽ ném lỗi và âm thầm tụt
  toàn bộ settings về BASELINE mà không rõ nguyên nhân.

### 6.1 Khoá nào THỰC SỰ có tác dụng

Đây là phần dễ hiểu lầm nhất. `SETTABLE` phơi ra ~50 khoá cho đúng bề mặt của exe, nhưng **không phải
khoá nào cũng có người đọc**. Bảng này là sự thật đo bằng cách truy vết người tiêu thụ:

| Khoá | Ai đọc | Tác dụng |
| --- | --- | --- |
| `VIDEO_OUTPUT_DIR`, `CURRENT_PROJECT` | `media._export_to_output_dir` | ✅ nơi ghi file ra ngoài |
| `VIDEO_ASPECT_RATIO` | cả 5 tab (seed control) | ✅ |
| `VEO_MODEL` | tab t2v (quality), i2v (model) | ✅ |
| `VIDEO_DURATION_SECONDS` | **chỉ** tab i2v | ✅ ở i2v; t2v cố ý bỏ qua (khoá 8s, §4) |
| `CREATE_IMAGE_MODEL` | tab t2i, i2i | ✅ |
| `OUTPUT_COUNT` | tab t2i (số biến thể) | ✅ |
| `MULTI_VIDEO` | worker semaphore | ✅ (env `FLOWBOARD_MAX_CONCURRENT` ghi đè) |
| `RETRY_WITH_ERROR` | `max_attempts` lúc tạo request | ✅ |
| `LOCAL_STT_ENABLED`, `LOCAL_STT_MODEL` | `services/stt` | ✅ bật nguồn phiên âm offline (faster-whisper) |
| `OPENAI_IMAGE_ENABLED`, `OPENAI_IMAGE_RELAY_ENABLED`, `OPENAI_IMAGE_PREFER_RELAY`, `OPENAI_IMAGE_MODEL`, `OPENAI_IMAGE_QUALITY` | `services/openai_images` | ✅ engine ảnh thứ hai (§6.3) |
| `VIDEO_RESOLUTION`, `AUTO_UPSCALE`, `DOWNLOAD_MODE`, `SEED_*`, `WAIT_*`, `CLEAR_*`, `SUB_*`, `LOGO_*`, `WM_MODE`, `IDEA_*`, `VOICE_*`, `GROK_*`, `RUN_CAPTCHA`, `FIX_403_RECAPTCHA`, `SKIP_AUDIO_ERROR`, `CREATE_IMAGE_QUALITY` | **không ai** | ⚠️ **trơ** — lưu được nhưng chưa nối vào hành vi |

> **Bắt buộc khi dựng tab Cài đặt:** không phơi control cho khoá trơ, hoặc phải nói rõ nó chưa có tác
> dụng. Dựng nút không ai nghe là đúng cái bẫy đã mắc một lần với `🖼 Quality`.
>
> `SUB_*` và `LOGO_*` trơ ở tầng settings là **có chủ đích**: postprod nhận giá trị này qua tham số
> request tường minh chứ không đọc settings. Muốn nối thì sửa ở tầng gọi postprod.

### 6.2b Mẫu cá nhân, RUN Lỗi, Dừng toàn bộ

**Ba nguồn mẫu, chỉ một nguồn ghi được.** `system_workflows/` (9 mẫu đóng gói) và `Workflows/`
(board tool gốc tự lưu) **chỉ đọc** — chúng thuộc về exe. Mẫu do app này lưu nằm ở
`storage/templates/`, và là nơi duy nhất route sửa/xoá chạm tới. `TemplateSummary` trả thêm
`source` + `writable` để UI không bày nút Xoá lên file của người khác.

**Mẫu cá nhân dùng định dạng riêng, không dùng định dạng exe.** Lý do phải ghi lại vì nó ngược
trực giác "cho gọn thì dùng chung một format": `NODE_TYPE_MAP` là ánh xạ nhiều-về-một (4 loại
prompt của exe gộp thành 1 node `prompt`), và **5 loại node của canvas này không có đối ứng nào
bên exe** — `character`, `note`, `Storyboard`, `review_video`, `remove_watermark`. Lưu qua bộ
chuyển đó sẽ **âm thầm rơi mất node của chính user**. Lưu mà mất việc còn tệ hơn không lưu.
Bộ đọc nhận cả hai: có `connections` → định dạng exe → `template_import`; có `format` → định
dạng này. Nhờ vậy "nhập file JSON" vẫn nhận được file workflow của tool gốc.

Khoá mô tả **một lần chạy** (`mediaId`, `mediaIds`, `status`, `error`, `renderedAt`…) bị loại khi
lưu: mẫu là cách bố trí, không phải kết quả — giữ lại thì mọi board dựng từ mẫu đều khoe ảnh của
board khác.

**RUN Lỗi** (`POST /api/boards/{id}/rerun-failed`) đặt `_run_only_node_ids` trên plan. Executor
vẫn **nạp** node ngoài tập đó (dây nối vào phải phân giải được `mediaId` của node trên), chỉ
**không dispatch**. Node `queued` tính là lỗi — một entry còn sót lại từ run chết không phân biệt
được với entry chưa bao giờ chạy, và thử lại là cách đọc vô hại. `ensure_board_plan` **xoá**
`_run_only_node_ids` để lần Run đầy đủ kế tiếp không lặng lẽ bỏ qua phần đã xong.

**Dừng toàn bộ** (`POST /api/boards/{id}/stop`) dừng cả ba tầng: task executor, hàng `PipelineRun`,
và mọi `Request` đang `queued` (trạng thái `"canceled"` — đúng một chữ L, đúng chính tả worker
kiểm lại). Request **đang chạy** thì không đụng: cuộc gọi đó đã trả tiền rồi, huỷ đi là mất tiền
mà không được gì.

**Hàng đợi** có `GET/POST /api/requests/queue{,/pause,/resume,/clear}`. Cờ tạm dừng kiểm **trước
khi pop** khỏi queue, cùng chỗ và cùng lý do với cầu dao 403: pop trước sẽ ăn mất một id rồi bỏ,
để lại row `queued` trong DB mà không có gì trong bộ nhớ trỏ tới.

`EdgeCreate` nay nhận `source_port`/`target_port`. Trước đó dây vẽ tay không mang tên cổng còn dây
nhập từ mẫu thì có — cùng hai node, cùng cách nối, mà hành vi khác nhau tuỳ board đến từ đâu.

### 6.2c Fan-out, cổng nhân vật, cổng nhánh, 7 dạng board

**Fan-out `prompt_list`** (`POST /api/boards/{id}/fan-out`) biến mỗi dòng của một node prompt thành
một node sinh riêng. **Là hành động riêng, không phải việc executor tự làm lúc chạy** — fan-out
trong lúc chạy sẽ khiến bảng chi phí báo 3 lần gọi rồi thực tế tiêu 20. **Idempotent**: mỗi bản sao
nhớ nó giữ dòng thứ mấy của node prompt nào, nên bấm lần hai là cập nhật/thêm/xoá chứ không nhân
đôi board. Runway chỉ chừa cho **cột mới**, nên bấm lại không xô board sang phải thêm lần nữa.
Trần `MAX_FANOUT = 50` vì mỗi dòng là một lần gọi có tính tiền.

**Cổng nhân vật** `character_1..N` (`services/character_ports.py`): thứ tự số đúng
(`character_10` **không** xếp giữa 1 và 2 — đó là thứ tự nhân vật trong prompt), trần theo lane
(3 với Veo, 10 với OMNI, lane lạ lấy số **nhỏ**), và 4 trường hợp từ chối trước khi dispatch.

> ⚠️ **Cổng này hiện mang ẢNH tham chiếu, không phải Character Entity.** Tool gốc đổ vào đây entity
> do hub `create_character` sinh ra; build này **không có hub** vì `flow_sdk` không có lệnh tạo
> character nào. Nên đường chạy là OMNI ingredients — năng lực thật, nhưng khác thứ SKILL.md mô tả.
> Việc từ chối "khung hình đầu + cổng nhân vật" **không** vì luật entity-vs-ảnh của Google (ca đó
> build này chưa chạm tới) mà vì **đường OMNI không có chỗ cho khung hình đầu**.

**Cổng nhánh** `image_prompts` / `video_prompts` / `voice_prompts`: một node prompt mang **ba câu
trả lời** (`imagePrompt`, `videoPrompt`, `voicePrompt`), cổng quyết định consumer lấy câu nào. Gộp
làm một output thì cả ba thành cùng một chuỗi — và giọng đọc sẽ đọc to lời chỉ đạo máy quay. Node
chỉ có một text vẫn trả lời mọi cổng, nên board cũ không vỡ.

**7 dạng board** (`services/archetypes.py`, `GET /api/prompt/archetypes`,
`POST /api/prompt/archetypes/classify`): phân loại theo đúng thứ tự của tool gốc — **user nói rõ >
tự suy từ dữ liệu > hỏi 2–3 lựa chọn**. Yêu cầu tường minh thắng nguyên vẹn, không đem cân với từ
khoá. Khi phải hỏi thì **luôn đưa ít nhất 2 lựa chọn**: hỏi mà chỉ có một đáp án thì đọc thành
khuyến nghị và được bấm đồng ý mà không suy nghĩ. Toàn bộ là khớp từ khoá, **không gọi model**.

### 6.2d 20 khoá settings của mẫu exe

Các khoá này **có trong file mẫu và trước đây không ai đọc**: import giữ nguyên giá trị trên node,
board trông như đã cấu hình, rồi lần chạy bỏ qua. Giá trị dưới đây đọc từ 11 file thật trên đĩa
chứ không đoán — `scale` đúng là viết `🔍 x1`, `thumbnail_position` đúng là chuỗi `end`.

| Khoá | Node | Nay làm gì |
| --- | --- | --- |
| `scale` | gen_image/gen_video, edit_video | Hệ số phóng to. `x1` **không** thêm pass nào (26 node mẫu đều x1; thêm pass là thêm một lần encode lại và mất chất lượng vô ích). Dùng đúng tên tham số `scale` mà `_op_upscale` đọc — đặt tên khác là bị bỏ qua và âm thầm phóng 4x |
| `enable_thumbnail`, `thumbnail_position`, `thumbnail_duration` | edit_video | Chèn ảnh bìa đầu/cuối clip. Ảnh tới qua **cổng `image_thumbnail`**, không phải setting path. Chèn **sau** phụ đề — chèn trước thì bìa dính caption của clip |
| `voice_speed` | edit_video | Đổi tốc độ giọng đọc bằng op `voice_speed` riêng (file audio không có luồng video, `change_speed` sẽ gãy). 1.0 không thêm pass |
| `auto_voice_speed` | edit_video | Khớp giọng vào độ dài clip — là **pass khác** chứ không phải một con số, vì tỉ lệ chỉ biết sau khi đo cả hai. Bật cái này thì bỏ `voice_speed` |
| `start_time` | edit_video, add_bgm | Bỏ qua bao nhiêu giây đầu của **nhạc nền**. Không áp cho giọng đọc — làm thế là cắt mất mấy chữ đầu |
| `engine` | create_voice | Chuẩn hoá tên engine TTS (`ChatGPT`→openai, `Clone Voice`→clone). Trả về **ý định**, không phải kết luận về khả dụng |
| `gemini_model`, `fallback_gemini_model` | analyze_video | Bóc id model ra khỏi nhãn `⚡ API — gemini-3.5-flash`. ⚠️ **Mới bóc, chưa đổi model thật được**: `LLMProvider.run()` không có tham số `model`, thêm vào là đổi hợp đồng P1 đã cố ý giữ ổn định |
| `path`, `image_path`, `import_excel_path` | upload_media, video_image_list, link_list | Trả về chuỗi đường dẫn, **không mở file** — mẫu là dữ liệu không tin được, người gọi mới quyết định đọc gì |
| `fashion_random_enabled`, `op_lung_enabled`, `selected_scene_id` | prompt_mau | Chuẩn hoá; `selected_scene_id` giữ dạng chuỗi vì `random` là một giá trị hợp lệ |
| `batch_idx`, `preview_height` | nhiều loại | **Chỉ lưu, cố ý không xử lý**: canvas này tự tính toạ độ, làm theo sẽ dời node người dùng đã đặt. Giữ để round-trip không làm phẳng bố cục |
| `sub_art_effect` | edit_video | Còn treo — 35 gradient preset thuộc P9 |

**Nợ P4 phát hiện khi làm P8:** cửa chặn phụ đề trong `postprod_plan` vẫn hỏi **mỗi khoá Gemini**
dù P4 đã thêm hai nguồn phiên âm. Máy có faster-whisper mà không có khoá Gemini thì phụ đề bị bỏ
qua dù hoàn toàn phiên âm được, offline và miễn phí. Nay hỏi `stt.sources()`.

### 6.2e Engine dự phòng & preset chữ nghệ thuật

**Upscale có hai binary trong cùng thư mục** `UpscaleEngine/`: `realesrgan-ncnn-vulkan.exe` và
`upscayl-bin.exe`. `available()` trước đây chỉ hỏi cái đầu, nên máy thiếu bản Vulkan báo "không
upscale được" trong khi engine chạy được nằm ngay bên cạnh. Nay hỏi cả hai, ưu tiên RealESRGAN, và
**ghi WARNING khi rơi sang Upscayl** — hai engine cho ra ảnh khác nhau thấy rõ, fallback im lặng sẽ
bị đổ lỗi cho model. Upscayl có thêm cờ `-z` (model-scale) mà RealESRGAN không có; **cố ý không
dùng**, để cả hai chạy model ở tỉ lệ gốc và mọi kích thước khác đi qua cùng một đường resample.

**Preset chữ nghệ thuật: 38 tên, 2 dải màu.** Tên đào được đầy đủ từ tuple đứng ngay trước
`TEXT_ART_PRESETS` trong binary. Nhờ đó app phân biệt hai câu khác hẳn nhau:

- *"Hiệu ứng X có trong tool gốc nhưng bản này chưa dựng lại được dải màu"* → giới hạn của bản này;
- *"Không có hiệu ứng chữ tên X"* → nhiều khả năng gõ sai trong workflow.

Màu thì **không đào được tin cậy** và đây là bằng chứng: màu nằm trong code object marshalled dưới
dạng hằng số intern tham chiếu theo chỉ số, nên thứ tự trong bảng chuỗi không phải cấu trúc. Thử
hai luật trên đúng 2 preset đã biết chắc — luật kề nhau lỏng đúng `gold luxury` nhưng sai
`sunset glow`; luật bám `setColorAt` đúng `sunset glow` nhưng không với tới cái nào khác. Một
phương pháp đúng chỗ này sai chỗ kia thì không dùng được cho 36 preset không ai biết đáp án, nên
chúng **rơi về màu phẳng và được gọi tên**, thay vì bị gán một dải màu gần giống.

### 6.2f Xoá watermark: ba cơ hội, không phải một

**Hai pass khác nhau, exe chạy cả hai** (đào binary 18/09):
`WorkflowRunner._execute_edit_video` gọi `gemini_video_watermark_remover.remove_gemini_video_watermark`
dưới khoá **`auto_remove_gemini_video_watermark`**, log *"Xóa logo Gemini: ưu tiên GPU, tự chuyển CPU
nếu GPU không khả dụng"* — đó là **máy dò**. Còn `enable_remove_veo_logo` + `veo_logo_method`
(`zoom` mặc định, `veo_logo_zoom_percent: 110`) là pass **hình học**: cắt biên cho mark ra ngoài
khung, hoặc làm mờ. Build này có cả hai và **không thay pass nào bằng pass kia** — `zoom` đổi bố
cục, đó là quyết định của workflow, không phải của module.

Chuỗi cho pass máy dò:

1. `GeminiWatermarkTool-Video.exe` — **tự dò** vị trí mark rồi inpaint, chạy GPU, nhanh nhất và
   tốt nhất. Luôn thử trước.
2. **MI-GAN trên CPU** (`services/inpaint.py`, extra `[inpaint]`, mặc định không cài) — chạy khi
   máy dò **từ chối** *hoặc* **không chạy được** (thiếu exe, không GPU — đúng câu "tự chuyển CPU"
   của exe), và có ô `veo_logo_*`. MI-GAN chỉ *lấp* chỗ được chỉ, nó không dò; không có ô thì
   không có gì để lấp.
3. Không cả hai → **clip đi tiếp kèm ghi chú**, nếu đây là một pass của `edit_video`
   (`optional: True`): một pass watermark chết không được kéo theo đổi khung, nhạc nền và phụ đề
   của cùng node đó. Node `remove_watermark` đứng riêng thì **báo lỗi** — đó là toàn bộ việc của nó.

> Cơ hội thứ ba tồn tại vì **"không dò thấy" khác "sạch"**: đã đo 02/09, `gwt-mini` báo
> *No watermark detected (3%)* trên đúng frame có logo Veo.

**Đường tới người dùng** (trước 18/09 chỉ có HTTP): ô `veo_logo_*` chỉ tồn tại trong file workflow
nhập vào — 1/9 mẫu có, và **tắt công tắc**. Nay card `remove_watermark` trên canvas có ô chọn
**Vùng dự phòng** (4 góc, ô 20%×10% đúng như mẫu đóng gói) → ghi `veo_logo_*` vào `sourceSettings`.
Máy dò vẫn chạy trước và vẫn được tự do tìm ở đâu cũng được; góc chỉ là chỗ MI-GAN lấp khi nó
không tìm được.

**Sáu dòng dưới đây đều đo trên model, không đoán** — và bốn trong số đó quyết định thiết kế:

| Đo được | Hệ quả |
| --- | --- |
| IO: `image` uint8 `[N,3,H,W]` + `mask` uint8 `[N,1,H,W]` → `result` | hợp đồng cố định |
| **`mask == 0` là chỗ cần xoá**, `255` là giữ | sai chiều thì xoá sạch mọi thứ *trừ* watermark |
| **Ngoài mask KHÔNG được giữ nguyên** — lệch tới 24 mức trên cả khung 720×1280 | nên **cắt tile 512 quanh mark và chỉ dán lại cái lỗ** + vành `FEATHER` 6 px; ngoài vành giống hệt từng bit |
| **~700 ms/frame bất kể kích thước** (đo ở 128², 256², 512², 720×1280) | cắt tile không nhanh hơn, nhưng **nét hơn** vì mark không bị nén xuống 512 cùng cả khung; trần **1200 lượt vẽ** (không phải frame — ô lưới nhân lên), quá thì báo số phút |
| **Model để nguyên ~2 px viền của chính cái lỗ** — 224/255 màu mark còn lại ở mép ô, còn 9 khi nới lỗ 3–4 px (giống nhau ở 4/6/8) | `GROW = 4`: lỗ = ô đã nới; và **vành mờ nằm NGOÀI lỗ** — vành nằm trong lỗ (bản đầu) chính là thứ giữ lại viền mark |
| **Ô rộng hơn tile thì phần ngoài tile không được vẽ** — 254/255 còn lại, mà hàm báo xong | ô > `MAX_HOLE` (256) → **lưới ô con**, vẽ lần lượt *vào kết quả của nhau*; mỗi lượt **mask cả vết** trong tile (không chỉ ô con) vì phần vết còn lại là "ngữ cảnh" mà model sẽ copy y nguyên |

Khung hình đi qua ống ffmpeg dạng `rgb24` thô, không qua PNG/đĩa — nhanh hơn và giữ được nguyên
tắc "app này không có dep ảnh nào" (`routes/upload` vẫn tự đọc header PNG/JPEG để khỏi dựng Pillow).

### 6.2g Motion Control & đọc dài theo đoạn

**`motion_control` không cần API mới.** Điều tra binary (05/09) cho thấy nó gọi đúng endpoint
reference-to-video mà build này đã có (`VIDEO_OMNI_URL`), với model họ `veo_3_1_r2v_*`. Dòng log của
chính exe là `[Thành Phần] Endpoint: … | Images= | Entities=` — payload chỉ có ảnh và entity.

> **Video dẫn động KHÔNG bao giờ lên Flow.** Chỗ duy nhất đụng tới frame của nó là thumbnail
> `scale=410:600` cho thẻ UI. Vai trò thật: người dùng xem nó để viết chuyển động vào prompt, một
> khung làm preview, và (tuỳ chọn) nguồn audio cho bản ghép. Đây là node **sinh video**, không phải
> node hậu kỳ — `_GENERATION_NODE_TYPES` trong executor.

Cổng: `motion_input_video` (chỉ local) · `image_1` nhân vật · `image_2` sản phẩm · `image_3`
background. **Thứ tự slot là thứ tự vào `Images=`** — đổi chỗ người mẫu với sản phẩm là ra video
khác. Bốn trường hợp **từ chối trước khi tiêu tiền**: không có ảnh nào; bật thay trang phục mà
thiếu ảnh sản phẩm (chạy sẽ ra đúng người mẫu cũ mà vẫn tính tiền); bật thêm background mà thiếu
ảnh; chưa chọn thời lượng (r2v tính tiền theo thời lượng nên không tự chọn hộ).

**Họ model r2v của Veo** — 4 key, đào hết từ binary chứ không lấy mẫu:

| Tier | Lane | Key |
| --- | --- | --- |
| Pro | fast | `veo_3_1_r2v_fast_portrait` |
| Ultra | fast | `veo_3_1_r2v_fast_portrait_ultra` |
| Ultra | **fast_relaxed (0 credit)** | `veo_3_1_r2v_fast_portrait_ultra_relaxed` |
| Ultra | **lite_relaxed (0 credit)** | `veo_3_1_r2v_lite_low_priority` |

Ba ràng buộc đi kèm, và cả ba đều **từ chối** thay vì thay thế: không có lane `lite`/`quality` trả
phí (thay bằng `fast` là tiêu nhiều tiền hơn board yêu cầu); **mọi key đào được đều dọc**, nên bản này
không có key ngang nào để gửi (dùng key dọc cho board ngang là trả tiền lấy clip sai khung — báo lỗi
và chỉ sang OMNI);
Pro không có lane 0-credit.

**Đọc dài theo đoạn** (`services/narration.py`). `tts.synthesize` gửi cả bài trong một request, nên
một truyện 10 phút chỉ cách việc mất trắng đúng một lần timeout. Nay bài dài hơn
`MAX_SEGMENT_CHARS` được cắt **theo ranh giới câu** (cắt giữa câu làm máy đọc hạ giọng và lấy hơi
giữa chừng — nghe ra ngay), đọc từng đoạn, **retry riêng từng đoạn**, và ghi manifest
`*.segments.json` để đọc lại **một** đoạn hỏng mà không phải trả tiền cho 40 đoạn còn lại.
Một đoạn chết không làm hỏng cả bài; nhưng **ghép quanh chỗ thủng thì bị từ chối** — bản thiếu một
câu nghe vẫn như đủ, người nghe không phát hiện được và board cũng không.

> **Cố ý KHÔNG dựng lại:** worker gốc (`_clone_segment_with_retry`) lái một dịch vụ clone giọng bên
> thứ ba qua `curl_cffi` (giả lập trình duyệt) — cùng loại với extension cào audio đang chờ quyết
> định. Bộ cắt đoạn thì không phụ thuộc engine, nên nó chạy trên giọng Gemini mà app có khoá hợp lệ.

### 6.3 Engine ảnh thứ hai: OpenAI bên cạnh Flow

Flow vẽ mọi khung hình là một điểm hỏng duy nhất — Flow sập, hết quota, hoặc từ chối prompt là công
việc ảnh dừng hẳn. `services/openai_images` thêm một nguồn bytes thứ hai; bytes đó vẫn đi qua
`services/image_ingest` để lên Flow lấy `media_id`, nên phía sau không có gì đổi: khung hình đầu vẫn
là khung hình đầu.

**Cả hai đường mặc định TẮT.** Một đường tính tiền thật mỗi ảnh, đường kia đốt quota ChatGPT mà user
đang dùng để làm việc — có sẵn credential trên máy không phải là sự đồng ý.

| Đường | Endpoint | Trả bằng gì | Trạng thái |
| --- | --- | --- | --- |
| API key (**chính**) | `POST api.openai.com/v1/images/generations` | tiền theo từng ảnh | có tài liệu, ổn định |
| Relay ChatGPT | `POST chatgpt.com/backend-api/codex/responses` (tool `image_generation`) | quota gói ChatGPT, ~3–5× một lượt text | **không chính thức**, backend nội bộ, có thể hỏng sau bản cập nhật Codex |

API key chạy trước dù relay không tốn thêm tiền: relay tiêu chính cái quota user đang dùng để code,
và đó là chi phí họ không thấy trước. `OPENAI_IMAGE_PREFER_RELAY` đảo thứ tự cho ai muốn ngược lại.

Ba quyết định đáng nhớ vì chúng chống lại trực giác "cứ giúp cho xong":

1. **Model mặc định `gpt-image-2`**, không phải `gpt-image-1-mini` rẻ hơn — OpenAI khai tử cả họ
   gpt-image-1 ngày **01/12/2026**. Bù chi phí bằng `quality` mặc định `low` (≈ ngang giá mini).
2. **Node chọn engine `openai` mà đường đó đang tắt → node LỖI, không âm thầm quay về Flow.** Quay
   về Flow là tiêu credit Flow cho việc user không hề yêu cầu.
3. **Node có ảnh tham chiếu nối vào + engine `openai` → LỖI.** Endpoint generations chỉ vẽ từ chữ;
   giữ engine mà bỏ ảnh tham chiếu sẽ ra đúng một bức ảnh sai người, và vẫn bị tính tiền.

Token Codex đọc từ `$CODEX_HOME/auth.json`, chỉ đi vào header Authorization — không vào log, không
vào thông báo lỗi, không vào `repr`. **Không có luồng refresh OAuth**: refresh với một endpoint không
tài liệu là đoán mò, nên token hết hạn thì báo `codex login` rồi rơi về đường API key.

`/api/boards/{id}/estimate` đếm ảnh OpenAI ở `openaiImageJobs`, **tách khỏi `billableJobs`** — hai
tài khoản khác nhau, gộp lại thì không khớp cái nào.

## 6.2 AI providers — vì sao dán API key, không đăng nhập CLI

Ba tính năng dùng LLM: `auto_prompt` (viết prompt cho canvas và Idea→Video),
`vision` (mô tả ảnh, phân tích video), `planner`.

| Provider | Cách xác thực | Ghi chú |
| --- | --- | --- |
| **Gemini** | **API key** (ưu tiên) hoặc CLI | Key thắng CLI khi có cả hai — xem dưới |
| OpenAI | Codex CLI hoặc API key | Đã có sẵn hai chế độ |
| Claude | Chỉ CLI | Không nhận key trong bản này |

**Vì sao key thắng CLI ở Gemini — đo được, không phải sở thích:** trên máy này
`gemini --version` thoát 0 (binary có cài) nhưng **mọi lệnh gọi thật đều chết**:

```text
IneligibleTierError: This client is no longer supported for Gemini Code Assist
for individuals … migrate to the Antigravity suite
```

Google đã khai tử đường đăng nhập đó cho tài khoản cá nhân. Nghĩa là **probe
`--version` không nói lên được dispatch có chạy hay không**, nên thứ tự phải là
key trước, CLI sau. Bản trước đó `routes/llm.py` còn **từ chối thẳng** key
Gemini ("uses CLI auth instead"), khoá luôn người dùng ra ngoài trong khi key
tốt nằm sẵn trên đĩa.

### Nguồn key (`services/gemini_keys.py`)

Thứ tự: env `GEMINI_API_KEY`/`GOOGLE_API_KEY` → `~/.flowboard/secrets.json`
(dán ở Settings, quyền 600) → `data_general/gemini_api_key.txt` (**đọc hết mọi
dòng**, không chỉ dòng đầu như trước).

Xoay vòng round-robin; key nào trả 429 thì **nghỉ 90 giây** rồi quay lại. Phân
biệt rõ: **429 = key hết quota** (cho key nghỉ) còn **503 = model quá tải**
(không phạt key — lỗi bên Google, phạt key chỉ làm pool nhỏ đi).

Key không bao giờ được ghi log, trả về response, hay echo ra UI.

### Chọn model — cũng là số đo

| Model | Thời gian | Kết quả |
| --- | --- | --- |
| `gemini-flash-latest` | 113s | **503 quá tải** |
| `gemini-3.6-flash` | 12.4s | 200 |
| **`gemini-flash-lite-latest`** | **1.3s** | 200 |

Chọn `gemini-flash-lite-latest`. Chất lượng viết prompt tương đương mà nhanh
gấp 10; model này bị gọi một lần mỗi node trên canvas nên 12 giây đã là "trông
như hỏng". Ghi đè bằng `FLOWBOARD_GEMINI_MODEL` nếu planner cần model nặng hơn.

Hai cái bẫy đã thử và loại: `gemini-2.5-flash` trả **404 "no longer available
to new users"** (nên không hardcode phiên bản), và
`generationConfig.thinkingConfig.thinkingBudget = 0` trả **400** (không mua lại
được tốc độ bằng cách tắt thinking).

**Test không bao giờ được đụng key thật.** `ASSET_ROOT` cố ý trỏ vào thư viện
đóng gói (test postprod chạy ffmpeg thật), nên conftest đặt
`FLOWBOARD_GEMINI_KEY_FILE=""` để tắt nguồn file — nếu không, mỗi lần chạy test
sẽ tiêu quota thật và gọi API thật (đã đo: bộ test từ 68 giây vọt lên quá 600).

## 7. Postprod & Upscale (chạy offline)

### 7.0 Ba endpoint làm cho tính năng này với tới được

Bộ endpoint postprod vốn **không dùng được từ trình duyệt**: chúng nhận đường dẫn file bị giới hạn
trong thư mục server (trang web không ghi vào đó được) và trả về đường dẫn tuyệt đối (trình duyệt
không mở được). Ba endpoint sau lấp đúng khoảng đó:

| Endpoint | Vì sao cần |
| --- | --- |
| `GET /api/postprod/library` | Liệt kê file **được phép** làm đầu vào (`sources` = media đã tạo, `renders` = kết quả cũ) kèm đường dẫn thật. UI chọn từ đây thay vì bịa đường dẫn. |
| `GET /renders/<name>` | Phát/tải kết quả. Giới hạn trong thư mục renders bằng đúng luật resolve-rồi-kiểm-chứa của đường ghi; chỉ trả về phần mở rộng media đã biết. |
| `POST /api/postprod/srt` | Lưu phụ đề dán tay thành `.srt` trong thư mục renders. Ghi kèm **BOM UTF-8** — thiếu BOM thì ffmpeg đọc theo codepage hệ thống trên Windows và khắc vĩnh viễn chữ vỡ dấu vào video. |

`renders` vừa là đầu ra vừa là đầu vào hợp lệ, nên chuỗi **ghép → phụ đề → logo → nhạc nền** chạy
liền mạch. Đã kiểm thật: ghép 2 clip 8s → 16.02s, rồi khắc phụ đề lên chính kết quả đó.

**Mọi endpoint postprod đều đồng bộ** — request giữ kết nối suốt quá trình encode, không có job id để
poll và không có thanh tiến trình. UI phải nói rõ điều này thay vì trông như treo.


`services/postprod.py` (FFmpeg): ghép clip (chuẩn hoá codec/aspect/fps trước khi concat) · khắc phụ đề
SRT (font tiếng Việt từ thư viện asset) · chèn logo/watermark · trộn nhạc nền · lồng tiếng TTS · co
thời lượng clip khớp lời đọc.

`services/upscale.py`: **RealESRGAN x4 cục bộ, hoàn toàn offline, không tốn credit** — đúng như exe
(`UpscaleEngine/`), thay cho đường upscale trả phí của Flow.

Những chỗ đã trả giá để học, đừng phá:

- Video upscale nổ từng frame ra PNG → **giữ nguyên fps và mux lại audio gốc**. Bản làm rơi tiếng còn
  tệ hơn không upscale, vì sản phẩm chính là clip có lời đọc.
- Model x4 là cố định. Ép `-s 2` vẫn **exit 0** và cho ra ảnh đúng kích thước nhưng pixel hỏng (đo
  được: 4.5 dB PSNR so với 26 dB ở `-s 4`). Vì vậy tỉ lệ lấy theo **tên model**, không theo usage text;
  muốn kích thước tuỳ ý thì dùng `target_height` để resample sau.
- Đường dẫn có ký tự **non-ASCII** làm binary chết bằng `STATUS_STACK_BUFFER_OVERRUN` trước khi ghi gì
  → phải staging qua thư mục ASCII.
- Exit code 0 **không** đồng nghĩa thành công; mọi caller tự kiểm lại output của mình.
- Chặn nguồn quá dài (`MAX_UPSCALE_DURATION_S = 300`): 10 phút 1080p ≈ 18.000 file PNG, hơn 100 GB.

Đầu vào postprod đi qua `assets._library_file()` (resolve + kiểm chứa) để một tên file tuyệt đối không
thể đọc ra ngoài thư viện.

## 8. Đối chiếu: lấy từ exe vs mới

### A. Parity — cơ chế lấy từ exe (không phát minh lại)

| Cơ chế | Bằng chứng exe |
| --- | --- |
| Flow qua `aisandbox-pa` + Bearer | manifest & background.js của extension gốc |
| Ops dispatch: text / start-image / start-and-end / reference-images | chuỗi `video:batchAsyncGenerateVideo*` trích từ binary |
| Poll `batchCheckAsyncVideoGenerationStatus` | chuỗi trong mọi `API_*.py` của exe |
| Lấy URL qua `trpc/media.getMediaUrlRedirect` | chuỗi mined cạnh `build_media_download_url`, `fifeUrl`, `servingBaseUri` |
| Nhịp poll 7s, trần 10 phút | `WAIT_GEN_VIDEO: 7`; chuỗi "timeout sau 10 phút polling" |
| Retry mặc định 3 | `RETRY_WITH_ERROR: 3` |
| Song song 4 | `MULTI_VIDEO: 4` |
| Upscale RealESRGAN cục bộ | `UpscaleEngine/` + model NCNN |
| Bố cục output theo dự án | `VIDEO_OUTPUT_DIR`, `CURRENT_PROJECT`, `PROJECTS` |
| Bộ postprod offline | FFmpeg/FFprobe, `CapCut_Fonts/`, `nhac_nen/`, `SUB_*`, `LOGO_*` |
| Captcha qua tab Flow | `RUN_CAPTCHA: "v2"` |
| Cú pháp `{Tên nhân vật}` | user-guide mục "Đồng bộ nhân vật" |

### A.1 Cầu phiên đăng nhập — một cơ chế, ba đích

Exe phải dùng **patchright + CDP lái một Chrome riêng** để gọi API nội bộ của
grok.com và shopee.vn, vì nó là app desktop không có extension. Bản này **đã có**
extension fetch kèm cookie cho `labs.google`, nên chỉ cần nới đúng cầu đó:

| Kênh | Đích | Bearer Google |
| --- | --- | --- |
| `labs.google/fx/api/trpc/` | Flow metadata + lấy URL media | **có** |
| `grok.com/rest/` | Grok tạo ảnh/video | **không** |
| `shopee.vn/api/v4/` | Đọc sản phẩm | **không** |

**Allowlist theo đường dẫn, không theo domain** — `grok.com/` sẽ với tới cả trang
cài đặt tài khoản. Và token Google **chỉ** gắn cho kênh Flow: nới host mà giữ
nguyên header là trao credential của cả tool cho bên thứ ba.
(`SESSION_CHANNELS` trong `background.js` ↔ `_ALLOWED_TRPC_PREFIXES` trong `processor.py`.)

Grok cần thêm một bước nữa: request của nó mang header phiên mà **chỉ trang đang
chạy mới có**, và trả về **luồng JSON nối liền** chứ không phải một document.
Fetch từ service worker không có cả hai. Nên nó chạy trong page context qua
`content-grok.js` + `injected-grok.js` — đúng chỗ exe dùng CDP. Hệ quả: **phải có
tab grok.com đang mở và đã đăng nhập**.

### B. Mới — flowkit/flowboard, exe KHÔNG có

| Tính năng | Vì sao có |
| --- | --- |
| Canvas vô hạn, node/edge/board | Mô hình làm việc mới; exe là app PyQt dạng tab |
| LLM registry phía server (Gemini/OpenAI) | Exe điều khiển Chrome Gemini/Grok qua browser profile |
| Circuit breaker 403 + probe nửa mở | Exe chỉ có cờ `FIX_403_RECAPTCHA` và retry đếm |
| Job store một poller + rehydrate khi F5 | Hệ quả của kiến trúc web |
| Allowlist proxy hai đường | Phòng thủ nhiều lớp |
| Settings whitelist loại trừ credential | Exe cất token thẳng trong config.json |
| Kiểm chứa đường dẫn asset postprod | Chặn đọc file ngoài thư viện |
| Bề mặt `model_substitutions` | Minh bạch: người dùng phải biết mình vừa bị tính tiền |
| Route `vision`, `chat`, `prompt` synthesis | Mới |
| Frontend React + MediaPicker + ErrorBoundary | Thay UI PyQt |

### C. Exe có — bản này CHƯA (khoảng trống đã biết)

- **Grok chạy thật** — cầu nối đã chứng minh (403 từ chính grok.com, không phải lỗi extension), còn
  lại là header phiên; user đã gác lại.
- **Canvas nạp workflow mẫu** — cố ý KHÔNG làm, xem C.2.

### C.1 Đã lấp (01/09)

- **Đồng bộ nhân vật**: dựng trên `gen_video_omni` (đường reference-images). Ràng buộc của exe được
  ép ở UI: ≤10 nhân vật, ≤3 mỗi prompt, gọi bằng `{Tên}`; chỉ những nhân vật một dòng thật sự gọi
  mới được gửi kèm dòng đó.
- **Ý tưởng → Video** (`POST /api/prompt/idea`): ý tưởng → N cảnh nối tiếp, nhân vật mô tả nhất quán
  xuyên suốt vì model không có trí nhớ giữa các clip. Prompt trả về cho user sửa **trước khi** gửi —
  mỗi cảnh là một lần trừ credit.
- **Phân tích video** (`POST /api/vision/video`): ffmpeg lấy mẫu khung hình rồi báo cho model biết
  đó là *một chuỗi* — thiếu câu đó thì nó tả từng ảnh rời và không bao giờ nhắc tới chuyển động.
- **Phụ đề tự động** (`POST /api/postprod/transcribe`): tách audio 16 kHz mono → Gemini → SRT (kèm
  BOM). Cố ý gắn thẳng Gemini chứ không qua `run_llm`: nó gửi audio mà provider khác trong bản này
  không nhận.
- **Xóa logo** (`POST /api/postprod/delogo`): filter `delogo` của ffmpeg — *che* được vết, **không**
  khôi phục ảnh gốc phía sau. UI nói rõ điều này.
- **Video Clone** (`POST /api/postprod/clone`): yt-dlp, tải vào thư mục renders nên dùng lại được
  ngay ở Cắt&Ghép/Upscale. Chặn SSRF như đường upload ảnh.
- **Affiliate** (`POST /api/affiliate/product`): đọc thẻ **Open Graph** thay vì API Shopee (exe cần
  credential đối tác, dễ vỡ). Giới hạn thật: sàn dựng bằng JS như Shopee thường **không** công bố
  thẻ đọc được — UI có sẵn đường chọn ảnh thủ công cho trường hợp đó.
- **GROK** (`/api/grok/*`): **cả ảnh lẫn video**, qua phiên grok.com của user — KHÔNG phải `api.x.ai`
  và không cần API key. Bốn chế độ đúng như exe (`MODE_GROK_{CREATE_IMAGE,IMAGE_TO_IMAGE,
  TEXT_TO_VIDEO,IMAGE_TO_VIDEO}`). Endpoint + thân request đào từ binary; **hình dạng phản hồi thì
  không** — nên `_pick_media` quét mọi chuỗi giống URL thay vì bám một đường cứng, và có nút "Dò kết
  nối" để lấy hình dạng thật. **Chưa chạy với tài khoản Grok thật.**
- **Affiliate = thời trang VTON** (`AffiliateVTONWorkflow` của exe): người mẫu + trang phục → ảnh thử
  đồ → **video** (không phải ảnh quảng cáo như bản trước tôi làm sai). Shopee đọc qua
  `/api/v4/item/get` trên phiên đăng nhập; Open Graph giữ làm đường lùi cho sàn khác.
- **Cắt video theo độ dài** (`/api/postprod/cut`): re-encode chứ không stream-copy — copy chỉ cắt được
  ở keyframe nên mảnh lệch khỏi độ dài yêu cầu. Đo thật: nguồn 8s, cắt 3s → 3.02 / 3.00 / 2.00 s.
- **Excel batch** (`/api/batch/{parse,export}`): khớp cột **theo tên, không theo vị trí**, hiểu tiêu đề
  tiếng Việt; "9:16"/"ngang" → token aspect; bỏ dòng trống (mỗi dòng là một lần trừ credit).
- Tab **Cài đặt · Nhật ký · Cắt & Ghép · Upscale ảnh/video · Hướng dẫn · ỦNG HỘ**.
- **`edit_video` đã đủ**: đào lại cho thấy trong exe nó là **bước lắp ráp bằng ffmpeg**
  (`_build_atempo_filter`, `edit_video_encoder_candidates`, `_edit_video_thumbnail_command`), không
  phải lệnh Flow. Nay có nốt **chèn tiêu đề** (`/title`) và **ảnh bìa** (`/thumbnail`).
- **Cắt frame cuối** (`/api/postprod/last-frame`): khung cuối clip N làm khung đầu clip N+1 — cách exe
  nối cảnh (template dùng 4 lần). Giải mã tiến tới khung cuối thật thay vì seek gần cuối, vì seek bám
  keyframe nên trượt. Có `project_id` thì đẩy luôn lên Flow để dùng ngay ở Start-End.
- **Lưu vào thư viện**: `MediaPicker` vốn **đọc** được `Reference` nhưng không đâu **ghi** vào, nên thư
  viện luôn rỗng. Nút lưu đặt ở `JobList` để mọi tab cùng có, thay vì mỗi tab một nút.

### C.2 Nạp 9 workflow mẫu vào canvas — `POST /api/templates/{file}/import`

**Mục này từng ghi ngược lại.** Bản trước lập luận rằng không nạp được vì template dùng 17 loại node
còn canvas chỉ có 7, "đa số node không biểu diễn được". Lập luận đó sai, và lý do thật khiến nó được
viết ra là khối lượng việc, không phải giới hạn kỹ thuật:

| Khảo sát lại | Kết quả |
| --- | --- |
| 4 loại prompt (`text_prompt`, `prompt_list`, `prompt_mau`, `gemini_prompt`) | đều là **một** node `prompt` ở đây |
| 3 loại đầu vào (`upload_media`, `link_list`, `video_image_list`) | đều là `visual_asset` |
| 7 loại hậu kỳ | **đã thêm** thành 7 node type riêng |
| `sync_image_voice` | **đã dựng** — Ken Burns bằng ffmpeg local (xem C.5) |

Khảo sát cũ còn ghi `sync_image_voice` và `video_image_list` xuất hiện **0 lần**; đếm lại trên file
thật thì mỗi loại **1 lần**. Con số cũ sai.

**Kết quả đo được (chạy thật, cả 9 file):** 107/107 node, 129/129 cạnh, 0 cạnh mồ côi, và
**17/17 loại node** đều có node tương ứng trên canvas — không còn loại nào phải nạp thành ghi chú.

Cơ chế:

- `services/template_import.py` dựng Board + Node + Edge, **giữ nguyên toạ độ gốc** (các board này
  được sắp bằng tay, đọc từ trái sang phải; tự layout lại là phá thứ làm chúng đọc được).
- `Edge` có thêm `source_port`/`target_port` — template dùng cổng đặt tên (`start_frame`, `image_1`,
  `image_2`, `video`, `voice`…) và không có tên cổng thì executor chỉ còn cách đoán theo thứ tự đến,
  đúng kiểu ghép-theo-vị-trí mà cả bản này đã bỏ. Migration `_ensure_columns`, cột nullable, dữ liệu
  cũ không đụng tới.
- Prompt trong các workflow này **đi qua dây** chứ không nằm trên node sinh (29/129 dây là
  `text → prompt`). Executor trước chỉ đọc `node.data["prompt"]`, nên board vừa nạp **dispatch 0 job
  mà vẫn báo `done`**. `_prompt_for` giờ đọc node trước, rồi tới node `prompt` phía trên. Đo lại:
  **12/24** node sinh chạy được ngay; 12 node còn lại nằm trong các template cố ý để trống prompt.
- Mỗi lần nạp tạo thêm một `Plan` trỏ vào đúng các node vừa dựng
  (`spec._materialized_node_ids`), nên board chạy được bằng **`run_pipeline` sẵn có** —
  `materialize_plan` nhận ra đã materialise rồi và trả lại nguyên các node đó (có test khẳng định nó
  không dựng bản sao thứ hai).
- Nạp lại tạo board mới, **không ghi đè** board cũ: đây là điểm khởi đầu để sửa, không phải tài liệu.

Phần công thức đọc được (`GET /api/templates/{file}`) vẫn giữ — nó hữu ích khi muốn làm tay theo các
tab.

### C.3 Node hậu kỳ trên canvas — một request type `postprod`

7 node type mới (`analyze_video`, `merge_video`, `edit_video`, `extract_last_frame`, `add_bgm`,
`create_voice`, `align_video_voice`) **không** đẻ ra 7 request type. Tất cả đi qua **một** type
`postprod` mang `{op, ...}`, gọi đúng các hàm `services/postprod.py` mà route HTTP đã gọi — nên hành
vi không lệch giữa tab thủ công và đồ thị.

`services/postprod_plan.py` quyết định node nào chạy op nào — **đọc theo CỔNG của dây, không đoán
theo loại node upstream**. Điểm này không hàn lâm: trong workflow gốc, `edit_video` có `create_voice`
nối vào cổng `voice` — đó là **giọng đọc**. Bản đầu tôi viết lấy "upstream đầu tiên sinh ra audio" và
trộn giọng đọc vào làm **nhạc nền** ở âm lượng 0.9. Dây đã ghi rõ vai trò; chỉ là không ai đọc.
`Edge.source_port`/`target_port` sinh ra để làm việc đó — trước khi sửa, hai cột này chỉ được **ghi
vào mà không ai đọc**.

Bảng cổng thật, đo từ chín file (không suy đoán):

| Node | Cổng vào |
| --- | --- |
| `merge_video` | `media` ← gen_video |
| `add_bgm` | `media` ← merge_video — **không có dây audio nào**, nhạc lấy từ settings |
| `align_video_voice` | `media` ← video · `voice` ← create_voice · `text` ← prompt |
| `edit_video` | `media` ← video · `voice` ← create_voice · `title` ← prompt · `image_thumbnail` ← image |
| `extract_last_frame` | `video` ← gen_video |
| `create_voice` | `text` ← prompt |

Cạnh vẽ tay trên canvas không có tên cổng (`None`) — nó được coi là **socket mặc định** để board tự
dựng vẫn chạy được, nhưng vai trò đặc biệt như `voice` thì **bắt buộc phải gọi tên**: đoán chính là
lỗi ban đầu.

Hai điểm đáng nói khác:

- `edit_video` **không phải một op** — trong exe nó là hộp năm chục setting chạy nhiều lượt. Ở đây nó
  trả về một **chuỗi** op, mỗi lượt ăn output của lượt trước (`PREVIOUS`). Chỉ lượt đầu đọc clip gốc;
  nếu mọi lượt đều đọc clip gốc thì chúng ghi đè nhau và chỉ lượt cuối sống sót.
- `analyze_video` **không** bị giả làm op ffmpeg. Nó đọc video bằng Gemini qua `/api/vision/video` —
  trả về một op cục bộ ở đây sẽ khiến node báo thành công trong khi đã làm việc khác hẳn.

Node thiếu đầu vào thì **không chạy và không lỗi** — giống hệt node sinh ảnh không có prompt. Nhờ vậy
board nối dở vẫn chạy được phần đã sẵn sàng.

### C.4 Xem chi phí rồi mới chạy — `GET /api/boards/{id}/estimate`

Cam kết của endpoint này hẹp một cách có chủ ý, vì bằng chứng chỉ tới đó:

- **Số lần gọi là chính xác** — suy ra từ đồ thị (mỗi node, nhân số variant). Đây mới là con số bảo vệ
  người dùng.
- **Chỉ báo giá ở nơi có bảng giá.** OMNI tính theo độ dài và SDK có bảng đó. Veo và lane ảnh **không
  có bảng giá công bố** ở bản này — và exe cũng không: dò chuỗi trong `RUN_VEO_3_ULTRA_PROMAX.exe` chỉ
  thấy nó **đọc số dư** từ `/v1/credits`, chưa bao giờ ước tính. Bịa số ở đây sẽ in một tổng tự tin lên
  màn hình mà tài khoản không khớp. `credits: null` nghĩa là **chưa biết**, không phải miễn phí.
- **Số dư là thật**, lấy từ `flow_client.credits`, nên "34 lần tính tiền / đang có 1040 credit" dùng
  được ngay mà không cần đơn giá.
- Bước hậu kỳ đếm riêng và bằng 0: ffmpeg chạy trên máy này.

Loại node chưa phân loại được tính là **có thể tốn tiền** — sai theo hướng bắt xác nhận thừa, không
sai theo hướng tính thiếu.

Con số phải là con số **thật sự xảy ra**, nên endpoint đếm đúng tập mà `run_pipeline` dispatch:

- `character` và `Storyboard` bị executor bỏ qua trong lần chạy board → **không đếm** (chạy riêng
  từng node là hành động khác, có xác nhận riêng).
- Node sinh **chưa có prompt** cũng bị bỏ qua → đếm vào `notReadyJobs`, liệt kê riêng kèm lý do.
  Nhiều template gốc **cố ý** để trống ô prompt cho người dùng điền: `Người que NEW` báo 0 lần tính
  tiền + 3 bước thiếu prompt, còn `Thời Trang Nam 3 cảnh` báo 4 lần tính tiền.
- `edit_video` là chuỗi nhiều lượt nên đếm **từng lượt**, dùng chung `ops_for(..., assume_ready=True)`
  chứ không viết hàm đếm thứ hai.

### D. Cố ý KHÔNG làm (non-goals)

- **License gate** của tác giả (Google Apps Script, `license_state.*`, "ERROR 399") — bản này chạy
  bằng tài khoản Google của chính người dùng, không cần và không nên có cổng license.
- Telegram, Grok, Shopee, NordVPN, tải video mạng xã hội, voice-clone bên thứ ba — ngoài phạm vi dùng
  cá nhân.
- Tự động cập nhật từ GitHub của tác giả.
- **Telemetry giả (`batchLog`) — TỪ CHỐI, không phải chưa làm.** Exe cứ 45–120 s lại bịa một sự kiện
  `FLOW_IMAGE_LATENCY` / `GRID_SCROLL` rồi gửi lên Google để phiên trông giống người thật đang dùng
  UI. Đó là đánh lừa hệ thống chống lạm dụng của nhà cung cấp, không phải tính năng — và nếu bị nhận
  ra thì cái mất là **tài khoản Google của người dùng**, không phải của tác giả tool. Bản này gửi
  đúng những request nó thật sự cần.
- **Cầu cào audio TTS (`AudioTTS_Extension`, 29 file) — KHÔNG dựng; đường hợp lệ ĐÃ dựng thay.**
  Extension của exe cào audio từ elevenlabs.io / heygen.com / azure / chatgpt.com bằng phiên đăng
  nhập của người dùng. Thay bằng **OpenAI TTS chính thức** `/v1/audio/speech` (có tài liệu, có hoá
  đơn, mặc định tắt) + bảng map 67 tên giọng của exe (`GPT_VOICE` 9, CapCut 22 Việt + 36 Anh) sang
  giọng Gemini gần nhất theo **cao độ đo được** — mục F dưới. Cùng lý do, `voice/VOICE_CAPCUT` (gọi
  API nội bộ CapCut/ByteDance) và **nhân bản giọng** (`Clone Voice`, lái dịch vụ bên thứ ba bằng
  HTTP client giả trình duyệt) cũng không dựng: tên đó nhận được một giọng thay thế kèm câu nói rõ
  thiếu tính năng nào, chứ không im lặng.

### E. Kho tri thức: cái gì tới được prompt, và bằng đường nào

`ASSET_ROOT` có 36 file kỹ năng (413 KiB) + 5 `SKILL.md`. Cách chia:

| Đường | Ai chọn | Nội dung |
| --- | --- | --- |
| `knowledge.TASKS` | không ai — luôn nạp theo tác vụ | `storyboard` (12 nguồn), `write_prompt` (8), `review_clip` (4), `canvas` (4), `prompt_rules` (2) |
| `script_genres.GENRES` | người dùng chọn thể loại | 8 công thức kịch bản Việt + 4 file drama trên đĩa |
| `script_genres.SUPPLEMENTS` | 3 ô chọn trên tab Ý tưởng | hook 3 s · phim dài 10–30′ · 3 hồi 8 sequence |
| `knowledge.FORMATS` | ô "Dạng video" | trailer · micro-drama · found footage · nhiều shot · bảng kế hoạch |

**Ngân sách chia theo NHU CẦU, không theo đầu người** (`_shares`): ai cần ít được cấp đủ, phần
không dùng chia lại cho người còn thiếu; `_fit` cắt ở ranh giới `## ` nên phần dư được hoàn lại một
lượt nữa. Đo được: `storyboard` mọi nguồn ≥ 88 %, `write_prompt` chỉ **một** nguồn dưới 50 % (guide
Veo 146 KiB — không ngân sách nào chứa nổi). Trước đó chia đều: `mx_shell` còn 5 %, guide Veo 3,5 %.
Chọn mục theo **chủ đề luân phiên**, không theo thứ tự trong file — nếu không, 6 mục audio đầu file
guide Veo ăn hết phần và `prompt structure` (mục caller gọi tên đầu tiên) không bao giờ tới.

**Kiểm luật miễn phí chạy TRƯỚC dispatch** (`prompt_checks.check` trong `pipeline_executor`): tag
`@@` trong lời thoại **bị đọc thành tiếng** trong audio đã trả tiền, tên người nổi tiếng trong phần
mô tả hình **bị bộ lọc từ chối** sau khi request đã gửi. Hai luật đó chặn node (`prompt_rule:<rule>`);
các luật còn lại chỉ ghi log vì chúng phụ thuộc lane/cast mà executor phải tự suy. Tab Ý tưởng hiện
findings ngay dưới từng prompt để sửa trước khi bấm chạy.

**Ba cách viết prompt, chọn trên node** (`promptMode`): `single` (provider đã pin), `ensemble` (mọi
provider viết, một bản được chọn), `relay` (dàn ý → viết → kiểm luật miễn phí → soát nghĩa, đúng 1
vòng sửa). P3 dựng cả ba; trước 18/09 chỉ `single` có đường gọi từ UI.

### F. Giọng đọc: tên nào chạy được, và thay bằng gì khi không

Mẫu của exe mang tên giọng bản này không dùng được. Đếm trên 9 file đóng gói: `voice` có đúng 3 giá
trị — `🗣️ Không chọn` (11 lần), `Ember` (6), `Clone Voice` (2) — và `engine` có `ChatGPT` (6) và
`Clone Voice` (2). `tts.synthesize` chỉ nhận 30 giọng Gemini, nên trước đây mỗi node đó gãy ở bước
cuối, **sau khi ảnh và video đã trả tiền**.

`services/voice_catalog.py` trả lời bằng một trong năm kết quả, và **mỗi lần thay giọng đều có một
câu nói rõ đã thay gì bằng gì**:

| Kết quả | Khi nào | Hành vi |
| --- | --- | --- |
| `exact` | tên là một giọng Gemini | dùng nguyên, không cảnh báo |
| `no_voice` | `🗣️ Không chọn`, `Không tạo giọng`, `no_voice`, `none`, `tắt giọng`… | **không đọc gì** — tầng plan không phát op narrate |
| `alias` | 67 tên mẫu của ChatGPT/CapCut | giọng Gemini **gần nhất theo cao độ đo được** + cảnh báo |
| `refused` | `Clone Voice` | giọng mặc định + câu nói rõ bản này không nhân bản giọng |
| `unknown` | còn lại | giọng mặc định + cảnh báo có tên gốc |

Ba điều đáng ghi vì chúng là quyết định, không phải chi tiết:

1. **`🗣️ Không chọn` phải là "không đọc gì", không phải "giọng mặc định".** 11 node đóng gói lưu đúng
   chuỗi đó; đọc chúng bằng giọng mặc định là lồng tiếng lên video cố ý không có tiếng. Ô `voice`
   **rỗng** thì ngược lại — mẫu nói rõ khi muốn im lặng, nên rỗng là "chưa đặt".
2. **"Gần nhất" là đo được, kèm sai số công bố.** `median_f0_hz` của cả 30 giọng Gemini và 67 tên lạ
   đo trên chính file mẫu đóng gói. Giới tính và mô tả tiếng Việt là `GOOGLE_VOICE_GROUPS` của exe,
   đào nguyên văn — và vì exe ghi rõ giới tính, chúng cũng là tập nhãn để **đo phương pháp**:
   leave-one-out rơi đúng giới tính **25/30**, 5 lần sai đều trong dải 146–169 Hz nơi hai nhóm chồng
   nhau. Nên một lần map là **thay thế hợp lý, không phải khẳng định về người nói**.
3. **`voice_catalog.py` là file SINH RA** từ `tools/voice_catalog/` (đo → map → sinh). Sửa tay ở đó
   mất im lặng ở lần sinh sau; có banner và có test so file với đầu ra generator.

**Giọng OpenAI (`/v1/audio/speech`) — mặc định TẮT.** `OPENAI_TTS_ENABLED` + khoá OpenAI; khoá một
mình không bật được. Node `engine: ChatGPT` gặp đường này **đang tắt** thì **đọc bằng Gemini và nói
rõ** — ngược với node ảnh `engine: openai` (ở đó từ chối, vì rơi về Flow là tiêu credit người dùng
không yêu cầu). Ở đây fallback là engine mặc định của chính app, đã được `/estimate` đếm, còn từ chối
thì 6 node trong 9 mẫu đóng gói gãy ngay khi nhập. Công tắc **bật mà không có khoá** thì báo lỗi: đó
là cấu hình sai do người dùng chủ động tạo ra.

`/estimate` đếm giọng OpenAI bằng **ký tự** (`openaiTtsChars`), không bằng lượt gọi, vì endpoint tính
tiền theo ký tự — "1 lần đọc" đặt một caption và một truyện 10 phút vào cùng một hàng. Node lấy kịch
bản từ dây thì báo **"chưa đo được"** (`openaiTtsUnknownNodes`) chứ không báo 0.

### G. Captcha: một phép thử để phân biệt ba lỗi giống nhau

Bộ giải captcha đã chạy bên trong **mọi** request từ đầu. Thứ nó chưa có là một câu trả lời khi
generation đứng: "Flow chậm", "token Bearer hết hạn" và "trang đã ngừng phát captcha token" là ba
vấn đề khác nhau với cùng một triệu chứng, và chỉ cái thứ ba được chữa bằng reload tab Flow.

Nay có method WS `solve_captcha` riêng, `POST /api/auth/captcha-test`, nút **Test captcha** trong
popup, và metrics `captchaCount` / `captchaFailed` / `lastCaptchaError` đếm cả lượt trong request lẫn
lượt test. **Token không bao giờ rời chỗ dùng nó**: kết quả chỉ mang `ok`, thời gian, độ dài token,
lỗi. Không tốn gì — token do trang phát, không mua của Google.

## 9. Bất biến không được phá

1. **Không bao giờ retry một dispatch đã tạo operation** — credit đã trừ.
2. **Không bao giờ nâng làn** model thay người dùng; hạ làn phải báo qua `model_substitutions`.
3. **Lỗi do mình sinh ra không được chạm vào breaker** (xem §5.1).
4. **Không đoán aspect**; không rơi về key non-FL ở chế độ Start-End.
5. **Ghi ra thư mục ngoài không được ném lỗi** làm hỏng generation đã trả tiền.
6. **Không phơi control cho khoá settings trơ** (§6.1).
7. Người dùng cuối chỉ được yêu cầu: mở app → bấm nút. **Không DevTools, không dán token.**


---

## 10. Đối chiếu lại với exe và với flowkit (01/09, vòng 2)

### 10.1 Claim cũ trong tài liệu này là NÓI QUÁ

Các mục trước viết rằng bản này mang "tính năng mới nhất của flowkit/flowboard". Đối chiếu thật với
`_upstream/flowkit` (repo `crisng95/flowkit`, commit `66e8596`, 18/08) cho thấy **hai bên là anh em,
không phải bản sau**:

| flowkit có, bản này KHÔNG | Ghi chú |
| --- | --- |
| **Suno** sinh nhạc (`agent/api/music.py`, `agent/services/suno.py`) | Khoảng trống lớn nhất — và nó nối thẳng với `add_bgm`/`edit_video` ở đây, vốn cần một file nhạc |
| Review video bằng Claude Vision (`agent/api/reviews.py`) | Kiểm chất lượng sau khi tạo |
| i18n 7 ngôn ngữ (`dashboard/src/i18n/`) | Không cần cho công cụ chạy máy cá nhân |

| Bản này có, flowkit KHÔNG |
| --- |
| Canvas node/edge + chạy đồ thị (toàn bộ `canvas/`, `routes/boards|nodes|edges|plans`) |
| Grok qua cầu phiên trình duyệt |
| Hậu kỳ đầy đủ hơn: concat, upscale RealESRGAN, SRT, trộn lồng tiếng |

Một điểm khảo sát tự động báo sai và đã kiểm lại: **thư viện phong cách KHÔNG thiếu** —
`GET /api/prompt/styles` + `services/styles.py` có sẵn.

### 10.2 Ba lỗi tìm được khi đối chiếu exe

**Lỗi tôi tự tạo ra, và contract test hụt một tầng.** `_delogo_op` sinh chuỗi `"78.0%"`, nhưng
`postprod.remove_logo` kiểm `isinstance(value, int)` và ném lỗi với mọi thứ khác. Contract test cũ
không bắt được vì nó stub `_run`, tức dừng **ngay trên** hàm service nơi validation nằm. Đã sửa: phần
trăm được giữ nguyên tới handler, ở đó `postprod.frame_size` probe khung rồi quy ra pixel — nên
`remove_logo` vẫn chỉ nhận số nguyên và tính chất "không có chuỗi nào của caller lọt vào filtergraph"
được giữ. Contract test giờ chạy đúng validation của service.

Nhẹ hơn thực tế báo cáo: `enable_remove_veo_logo` là **False** trong template duy nhất có nó, nên
không workflow nào đang chạy bị ảnh hưởng.

**Đường nhạc chết.** Ba trong bốn tham chiếu nhạc của template là đường dẫn tuyệt đối trên máy tác giả
gốc (`d:\ABCD\VEO3_GROK_NEW\...`). Tên file thì **có** trong `nhac_nen` của máy này. `_bgm_track`
giờ cắt thư mục chết và giữ tên file. An toàn vì `assets.bgm_path` chỉ tra trong thư mục thư viện và
từ chối mọi thứ thoát ra.

**Hai bộ giải upstream, hai quy ước.** `postprod_plan` đọc theo cổng còn `_prompt_for` thì mù cổng —
đúng loại bất đối xứng đã gây ra bốn blocker trước. Đã hợp nhất: `_prompt_for` dùng chung
`postprod_plan.TEXT_PORTS`. Trên dữ liệu thật không đổi hành vi (24/24 dây prompt vào cổng `prompt`),
nhưng giờ chỉ còn **một** quy ước.

### 10.3 `sync_image_voice` — loại node cuối cùng, đã dựng

Bằng chứng từ exe: `WorkflowRunner._execute_sync_image_voice` với `_probe_duration`, `_run_segment`,
`_run_mux`, `_run_concat`, `_run_upscale`, và hai chuỗi filter `zoompan=z='min(zoom+` (zoom vào) /
`zoompan=z='max(1.15-` (zoom ra).

`postprod.ken_burns` dựng lại: ảnh tĩnh + giọng đọc → video **dài đúng bằng audio**. Hai điểm dễ sai:
`zoompan` đếm theo **frame** chứ không phải giây (probe rồi nhân fps), và nó lấy mẫu trên ảnh **đã
scale** — chạy ở độ phân giải gốc sẽ ra cú zoom giật khấc. Đo thật: ảnh + audio 3.50s → clip 3.51s,
đúng khung, có tiếng, cả hai chiều zoom.

### 10.4 Còn thiếu, có bằng chứng, chưa làm

| Thiếu | Bằng chứng trong exe | Vì sao chưa làm |
| --- | --- | --- |
| **Phụ đề** | `capcut_stt_to_srt`, `_burn_subtitles_pictex`, `make_stt_task` | Exe dùng **CapCut STT** để lấy transcript. Cần dịch vụ ngoài; `postprod.burn_subtitles` đã sẵn, chỉ thiếu nguồn SRT |
| **Xoá logo kiểu zoom** | `veo_logo_method: "zoom"`, `veo_logo_zoom_percent: 110` | Exe mặc định **crop phóng to đẩy logo ra khỏi khung**, không làm mờ. Bản này chỉ có delogo — kết quả khác |
| **Suno sinh nhạc** | — (của flowkit, không phải exe) | Cần API key trả phí của người dùng |

Ba mục này đều cần một quyết định (dịch vụ ngoài, hoặc tiền), nên chúng được **báo cáo** thay vì tự
dựng.


---

## 11. Vòng 3 (01/09 tối) — nút Apply, phụ đề, xoá logo kiểu zoom

### 11.1 Nút Apply: một lỗi UX che hai lỗi backend

Người dùng không bấm được **Apply changes** cho Claude hay Codex. Nguyên nhân trực tiếp là UX —
`canApply` đòi test xanh, và lý do đó chỉ nằm trong `title` tooltip, **bị cắt cụt ở mép cửa sổ**.
Log agent xác nhận: 443 lần poll `/api/llm/config` mà **0 lần** gọi `/test`. Vì Gemini đang active
(`selectionUnchanged` → "Đang dùng"), người dùng **chưa bao giờ** thấy nút Apply ở trạng thái bật.

Nhưng khi chạy thật hai endpoint test thì lộ hai lỗi backend nặng hơn:

| Lỗi | Bằng chứng | Sửa |
| --- | --- | --- |
| Cách gọi codex sai **ba** chỗ | `unexpected argument '--output-format'`; đo trên codex-cli 0.147.0: `-p` là `--profile`, `--system` không tồn tại | `--output-last-message` + prompt qua stdin + gấp system prompt vào text |
| Dispatch CLI **chặn cả event loop** | `GET /api/health` timeout ở 10s trong lúc một dispatch chạy | `cli_utils.run_cli()` chạy trên worker thread |
| Timeout **không có hiệu lực** | gọi codex timeout 120s vẫn chạy quá 300s, để lại 9 `node.exe` mồ côi | drain có giới hạn 5s + `taskkill /T` |

**Mutation test lật một quy công sai của chính tôi:** tôi viết comment rằng tree-kill gỡ treo. Bỏ
tree-kill → test vẫn xanh; bỏ **giới hạn drain** → test đỏ, đúng 60s. Comment đã sửa lại cho đúng.

Đo lại sau khi sửa: codex trả về ở **121s** đúng ngân sách, và `GET /api/health` trả lời **53–68ms**
trong suốt một Claude dispatch.

Frontend: yêu cầu "phải Test trước" giờ là **chữ trong panel**, có đồng hồ giây khi test, Test là nút
chính khi chưa xanh.

**Còn lại của Codex:** CLI báo `ERROR: Reconnecting… 1/5` — mạng/endpoint của chính codex trên máy
này, không phải lỗi code. Giờ nó hiện đúng như vậy.

### 11.2 Phụ đề — tôi đã nói sai, và nó nhỏ hơn nhiều

Lượt trước tôi bảo phụ đề "cần CapCut STT". Sai: `POST /api/postprod/transcribe` (Gemini),
`postprod.burn_subtitles` (libass) và op `subtitles` **đều đã có**. Chỉ thiếu dây nối.

Đã tách `services/transcribe.py` để route và worker dùng **chung một đường** thay vì hai bản. Chuỗi
trong `edit_video`: `transcribe` → `subtitles`.

**Một bẫy thật trong thiết kế, suýt ship:** op `subtitles` cần **hai** đầu vào — SRT vừa tạo *và*
clip gốc. Sentinel `PREVIOUS` chỉ mang được một, nên sau bước transcribe thì `video` cũng thành SRT:
nó sẽ vẽ phụ đề lên một file phụ đề. Thêm tham chiếu theo bước (`__step_N__`), và test bắt đúng bẫy
đó khi đột biến ngược lại.

Sửa thêm: `sub_outline_width` trong template là **số thực** (0.2 / 0.5 / 2.5) còn `burn_subtitles`
ép `int()` — 0.2 thành 0, tức mất viền. Giờ giữ số thực; thêm cả `Shadow` mà exe có.

**Chi phí:** mỗi lần phiên âm là một lần gọi Gemini. `BoardEstimate.transcribeJobs` đếm riêng —
không gộp vào `localJobs` (không miễn phí) cũng không gộp vào `billableJobs` (không tốn credit Flow)
— và hộp xác nhận hiện nó trước khi chạy. 6/9 template bật `enable_sub`.

### 11.3 Xoá logo kiểu zoom — và exe mặc định của chính nó không đủ

`veo_logo_method: "zoom"` ở `110%` là mặc định của exe, tức **crop vào rồi phóng lại** để đẩy mark ra
khỏi khung — khác hẳn delogo (làm mờ tại chỗ). Đã dựng `postprod.zoom_out_logo`, cửa sổ crop lệch
**khỏi góc chứa mark**, góc đó suy từ chính bốn số phần trăm.

Tính trên số thật của template (1920×1080, mark 78%/2% cỡ 20%×10%): **110% không xoá hết** — cần
khoảng **114%**. Hàm ghi cảnh báo nói rõ con số cần, chứ **không tự tăng zoom**: cắt nhiều hơn mức
được yêu cầu là đổi khung hình của mọi cảnh, và đó là quyết định của người dùng.

Mốc sau vòng này: backend **1085 passed** + 2 lỗi POSIX cũ; frontend 83; `tsc` + build sạch.

### 11.4 Còn lại

**Suno** (sinh nhạc) — user đã chốt **defer**; hồ sơ đầy đủ ở `.omc/plans/suno-music.md`.

Sửa lại một câu tôi từng viết ở đây: "3/4 tham chiếu nhạc trỏ vào máy tác giả gốc **nên hiện chưa có
cách lấy nhạc nền**". Vế sau sai. Đo lại: 4 template có `audio_path`, cả 4 trỏ vào ổ không tồn tại,
nhưng `postprod_plan._bgm_track` đã cắt về basename và **3/4 khớp file có thật** trong
`data_general/nhac_nen/` (9 track). Chỉ 1 tham chiếu (`nhac nền  hàn.mp3`) là không có. Giá trị thật
của Suno là **sinh nhạc gốc** thay vì xoay vòng 9 track cố định, không phải "không có nhạc".

---

## 12. Vòng 4 (01/09 khuya) — danh tính OAuth của provider CLI

### 12.1 Lỗi Codex: không phải mạng, mà là key chết

Vòng trước tôi kết luận `ERROR: Reconnecting... 1/5` là "vấn đề mạng/endpoint, không phải lỗi code".
Sai. Đo lại:

| Kiểm tra | Kết quả |
| --- | --- |
| `api.openai.com` / `auth.openai.com` không kèm auth | 401 / 200 — **mạng thông** |
| `~/.codex/auth.json` | `auth_mode: "apikey"`, `tokens` rỗng |
| Chính key đó gọi `GET /v1/models` | **401 · `invalid_api_key`** |
| `claude auth status --json` | `loggedIn: true`, `authMethod: "claude.ai"`, gói `max` |

Key trong kho auth của chính codex đã chết. `Reconnecting` không hề nhắc tới auth, nên chẩn đoán đi
sang mạng rồi ở lại đó. Claude thì **đã chạy OAuth sẵn** — không có gì để đổi.

### 12.2 Vì sao không nhìn ra — ba chỗ trong code

1. `/api/llm/providers` báo **transport** (`cli`/`api`), không báo **danh tính** (oauth/apikey).
2. `ProviderCard` **hardcode** dòng mô tả — "ChatGPT CLI · API key", "Anthropic CLI · OAuth". Đúng
   ngẫu nhiên trên máy viết ra nó, sai ngay khi ai đó đăng nhập kiểu khác.
3. `is_available()` chỉ chạy `--version`, **không kiểm đăng nhập** — thẻ ghi "Connected" cho một
   provider không trả lời được câu nào.

Hai nhánh UI để nói đúng chuyện này **đã có sẵn nhưng chết**, vì backend chưa từng phát `lastError`.

### 12.3 Đã dựng

`services/llm/cli_auth.py` hỏi chính CLI: `claude auth status --json` (hợp đồng JSON thật, có
`authMethod`) và `codex login status`. **Hai CLI trả lời trên hai luồng khác nhau** — claude ghi
stdout, codex ghi **stderr**. Đo được; đọc stdout cho cả hai (phỏng đoán đầu tiên, đã ship khoảng
mười phút) báo mọi lần đăng nhập codex thành "chưa đăng nhập".

Parser codex nhận diện dương tính **chỉ** câu chữ đã quan sát (`Logged in using an API key`), phần
còn lại quy về oauth — vì chưa có lần đăng nhập ChatGPT nào để đọc câu chữ thật. Khi có, phải chụp
lại và ghim vào test.

`/providers` thêm `authMode`, phát `lastError: not_authenticated`, và thêm `POST /api/llm/recheck`
để sau khi `codex login` không phải đợi hết 60s cache.

### 12.4 Hai lỗi phát sinh trên đường đi

**Probe chặn event loop.** Vòng trước mới sửa đường dispatch; `_probe_cli` và `_probe_available` vẫn
gọi `subprocess.run` thẳng trong coroutine. Bound 5s nên chỉ đơ ngắn, và đó chính là lý do nó sống
sót qua vòng sửa trước. Đã cho tất cả qua `run_cli`.

**Đua lúc khởi động.** `_probe_cli` đặt cờ "đã probe" **trước** await đầu tiên, nên request tới giữa
chừng bỏ qua chờ và đọc field chưa ai điền. Quan sát thật: lần poll đầu sau khi khởi động lại agent
báo OpenAI `available: false, mode: none`, lần sau báo `cli` — đọc y như một cài đặt hỏng. Đã thêm
lock và chỉ đặt cờ sau khi xong.

### 12.5 Một thứ đã dựng rồi tự bác bỏ — giữ lại nhưng nói đúng

Env scrub (`env_without_api_keys`) được dựng với lý do: `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` trong
shell sẽ **âm thầm đè** login OAuth. Đem đi thử thì **lý do đó sai** trên phiên bản hiện tại: xuất
một `ANTHROPIC_API_KEY` bậy rồi chạy agent, `claude auth status` vẫn báo `claude.ai` và một dispatch
thật vẫn **thành công** — điều không thể xảy ra nếu CLI dùng key bậy. `codex login status` cũng phớt
lờ `OPENAI_API_KEY`.

Giữ lại vì thứ tự ưu tiên auth là chi tiết nội bộ của CLI (từng đổi giữa các phiên bản) và vì yêu
cầu của bản này là OAuth **phải** là thứ chạy. Nhưng **không** tuyên bố rằng bỏ nó đi thì hôm nay sẽ
sai — sẽ không. Comment trong code và docstring test nói đúng như vậy.

### 12.6 Đo được sau vòng này

- Backend **1121 passed** + 2 lỗi POSIX-on-Windows cũ; frontend **83**; `tsc` + `vite build` sạch.
- `/providers` trả `claude: oauth`, `gemini: apikey`, `codex: apikey` — khớp thực tế.
- Ba poll đồng thời lúc agent nguội đều nhất quán (trước khi có lock thì không).
- `GET /api/health` **18–40ms** trong lúc một dispatch Claude chạy; test Claude `ok:true` 5.0s.
- Sau khi user chạy `codex login`: câu chữ OAuth thật là **`Logged in using ChatGPT`** (`auth_mode:
  "chatgpt"`, có `tokens`, trường `OPENAI_API_KEY` rỗng). Đã ghim vào test, thay cho chuỗi suy đoán.
  Parser vẫn **chỉ** khớp dương tính nhánh api-key, để nếu OpenAI đổi câu chữ thì kết quả suy biến về
  `oauth` chứ không về "chưa đăng nhập".
- Đã xác minh cả ba trạng thái trên agent thật: `oauth` (claude), `apikey` (gemini), và `none` +
  `lastError: not_authenticated` (codex sau khi mất login). Trạng thái thứ ba trước vòng này hiển thị
  là **"Connected · ChatGPT CLI · API key"** — tức sai hoàn toàn.

### 12.7 Vì sao codex trên máy này vẫn chưa chạy (không phải lỗi Flowboard)

`~/.codex/config.toml` trỏ codex vào **proxy bên thứ ba** `http://47.84.182.41:8080` với model
`gpt-5.6-sol` chỉ tồn tại trên proxy đó. Host này **không phản hồi** (TCP connect timeout 10s). Đây
là nguyên nhân của `Reconnecting... 1/5` lịch sử, cộng với key chết ở §12.1.

Kèm một rủi ro bảo mật: `base_url` là **HTTP trần**, nên nếu host đó sống lại, access token OAuth của
ChatGPT sẽ đi tới một IP lạ **không mã hoá**.

Đã chứng minh codex + tài khoản này chạy được: chạy với `CODEX_HOME` tạm chỉ chứa `auth.json` (không
có config.toml) → **exit=0, 10.5s, trả lời `OK`**. Gói là `team`, còn hạn tới 30/09/2026. Vậy CLI,
tài khoản và OAuth đều tốt; chỉ mỗi config là hỏng.

**Lưu ý cho lần sau:** trong phiên này `config.toml` rồi `auth.json` lần lượt **biến mất** khỏi
`~/.codex` giữa chừng (`codex login status` → `Not logged in`). Không rõ nguyên nhân. Mọi lần
`Reconnecting 2/5` **sau** mốc đó là do **không có credential**, không phải do proxy — hai nguyên
nhân khác nhau cho cùng một câu báo lỗi, và tôi đã đổ lỗi nhầm cho proxy vài lượt.

### 12.8 Kết quả cuối — codex chạy được

Sau khi user `codex login` lại (và `config.toml` trỏ proxy vẫn vắng mặt, tức codex dùng mặc định là
endpoint OpenAI chính thức):

| Đo | Kết quả |
| --- | --- |
| `/providers` | claude `oauth`, gemini `apikey`, **codex `oauth`** |
| `POST /providers/openai/test` | **`ok:true`, 8.3s** — rồi 6.2s ở lần sau |
| `POST /providers/claude/test` | `ok:true`, 33.9s (không hồi quy) |
| `GET /api/health` trong lúc codex dispatch | **18–28ms** |
| Codex test khi agent chạy với `OPENAI_API_KEY` **bậy** trong env | **`ok:true`, 6.0s** — env scrub không phá gì |

Đây là lần đầu tiên trong cả chuỗi vòng lặp mà Codex trả lời được. Chuỗi nguyên nhân đầy đủ: key
chết (§12.1) **cộng** config trỏ proxy chết (§12.7); sửa cả hai thì chạy.

Mốc cuối: backend **1121 passed** + 2 lỗi POSIX cũ; frontend **83**; `tsc` + `vite build` sạch.
