# Capture một RPC mới của Google Flow

Tài liệu này là cách **đóng** các mục "chưa có payload" trong
`plans/reports/gaps-260918-1729-flow-batch-migration.md`. Không có nó, những mục đó chỉ có thể nằm
chờ — và đã có một phiên đi đoán payload thay vì capture, kết quả là dispatch được nhận rồi bị bỏ qua
mà vẫn tính tiền.

Bốn quy tắc đứng trên mọi bước dưới đây:

1. **Đọc, không đoán.** Một payload đúng 90% thường được Flow *nhận*, rồi bỏ qua phần sai và tính tiền.
   Từ ngoài nhìn vào nó giống hệt thành công.
2. **Không bao giờ in giá trị credential.** `at` (`SNlM0e`), cookie, token captcha: ghi lại **độ dài và
   sự tồn tại**, không bao giờ nội dung. Chúng đủ để ký lệnh trên tài khoản của bạn.
3. **Một lần sinh là một lần trả tiền.** Chọn cấu hình rẻ nhất tái hiện được năng lực cần capture, và
   hỏi trước khi chạy một lần có giá.
4. **Golden test đỏ nghĩa là capture lại, không phải sửa golden.** Golden là *sự thật dây* đã đo; sửa
   nó để test xanh là xoá đúng thứ nó tồn tại để bảo vệ.

---

## 1. Chuẩn bị

- Chrome đang đăng nhập Flow, mở một project: `https://flow.google.com/project/<uuid>`.
- DevTools → Network, filter `batchexecute`, bật **Preserve log**.
- Biết trước mình đang capture năng lực nào và ô nào còn thiếu. Mở trang trước, làm một lần sinh bình
  thường, rồi mới đọc — log sẽ đầy các RPC nền (`Zzl0ze`, `as29s`) và cần phân biệt được.

## 2. Tìm đúng request

Một lần bấm sinh trong UI thường tạo nhiều RPC. Cái cần tìm là request `POST …/batchexecute` có
`?rpcids=<id>` mới, xảy ra **đúng lúc** bấm.

Đọc theo thứ tự này:

| Chỗ đọc | Lấy gì |
| --- | --- |
| Query `rpcids=` | rpcid — thứ duy nhất định danh lệnh |
| Query `source-path=` | đường dẫn trang; agent gửi lại đúng chuỗi này |
| Query `bl=`, `f.sid=` | phiên bản frontend và session id; **thay đổi theo lần tải trang**, nên extension đọc từ trang chứ không ghim |
| Form body `f.req` | envelope — phần cần capture |
| Form body `at` | token CSRF. **Không chép ra.** Chỉ ghi "có" |

Copy `f.req` (chuột phải → Copy value, ở tab Payload chọn **view source** để lấy nguyên chuỗi chưa
decode).

## 3. Giải envelope

```bash
python - <<'EOF'
import json, urllib.parse
raw = urllib.parse.unquote(open("freq.txt", encoding="utf-8").read().strip())
outer = json.loads(raw)
rpcid, inner = outer[0][0][0], json.loads(outer[0][0][1])
print(rpcid)
print(json.dumps(inner, indent=1, ensure_ascii=False))
EOF
```

Hình dạng luôn là `[[[rpcid, "<payload dạng chuỗi JSON>", null, "generic"]]]`. Phần đáng đọc là
`inner`: một mảng lồng nhau **không có tên trường nào**. Đó là lý do mỗi builder trong
`agent/flowboard/services/flow_batch.py` phải có một golden test: chỉ số ô là hợp đồng duy nhất, và
không có gì trong mã tự giải thích được "ô số 4 là aspect".

Khi đọc `inner`, ghi lại từng ô theo **cái đã thay đổi**, không theo phỏng đoán:

- sinh hai lần với **cùng mọi thứ trừ một tuỳ chọn** → ô nào đổi chính là ô đó;
- prompt là chuỗi dễ nhận nhất, dùng nó để định vị (`[[[prompt]]]`);
- uuid xuất hiện nhiều lần: một số là client-generated (đổi mỗi lần), một số là mediaId/projectId (giữ
  nguyên). So hai lần chạy để tách hai loại;
