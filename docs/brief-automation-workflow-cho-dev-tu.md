# Brief: dựng automation workflow sinh video từ Google Sheet

**Người nhận:** Tú (developer).
**Người đặt:** chủ dự án.
**Ngày:** 02/10/2026.

---

## 0. Đọc mục này trước, 2 phút

Việc cần dựng: **một workflow nhận một Google Sheet kịch bản, mỗi hàng là một video, tự sinh ảnh →
sinh cảnh video → đọc lời → chèn chữ → ghép thành file hoàn chỉnh.**

Ba điều quyết định toàn bộ kiến trúc, nói trước để Tú không đi sai đường:

1. **Không gọi API trực tiếp được.** Google đã ngắt token của API Flow. Lệnh phải được **ký ngay trong
   tab trình duyệt đã đăng nhập**, nên bắt buộc có một extension/bridge. Không có chế độ headless —
   đây là **ràng buộc cấu trúc, không phải lựa chọn cấu hình**. Chi tiết §3.
2. **Tiền là ràng buộc thiết kế, không phải tối ưu sau.** Mỗi lần sinh video là tiền thật. Luật xuyên
   suốt: **thiếu thông tin thì TỪ CHỐI, không tự thay thế**. Chi tiết §4.
3. **Nhiều tài khoản, nhiều hãng.** Chủ dự án chốt: v1 phải có hồ tài khoản và lớp chọn nhà cung cấp
   theo giá/quota còn lại. Chi tiết §5.

---

## 1. Quy ước mức bằng chứng — áp cho mọi dòng trong tài liệu này

Chủ dự án bàn giao kết quả reverse-engineer từ tool đóng gói, **kèm lời nhắc: không chắc chạy được.**
Lời nhắc đó đúng với phần lớn, nhưng **không đúng với tất cả** — có những thứ đã chạy thật trên máy
và đã in ra số. Trộn hai loại lại là cách nhanh nhất để Tú vừa mất thời gian kiểm lại thứ đã chắc,
vừa tin tưởng thứ chưa ai thử.

Nên mỗi khẳng định dưới đây mang một nhãn:

| Nhãn | Nghĩa | Tú nên làm gì |
| --- | --- | --- |
| 🟢 **ĐO THẬT** | Đã chạy trên tài khoản thật, có artefact (file video, số dư, id) | Tin được. Đừng làm lại cho tốn tiền. |
| 🟡 **ĐỌC ĐƯỢC** | Đọc từ code/capture của người khác, chưa tự chạy | Dựng theo, nhưng lần chạy đầu coi như phép thử. |
| 🔴 **CHƯA KIỂM** | Suy luận, hoặc hai nguồn không khớp | **Không** dựa vào. Phải đo trước khi tin. |

Nếu một dòng không có nhãn, mặc định là 🔴.

---

## 2. Bài toán thật — file `Brand Template`, sheet `SCRIPT VN`

Link: `https://docs.google.com/spreadsheets/d/1TjgpGpvjtP4ocHLvP4-nkj-srmA-IMu7wzRweSUMnTI`

🟢 **ĐO THẬT** — tôi đã đọc file. Dưới đây là cấu trúc thật, không phải mô tả chung.

### 2.1 Hình dạng

- Vùng dữ liệu `A1:AB3` → header + **2 hàng mẫu**.
- **MỘT HÀNG = MỘT VIDEO.** Cột `AB` (`FINAL`) ghi `VIDEO 1`, `VIDEO 2`.
- 4 cột đầu là thông tin chung của cả video: `ID`, `ANGLE`, `PRODUCT`, `CAPTION`.
- Sau đó là **một khối 5 cột lặp cho mỗi cảnh**, chèn một cột transition giữa hai khối:

