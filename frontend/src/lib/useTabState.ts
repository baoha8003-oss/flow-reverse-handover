import { useEffect, useRef, useState } from "react";

/** Fold a loaded state into what the tab is holding right now.
 *
 * Pulled out of the hook because both rules here are easy to get wrong and
 * invisible when wrong. `dirty` means the user has already typed something
 * while the load was in flight — merging then would replace their keystroke
 * with yesterday's value, which reads as the field editing itself. And the
 * merge goes OVER the defaults rather than replacing them, so a field added
 * since the state was written comes back as its default instead of undefined.
 */
export function applyLoadedTabState<T extends Record<string, unknown>>(
  current: T,
  stored: Partial<T> | null | undefined,
  dirty: boolean,
): T {
  if (dirty || !stored) return current;
  return { ...current, ...stored };
}

/** Remember what a tab had typed in it, across reloads.
 *
 * The packaged tool keeps one state file per tab — `affiliate_state.json` with
 * 25 keys, `data_clone.json` with 13 — and losing that on every reload is the
 * difference between "pick up where I left off" and "type the product URL
 * again". This build had none of it.
 *
 * Deliberately NOT localStorage: the agent owns `storage/`, the same place the
 * templates and the character registry live, so a tab's memory survives a
 * browser profile and can be inspected when something looks wrong.
 *
 * Writes are debounced, because this fires on every keystroke and the value is
 * worth one request per pause, not one per letter.
 */
export function useTabState<T extends Record<string, unknown>>(
  tab: string,
  initial: T,
  options: { debounceMs?: number } = {},
): [T, (next: Partial<T>) => void, { loaded: boolean }] {
  const { debounceMs = 600 } = options;
  const [state, setState] = useState<T>(initial);
  const [loaded, setLoaded] = useState(false);
  const timer = useRef<number | null>(null);
  // The load must not be overwritten by a save of the pre-load defaults.
  const dirty = useRef(false);

  useEffect(() => {
    let live = true;
    fetch(`/api/tab-state/${tab}`)
      .then((res) => (res.ok ? res.json() : null))
      .then((body) => {
        if (!live) return;
        setState((prev) => applyLoadedTabState(prev, body?.state, dirty.current));
      })
      .catch(() => {})
      .finally(() => live && setLoaded(true));
    return () => {
      live = false;
    };
  }, [tab]);

  function update(next: Partial<T>) {
    dirty.current = true;
    setState((prev) => {
      const merged = { ...prev, ...next };
      if (timer.current !== null) window.clearTimeout(timer.current);
      timer.current = window.setTimeout(() => {
        void fetch(`/api/tab-state/${tab}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ state: merged }),
          // A tab that cannot save its state still works; the memory is the
          // convenience, not the feature.
        }).catch(() => {});
      }, debounceMs);
      return merged;
    });
  }

  return [state, update, { loaded }];
}
