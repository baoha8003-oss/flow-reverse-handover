import { create } from "zustand";

/** Two view switches for the canvas, remembered across reloads.
 *
 * Both are purely how the canvas LOOKS. Neither touches board data, and that
 * is the point of hiding previews: on a board of twenty finished nodes the
 * thumbnails are the slow part, and someone rearranging the graph wants the
 * shape, not the pictures. Hiding them must never be confused with clearing
 * them — the media ids stay exactly where they were.
 */

const LS_KEY = "veo3.canvasUi.v1";

interface Persisted {
  previewsHidden: boolean;
  paletteCollapsed: boolean;
}

function loadPersisted(): Persisted {
  const fallback: Persisted = { previewsHidden: false, paletteCollapsed: false };
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (!raw) return fallback;
    const parsed = JSON.parse(raw) as Partial<Persisted>;
    return {
      previewsHidden: parsed.previewsHidden ?? fallback.previewsHidden,
      paletteCollapsed: parsed.paletteCollapsed ?? fallback.paletteCollapsed,
    };
  } catch {
    // Private windows / disabled storage throw on read.
    return fallback;
  }
}

function persist(state: Persisted): void {
  try {
    localStorage.setItem(LS_KEY, JSON.stringify(state));
  } catch {
    // Best-effort; the switches still work for this session.
  }
}

interface CanvasUiState extends Persisted {
  togglePreviews(): void;
  togglePalette(): void;
}

export const useCanvasUiStore = create<CanvasUiState>((set, get) => ({
  ...loadPersisted(),
  togglePreviews() {
    const next = !get().previewsHidden;
    set({ previewsHidden: next });
    persist({ previewsHidden: next, paletteCollapsed: get().paletteCollapsed });
  },
  togglePalette() {
    const next = !get().paletteCollapsed;
    set({ paletteCollapsed: next });
    persist({ previewsHidden: get().previewsHidden, paletteCollapsed: next });
  },
}));