| Cột | Ý nghĩa |
| --- | --- |
| `SCENE N (description)` | mô tả cảnh → đây là **prompt sinh ảnh/video** |
| `TIMECODE` | ví dụ `00:00 - 00:02` → độ dài mong muốn của cảnh |
| `SCENE N (REF)` | ảnh tham chiếu (cả 2 hàng mẫu đang **để trống**) |
| `TEXT` | chữ hiện trên màn hình |
| `VO` | lời đọc (voice-over) |
| `Transition SN->SN+1` | kiểu chuyển cảnh, ví dụ `Match cut` |

Template hiện có **4 khối cảnh**: `E–I`, `K–O`, `Q–U`, `W–AA`; transition ở `J`, `P`, `V`.

### 2.2 ⚠️ Một lỗi trong header mà Tú PHẢI biết trước khi viết parser

🟢 **ĐO THẬT** — cột REF được đặt tên: `SCENE 1 (REF)` (G), rồi **`SCENE 2 (REF)` ba lần** ở M, S, Y.
Đây là lỗi copy-paste của người làm sheet.

**Hệ quả:** parser nào map cảnh **theo tên header** sẽ gán sai cảnh 3 và cảnh 4 — ảnh tham chiếu của
cảnh 4 sẽ nhảy vào cảnh 2. Và nó sai **im lặng**, video vẫn ra, chỉ sai nội dung.

→ **Map theo VỊ TRÍ cột, không theo tên header.** Cụ thể: tìm các cột `SCENE N (description)` làm mốc,
rồi đọc 4 cột kế tiếp theo thứ tự cố định. Đừng tin chữ trong header.

### 2.3 Hai hàng mẫu khác nhau rất nhiều — v1 phải chịu được cả hai

| | Hàng 1 (Combo Super Collagen Peptides) | Hàng 2 (Super SPF50+) |
| --- | --- | --- |
| Số cảnh dùng | **3** (cảnh 4 trống) | **4** |
| VO | **không có dòng nào** → video không lời | đủ 4 cảnh đều có VO |
| Transition | chỉ `Match cut` ở S2→S3 | không điền |
| Timecode | 0-2, 2-6, 6-10 → **10s** | 0-3, 3-6, 6-10, 10-15 → **15s** |
| Độ dài cảnh | **2s**, 4s, 4s | 3s, 3s, 4s, **5s** |

Rút ra, và đây là phần Tú dễ bị sa vào:

- **Số cảnh không cố định.** Cảnh trống phải bỏ qua, không được sinh một clip rỗng.
- **Có video không có lời.** Không được mặc định đọc bằng giọng mặc định — một video cố ý im lặng mà
  bị lồng tiếng là lỗi người dùng không sửa được bằng cách bỏ qua.
- **Độ dài cảnh lẻ (2s, 5s).** Model video chỉ render độ dài cố định — xem §4.3. Nên **bắt buộc có
  bước ffmpeg cắt/retime**.

### 2.4 Chủ dự án chốt về timecode

> *"timecode có thể không đúng 100%. Timecode là đề xuất thôi. Phần này lúc code có thể đề xuất lại."*

Nghĩa là: **Tú được quyền đề xuất cách xử lý khác** (ví dụ: làm tròn mỗi cảnh về độ dài model hỗ trợ,
hoặc cắt theo tỉ lệ, hoặc giữ nguyên clip gốc và chỉ khớp tổng). Đừng ép mình phải đúng từng giây —
nhưng phải **nói rõ cách mình chọn** và tổng thời lượng lệch bao nhiêu.

---

## 3. Vì sao không gọi API trực tiếp — phần Tú chưa có nền

Đây là mục quan trọng nhất nếu Tú chưa làm media automation.

### 3.1 Chuyện đã xảy ra

🟢 **ĐO THẬT** — tháng 9/2026 Google chuyển Google Flow sang `flow.google.com` và **ngừng phát token
`Bearer ya29…`**. Toàn bộ endpoint REST cũ (`aisandbox-pa.googleapis.com`) không còn xác thực được.

