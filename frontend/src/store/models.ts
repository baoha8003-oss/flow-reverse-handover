import { create } from "zustand";
import {
  getModels,
  type DurationOption,
  type LaneInfo,
  type ModelOption,
  type ModelsResponse,
  type SettingsOptions,
} from "@/api/client";

/** What this account can generate with — fetched once, read by every tab.
 *
 * Before this, each tab carried its own copy of the model / quality / aspect
 * / duration lists. Twenty of them across ten files, and they had drifted:
 * one offered a Square aspect for video that no model key supports, another
 * locked the duration to 8s while Settings offered 4–10, and an unknown
 * quality fell back to a different lane in the frontend than in the backend.
 *
 * The backend now answers all of it, filtered by the signed-in plan, so the
 * UI stops guessing and the lists cannot disagree.
 */

/** Every lane is empty until the fetch lands. Tabs render their controls from
 * whatever is here, so an empty lane means "disabled", never "guess". */
const EMPTY_LANE: LaneInfo = { qualities: [], aspects: [], durations: [] };
const EMPTY_OPTIONS: ModelOption[] = [];

interface ModelsState {
  data: ModelsResponse | null;
  loaded: boolean;
  error: string | null;
  load: () => Promise<void>;
}

let inFlight: Promise<void> | null = null;

export const useModelsStore = create<ModelsState>((set, get) => ({
  data: null,
  loaded: false,
  error: null,
  async load() {
    if (get().loaded) return;
    // Nine tabs call this on mount. Without the guard that is nine identical
    // requests racing on first paint.
    if (inFlight) return inFlight;
    inFlight = (async () => {
      try {
        set({ data: await getModels(), loaded: true, error: null });
      } catch (e) {
        set({
          error: e instanceof Error ? e.message : "Không đọc được danh sách model.",
        });
      } finally {
        inFlight = null;
      }
    })();
    return inFlight;
  },
}));

/** Whether it is safe to dispatch yet.
 *
 * Not cosmetic. Before the registry existed the tabs seeded their controls
 * from hardcoded literals, so a control always held a value. Now they start
 * empty and fill in when the fetch lands — and an empty quality does NOT
 * fail at the backend: `resolve_video_model(tier, aspect, "")` returns
 * `veo_3_1_i2v_s_fast_portrait`, i.e. it falls back to `fast`, the pricier
 * lane. Dispatching before the registry arrives would quietly bill the user
 * more than the default they used to get. So the dispatch buttons wait. */
export function useModelsReady(): boolean {
  return useModelsStore((s) => s.loaded);
}

/** The reason the registry could not be read, for the tab to show. */
export function useModelsError(): string | null {
  return useModelsStore((s) => s.error);
}

/** Whether a tab may seed its controls yet.
 *
 * Both halves are required, and the second is the one that was missed in
 * three of the nine tabs. Seeding from settings alone leaves every control
 * holding "" — and an empty quality does NOT fail at the backend:
 * `resolve_video_model(tier, aspect, "")` resolves to `fast`, a pricier lane
 * than the `lite` these tabs used to default to.
 *
 * Whatever calls this must also keep the options array in its effect's
 * dependency list, or the effect cannot re-run when the fetch lands and the
 * empty value sticks for the life of the page. */
export function canSeed(configLoaded: boolean, options: unknown[]): boolean {
  return configLoaded && options.length > 0;
}

/** One lane's options, safe to read before the fetch resolves. */
export function useLane(name: "t2v" | "i2v" | "startEnd" | "omni"): LaneInfo {
  return useModelsStore((s) => s.data?.[name] ?? EMPTY_LANE);
}

const EMPTY_SETTINGS: SettingsOptions = {
  veoModel: [],
  imageModel: [],
  aspect: [],
  duration: [],
  resolution: [],
};

/** The image models and aspects, safe to read before the fetch lands.
 *
 * Not a lane: image generation has no quality or duration axis, so the two
 * lists sit at the top level of the response. */
export function useImageOptions(): {
  models: ModelOption[];
  aspects: ModelOption[];
} {
  const models = useModelsStore((s) => s.data?.imageModels ?? EMPTY_OPTIONS);
  const aspects = useModelsStore((s) => s.data?.imageAspects ?? EMPTY_OPTIONS);
  return { models, aspects };
}

/** The Settings screen's option lists, safe to read before the fetch lands. */
export function useSettingsOptions(): SettingsOptions {
  return useModelsStore((s) => s.data?.settingsOptions ?? EMPTY_SETTINGS);
}

/** The `<select>` shape used across the tabs. */
export function toSelectOptions(
  options: ModelOption[],
): { value: string; label: string }[] {
  return options.map((o) => ({ value: o.value, label: o.label }));
}

/** The warning for a chosen lane, or null when it runs as labelled.
 *
 * "Lower Priority" reads as a free queue; on a plan without one it is billed.
 * Saying so before the click is the whole point of carrying the note. */
export function noteFor(options: ModelOption[], value: string): string | null {
  return options.find((o) => o.value === value)?.note ?? null;
}

/** A settings value like "9:16" → the matching aspect in this lane.
 *
 * Settings stores the human spelling; dispatch needs the enum. Matching
 * against the lane rather than a hardcoded pair means a lane that does not
 * offer an orientation cannot be seeded into it. */
export function aspectFromConfig(
  value: unknown,
  aspects: ModelOption[],
): string {
  const wantsLandscape = String(value ?? "") === "16:9";
  const suffix = wantsLandscape ? "LANDSCAPE" : "PORTRAIT";
  return (
    aspects.find((a) => a.value.endsWith(suffix))?.value ??
    defaultValue(aspects)
  );
}

/** The Omni render resolution from Settings, or undefined when nothing usable
 * is stored.
 *
 * Undefined rather than `"720p"`: the SDK owns that default, and a second copy
 * of it here would keep quoting 720p after the SDK's default changed. The
 * packaged tool's `config.json` ships `480p`, which Flow's Omni builder does not
 * accept — it is dropped here as well as in the backend, because the value can
 * also arrive from a stale browser cache.
 */
export function resolutionFromConfig(value: unknown): string | undefined {
  const raw = String(value ?? "").toLowerCase();
  return raw === "360p" || raw === "720p" ? raw : undefined;
}

/** A settings label like "Veo 3.1 - Lite" → its quality token.
 *
 * Matched on the exact label, never a substring: sniffing for "lite" or "2"
 * used to land on the wrong lane in both directions, and an unmatched label
 * silently fell through to the most expensive option. An unknown label now
 * resolves to the first lane with no billing warning. */
export function qualityFromLabel(
  label: unknown,
  qualities: ModelOption[],
): string {
  const wanted = String(label ?? "").trim();
  return (
    qualities.find((q) => q.label === wanted)?.value ?? defaultValue(qualities)
  );
}

/** What one clip of this length costs on this lane, or null when the lane
 * does not price by length.
 *
 * The price table used to be copied into the frontend as a literal. It is
 * the backend's number — a stale copy here would quote a cost the account
 * never gets charged. */
export function creditsFor(
  durations: DurationOption[],
  value: number | string,
): number | null {
  return durations.find((d) => d.value === Number(value))?.credits ?? null;
}

/** First available value, for seeding a control before the user picks.
 *
 * Prefers an option with no warning: defaulting someone onto a silently
 * billed lane is exactly the mistake this store exists to prevent. */
export function defaultValue(options: ModelOption[], fallback = ""): string {
  return options.find((o) => !o.note)?.value ?? options[0]?.value ?? fallback;
}