- đừng nhầm **sceneId** với **mediaId**. Đọc sceneId thành mediaId là lý do mọi lần tra `as29s` trả
  NOT_FOUND một lần trước đây.

## 4. Dựng builder + golden test

1. Thêm builder vào `flow_batch.py`, dựng đúng thứ tự ô đã đọc. Hằng số nào chưa hiểu thì **giữ nguyên
   giá trị đã capture kèm comment "chưa biết nghĩa"** — bỏ đi là đổi payload.
2. Thêm golden test vào `agent/tests/test_flow_batch_golden.py`: so **từng byte** envelope builder sinh
   ra với chuỗi đã capture. Ghim uuid bằng monkeypatch để so được.
3. Chạy `pytest tests/test_flow_batch_golden.py`.

Golden test là thứ biến "payload này từng đúng" thành "payload này vẫn đúng". Khi Google đổi frontend
lần nữa, nó đỏ — và đó là tín hiệu capture lại.

## 5. Đọc response

```bash
python - <<'EOF'
import json
text = open("response.txt", encoding="utf-8").read()
body = text.split(")]}'", 1)[1]          # bỏ sentinel chống JSON hijacking
# Thân là các chunk có tiền tố độ dài; parse_envelope trong flow_batch.py làm việc này.
from flowboard.services.flow_batch import parse_envelope
for r in parse_envelope(text):
    print(r.rpcid, "ERROR" if r.error else "ok")
    print(json.dumps(r.data, indent=1, ensure_ascii=False)[:2000])
EOF
```

Lỗi nằm ở **ô [5]** của chunk. `[8]` là transient (phía sinh của Flow từ chối vì tải) — đó là mã duy
nhất đáng retry.

## 6. Nối vào SDK

Đặt reader cạnh builder (`read_*`), rồi gọi từ `flow_sdk.py`. Ba điều bắt buộc, vì cả ba đã từng bị bỏ
sót và mỗi lần đều tốn tiền:

- **Giữ nguyên hình dạng trả về** của method SDK. Worker, executor và frontend đọc hình dạng đó; đổi nó
  là kéo cả blast radius ra ngoài file này.
- **Trả về model key thật sự đã dispatch** trong result. Vòng review đọc key này để quyết có được tự
  chạy lại hay không, và thiếu nó thì làn 0 credit bị coi là có phí.
- **Một năng lực chưa capture thì TỪ CHỐI có tên lý do**, không thay bằng năng lực gần nhất. Thay thế
  là tính tiền cho thứ không ai chọn.

## 7. Còn thiếu gì, và cần capture cái gì

| Mục | Cần capture | Cách tái hiện trong Flow UI |
| --- | --- | --- |
| Veo text-to-video | `YhhmEf` (hoặc rpcid khác) với key `veo_3_1_t2v_*` | chọn model Veo ở tab text-to-video, sinh một clip |
| Veo first→last | rpcid + ô ảnh cuối | chọn ảnh đầu **và** ảnh cuối trên làn Veo |
| Veo references (r2v) | rpcid + ô refs với key Veo | Thành Phần / Ingredients trên làn Veo |
| Character Entity | ô `referenceEntities` trong payload r2v | tạo nhân vật trong Flow UI, tag `@@Tên` trong prompt, sinh |
| Danh sách project | rpcid của màn hình danh sách project | mở trang danh sách project, đọc RPC lúc tải |
| Số dư credit | rpcid trả số credit | mở chỗ Flow hiện số dư |
| Edit Video (`abra_edit`) | rpcid edit + **giao thức upload video** | đưa một video vào Flow rồi edit nó |

Ba mục đầu là những mục duy nhất còn khiến người dùng thấy `unsupported_on_batch_*` trên đường chạy
thường. Hai mục cuối chặn P9c.

---

## Phụ lục — vì sao không có listener `webRequest` cố định

Đọc `f.req` từ DevTools là việc làm một lần. Cài một listener thường trú để tự bắt payload thì tiện
hơn, và bản này **không làm**: một extension đọc thân request tới Google là đúng thứ mà một extension
không nên có quyền làm, và quyền đó ở lại trong manifest lâu hơn nhu cầu capture rất nhiều. Cách đúng
là dùng DevTools trong lúc capture, rồi ghim kết quả bằng golden test.