Bằng chứng, không phải suy đoán:
- repo cộng đồng `crisng95/flowkit` v1.2.0 xoá hẳn tầng REST, **−1043 dòng**, ghi rõ lý do;
- log của bản dựng trên máy này cho `auth_seen: []` suốt cả phiên → trang mới **không gửi header
  `Authorization` nào cả**.

### 3.2 Thay vào đó Flow làm gì

Trang web tự ký lệnh của nó bằng:
- **cookie phiên** của chính tab đó, cộng
- một **token CSRF `at`** sống theo từng lần tải trang (đọc từ `WIZ_global_data.SNlM0e`).

Hai thứ này **không rời khỏi tab**. Hệ quả cứng:

> **Backend không thể gọi Flow một mình. Phải có một tab Chrome đang đăng nhập, và một extension đứng
> trong tab đó để gửi lệnh hộ.** Không có headless. Đây là cấu trúc.

### 3.3 Giao thức thật: `batchexecute`

🟢 **ĐO THẬT** — lệnh gửi tới:

```
POST flow.google.com/_/AiSandboxAngularFrontend/data/batchexecute
    ?rpcids=<id>&source-path=…&bl=<cfb2h>&f.sid=<FdrFJe>&hl=…&_reqid=…&rt=c
body: f.req=[[[rpcid,"<payload JSON dạng chuỗi>",null,"generic"]]]&at=<SNlM0e>
credentials: include
```

Trả về: `)]}'` + chunk có độ dài đứng trước + `[["wrb.fr", rpcid, "<payload>"]]`.

**Cái khó nhất của giao thức này:** payload là **mảng theo vị trí, không có tên trường.** Khác biệt
giữa "video từ 1 ảnh" và "video từ ảnh đầu + ảnh cuối" là **một ô chèn ở vị trí 5**. Không ai đoán ra
được — phải capture từ trang thật. Quy trình capture đã được viết sẵn, xem §8.

### 3.4 Bảng rpcid đã biết

🟡 **ĐỌC ĐƯỢC** trừ các dòng ghi 🟢.

| rpcid | Việc | Ghi chú |
| --- | --- | --- |
| `ogiZ0b` | sinh ảnh | 🟢 đã chạy thật. URL ký trả ngay trong response. Một RPC cho mỗi biến thể |
| `eb1hJf` | ảnh → video (cả Veo và Omni) | model là chuỗi trong ô; aspect **1 dọc / 2 ngang** (khác bảng ảnh!) |
| `nprQif` | Omni ảnh đầu + ảnh cuối | Veo **không có** payload cho việc này |
| `YhhmEf` | text → video | 🟢 đã chạy thật (clip Veo 8s) |
| `MZZa6b` | video từ nhiều ảnh tham chiếu | 🟢 đã chạy thật (3 clip Omni 4s) |
| `jwpduf` | poll tiến độ | `CAE` = xong. *"Media not found."* là **chẩn đoán, không phải lỗi** |
| `Zzl0ze` | liệt kê media của project | ~17 MB → phải cắt cửa sổ quanh từ khoá. **Đây mới là chỗ có mediaId** |
| `as29s` | mediaId → URL tải | poster `/image/` về trước, `/video/` về sau — chỉ xong khi có `/video/` |
| `maseQ` | upload ảnh | 🟢 đã chạy. **Có captcha** (đường REST cũ thì không) |
| `jHPbke` | tạo project | 🟢 đã chạy thật |
| `nzlxg` | đọc số dư credit | 🟢 đã chạy thật (đọc ra 989, rồi 1039). **0 credit, không captcha** |
| `C4BZMd` | tạo nhân vật (character entity) | 🟢 đã chạy thật, có entityId thật |
| `rqZuUc` | tạo scene từ clip đã xong | dựng rồi nhưng **chưa chạy** |
| `fZytfe` | nối tiếp clip | dựng rồi, **chưa chạy**; giá 🔴 chưa đo |
| `p0UkFb` | upscale 1080p | dựng rồi, **chưa chạy**; giá 🔴 chưa đo |

