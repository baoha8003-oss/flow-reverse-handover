import { create } from "zustand";
import {
  applyAgentActions,
  getAgentHistory,
  undoAgentAction,
  type AgentApplyResult,
  type AgentHistoryEntry,
} from "../api/client";
import { planView, type AgentPlanView } from "../lib/agentPlanView";
import { errorLabel } from "../lib/errorLabels";

/** State for the canvas agent panel.
 *
 * Deliberately has no "run" action. `apply` can come back with a run *intent* —
 * a node id list — and the panel hands that to the existing cost dialog. A
 * `startRun` in here would make the agent able to charge the user, which is the
 * one thing this whole surface is shaped to prevent.
 */
interface AgentState {
  boardId: number | null;
  /** The last applied proposal, as the diff card reads it. */
  view: AgentPlanView | null;
  history: AgentHistoryEntry[];
  busy: boolean;
  /** Already translated — the panel renders it as-is. */
  error: string | null;
  /** True when the board has a run in flight, so Apply and Undo are disabled
   *  rather than failing with a 409 the user has to interpret. */
  locked: boolean;

  attach(boardId: number): Promise<void>;
  apply(
    actions: { kind: string; payload: Record<string, unknown> }[],
    provider?: string | null
  ): Promise<AgentApplyResult | null>;
  undo(): Promise<boolean>;
  discard(): void;
  clearError(): void;
}

/** A 409 from apply/undo means "the board is busy", which is a state the panel
 *  can show rather than an error the user has to read twice. */
function isConflict(err: unknown): boolean {
  return err instanceof Error && err.message.startsWith("409");
}

function label(err: unknown): string {
  const raw = err instanceof Error ? err.message : String(err);
  // The backend sends `code: message`; the code is what `errorLabels` knows and
  // the message is already Vietnamese, so prefer the message when there is one.
  const [, tail] = raw.split(/:\s(.+)/s);
  return tail?.trim() || errorLabel(raw) || raw;
}

export const useAgentStore = create<AgentState>((set, get) => ({
  boardId: null,
  view: null,
  history: [],
  busy: false,
  error: null,
  locked: false,

  async attach(boardId: number) {
    // Switching boards must drop the previous board's proposal: an Undo button
    // wired to another board's newest action is the worst kind of working.
    set({ boardId, view: null, history: [], error: null, locked: false });
    try {
      const body = await getAgentHistory(boardId);
      if (get().boardId === boardId) set({ history: body.actions });
    } catch {
      // History is context, not function. Losing it must not break the panel.
    }
  },

  async apply(actions, provider) {
    const boardId = get().boardId;
    if (boardId === null || get().busy) return null;
    set({ busy: true, error: null });
    try {
      const result = await applyAgentActions(boardId, actions, provider);
      set({ view: planView(result), locked: false });
      const body = await getAgentHistory(boardId).catch(() => null);
      if (body && get().boardId === boardId) set({ history: body.actions });
      return result;
    } catch (err) {
      set({
        error: label(err),
        locked: isConflict(err),
      });
      return null;
    } finally {
      set({ busy: false });
    }
  },

  async undo() {
    const boardId = get().boardId;
    if (boardId === null || get().busy) return false;
    set({ busy: true, error: null });
    try {
      await undoAgentAction(boardId);
      const body = await getAgentHistory(boardId).catch(() => null);
      if (body && get().boardId === boardId) set({ history: body.actions });
      // The proposal is gone from the board, so it must go from the card too.
      set({ view: null });
      return true;
    } catch (err) {
      set({ error: label(err), locked: isConflict(err) });
      return false;
    } finally {
      set({ busy: false });
    }
  },

  discard() {
    // Card only. The board keeps what was applied — "Discard" dismisses the
    // summary, and Undo is the button that changes the board. Conflating them
    // would make a dismissal quietly delete nodes.
    set({ view: null, error: null });
  },

  clearError() {
    set({ error: null });
  },
}));
