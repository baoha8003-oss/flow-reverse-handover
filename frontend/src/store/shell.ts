import { create } from "zustand";
import type { TopPillId, ToolTabId } from "@/shell/tabs";

/** Which top pill and (under VEO3) which sidebar tab is active.
 *
 * The original is a single-window desktop app with no URLs, so navigation
 * lives in a store rather than a router. The last-open tab is remembered in
 * localStorage so a reload lands where you left off. */

const LS_KEY = "veo3.shell.v1";

interface Persisted {
  pill: TopPillId;
  tab: ToolTabId;
}

function loadPersisted(): Persisted {
  const fallback: Persisted = { pill: "veo3", tab: "text-to-video" };
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (!raw) return fallback;
    const parsed = JSON.parse(raw) as Partial<Persisted>;
    return {
      pill: parsed.pill ?? fallback.pill,
      tab: parsed.tab ?? fallback.tab,
    };
  } catch {
    // Private windows / disabled storage throw on read — fall back cleanly.
    return fallback;
  }
}

function persist(state: Persisted): void {
  try {
    localStorage.setItem(LS_KEY, JSON.stringify(state));
  } catch {
    // Best-effort only; navigation still works without persistence.
  }
}

interface ShellState {
  pill: TopPillId;
  tab: ToolTabId;
  setPill: (pill: TopPillId) => void;
  setTab: (tab: ToolTabId) => void;
}

const initial = loadPersisted();

export const useShellStore = create<ShellState>((set, get) => ({
  pill: initial.pill,
  tab: initial.tab,
  setPill: (pill) => {
    set({ pill });
    persist({ pill, tab: get().tab });
  },
  setTab: (tab) => {
    // Selecting a tool tab implies the VEO3 pill that hosts the sidebar.
    set({ tab, pill: "veo3" });
    persist({ pill: "veo3", tab });
  },
}));