### 3.5 Ba cái bẫy của giao thức, đã trả giá để biết

1. 🟡 **Token captcha dùng MỘT LẦN.** Gửi lại token cũ → Google trả
   `PUBLIC_ERROR_UNUSUAL_ACTIVITY`. Nhịp an toàn đã dùng: ≥ 1 giây giữa các lệnh, ≤ 4–5 lệnh song song.
2. 🟡 **Upscale cần HAI id khác nhau** — `operation id` ở ô 0 và `media id` ở ô 4. Capture ghi rõ: dùng
   lẫn thì Google **nhận rồi báo NOT_FOUND**. Tức là nhìn từ ngoài giống thành công.
3. 🟡 **Nối clip: lần đầu dùng CLONE, lần sau dùng operation id của lần nối trước.** Dùng sai thì cũng
   "nhận rồi NOT_FOUND" — đã tính tiền, không có clip.

---

## 4. Chọn model theo chi phí — phần nghiệp vụ Tú cần nắm

### 4.1 Sinh ảnh

🟢 **ĐO THẬT** — Flow nhận 3 model ảnh, gọi qua biệt danh:

| Biệt danh | Wire id |
| --- | --- |
| `NANO_BANANA_PRO` | `GEM_PIX_2` |
| `NANO_BANANA_2` | `NARWHAL` |
| `NANO_BANANA_2_LITE` | `HARBOR_SEAL` |

**Mọi model ảnh cùng giá**, nên chọn sai chỉ ảnh hưởng chất lượng chứ không ảnh hưởng tiền. Vì vậy
key ảnh lạ được phép rơi về mặc định — **và đây là điểm khác biệt quan trọng so với video.**

Ngoài Flow còn đường ChatGPT/gpt-image. Nhận xét từ kinh nghiệm dự án: Nano Banana mạnh hơn ở ảnh sản
phẩm/bối cảnh thương mại; gpt-image dễ điều khiển bằng chữ hơn. 🔴 chưa có phép đo so sánh chính thức.

### 4.2 Sinh video — và luật tiền tuyệt đối

🟢 **ĐO THẬT** — hai họ model:

**Omni (`abra_*`)** — giá **đã công bố**, tính theo độ dài:

| Độ dài | Credit |
| --- | --- |
| 4s | 15 |
| 6s | 20 |
| 8s | 25 |
| 10s | 30 |

**Veo (`veo_3_1_*`)** — chất lượng cao hơn, nhưng **giá không công bố**, và quyền dùng tuỳ theo gói.

🟢 **ĐO THẬT trên một tài khoản Pro:**
- làn miễn phí `veo_3_1_i2v_lite_low_priority` → **`PUBLIC_ERROR_MODEL_ACCESS_DENIED`**. Tức **gói Pro
  KHÔNG có làn 0 credit.** Ai dựng cả board 15 node trên làn đó rồi mới biết là mất một buổi.
- `veo_3_1_t2v_lite` 8s → **chạy được**, ra clip thật 8,00s · 1280×720 · h264+aac.
- cùng làn nhưng 4s và 6s → **bị từ chối theo gói**.

→ **Quyền dùng phụ thuộc tài khoản, và chỉ biết được bằng cách thử.** Đây chính là lý do §5 (hồ tài
khoản) là yêu cầu chứ không phải tối ưu.

> ### ⛔ LUẬT TIỀN — không thương lượng
>
> **Thiếu thông tin thì TỪ CHỐI, không tự thay thế.**
>
> Cụ thể, mỗi dòng dưới đây là một lỗi đã thật sự xảy ra trong dự án:
>
> - key model **lạ** không bao giờ được coi là miễn phí. Gập một key lạ về mặc định chính là cách một
>   làn 0 credit biến thành làn trả phí;
> - board xin làn A mà hệ thống **lặng lẽ** chạy làn B đắt hơn là lỗi, dù video vẫn ra;
> - giá **chưa đo** phải hiện là *"chưa đo được giá"*, **không** hiện là 0. Số 0 là con số người dùng
>   dám bấm;
> - vòng tự động (tự chấm điểm rồi render lại) **chỉ** được chạy trên làn đã đo đúng 0 credit.

