import { useEffect, useState } from "react";
import { getHealth, type HealthResponse, type WsStats } from "@/api/client";
import { cn } from "@/lib/utils";

function formatAge(sec: number | null | undefined): string | null {
  if (sec == null) return null;
  if (sec < 60) return `${sec}s`;
  const m = Math.floor(sec / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  return `${h}h${m % 60}m`;
}

/** The 30px bottom bar. Left: live agent + extension health, and whether the
 * Flow tab can sign a call — the three things that decide whether generation
 * works at all. Right: the "VPN : OFF" and "Profile_CAPTCHA" chips.
 *
 * This row used to read "token 4m", the age of a captured Bearer. Google stopped
 * issuing one in September 2026, and the part that matters here is that the
 * number kept ticking: it said "token 206h" while the user was signed in and
 * using Flow normally, and every call was failing. A stale measurement of
 * something that no longer exists is worse than no measurement, because it
 * reads as a healthy bridge.
 *
 * What replaced it reports the condition that actually blocks a dispatch — the
 * page being able to sign one — and shows how old that answer is, so a stale
 * one looks stale.
 */
export function StatusBar() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [agentOk, setAgentOk] = useState(false);

  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        const h = await getHealth();
        if (!alive) return;
        setAgentOk(h.ok);
        setHealth(h);
      } catch {
        if (!alive) return;
        setAgentOk(false);
        setHealth(null);
      }
    };
    void poll();
    const t = setInterval(poll, 3000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);

  const extConnected = health?.extension_connected ?? null;
  const stats = health?.ws_stats;
  const probeAge = formatAge(stats?.flow_probe_age_s ?? null);

  return (
    <footer className="flex h-(--spacing-statusbar) shrink-0 items-center gap-3 border-t border-line bg-surface px-3 text-[11px] text-ink-mute">
      <Dot ok={agentOk} label="agent" />
      <Dot ok={extConnected} label="extension" muteWhenOff />
      {extConnected && (
        <span
          className={cn("flex items-center gap-1", flowTabColor(stats))}
          // Three states, three different fixes: open a tab, sign in on the tab
          // you have, or nothing. One "Open Flow" hint covered all three badly.
          title={flowTabHint(stats)}
        >
          <span aria-hidden>●</span>
          <span>Flow tab</span>
          {probeAge && <span className="text-ink-mute">· {probeAge}</span>}
        </span>
      )}
      <div className="ml-auto flex items-center gap-2">
        <span className="rounded bg-card px-2 py-0.5">VPN : OFF</span>
        <span className="rounded bg-card px-2 py-0.5">Profile_CAPTCHA</span>
      </div>
    </footer>
  );
}

export function flowTabColor(stats: WsStats | undefined): string {
  if (stats?.at_token_present) return "text-ok";
  // Unknown is dim, not red: no probe has run yet, and colouring that as broken
  // sends people to fix a browser that may be fine.
  if (stats?.flow_tab_present == null) return "text-ink-dim";
  return "text-danger";
}

export function flowTabHint(stats: WsStats | undefined): string {
  if (!stats || stats.flow_tab_present == null) {
    return "Chưa kiểm tra tab Flow. Mở Tài khoản → Kiểm tra tab Flow.";
  }
  if (!stats.flow_tab_present) {
    return "Chưa có tab Flow nào. Mở https://flow.google.com/ và để tab đó mở.";
  }
  if (!stats.at_token_present) {
    return "Có tab Flow nhưng trang chưa ký được — đăng nhập rồi đợi app load xong.";
  }
  return "Tab Flow ký được — lệnh gửi Flow sẽ chạy.";
}

function Dot({
  ok,
  label,
  muteWhenOff = false,
}: {
  ok: boolean | null;
  label: string;
  muteWhenOff?: boolean;
}) {
  const color =
    ok === null
      ? "text-ink-dim"
      : ok
        ? "text-ok"
        : muteWhenOff
          ? "text-ink-mute"
          : "text-danger";
  return (
    <span className={cn("flex items-center gap-1", color)}>
      <span aria-hidden>●</span>
      <span>{label}</span>
    </span>
  );
}
