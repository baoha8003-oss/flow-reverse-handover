import { useEffect, useRef } from "react";
import { ReactFlowProvider } from "@xyflow/react";
import { Board } from "@/canvas/Board";
import { AddNodePalette } from "@/canvas/AddNodePalette";
import { RunBoardButton } from "@/canvas/RunBoardButton";
import { BoardActions } from "@/canvas/BoardActions";
import { Toolbar } from "@/components/Toolbar";
import { ProjectSidebar } from "@/components/ProjectSidebar";
import { ReferencesPanel } from "@/components/ReferencesPanel";
import { CanvasAgentPanel } from "@/components/CanvasAgentPanel";
import { useBoardStore } from "@/store/board";
import { useReferencesStore } from "@/store/references";

/** The existing React Flow canvas, unchanged, hosted under the WORKFLOW
 * pill. It keeps its own chrome (board sidebar, toolbar, references panel).
 * In P8 this becomes the fully-featured node editor; for now it preserves
 * everything the app could already do. Board/reference state is loaded
 * lazily the first time WORKFLOW is opened. */
export function WorkflowCanvasView() {
  const loadInitialBoard = useBoardStore((s) => s.loadInitialBoard);
  const loadReferences = useReferencesStore((s) => s.load);
  const loading = useBoardStore((s) => s.loading);
  const boardId = useBoardStore((s) => s.boardId);
  const ran = useRef(false);

  useEffect(() => {
    if (ran.current) return;
    ran.current = true;
    void loadInitialBoard();
    void loadReferences();
  }, [loadInitialBoard, loadReferences]);

  return (
    <div className="app min-h-0 flex-1">
      <ProjectSidebar />
      <ReactFlowProvider>
        <div className="canvas-wrap">
          <Toolbar />
          {loading && boardId === null ? (
            <div className="canvas-loading">Loading board…</div>
          ) : (
            <>
              <Board />
              <AddNodePalette />
              <RunBoardButton />
              <BoardActions />
            </>
          )}
          <ReferencesPanel />
          <CanvasAgentPanel />
        </div>
      </ReactFlowProvider>
    </div>
  );
}
