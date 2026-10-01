import { useEffect, useState } from "react";
import { ensureBoardPlan } from "@/api/client";
import { useAgentStore } from "@/store/agent";
import { useBoardStore } from "@/store/board";
import { usePipelineStore } from "@/store/pipeline";
import { RunCostDialog } from "./RunCostDialog";
import { ChatSidebar } from "./ChatSidebar";

/** The canvas agent's panel: the chat, plus what its last proposal did.
 *
 * `ChatSidebar` shipped complete and unmounted — nothing imported it — so this
 * is the frame that puts it on screen rather than a second chat.
 *
 * The one rule the layout enforces: **the agent never charges anybody.** A run
 * it suggests appears as an offer with a "Chạy" button that opens the existing
 * cost dialog, the same one the Run button uses. There is no path from this
 * panel to a dispatch that skips the price.
 */

function ProviderBadge({ provider }: { provider: string | null }) {
  if (!provider) return null;
  return (
    <span className="agent-diff__badge" title="AI đã trả lời đề xuất này">
      {provider}
    </span>
  );
}

function AgentDiffCard() {
  const view = useAgentStore((s) => s.view);
  const busy = useAgentStore((s) => s.busy);
  const locked = useAgentStore((s) => s.locked);
  const error = useAgentStore((s) => s.error);
  const undo = useAgentStore((s) => s.undo);
  const discard = useAgentStore((s) => s.discard);
  const refresh = useBoardStore((s) => s.refreshBoardState);
  const boardId = useBoardStore((s) => s.boardId);
  const boardName = useBoardStore((s) => s.boardName);
  const startRun = usePipelineStore((s) => s.startRun);
  const [confirming, setConfirming] = useState(false);

  if (!view && !error) return null;

  // The same two steps the Run button takes, with the agent's subset: scope the
  // plan, then start it. Reusing this path rather than adding a second one is
  // what keeps "the agent cannot charge you" true — the price is shown by the
  // same dialog, and the confirm is still the user's click.
  async function runScoped() {
    if (boardId === null || !view) return;
    const plan = await ensureBoardPlan(boardId, view.runNodeIds);
    setConfirming(false);
    await startRun(plan.id);
  }

  return (
    <div className="agent-diff">
      {error && <p className="agent-diff__error">{error}</p>}

      {view && (
        <>
          <header className="agent-diff__head">
            <strong>Đã áp dụng {view.lines.length} thay đổi</strong>
            <ProviderBadge provider={view.provider} />
          </header>

          <ul className="agent-diff__lines">
            {view.lines.map((line, i) => (
              <li key={i}>{line}</li>
            ))}
          </ul>

          {view.errors.length > 0 && (
            <ul className="agent-diff__findings agent-diff__findings--error">
              {view.errors.map((f, i) => (
                <li key={i}>{f.message}</li>
              ))}
            </ul>
          )}
          {view.warnings.length > 0 && (
            <ul className="agent-diff__findings">
              {view.warnings.map((f, i) => (
                <li key={i}>{f.message}</li>
              ))}
            </ul>
          )}

          {view.diagram && (
            // The Mermaid SOURCE, not a rendered diagram: rendering it would
            // mean a new ~3 MB dependency for something decorative. This text
            // is readable as-is and pastes into any Mermaid viewer.
            <details className="agent-diff__diagram">
              <summary>Sơ đồ ({view.lines.length} bước)</summary>
              <pre>{view.diagram}</pre>
            </details>
          )}

          {view.runOffer && (
            <div className="agent-diff__offer">
              <span>{view.runOffer}</span>
              <button
                type="button"
                disabled={busy || locked}
                onClick={() => setConfirming(true)}
              >
                Chạy
              </button>
            </div>
          )}

          <footer className="agent-diff__actions">
            <button type="button" onClick={discard} disabled={busy}>
              Bỏ qua
            </button>
            <button
              type="button"
              disabled={busy || locked}
              title={
                locked
                  ? "Board đang chạy — dừng hoặc đợi xong rồi mới hoàn tác được"
                  : "Hoàn tác thay đổi mới nhất (chỉ cấu trúc)"
              }
              onClick={async () => {
                if (await undo()) await refresh();
              }}
            >
              Hoàn tác
            </button>
          </footer>

          {confirming && boardId !== null && (
            <RunCostDialog
              boardId={boardId}
              boardName={boardName}
              nodeIds={view.runNodeIds}
              onConfirm={() => void runScoped()}
              onCancel={() => setConfirming(false)}
            />
          )}
        </>
      )}
    </div>
  );
}

export function CanvasAgentPanel() {
  const boardId = useBoardStore((s) => s.boardId);
  const attach = useAgentStore((s) => s.attach);

  useEffect(() => {
    if (boardId !== null) void attach(boardId);
  }, [boardId, attach]);

  return (
    <aside className="canvas-agent">
      <ChatSidebar />
      <AgentDiffCard />
    </aside>
  );
}
