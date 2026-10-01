import { create } from "zustand";
import { createRequest, getRequest, patchNode } from "../api/client";
import { useBoardStore } from "./board";
import { useGenerationStore } from "./generation";
import {
  extendLanding,
  extendReadiness,
  upscaleLanding,
  upscaleReadiness,
  type ClipOpNodeState,
} from "../lib/clipOps";
import { errorLabel } from "../lib/errorLabels";

/**
 * Running an operation on a clip that already rendered: upscale, or extend.
 *
 * Deliberately NOT part of `dispatchGeneration`. That flow settles its result by
 * writing `mediaId` / `mediaIds`, which is correct for a generation and wrong
 * here: the original clip is the thing that was paid for. These land on their own
 * keys, through `lib/clipOps`, where the rule is tested.
 *
 * One operation per node at a time. Two extensions racing would both read the
 * same `position` and the same previous operation id, and the second would be a
 * billed render of a link that already exists.
 */

type Kind = "upscale" | "extend";

interface Running {
  kind: Kind;
  requestId: number;
  timerId: ReturnType<typeof setTimeout> | null;
}

interface ClipOpsState {
  running: Record<string, Running>;
  /** Per-node message, shown on the card. Refusals land here too. */
  notes: Record<string, string | null>;

  upscaleClip(rfId: string): Promise<void>;
  extendClip(rfId: string, prompt: string, opts?: { paidLane?: boolean }): Promise<void>;
  clearNote(rfId: string): void;
}

const POLL_MS = 3000;
/** Same shape as the generation poller's ceiling: long renders are normal. */
const MAX_POLLS = 400;

function nodeData(rfId: string): ClipOpNodeState | null {
  const node = useBoardStore.getState().nodes.find((n) => n.id === rfId);
  return node ? (node.data as ClipOpNodeState) : null;
}

export const useClipOpsStore = create<ClipOpsState>((set, get) => {
  function note(rfId: string, message: string | null) {
    set((s) => ({ notes: { ...s.notes, [rfId]: message } }));
  }

  function stop(rfId: string) {
    const entry = get().running[rfId];
    if (entry?.timerId) clearTimeout(entry.timerId);
    set((s) => {
      const next = { ...s.running };
      delete next[rfId];
      return { running: next };
    });
  }

  /** Merge deltas into the node in memory AND persist them.
   *
   * Persisting matters more here than for a generation: the extend chain reads
   * `extensionOperationIds` to know what the next link references. If that only
   * lived in memory, a reload would restart the chain from the clone — a billed
   * render of a link the user already has. */
  function land(rfId: string, patch: Record<string, unknown>) {
    if (Object.keys(patch).length === 0) return;
    useBoardStore.getState().updateNodeData(rfId, patch);
    const dbId = parseInt(rfId, 10);
    if (!isNaN(dbId)) {
      patchNode(dbId, { data: patch }).catch(() => {
        // In-memory state is still right for this session; the note already
        // told the user what happened to the render itself.
      });
    }
  }

  function poll(rfId: string, kind: Kind, requestId: number, rounds: number) {
    const timerId = setTimeout(() => {
      void (async () => {
        if (get().running[rfId]?.requestId !== requestId) return;
        let req;
        try {
          req = await getRequest(requestId);
        } catch {
          // A network blip is not a failed render. Keep waiting; the round
          // budget is what ends this.
          if (rounds + 1 >= MAX_POLLS) {
            note(rfId, "Mất liên lạc với agent khi đang chờ kết quả.");
            stop(rfId);
            return;
          }
          poll(rfId, kind, requestId, rounds + 1);
          return;
        }
        if (req.status === "queued" || req.status === "running") {
          if (rounds + 1 >= MAX_POLLS) {
            note(rfId, "Chờ quá lâu — xem lại trong Flow.");
            stop(rfId);
            return;
          }
          poll(rfId, kind, requestId, rounds + 1);
          return;
        }
        const result = (req.result ?? {}) as Record<string, unknown>;
        // Even a failed extend can carry a scene that now exists on Flow, so the
        // landing runs before the error is reported. Forgetting the scene would
        // make the next attempt create a second one and restart the chain.
        const landing =
          kind === "upscale"
            ? upscaleLanding(result)
            : extendLanding(nodeData(rfId) ?? {}, result);
        land(rfId, landing.patch);
        if (req.status === "done" && !landing.error) {
          note(rfId, kind === "upscale" ? "Đã có bản 1080p." : "Đã nối thêm một đoạn.");
        } else {
          const why = req.error ? errorLabel(req.error) : landing.error;
          note(rfId, why ?? "Không hoàn tất.");
        }
        stop(rfId);
      })();
    }, POLL_MS);
    set((s) => ({ running: { ...s.running, [rfId]: { kind, requestId, timerId } } }));
  }

  async function start(
    rfId: string,
    kind: Kind,
    params: Record<string, unknown>,
  ): Promise<void> {
    const dbId = parseInt(rfId, 10);
    if (isNaN(dbId)) {
      note(rfId, "Node này chưa được lưu — bấm lưu board rồi thử lại.");
      return;
    }
    note(rfId, kind === "upscale" ? "Đang nâng cấp…" : "Đang nối…");
    set((s) => ({
      running: { ...s.running, [rfId]: { kind, requestId: -1, timerId: null } },
    }));
    try {
      const req = await createRequest({
        type: kind === "upscale" ? "upscale_video" : "extend_video",
        node_id: dbId,
        params,
      });
      poll(rfId, kind, req.id, 0);
    } catch (e) {
      note(rfId, e instanceof Error ? e.message : "Không gửi được yêu cầu.");
      stop(rfId);
    }
  }

  return {
    running: {},
    notes: {},

    clearNote(rfId) {
      note(rfId, null);
    },

    async upscaleClip(rfId) {
      if (get().running[rfId]) return;
      const data = nodeData(rfId);
      if (!data) return;
      const ready = upscaleReadiness(data);
      if (!ready.ok) {
        note(rfId, ready.reason);
        return;
      }
      const projectId = await useGenerationStore.getState().ensureProjectId();
      if (!projectId) {
        note(rfId, "Board chưa có project Flow.");
        return;
      }
      await start(rfId, "upscale", {
        operation_id: ready.operationId,
        media_id: ready.mediaId,
        project_id: projectId,
        aspect_ratio: data.aspectRatio,
        resolution: "1080p",
      });
    },

    async extendClip(rfId, prompt, opts) {
      if (get().running[rfId]) return;
      const data = nodeData(rfId);
      if (!data) return;
      const ready = extendReadiness(data);
      if (!ready.ok) {
        note(rfId, ready.reason);
        return;
      }
      const projectId = await useGenerationStore.getState().ensureProjectId();
      if (!projectId) {
        note(rfId, "Board chưa có project Flow.");
        return;
      }
      await start(rfId, "extend", {
        prompt,
        project_id: projectId,
        media_id: ready.mediaId,
        scene_id: ready.sceneId,
        scene_clone_media_id: ready.sceneCloneMediaId,
        previous_operation_id: ready.previousOperationId,
        position: ready.position,
        source_model_key: ready.sourceModelKey,
        aspect_ratio: data.aspectRatio,
        // Only ever true when the caller said so. Nothing infers it.
        paid_lane: opts?.paidLane === true,
      });
    },
  };
});
