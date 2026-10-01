import { beforeEach, describe, expect, it, vi } from "vitest";
import { errorLabel } from "../lib/errorLabels";

/** Which service the canvas Generate button actually pays.
 *
 * The board run has honoured a node's `imageEngine` since P5; this button did
 * not. A node switched to OpenAI still dispatched `gen_image` and spent Flow
 * credits — the same silent engine substitution the executor explicitly
 * refuses to make, arriving through the one control users press most.
 *
 * These tests assert on the request TYPE that leaves the store, because that
 * is the field that decides whose bill the image lands on.
 */

const api = vi.hoisted(() => ({
  createRequest: vi.fn(),
  getRequest: vi.fn(),
  getAuthMe: vi.fn(),
  ensureBoardProject: vi.fn(),
  patchNode: vi.fn(),
  getMediaUrl: vi.fn(),
}));

vi.mock("@/api/client", () => api);

type NodeStub = {
  id: string;
  data: Record<string, unknown>;
};

const board = vi.hoisted(() => ({
  nodes: [] as NodeStub[],
  edges: [] as unknown[],
  boardId: 1,
  patches: [] as Array<{ rfId: string; patch: Record<string, unknown> }>,
}));

vi.mock("@/store/board", () => ({
  useBoardStore: {
    getState: () => ({
      nodes: board.nodes,
      edges: board.edges,
      boardId: board.boardId,
      updateNodeData: (rfId: string, patch: Record<string, unknown>) => {
        board.patches.push({ rfId, patch });
      },
    }),
  },
}));

vi.mock("@/store/settings", () => ({
  useSettingsStore: {
    getState: () => ({
      imageModel: "NANO_BANANA_2",
      videoModel: "veo",
      videoQuality: "fast",
      omniFlashDuration: 8,
    }),
  },
}));

const PRO = "PAYGATE_TIER_ONE";

async function freshStore() {
  vi.resetModules();
  const mod = await import("./generation");
  return mod.useGenerationStore;
}

function imageNode(over: Record<string, unknown> = {}): NodeStub {
  return {
    id: "n1",
    data: { type: "image", shortId: "A1", title: "Ảnh", ...over },
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  board.nodes = [imageNode()];
  board.edges = [];
  board.patches = [];
  api.ensureBoardProject.mockResolvedValue({ flow_project_id: "abcd1234" });
  api.getAuthMe.mockResolvedValue({ paygate_tier: "PAYGATE_TIER_ONE" });
  api.createRequest.mockResolvedValue({
    id: 1, node_id: 1, type: "gen_image", params: {}, status: "queued",
    result: {}, error: null, created_at: "", finished_at: null,
  });
  api.getRequest.mockResolvedValue({
    id: 1, node_id: 1, type: "gen_image", params: {}, status: "done",
    result: { media_ids: ["m-1"] }, error: null, created_at: "",
    finished_at: "",
  });
});

describe("the engine the node asks for is the engine that bills", () => {
  it("dispatches OpenAI for a node set to OpenAI", async () => {
    board.nodes = [imageNode({ imageEngine: "openai" })];
    const store = await freshStore();

    await store.getState().dispatchGeneration("n1", { prompt: "một con mèo", paygateTier: PRO });

    expect(api.createRequest).toHaveBeenCalledTimes(1);
    expect(api.createRequest.mock.calls[0][0].type).toBe("gen_image_openai");
  });

  it("still dispatches Flow for a node that never asked for anything else", async () => {
    const store = await freshStore();

    await store.getState().dispatchGeneration("n1", { prompt: "một con mèo", paygateTier: PRO });

    expect(api.createRequest.mock.calls[0][0].type).toBe("gen_image");
    expect(api.createRequest.mock.calls[0][0].params.image_model).toBe(
      "NANO_BANANA_2",
    );
  });

  it("does not send a Flow image model on the OpenAI path", async () => {
    board.nodes = [imageNode({ imageEngine: "openai" })];
    const store = await freshStore();

    await store.getState().dispatchGeneration("n1", { prompt: "x", paygateTier: PRO });

    expect(
      api.createRequest.mock.calls[0][0].params.image_model,
    ).toBeUndefined();
  });

  it("refuses an OpenAI node that has reference photos wired", async () => {
    // The generations endpoint draws from text alone. Honouring the engine
    // while dropping the photos would buy a confident image of the wrong
    // person and charge for it.
    board.nodes = [
      imageNode({ imageEngine: "openai" }),
      { id: "ref", data: { type: "character", mediaIds: ["m-ref"] } },
    ];
    board.edges = [{ source: "ref", target: "n1", data: {} }];
    const store = await freshStore();

    await store.getState().dispatchGeneration("n1", { prompt: "x", paygateTier: PRO });

    expect(api.createRequest).not.toHaveBeenCalled();
    const stamped = board.patches.at(-1);
    expect(stamped?.patch.status).toBe("error");
    /* The CODE the executor stamps, not a sentence of its own.
     *
     * This asserted `toContain("ảnh tham chiếu")` — loose enough to pass under
     * either wording, which is why nobody noticed this path wrote Vietnamese
     * prose into `node.data.error` while the executor wrote a code there.
     * `data.error` is what logs, tests and re-runs match on, so the value must be
     * the code; the sentence is the registry's job. */
    expect(stamped?.patch.error).toBe("openai_image_refs_unsupported");
    // And it has to be a code the card can actually render.
    expect(errorLabel("openai_image_refs_unsupported")).not.toBe(
      "openai_image_refs_unsupported",
    );
  });
});
