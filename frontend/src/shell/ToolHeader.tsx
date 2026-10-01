import type { ToolTab } from "./tabs";

/** The header at the top of each tool tab: a rounded gradient icon square
 * (tinted to the active sidebar item, matching the original's
 * per-tab-colored header) followed by the tab title. */
export function ToolHeader({ tab }: { tab: ToolTab }) {
  return (
    <div className="mb-4 flex items-center gap-2.5">
      <span
        aria-hidden
        className="flex size-8 items-center justify-center rounded-(--radius-icon) text-[16px]"
        style={{
          backgroundImage: `linear-gradient(135deg, ${tab.from}, ${tab.to})`,
        }}
      >
        {tab.icon}
      </span>
      <h1 className="text-[17px] font-semibold text-ink">{tab.title}</h1>
    </div>
  );
}