### 4.3 Độ dài cố định — và vì sao bắt buộc có ffmpeg

🟢 **ĐO THẬT** — Omni render 4/6/8/10s; key Veo mang sẵn 8 giây của nó, tham số độ dài bị bỏ qua.

Sheet lại yêu cầu 2s và 5s (§2.3). Nên **không có cách nào** ra đúng timecode mà không cắt/ghép bằng
ffmpeg. ffmpeg là thành phần bắt buộc, không phải lựa chọn.

### 4.4 Những năng lực chưa chắc

| Năng lực | Trạng thái |
| --- | --- |
| upscale 1080p | 🟡 dựng rồi, **chưa chạy**, 🔴 giá chưa đo |
| upscale 4K | 🔴 **hai capture độc lập không khớp** ở ô tier. 4K tốn 50 credit → **đoán ở đây là tiêu tiền của khách để biết ai đúng.** Đang TỪ CHỐI |
| nối tiếp clip (extend) | 🟡 dựng rồi, chưa chạy. Chỉ clip **Veo** nối được, clip Omni thì Flow làm mờ nút |
| Edit Video / motion control | 🔴 chưa capture được payload |
| liệt kê project | 🔴 không có RPC, chỉ có tRPC cũ |

---

## 5. Nhiều tài khoản + nhiều hãng — yêu cầu v1

Chủ dự án chốt: **"Có, và cần cả gói subscription nhiều hãng."**

Lưu ý thẳng: bản dựng hiện có **cố ý chỉ dùng một profile Chrome** — đó là quyết định cũ, không phải
thiếu sót. Nên đây là **yêu cầu mới**, và là phần nặng nhất về vận hành.

### 5.1 Vì sao đi hướng nhiều account thay vì trả theo API

Gói subscription trả tiền theo tháng, không theo lượt. Với khối lượng ecommerce (nhiều SKU × nhiều
angle × nhiều cảnh), chi phí theo lượt tăng tuyến tính còn chi phí theo gói thì không. Đổi lại phải
chịu ba thứ: quota giới hạn, nhiều phiên đăng nhập phải quản, và rủi ro bị khoá nếu nhịp gửi bất
thường.

### 5.2 Tú phải dựng những gì

1. **Hồ profile Chrome** — mỗi account một profile, biết profile nào đang rảnh.
2. **Đọc số dư từng account** — đã có đường miễn phí (`nzlxg`, §3.4), gọi được bao nhiêu lần cũng
   không mất gì. Dùng nó, đừng đoán.
3. **Luân phiên khi hết quota hoặc bị từ chối theo gói.** Phân biệt cho đúng ba loại trả về:
   *hết quota* · *gói không có model này* · *bị chặn vì hoạt động bất thường*. Loại thứ ba **phải dừng
   lại**, không được chuyển account rồi thử tiếp — đó là cách mất cả cụm account.
4. **Lớp chọn nhà cung cấp** cho ảnh / video / TTS: ưu tiên theo giá và quota còn lại, nhưng **không
   được tự hạ chất lượng** (xem lại luật tiền §4.2).
5. **Ghi lại mỗi lần tiêu** — account nào, model nào, bao nhiêu credit, cho hàng nào trong sheet.
   Không có sổ này thì không ai trả lời được "tháng này tốn bao nhiêu, vì sao".

### 5.3 Điều KHÔNG được làm

🔴 Dự án này đã **từ chối** một việc và lý do nên giữ: tool đóng gói có đoạn **bịa telemetry giả**
(cứ 45–120s gửi một sự kiện giả cho giống người thật). Đó là đánh lừa hệ thống chống lạm dụng của
Google, và cái mất khi bị phát hiện là **tài khoản của khách**. Không port.

