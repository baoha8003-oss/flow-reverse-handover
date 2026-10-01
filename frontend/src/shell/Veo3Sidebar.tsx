import { useShellStore } from "@/store/shell";
import {
  SIDEBAR_GROUPS,
  tabsInGroup,
  type ToolTab,
} from "./tabs";
import { cn } from "@/lib/utils";

/** The 232px left sidebar shown under the VEO3 pill: small-caps cyan group
 * headers, each item a gradient icon square + bold title + grey subtitle.
 * The active item gets the indigo→grape gradient. */
export function Veo3Sidebar() {
  return (
    <aside className="flex w-(--spacing-sidebar) shrink-0 flex-col overflow-y-auto border-r border-line bg-sidebar py-3">
      {SIDEBAR_GROUPS.map((group) => (
        <div key={group} className="mb-2">
          <div className="px-4 pb-1.5 pt-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-accent">
            {group}
          </div>
          {tabsInGroup(group).map((tab) => (
            <SidebarItem key={tab.id} tab={tab} />
          ))}
        </div>
      ))}
    </aside>
  );
}

function SidebarItem({ tab }: { tab: ToolTab }) {
  const active = useShellStore((s) => s.tab === tab.id);
  const setTab = useShellStore((s) => s.setTab);

  return (
    <button
      type="button"
      onClick={() => setTab(tab.id)}
      className={cn(
        "flex w-full items-center gap-2.5 px-3 py-1.5 text-left transition-colors",
        active
          ? "bg-gradient-to-r from-indigo to-grape text-white"
          : "text-ink hover:bg-elevated",
      )}
    >
      <span
        aria-hidden
        className="flex size-7 shrink-0 items-center justify-center rounded-(--radius-icon) text-[15px]"
        style={{
          backgroundImage: `linear-gradient(135deg, ${tab.from}, ${tab.to})`,
        }}
      >
        {tab.icon}
      </span>
      <span className="min-w-0">
        <span className="block truncate text-[13px] font-semibold leading-tight">
          {tab.title}
        </span>
        <span
          className={cn(
            "block truncate text-[11px] leading-tight",
            active ? "text-white/70" : "text-ink-mute",
          )}
        >
          {tab.subtitle}
        </span>
      </span>
    </button>
  );
}
