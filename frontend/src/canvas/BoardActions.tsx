import { useEffect, useState } from "react";
import {
  buildArchetypeBoard,
  classifyArchetype,
  clearQueue,
  getQueueStatus,
  listArchetypes,
  openOutputFolder,
  rerunFailed,
  saveBoardAsTemplate,
  setQueuePaused,
  stopBoard,
  type Archetype,
  type QueueStatus,
} from "@/api/client";
import { useBoardStore } from "@/store/board";
import { useCanvasUiStore } from "@/store/canvasUi";
import { usePipelineStore } from "@/store/pipeline";

/** The things you do to a board that are not "run it".
 *
 * They live together because they share one property: each of them is what
 * you reach for when a run went wrong or a board is worth keeping. Scattering
 * them across the canvas would mean hunting for Stop while credits are being
 * spent.
 *
 * Two of them are deliberately narrower than they sound:
 *
 * * **RUN Lỗi** re-runs only the nodes that errored. Running the board again
 *   would re-generate everything that already worked, which on a fifteen-node
 *   board is most of the cost.
 * * **Ẩn preview** hides thumbnails and nothing else. The media ids stay
 *   exactly where they are — this is a view switch, not a clear.
 */
export function BoardActions() {
  const boardId = useBoardStore((s) => s.boardId);
  const boardName = useBoardStore((s) => s.boardName);
  const activeRun = usePipelineStore((s) => s.activeRun);
  const previewsHidden = useCanvasUiStore((s) => s.previewsHidden);
  const togglePreviews = useCanvasUiStore((s) => s.togglePreviews);

  // The seven shapes. `classify` is free and deterministic, so "Tự nhận diện"
  // costs nothing but a keyword match — but its answer can be "ask", and on
  // `ask` nothing is built: a guessed board is one the user takes apart.
  const [shapes, setShapes] = useState<Archetype[]>([]);
  const [shape, setShape] = useState("");
  const [queue, setQueue] = useState<QueueStatus | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // The queue is global, not per-board, and it changes without this
  // component doing anything — so it is polled rather than read once.
  useEffect(() => {
    let live = true;
    const tick = () =>
      getQueueStatus()
        .then((q) => live && setQueue(q))
        .catch(() => {});
    void tick();
    void listArchetypes()
      .then(setShapes)
      .catch(() => setShapes([]));
    const id = window.setInterval(tick, 4000);
    return () => {
      live = false;
      window.clearInterval(id);
    };
  }, []);

  if (boardId === null) return null;

  const running =
    activeRun?.status === "running" || activeRun?.status === "pending";

  async function act(key: string, fn: () => Promise<string | null>) {
    setBusy(key);
    setError(null);
    setNote(null);
    try {
      setNote(await fn());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Không thực hiện được.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="board-actions" aria-label="Thao tác board">
      <button
        type="button"
        className="board-actions__btn"
        disabled={busy !== null || running}
        title="Chạy lại CHỈ những node đang lỗi — giữ nguyên mọi kết quả đã xong"
        onClick={() =>
          void act("rerun", async () => {
            if (boardId === null) return null;
            const plan = await rerunFailed(boardId);
            await usePipelineStore.getState().startRun(plan.id);
            return "Đang chạy lại các node lỗi.";
          })
        }
      >
        ⚠ RUN Lỗi
      </button>

      <button
        type="button"
        className="board-actions__btn board-actions__btn--danger"
        disabled={busy !== null}
        title="Dừng run, huỷ mọi request đang chờ. Request đang chạy dở thì để yên — đã trả tiền rồi."
        onClick={() =>
          void act("stop", async () => {
            if (boardId === null) return null;
            const out = await stopBoard(boardId);
            // `refreshBoardState`, not `switchBoard`: switching to the board
            // you are already on returns immediately, so the nodes kept
            // showing as running until something else happened to reload them.
            await useBoardStore.getState().refreshBoardState();
            const released = out.nodesReleased ?? 0;
            return (
              `Đã dừng ${out.runsStopped} run, huỷ ${out.requestsCancelled} ` +
              `request đang chờ` +
              (released > 0 ? `, mở ${released} node để chạy lại.` : ".")
            );
          })
        }
      >
        ■ Dừng toàn bộ
      </button>

      {shapes.length > 0 && (
        <span className="board-actions__shape">
          <select
            value={shape}
            onChange={(e) => setShape(e.target.value)}
            title="Dựng sẵn sơ đồ của một dạng board. Không sinh prompt, không tốn credit."
          >
            <option value="">Tự nhận diện…</option>
            {shapes.map((s) => (
              <option key={s.key} value={s.key} title={s.chain}>
                {s.label}
              </option>
            ))}
          </select>
          <button
            type="button"
            className="board-actions__btn"
            disabled={busy !== null || running}
            title="Đặt node + dây của dạng đã chọn. Node sinh để trống — chưa có prompt thì chưa chạy được, nên không tốn tiền."
            onClick={() =>
              void act("shape", async () => {
                if (boardId === null) return null;
                let key = shape;
                let why = "";
                if (!key) {
                  const brief = window.prompt(
                    "Mô tả ngắn video bạn muốn làm (để tự nhận diện dạng):",
                    "",
                  );
                  if (brief === null || !brief.trim()) return null;
                  const choice = await classifyArchetype({ brief: brief.trim() });
                  if (!choice.archetype) {
                    // `ask`: two or three fit. Naming them is more use than
                    // building one of them and hoping.
                    const names = choice.options.map((o) => o.label).join(" · ");
                    return `Chưa rõ dạng nào — chọn tay: ${names}`;
                  }
                  key = choice.archetype.key;
                  why = choice.explanation ? ` (${choice.explanation})` : "";
                }
                const out = await buildArchetypeBoard(boardId, {
                  key,
                  scene_count: 3,
                });
                await useBoardStore.getState().refreshBoardState();
                return `Đã dựng ${out.nodes} node, ${out.edges} dây${why}.`;
              })
            }
          >
            ✨ Dựng dạng
          </button>
        </span>
      )}

      <button
        type="button"
        className="board-actions__btn"
        disabled={busy !== null}
        title="Lưu sơ đồ hiện tại thành mẫu dùng lại được"
        onClick={() =>
          void act("save", async () => {
            if (boardId === null) return null;
            const name = window.prompt("Tên mẫu:", boardName || "Mẫu của tôi");
            if (name === null || !name.trim()) return null;
            const saved = await saveBoardAsTemplate(boardId, name.trim());
            return `Đã lưu mẫu “${saved.name}” (${saved.stepCount} node).`;
          })
        }
      >
        ★ Lưu thành mẫu
      </button>

      <button
        type="button"
        className="board-actions__btn"
        disabled={busy !== null}
        title="Mở thư mục lưu media trong File Explorer"
        onClick={() =>
          void act("folder", async () => {
            const out = await openOutputFolder();
            return `Đã mở ${out.path}`;
          })
        }
      >
        📁 Thư mục kết quả
      </button>

      <button
        type="button"
        className={`board-actions__btn${previewsHidden ? " is-on" : ""}`}
        title="Ẩn ảnh xem trước cho board nhẹ hơn. Kết quả vẫn còn nguyên."
        onClick={togglePreviews}
      >
        {previewsHidden ? "👁 Hiện preview" : "👁 Ẩn preview"}
      </button>

      {queue && (queue.queued > 0 || queue.paused) && (
        <span className="board-actions__queue">
          <span className="board-actions__count">
            hàng đợi: {queue.queued}
            {queue.running > 0 && ` (+${queue.running} đang chạy)`}
          </span>
          <button
            type="button"
            className={`board-actions__btn${queue.paused ? " is-on" : ""}`}
            disabled={busy !== null}
            title="Tạm dừng nhận việc mới. Việc đang chạy vẫn chạy nốt."
            onClick={() =>
              void act("pause", async () => {
                const out = await setQueuePaused(!queue.paused);
                setQueue({ ...queue, paused: out.paused });
                return out.paused ? "Đã tạm dừng hàng đợi." : "Đã chạy tiếp.";
              })
            }
          >
            {queue.paused ? "▶ Chạy tiếp" : "⏸ Tạm dừng"}
          </button>
          <button
            type="button"
            className="board-actions__btn"
            disabled={busy !== null || queue.queued === 0}
            title="Huỷ mọi request đang chờ. Việc đang chạy dở không bị huỷ."
            onClick={() =>
              void act("clear", async () => {
                if (!window.confirm(`Huỷ ${queue.queued} request đang chờ?`)) {
                  return null;
                }
                const out = await clearQueue();
                setQueue({ ...queue, queued: 0 });
                return `Đã huỷ ${out.cancelled} request.`;
              })
            }
          >
            ✕ Xoá hàng đợi
          </button>
        </span>
      )}

      {note && <span className="board-actions__note">{note}</span>}
      {error && <span className="board-actions__error">{error}</span>}
    </div>
  );
}
