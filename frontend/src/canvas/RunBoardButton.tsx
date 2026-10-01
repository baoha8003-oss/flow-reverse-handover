import { useState } from "react";
import { RunCostDialog } from "@/components/RunCostDialog";
import { useBoardStore } from "@/store/board";
import { usePipelineStore } from "@/store/pipeline";
import { ensureBoardPlan } from "@/api/client";

/** Run the whole board, after showing what it will spend.
 *
 * Individual nodes have always had their own Run button. This one exists for
 * imported workflows, where the point is the graph: fifteen nodes run in
 * dependency order, and clicking them one at a time defeats the import.
 *
 * The cost dialog is not optional and not a preference. A board can dispatch
 * dozens of billable calls, and the difference between "I meant to look at
 * this" and "I meant to run this" is one click.
 */
export function RunBoardButton() {
  const boardId = useBoardStore((s) => s.boardId);
  const boardName = useBoardStore((s) => s.boardName);
  const activeRun = usePipelineStore((s) => s.activeRun);
  const startRun = usePipelineStore((s) => s.startRun);

  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);

  if (boardId === null) return null;

  const running =
    activeRun?.status === "running" || activeRun?.status === "pending";

  async function run() {
    if (boardId === null) return;
    setStarting(true);
    setError(null);
    try {
      // The executor takes a plan id, not a board id. Ensure rather than
      // fetch: a hand-built board has no plan at all, and an imported one
      // lists only the nodes that existed at import — a node added since
      // would be skipped without a word.
      const plan = await ensureBoardPlan(boardId);
      setConfirming(false);
      await startRun(plan.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Không chạy được board.");
    } finally {
      setStarting(false);
    }
  }

  return (
    <>
      <button
        type="button"
        className="run-board-btn"
        disabled={running || starting}
        onClick={() => setConfirming(true)}
        title="Chạy toàn bộ board theo thứ tự phụ thuộc"
      >
        {running ? "▮ Đang chạy…" : "▶ Chạy cả board"}
      </button>
      {error && <div className="run-board-error">{error}</div>}
      {confirming && (
        <RunCostDialog
          boardId={boardId}
          boardName={boardName}
          onConfirm={() => void run()}
          onCancel={() => setConfirming(false)}
        />
      )}
    </>
  );
}
