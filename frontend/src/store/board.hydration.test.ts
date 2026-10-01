import { beforeEach, describe, expect, it, vi } from "vitest";

/** What survives a page reload.
 *
 * The three board loaders (`loadInitialBoard`, `switchBoard`,
 * `refreshBoardState`) each rebuild node data from the API with an explicit
 * allow-list of keys. TypeScript cannot help here: a field declared on
 * `FlowboardNodeData` and simply omitted from those maps type-checks
 * perfectly and then vanishes on refresh. That is how `imageEngine` came to
 * be settable on a node, saved to the database, and silently reset to Flow
 * the next time the page opened — a money field, lost without a word.
 *
 * So this test compares the maps against the database instead of against the
 * type, and it walks all three because they are three copies of the same
 * list and copies drift.
 */

const api = vi.hoisted(() => ({
  listBoards: vi.fn(),
  getBoard: vi.fn(),
  createBoard: vi.fn(),
  patchNode: vi.fn(),
  createNode: vi.fn(),
  deleteNode: vi.fn(),
  createEdge: vi.fn(),
  deleteEdge: vi.fn(),
  renameBoard: vi.fn(),
  deleteBoard: vi.fn(),
  getMediaUrl: vi.fn(),
  ensureBoardProject: vi.fn(),
}));

vi.mock("@/api/client", () => api);

/** Node data the user can set and expects to find again after a refresh.
 *
 * Also everything a FINISHED run wrote that a LATER action needs. Those are not
 * user settings, but they fail the same way and one of them fails expensively:
 * an extend chain reads `extensionOperationIds` to know what link *n+1* must
 * reference. Lose it on reload and the next link falls back to the scene clone,
 * re-renders from the start of the clip, and Flow bills for it. */
const SAVED_BY_THE_UI: Record<string, unknown> = {
  title: "Ảnh sản phẩm",
  prompt: "một chiếc bánh",
  // The money one: which service draws this node.
  imageEngine: "openai",
  imageModel: "NANO_BANANA_2",
  videoQuality: "lite_relaxed",
  aspectRatio: "IMAGE_ASPECT_RATIO_PORTRAIT",
  variantCount: 2,
  // Written by a run, read by upscale and extend afterwards.
  operationNames: ["op-1"],
  sourceModelKey: "veo_3_1_i2v_lite",
  upscaledMediaId: "up-1",
  sceneId: "scene-1",
  sceneCloneMediaId: "clone-1",
  extensionMediaIds: ["e-1"],
  extensionOperationIds: ["op-ext-1"],
};

function nodeRow() {
  return {
    id: 1,
    board_id: 1,
    short_id: "A1",
    type: "image",
    x: 0,
    y: 0,
    w: 260,
    h: 180,
    status: "idle",
    data: { ...SAVED_BY_THE_UI },
  };
}

function boardDetail(id = 1, name = "B") {
  return {
    board: { id, name, created_at: "", updated_at: "" },
    nodes: [nodeRow()],
    edges: [],
  };
}

/** The suite runs in node, where `localStorage` does not exist; the board
 *  store reads a persisted board id from it. */
function installStorage(): void {
  const data = new Map<string, string>();
  (globalThis as { localStorage?: Storage }).localStorage = {
    getItem: (k: string) => data.get(k) ?? null,
    setItem: (k: string, v: string) => void data.set(k, String(v)),
    removeItem: (k: string) => void data.delete(k),
    clear: () => data.clear(),
    key: (i: number) => Array.from(data.keys())[i] ?? null,
    get length() {
      return data.size;
    },
  } as Storage;
}

installStorage();

async function freshStore() {
  vi.resetModules();
  const mod = await import("./board");
  return mod.useBoardStore;
}

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  api.listBoards.mockResolvedValue([{ id: 1, name: "B" }]);
  api.getBoard.mockResolvedValue(boardDetail());
});

describe("a node's settings survive a reload", () => {
  it("loadInitialBoard keeps every field the UI saved", async () => {
    const store = await freshStore();
    await store.getState().loadInitialBoard();

    const data = store.getState().nodes[0].data as Record<string, unknown>;
    for (const [key, value] of Object.entries(SAVED_BY_THE_UI)) {
      expect(data[key], `${key} was dropped on load`).toEqual(value);
    }
  });

  it("switchBoard keeps them too", async () => {
    const store = await freshStore();
    await store.getState().loadInitialBoard();
    api.getBoard.mockResolvedValue(boardDetail(2, "C"));
    await store.getState().switchBoard(2);

    const data = store.getState().nodes[0].data as Record<string, unknown>;
    for (const [key, value] of Object.entries(SAVED_BY_THE_UI)) {
      expect(data[key], `${key} was dropped on switchBoard`).toEqual(value);
    }
  });

  /* The one that mattered, and the reason these two are no longer spot checks.
   *
   * Both used to assert `data.imageEngine` alone — one field out of the list the
   * first test walks in full — while the docstring above claimed all three were
   * compared. `aspectRatio` was missing from `refreshBoardState` and nothing
   * said so.
   *
   * It is the worst of the three to lose it from: a board run calls
   * `refreshBoardState` every 1500 ms, so the field was gone 1.5 s after Run.
   * `pickDefaultAspect` then read `undefined` upstream, fell back to landscape,
   * and a paid clip rendered landscape from a portrait source — and
   * `saveTileToLibrary` wrote that null onward into the reference library, so
   * the loss outlived the session. */
  it("refreshBoardState keeps them too", async () => {
    const store = await freshStore();
    await store.getState().loadInitialBoard();
    await store.getState().refreshBoardState();

    const data = store.getState().nodes[0].data as Record<string, unknown>;
    for (const [key, value] of Object.entries(SAVED_BY_THE_UI)) {
      expect(data[key], `${key} was dropped on refreshBoardState`).toEqual(value);
    }
  });
});
