import { create } from "zustand";

/** Effective app settings mirrored from the backend (config.json defaults +
 * user overrides). Tabs seed their control defaults from here so nothing is
 * hardcoded in the UI. */

interface AppConfigState {
  values: Record<string, unknown>;
  loaded: boolean;
  load: () => Promise<void>;
}

export const useAppConfigStore = create<AppConfigState>((set) => ({
  values: {},
  loaded: false,
  async load() {
    try {
      const res = await fetch("/api/settings");
      if (!res.ok) return;
      const values = (await res.json()) as Record<string, unknown>;
      set({ values, loaded: true });
    } catch {
      // Non-fatal: tabs fall back to their inline defaults.
    }
  },
}));