---

## 6. Nền open-source để dựng lên

| Thành phần | Dùng để | Ghi chú |
| --- | --- | --- |
| **ffmpeg** | cắt theo timecode, ghép cảnh, chèn chữ, trộn giọng, đổi khung | Bắt buộc (§4.3). Phần lớn công việc "ra video hoàn chỉnh" nằm ở đây |
| **flowkit** (`crisng95/flowkit`) | tham chiếu transport `batchexecute` | 🟢 Mốc đã đối chiếu: `d7977fd5` (20/09/2026). Đây là nguồn đáng tin nhất cho payload |
| **flowboard** | tham chiếu canvas/workflow node | |
| **vienu** | chủ dự án đề xuất — 🔴 tôi chưa đánh giá | Tú tự xem có dùng được không |
| **TTS** | đọc `VO` | Có đường chính thức (OpenAI `/v1/audio/speech`, Gemini TTS). 🔴 **Không** cào API nội bộ của CapCut/ElevenLabs — cùng lý do §5.3 |

Một kinh nghiệm về TTS đáng chép lại: 🟢 **ĐO THẬT** — bài dài phải **cắt đoạn rồi đọc từng đoạn**,
và ghi lại đoạn nào đã đọc xong. Gửi cả bài một lần thì một lần timeout là mất trắng cả bài. Và phải
cắt **theo ranh giới câu** — cắt giữa câu làm máy đọc hạ giọng và lấy hơi sai, nghe ra ngay.

---

## 7. Hai tool tham chiếu — Tú mua, chủ dự án gửi lại tiền

| Tool | Giá | Ai mua |
| --- | --- | --- |
| **NTA tool Veo 3** | 600.000đ | Tú mua, chủ dự án hoàn lại |
| **Tool Glabs Studio** | 200.000đ | Tú mua, chủ dự án hoàn lại |

### Nhận xét của chủ dự án

> **NTA tool Veo 3 sát nhất với nhu cầu ecommerce thật.** Nó cho đổi nhiều model từ sinh ảnh tới sinh
> video ngay trong workflow, và có phần tạo sẵn asset bối cảnh + nhân vật.
>
> **Glabs Studio đẹp về giao diện nhưng không sát nghiệp vụ.**

→ Tú nên dùng NTA làm **mẫu nghiệp vụ** (workflow nên có những node gì, người dùng mong đợi gì), và
Glabs chỉ để tham khảo giao diện.

### Bàn giao reverse-engineer

Chủ dự án bàn giao phần đã reverse-engineer từ tool đóng gói, **kèm lời nhắc: không chắc chạy được.**
Nhắc đó đúng với phần 🟡/🔴 — nhưng xin nói rõ để Tú không mất công: **những dòng 🟢 trong tài liệu này
đã chạy thật trên tài khoản thật và có artefact.** Cụ thể đã tự chạy được: tạo project · upload ảnh ·
sinh 1 ảnh · 3 clip Omni 4s · 1 clip Veo t2v 8s · đọc số dư · tạo 1 nhân vật.

---

## 8. Artefact bàn giao — đọc theo thứ tự này

| Thứ tự | File | Nội dung |
| --- | --- | --- |
| 0 | **https://github.com/baoha8003-oss/flow-reverse-handover** | Repo bàn giao. Đọc `HANDOVER.md` trước — nó ghi xuất xứ ba nguồn, thang bằng chứng, và ba test đỏ nói điều gì |
| 1 | `docs/spec.md` | Transport, bảng RPC, bảng model key, luật tiền. **Tài liệu quan trọng nhất** |
| 2 | `docs/template-format.md` | Định dạng 9 template đóng gói, để viết parser trên bản exe của chính Tú |
| 3 | `docs/flow-capture.md` | Quy trình capture một RPC mới — cách duy nhất đóng các năng lực 🔴 |
| 4 | `docs/reports/gaps-260918-1729-flow-batch-migration.md` | Sổ những gì còn thiếu và vì sao |
| 5 | `docs/reports/findings-260921-0015-exe-4642-rest-still-alive.md` | Phát hiện: tool tác giả vẫn dùng một đường REST khác |
| 6 | `agent/` · `frontend/` · `extension/` | Bản dựng tham chiếu (~24k dòng service, 130 file test) — **không bắt buộc đọc code**, nhưng là chỗ tra khi payload không khớp |

