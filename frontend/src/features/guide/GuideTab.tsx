import { useEffect, useRef, useState } from "react";
import { Card, GhostButton } from "@/ui/primitives";
import {
  deleteTemplate,
  exportTemplate,
  importTemplate,
  importTemplateJson,
  listTemplates,
  renameTemplate,
  replaceTemplate,
  readTemplate,
  type TemplateDetail,
  type TemplateImportResult,
  type TemplateSummary,
} from "@/api/client";
import { useBoardStore } from "@/store/board";
import { useShellStore } from "@/store/shell";

/** Hướng dẫn — how THIS build works.
 *
 * Deliberately not a transcription of the packaged tool's guide: several of
 * its steps (auto-login, license, Telegram) do not exist here, and a guide
 * that describes features the app lacks is worse than none. What is written
 * here is limited to what the tabs actually do today.
 */

function Step({ n, title, children }: { n: string; title: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-3">
      <span className="mt-[2px] flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-grape text-[12px] font-bold text-white">
        {n}
      </span>
      <div className="min-w-0">
        <p className="text-[13px] font-semibold text-ink">{title}</p>
        <div className="mt-1 text-[12px] leading-relaxed text-ink-mute">
          {children}
        </div>
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Card>
      <h2 className="text-[13px] font-extrabold text-ink">{title}</h2>
      <div className="mt-3 flex flex-col gap-3">{children}</div>
    </Card>
  );
}

/** The packaged tool's 9 sample workflows, as followable recipes.
 *
 * Two ways to use one: read it as a recipe (which tab does each step, with
 * the prompt text ready to copy), or press "Nạp vào canvas" to build the
 * whole graph as a board and run it there.
 */
