import { useShellStore } from "@/store/shell";
import { TOP_PILLS } from "./tabs";
import { BrandingChips } from "./BrandingChips";
import { cn } from "@/lib/utils";

/** The row of five pills, matching the packaged app's top bar. The active
 * pill gets the cyan gradient + glow; the rest are muted. Branding chips,
 * a Zalo button and a bell sit on the right. */
export function TopBar() {
  const pill = useShellStore((s) => s.pill);
  const setPill = useShellStore((s) => s.setPill);

  return (
    <header className="flex h-(--spacing-topbar) shrink-0 items-center gap-2 border-b border-line bg-surface px-3">
      <nav className="flex items-center gap-2">
        {TOP_PILLS.map((p) => {
          const active = p.id === pill;
          return (
            <button
              key={p.id}
              type="button"
              onClick={() => setPill(p.id)}
              className={cn(
                "flex h-8 items-center gap-1.5 rounded-full px-4 text-[13px] font-semibold transition-colors",
                active
                  ? "bg-gradient-to-r from-accent to-accent-deep text-[#052026] shadow-[0_0_14px_rgba(34,211,238,0.38)]"
                  : "bg-card text-ink-mute hover:bg-elevated hover:text-ink",
              )}
            >
              <span aria-hidden>{p.icon}</span>
              <span>{p.label}</span>
            </button>
          );
        })}
      </nav>

      <div className="ml-auto flex items-center gap-2">
        <BrandingChips />
        <button
          type="button"
          className="flex h-8 items-center gap-1.5 rounded-md bg-zalo px-3 text-[13px] font-semibold text-white hover:brightness-110"
        >
          <span aria-hidden>💬</span>
          <span>Zalo</span>
        </button>
        <button
          type="button"
          aria-label="Thông báo"
          className="flex size-8 items-center justify-center rounded-md bg-card text-ink-mute hover:bg-elevated hover:text-ink"
        >
          🔔
        </button>
      </div>
    </header>
  );
}