Tú dựng **từ đầu**, tự chọn stack. Code trong repo chỉ là nguồn tra cứu — đọc hàng 1–5 là đủ để bắt đầu, hàng 6 chỉ mở khi một payload không khớp.

---

## 9. Phạm vi v1

### Phải có

1. Nhận link hoặc file Google Sheet đúng dạng §2, **map theo vị trí cột** (§2.2).
2. Mỗi hàng → một video. Mỗi khối cảnh → một node trong workflow.
3. Sinh ảnh cho cảnh (nếu `SCENE N (REF)` trống) hoặc dùng ảnh tham chiếu (nếu có).
4. Sinh clip cho từng cảnh.
5. Đọc `VO` thành giọng — **bỏ qua khi `VO` trống**, không lồng tiếng mặc định (§2.3).
6. Chèn `TEXT` lên hình.
7. Cắt/ghép theo `TIMECODE` ở mức Tú đề xuất được (§2.4), ra **một file hoàn chỉnh cho mỗi hàng**.
8. Hồ nhiều account + đọc số dư + luân phiên (§5).
9. **Bảng giá trước khi chạy:** phải nói được "lần chạy này tốn khoảng bao nhiêu" **trước** khi bấm, và
   những gì chưa đo được thì ghi *chưa đo được*, không ghi 0.

### Không làm trong v1

- Upscale 4K (🔴 payload không chắc, 50 credit/lần).
- Edit Video / motion control (🔴 chưa capture được).
- Transition thật (match cut…) — 🟡 cắt thẳng là chấp nhận được cho v1; chủ dự án đã nói timecode và
  hiệu ứng là đề xuất.
- Telemetry giả, cào API nội bộ, nhân bản giọng từ dịch vụ bên thứ ba (§5.3).

---

## 10. Nghiệm thu — làm sao biết là xong

Tiêu chí: **một người dùng làm được từ giao diện**, không phải "có test".

1. Đưa đúng file `Brand Template` vào → ra **2 video**, không sửa tay gì.
2. Hàng 1 ra video **không có tiếng** (vì không có VO) — đây là phép thử thật, dễ sai nhất.
3. Hàng 2 ra video có đủ 4 cảnh, có VO từng cảnh, có chữ.
4. Trước khi chạy, hệ thống **báo giá**; sau khi chạy, số **credit thật tiêu** khớp với báo giá, hoặc
   chênh lệch được giải thích.
5. Thêm một account thứ hai vào hồ → chạy tiếp được khi account đầu hết quota.
6. Cố tình chọn một model mà gói không có → hệ thống **từ chối kèm lý do**, **không** tự đổi sang
   model đắt hơn. (Đây là phép thử luật tiền, và là phép thử tôi khuyên đưa vào CI.)

---

## 11. Câu hỏi còn mở — cần chủ dự án trả lời

1. **Bao nhiêu account, hãng nào?** Số lượng và loại gói quyết định thiết kế hồ tài khoản.
2. **Khối lượng thật:** mỗi ngày/tuần bao nhiêu hàng sheet? Con số này quyết định có cần hàng đợi và
   chạy song song hay không.
3. **Ngân sách credit mỗi lần chạy** — cần một trần để hệ thống tự dừng, giống trần 60 credit đang
   dùng trong dự án hiện tại.
4. **Ai vận hành khi một account bị Google chặn?** Cần người xử lý, hệ thống chỉ báo được.
5. `vienu` — chủ dự án muốn dùng vào việc gì? Tôi chưa đánh giá module này.