function TemplateLibrary() {
  const [list, setList] = useState<TemplateSummary[] | null>(null);
  const [open, setOpen] = useState<TemplateDetail | null>(null);
  const [loading, setLoading] = useState<string | null>(null);
  const [copied, setCopied] = useState<number | null>(null);
  const [importing, setImporting] = useState<string | null>(null);
  const [imported, setImported] = useState<TemplateImportResult | null>(null);
  const [importError, setImportError] = useState<string | null>(null);
  const [busyFile, setBusyFile] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  async function refresh() {
    try {
      setList(await listTemplates());
    } catch {
      // Keep whatever is on screen; the actions below report their own
      // failures and a blanked list would hide them.
    }
  }

  /** Overwrite a saved template from the board that is open.
   *
   * `PUT /api/templates/mine/{file}` and `client.replaceTemplate` both existed and
   * nothing called either — grepping `replaceTemplate` found only the declaration.
   * So editing a template meant saving again and living with `name (2).json`.
   *
   * Confirmed by name rather than a bare yes/no: this discards whatever the
   * template held and there is no undo.
   */
  async function overwrite(file: string, name: string) {
    const boardId = useBoardStore.getState().boardId;
    if (boardId === null) {
      setImportError("Mở một board trước — mẫu được ghi từ board đang xem.");
      return;
    }
    if (
      !window.confirm(`Ghi đè mẫu "${name}" bằng board đang mở? Không hoàn lại được.`)
    ) {
      return;
    }
    setBusyFile(file);
    setImportError(null);
    try {
      await replaceTemplate(file, boardId);
      await refresh();
    } catch (e) {
      setImportError(e instanceof Error ? e.message : "Không ghi đè được.");
    } finally {
      setBusyFile(null);
    }
  }

  /** Rename a personal template. Only ever called for `writable` ones —
   *  the packaged samples belong to the exe. */
  async function rename(file: string, current: string) {
    const next = window.prompt("Tên mới cho mẫu:", current);
    if (next === null || !next.trim() || next === current) return;
    setBusyFile(file);
    setImportError(null);
    try {
      await renameTemplate(file, { name: next.trim() });
      await refresh();
    } catch (e) {
      setImportError(e instanceof Error ? e.message : "Không đổi tên được.");
    } finally {
      setBusyFile(null);
    }
  }

  async function remove(file: string, name: string) {
    // Deleting a template is not undoable from here, and the list mixes
    // personal templates with files the exe owns — so the name goes in the
    // question rather than a bare "are you sure".
    if (!window.confirm(`Xoá mẫu “${name}”? Không khôi phục lại được.`)) return;
    setBusyFile(file);
    setImportError(null);
    try {
      await deleteTemplate(file);
      if (open?.file === file) setOpen(null);
      await refresh();
    } catch (e) {
      setImportError(e instanceof Error ? e.message : "Không xoá được.");
    } finally {
      setBusyFile(null);
    }
  }

  /** Write the template to a file the user picks. Works for every source:
   *  exporting a packaged sample is reading, which was never restricted. */
  async function download(file: string, name: string) {
    setBusyFile(file);
    setImportError(null);
    try {
      const doc = await exportTemplate(file);
      const blob = new Blob([JSON.stringify(doc, null, 1)], {
        type: "application/json",
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = file.endsWith(".json") ? file : `${name}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setImportError(e instanceof Error ? e.message : "Không xuất được.");
    } finally {
      setBusyFile(null);
    }
  }

  async function importFromFile(f: File) {
    setImportError(null);
    try {
      const doc = JSON.parse(await f.text()) as Record<string, unknown>;
      const saved = await importTemplateJson(doc, f.name.replace(/\.json$/i, ""));
      await refresh();
      setImportError(null);
      await show(saved.file);
    } catch (e) {
      setImportError(
        e instanceof SyntaxError
          ? "File không phải JSON hợp lệ."
          : e instanceof Error
            ? e.message
            : "Không nhập được file này.",
      );
    }
  }
  const switchBoard = useBoardStore((s) => s.switchBoard);
  const refreshBoardList = useBoardStore((s) => s.refreshBoardList);
  const setPill = useShellStore((s) => s.setPill);

  useEffect(() => {
    void listTemplates()
      .then(setList)
      .catch(() => setList([]));
  }, []);

  async function show(file: string) {
    setLoading(file);
    try {
      setOpen(await readTemplate(file));
    } catch {
      setOpen(null);
    } finally {
      setLoading(null);
    }
  }

  /** Build the board, then go stand on it.
   *
   * Importing and then leaving the user on this tab would make it look like
   * nothing happened — the board exists but is two clicks away. */
  async function loadToCanvas(file: string) {
    setImporting(file);
    setImportError(null);
    try {
      const result = await importTemplate(file);
      setImported(result);
      await refreshBoardList();
      await switchBoard(result.boardId);
      setPill("workflow");
    } catch (e) {
      setImportError(
        e instanceof Error ? e.message : "Không nạp được kịch bản này.",
      );
    } finally {
      setImporting(null);
    }
  }

  async function copy(text: string, order: number) {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(order);
      setTimeout(() => setCopied(null), 1500);
    } catch {
      // Clipboard blocked — the text is on screen and selectable.
    }
  }

  if (list === null) {
    return (
      <Card>
        <p className="text-[13px] text-ink-mute">Đang tải kịch bản mẫu…</p>
      </Card>
    );
  }
  // No early return on an empty list.
  //
  // This used to be `if (list.length === 0) return null`, which hid the whole
  // card — and the "⬆ Nhập file JSON" button below is the ONLY import control in
  // the app. On a machine without `FLOWBOARD_ASSET_ROOT` and with nothing saved
  // yet, the list is empty, so the first import was impossible: the way in
  // disappeared precisely when it was the only thing needed.
  const empty = list.length === 0;

  return (
    <Card>
      <h2 className="text-[13px] font-extrabold text-ink">
        📚 Kịch bản mẫu ({list.length})
      </h2>
      <p className="mt-1 text-[12px] text-ink-dim">
        {empty
          ? "Chưa có mẫu nào. Nhập một file JSON — bản xuất từ app này, hoặc file workflow của tool gốc."
          : "Lấy từ bản đóng gói. Bấm để xem từng bước làm ở tab nào và chép prompt có sẵn, hoặc nạp cả sơ đồ vào canvas rồi chạy ở đó."}
      </p>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => fileInputRef.current?.click()}
          className="rounded-(--radius-ctl) border border-line px-2 py-1 text-[11px] text-ink hover:bg-elevated"
          title="Nhận file xuất từ app này hoặc file workflow của tool gốc"
        >
          ⬆ Nhập file JSON
        </button>
        <input
          ref={fileInputRef}
          type="file"
          accept="application/json,.json"
          style={{ display: "none" }}
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) void importFromFile(f);
            e.target.value = "";
          }}
        />
      </div>
      {importError && (
        <p className="mt-2 text-[12px] text-danger">{importError}</p>
      )}
      {imported && (
        <p className="mt-2 text-[12px] text-ink-mute">
          Đã nạp “{imported.name}”: {imported.nodes}/{imported.nodesInFile} node,{" "}
          {imported.edges}/{imported.edgesInFile} liên kết.
          {imported.unsupported.length > 0 && (
            <>
              {" "}Bước chưa có trong bản này ({imported.unsupported.join(", ")})
              được giữ lại dưới dạng ghi chú để làm tay.
            </>
          )}
        </p>
      )}
      <div className="mt-3 flex flex-wrap gap-2">
        {list.map((t) => (
          <div
            key={t.file}
            className={`flex flex-col gap-1 rounded-(--radius-ctl) border p-2 text-[12px] ${
              open?.file === t.file
                ? "border-primary bg-grape text-white"
                : "border-line bg-card text-ink"
            }`}
          >
            <button
              type="button"
              onClick={() => void show(t.file)}
              className="text-left hover:underline"
            >
              <div className="font-semibold">{t.name}</div>
              <div className="opacity-70">
                {loading === t.file ? "đang mở…" : `${t.stepCount} bước`}
              </div>
            </button>
            <button
              type="button"
              onClick={() => void loadToCanvas(t.file)}
              disabled={importing !== null}
              title="Dựng sơ đồ này thành board — miễn phí, chưa chạy gì cả"
              className="rounded-(--radius-ctl) border border-line px-2 py-1 text-[11px] hover:bg-elevated disabled:opacity-50"
            >
              {importing === t.file ? "đang nạp…" : "⬇ Nạp vào canvas"}
            </button>
            <div className="flex items-center gap-1 text-[11px] opacity-80">
              <button
                type="button"
                onClick={() => void download(t.file, t.name)}
                disabled={busyFile !== null}
                title="Xuất ra file JSON"
                className="rounded-(--radius-ctl) border border-line px-2 py-[2px] hover:bg-elevated disabled:opacity-50"
              >
                ⤓
              </button>
              {/* Rename and delete only where this app owns the file. The
                  packaged samples and the exe's Workflows folder come
                  through the same list, and there is no undo for those. */}
              {t.writable && (
                <>
                  <button
                    type="button"
                    onClick={() => void rename(t.file, t.name)}
                    disabled={busyFile !== null}
                    title="Đổi tên mẫu"
                    className="rounded-(--radius-ctl) border border-line px-2 py-[2px] hover:bg-elevated disabled:opacity-50"
                  >
                    ✎
                  </button>
                  <button
                    type="button"
                    onClick={() => void overwrite(t.file, t.name)}
                    disabled={busyFile !== null}
                    title="Ghi đè mẫu này bằng board đang mở"
                    className="rounded-(--radius-ctl) border border-line px-2 py-[2px] hover:bg-elevated disabled:opacity-50"
                  >
                    ⟳
                  </button>
                  <button
                    type="button"
                    onClick={() => void remove(t.file, t.name)}
                    disabled={busyFile !== null}
                    title="Xoá mẫu"
                    className="rounded-(--radius-ctl) border border-line px-2 py-[2px] hover:bg-elevated disabled:opacity-50"
                  >
                    🗑
                  </button>
                </>
              )}
              {!t.writable && (
                <span title="Thuộc về tool gốc — chỉ đọc" className="px-1">
                  🔒
                </span>
              )}
            </div>
          </div>
        ))}
      </div>

      {open && (
        <div className="mt-4 border-t border-line pt-4">
          <div className="flex flex-wrap items-center gap-3">
            <span className="text-[13px] font-semibold text-ink">
              {open.name}
            </span>
            <GhostButton onClick={() => setOpen(null)}>Đóng</GhostButton>
          </div>
          {open.description && (
            <p className="mt-1 text-[12px] text-ink-mute">{open.description}</p>
          )}
          {open.unmappedTypes.length > 0 && (
            <p className="mt-2 text-[12px] text-warn">
              ⚠ Bước chưa có trong bản này: {open.unmappedTypes.join(", ")}
            </p>
          )}
          <ol className="mt-3 flex flex-col gap-2">
            {open.steps.map((s) => (
              <li key={s.order} className="flex gap-3">
                <span className="mt-[2px] flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-grape text-[11px] font-bold text-white">
                  {s.order}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="text-[12px] text-ink">
                    <b>{s.label}</b>{" "}
                    <span className="text-ink-dim">({s.type})</span>
                  </div>
                  <div className="text-[12px] text-ink-mute">→ {s.doneWith}</div>
                  {s.prompt && (
                    <div className="mt-1">
                      <GhostButton onClick={() => void copy(s.prompt!, s.order)}>
                        {copied === s.order ? "✓ Đã copy" : "📋 Copy prompt"}
                      </GhostButton>
                      <pre className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap rounded-(--radius-ctl) border border-line bg-card p-2 text-[11px] text-ink-mute">
                        {s.prompt}
                      </pre>
                    </div>
                  )}
                </div>
              </li>
            ))}
          </ol>
        </div>
      )}
    </Card>
  );
}

export function GuideTab() {
  return (
    <div className="flex flex-col gap-4 pb-8">
      <TemplateLibrary />
      <Section title="🚀 Chuẩn bị (làm 1 lần)">
        <Step n="1" title="Cài extension">
          Mở <code>chrome://extensions</code> → bật <b>Developer mode</b> →{" "}
          <b>Load unpacked</b> → chọn thư mục <code>extension</code> trong thư
          mục cài đặt.
        </Step>
        <Step n="2" title="Đăng nhập Google Flow">
          Mở <code>labs.google/fx/tools/flow</code> và đăng nhập tài khoản có
          gói Veo. Cứ để tab đó mở — extension lấy token từ đây.
        </Step>
        <Step n="3" title="Kiểm tra kết nối">
          Thanh trạng thái dưới cùng phải báo đã kết nối. Nếu chưa, tải lại tab
          Flow rồi tải lại trang này.
        </Step>
      </Section>

      <Section title="🎬 Tạo video">
        <Step n="1" title="Text to Video">
          Mỗi dòng là một prompt, mỗi prompt ra một video. Chọn tỉ lệ và model
          rồi bấm tạo. Thời lượng khoá ở 8s vì tài khoản Pro bị từ chối ở 4s/6s.
        </Step>
        <Step n="2" title="Image to Video">
          Mỗi dòng gồm một ảnh và một prompt. Ảnh lấy từ máy, từ link, từ thư
          viện hoặc từ ảnh vừa tạo trong app.
        </Step>
        <Step n="3" title="Video Start-End">
          Chọn ảnh đầu và ảnh cuối cho mỗi dòng, tool dựng chuyển động ở giữa.
          Model và thời lượng bị khoá vì họ model này chỉ có một lựa chọn.
        </Step>
      </Section>

      <Section title="🎨 Tạo ảnh">
        <Step n="1" title="Text to Image">
          Mỗi dòng một prompt. Có thể chọn số ảnh cho mỗi prompt.
        </Step>
        <Step n="2" title="Image to Image">
          Thêm ảnh tham chiếu và đặt tên cho từng ảnh, rồi gọi đúng tên đó
          trong prompt. Tên được ghép vào prompt để model phân biệt được các
          ảnh với nhau.
        </Step>
      </Section>

      <Section title="✂️ Sau khi có video">
        <Step n="1" title="Cut & Merge Video">
          Ghép nhiều clip, khắc phụ đề, chèn logo, trộn nhạc nền, lồng tiếng.
          Tất cả chạy bằng ffmpeg trên máy — <b>không tốn credit</b>. Kết quả
          mỗi bước dùng lại được làm đầu vào cho bước sau.
        </Step>
        <Step n="2" title="Upscale">
          Nâng nét ảnh hoặc video bằng RealESRGAN chạy offline. Video giữ
          nguyên tiếng gốc. Clip dài hơn 5 phút bị từ chối vì mỗi khung hình
          phải ghi ra đĩa trước.
        </Step>
      </Section>

      <Section title="📊 Theo dõi & cài đặt">
        <Step n="1" title="NHẬT KÝ">
          Xem mọi job, lọc theo loại, bấm vào dòng để xem chi tiết, và dừng job
          đang chạy.
        </Step>
        <Step n="2" title="Settings">
          Đổi model/tỉ lệ mặc định, số job chạy song song, và thư mục lưu media.
          Mỗi video xong được lưu 2 nơi: trong app để xem ngay, và ra thư mục
          bạn chọn theo tên dự án.
        </Step>
      </Section>

      <Section title="⚠️ Cần biết">
        <Step n="!" title="Credit bị trừ ngay khi gửi">
          Flow trừ credit lúc nhận yêu cầu, không phải lúc video xong. Vì vậy
          tool <b>không bao giờ tự gửi lại</b> một yêu cầu đã tạo được job —
          gửi lại là mất tiền lần hai.
        </Step>
        <Step n="!" title="Làn 0 credit chỉ có ở gói Ultra">
          Chọn “Lower Priority” trên gói Pro vẫn bị tính tiền. Khi điều đó xảy
          ra, tab sẽ hiện dòng cảnh báo nói rõ model nào đã thực sự chạy.
        </Step>
        <Step n="!" title="Xử lý hậu kỳ chạy đồng bộ">
          Ghép/upscale chạy thẳng trong một request, clip dài có thể mất vài
          phút và chưa có thanh tiến trình. Cứ để tab mở tới khi xong.
        </Step>
      </Section>
    </div>
  );
}
