import { useEffect, useRef, useState } from "react";
import {
  getAuthMe,
  logoutExtension,
  scanExtension,
  type AuthMe,
  type AuthScanResult,
} from "../api/client";
import { useGenerationStore } from "../store/generation";
import { getLatestRelease, isNewerVersion, type LatestRelease } from "../api/github";
import { SettingsPanel } from "./SettingsPanel";
import packageJson from "../../package.json";

//: How often to re-ask the bridge. 30s, matching `ForcedSetupGate` and the
//: other pollers. Was 8s, and each tick is a MAIN-world injection into the
//: user's Flow tab — see the effect below.
const ACCOUNT_POLL_INTERVAL_MS = 30_000;

const APP_VERSION: string = packageJson.version;

/**
 * Account chip pinned to the bottom of the project sidebar.
 *
 * It used to show an identity: the extension captured a Bearer token, called
 * Google's userinfo endpoint, and this chip rendered the name, email and avatar
 * that came back. Google's September 2026 Flow migration removed the token —
 * the page signs its own calls now and never hands a credential to this app —
 * so there is no identity to show and `/api/auth/me` returns nulls.
 *
 * What the chip reports instead is the thing the user actually needs from it:
 * can a generation be dispatched right now. Three states, three different
 * fixes, and telling them apart is the whole point — the second one looked
 * healthy for a week:
 *
 *   1. the extension is not connected — reload it in chrome://extensions;
 *   2. it is connected but no Flow tab can sign a call — open or sign in to
 *      https://flow.google.com/ and leave the tab open;
 *   3. signed, nothing to do.
 *
 * Everything here is presence, never values: whether the page carries its
 * signing token, not what the token is.
 */
export function AccountPanel({ collapsed = false }: { collapsed?: boolean }) {
  const setStorePaygateTier = useGenerationStore.setState;
  const [open, setOpen] = useState(false);
  const [profile, setProfile] = useState<AuthMe | null>(null);
  const [scan, setScan] = useState<AuthScanResult | null>(null);
  const [scanState, setScanState] = useState<"idle" | "scanning">("idle");
  const [logoutPending, setLogoutPending] = useState(false);
  const [pollNonce, setPollNonce] = useState(0);
  //: Shared by the poll loop and the manual "Kiểm tra tab Flow" button, so the
  //: two cannot inject into the Flow tab at the same moment.
  const probeInFlight = useRef(false);

  // Poll `/api/auth/me` for the chosen plan and the last probe result, and
  // `/api/auth/scan` for a fresh bridge state.
  //
  // `/scan` is NOT a cheap status read, and the comment here used to say it was
  // ("both are free and generate nothing" — true about billing, misleading about
  // cost). It reaches `flow_probe`, which runs
  // `chrome.scripting.executeScript({world:'MAIN'})` inside the user's signed-in
  // Flow tab: a page injection with the page's full privileges. At the old 8s
  // cadence, with no visibility gate and this panel mounted for the whole
  // session, that was ~450 injections an hour whether or not anyone was looking.
  //
  // Worse, `runFlowProbe` calls `reviveTabIfNeeded`, which RELOADS a discarded
  // tab (`extension/background.js`). Chrome discarding a backgrounded tab is
  // normal, so the loop turned that into a reload of flow.google.com every ~10.5s
  // for as long as Flowboard stayed open — a Flow tab that never stopped loading.
  //
  // The states `describeState` distinguishes (tab closed, session expired) change
  // on human timescales, so 30s plus an immediate refresh when the window regains
  // focus answers strictly sooner in the case that matters. Gate + listener copied
  // from `ForcedSetupGate`, which is the pattern the other four pollers use.
  useEffect(() => {
    let alive = true;
    const refresh = async () => {
      // Never two probes in the same tab at once. The loop and the "Kiểm tra tab
      // Flow" button share this ref, because pressing the button mid-tick used to
      // put two MAIN-world injections into the page simultaneously.
      if (probeInFlight.current) return;
      probeInFlight.current = true;
      try {
        const me = await getAuthMe();
        if (!alive) return;
        setProfile(me);
        // Mirror the plan into the generation store so every dispatch path reads
        // one source. It is a label — nothing is gated on it — but a stale one
        // would put the wrong price on screen.
        setStorePaygateTier({ paygateTier: me?.paygate_tier ?? null });
        const state = await scanExtension();
        if (alive) setScan(state);
      } catch {
        if (alive) setScan(null);
      } finally {
        probeInFlight.current = false;
      }
    };
    void refresh();
    const timer = setInterval(() => {
      if (document.visibilityState === "visible") void refresh();
    }, ACCOUNT_POLL_INTERVAL_MS);
    const onVisible = () => {
      if (document.visibilityState === "visible") void refresh();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      alive = false;
      clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [setStorePaygateTier, pollNonce]);

  async function handleLogout() {
    if (logoutPending) return;
    setLogoutPending(true);
    try {
      await logoutExtension();
      setPollNonce((n) => n + 1);
    } catch {
      // non-fatal: the next poll reflects the real state anyway
    } finally {
      setLogoutPending(false);
    }
  }

  async function handleScan() {
    // `scanState` guards this button against ITSELF; `probeInFlight` guards it
    // against the poll loop. Both are needed: the old code had only the first, so
    // pressing it mid-tick issued a second MAIN-world injection into the same Flow
    // tab while the first was still running.
    if (scanState === "scanning" || probeInFlight.current) return;
    setScanState("scanning");
    probeInFlight.current = true;
    try {
      setScan(await scanExtension());
    } catch {
      setScan(null);
    } finally {
      probeInFlight.current = false;
      setScanState("idle");
      setPollNonce((n) => n + 1);
    }
  }

  const [latestRelease, setLatestRelease] = useState<LatestRelease | null>(null);
  useEffect(() => {
    let alive = true;
    getLatestRelease().then((r) => {
      if (alive) setLatestRelease(r);
    });
    return () => {
      alive = false;
    };
  }, []);
  const updateAvailable =
    !!latestRelease?.tagName && isNewerVersion(latestRelease.tagName, APP_VERSION);

  const tier = profile?.paygate_tier ?? null;
  const tierLabel =
    tier === "PAYGATE_TIER_TWO" ? "Ultra" : tier === "PAYGATE_TIER_ONE" ? "Pro" : "—";

  const connected = scan?.extension_connected ?? null;
  const signed = scan?.flow_tab_signed ?? false;
  const ready = connected === true && signed;
  const state = describeState(scan);

  return (
    <>
      <div
        className={`account-panel${collapsed ? " account-panel--collapsed" : ""}${
          ready ? "" : " account-panel--disconnected"
        }`}
        role="region"
        aria-label="Account"
      >
        <div
          className="account-panel__avatar"
          title={collapsed ? `${state.short} · ${tierLabel}` : undefined}
          aria-hidden="true"
        >
          {ready ? "✓" : "!"}
        </div>
        {!collapsed && (
          <div className="account-panel__meta">
            <span className="account-panel__name">Google Flow</span>
            <span className="account-panel__state" title={state.hint}>
              {state.short}
            </span>
            <div className="account-panel__status-row">
              <span
                className={`account-panel__tier${
                  tier === "PAYGATE_TIER_TWO"
                    ? " account-panel__tier--ultra"
                    : " account-panel__tier--pro"
                }`}
              >
                {tierLabel}
              </span>
              <button
                type="button"
                className="account-panel__scan-btn"
                onClick={handleScan}
                disabled={scanState === "scanning"}
                title="Hỏi lại tab Flow xem có ký được không. Không tốn credit."
              >
                {scanState === "scanning" ? "Đang kiểm…" : "Kiểm tra tab Flow"}
              </button>
            </div>
          </div>
        )}
        <button
          type="button"
          className="account-panel__cog"
          onClick={() => setOpen((v) => !v)}
          aria-label="Open settings"
          title="Settings"
        >
          ⚙
        </button>
      </div>
      {!collapsed && !ready && connected !== null && (
        // The actionable half. Named after what is wrong, because "not
        // connected" covered a bridge that was down and a page that could not
        // sign — and only one of those is fixed in chrome://extensions.
        <div className="account-panel__scan-hint" role="alert">
          <span className="account-panel__scan-hint-title">⚠ {state.short}</span>
          <span className="account-panel__scan-hint-text">{state.hint}</span>
        </div>
      )}
      {!collapsed && (
        <div className="account-panel__version-row">
          <span className="account-panel__version-label">
            Flowboard <code>v{APP_VERSION}</code>
          </span>
          {scan?.extension_version && (
            <span
              className="account-panel__version-label"
              title="Bản extension đang chạy trong Chrome"
            >
              <code>ext {scan.extension_version}</code>
            </span>
          )}
          {updateAvailable && latestRelease && (
            <a
              className="account-panel__update-pill"
              href={latestRelease.htmlUrl}
              target="_blank"
              rel="noopener noreferrer"
              title={`Latest release ${latestRelease.tagName} — click to view`}
            >
              ↑ {latestRelease.tagName}
            </a>
          )}
        </div>
      )}
      {!collapsed && scan && !scan.has_paygate_tier && (
        // The plan is unset. It used to be detectable — the extension sniffed a
        // Bearer token and the agent asked `/v1/credits` — and this banner said
        // "open Flow once". Both are gone, so the recovery is a dropdown rather
        // than a page visit. Generation is NOT blocked by this: the plan only
        // decides which lanes are listed and which price is quoted.
        <div className="account-panel__tier-warning" role="alert">
          <span className="account-panel__tier-warning-icon" aria-hidden="true">
            ⚠
          </span>
          <div className="account-panel__tier-warning-body">
            <span className="account-panel__tier-warning-title">Chưa chọn gói Flow</span>
            <span className="account-panel__tier-warning-text">
              Bản mới của Flow không cho đọc gói tự động. Chọn Pro/Ultra trong Cài
              đặt để bảng giá hiện đúng — không chọn thì vẫn chạy được.
            </span>
          </div>
          <button
            type="button"
            className="account-panel__tier-warning-cta"
            onClick={() => setOpen(true)}
          >
            Mở Cài đặt
          </button>
        </div>
      )}
      <SettingsPanel
        open={open}
        onClose={() => setOpen(false)}
        // Kept because the frontend offers it and telling the extension is
        // harmless. What it cannot do is sign the user out: the Flow session
        // lives in Chrome's own cookie jar, which this app does not touch.
        onLogout={async () => {
          await handleLogout();
          setOpen(false);
        }}
        logoutPending={logoutPending}
      />
    </>
  );
}

/** One short label and one actionable sentence per bridge state. */
export function describeState(scan: AuthScanResult | null): {
  short: string;
  hint: string;
} {
  if (scan === null) {
    return {
      short: "Chưa kiểm tra",
      hint: "Chưa hỏi được agent. Kiểm tra agent có đang chạy không.",
    };
  }
  if (!scan.extension_connected) {
    return {
      short: "Extension chưa nối",
      hint: "Mở chrome://extensions và bấm Reload cho Flowboard.",
    };
  }
  if (!scan.flow_tab_present) {
    return {
      short: "Chưa có tab Flow",
      hint:
        "Mở https://flow.google.com/ , đăng nhập, và để tab đó mở — mọi lệnh " +
        "gửi Flow đều chạy bên trong nó.",
    };
  }
  if (!scan.flow_tab_signed) {
    return {
      short: "Tab Flow chưa ký được",
      hint:
        "Có tab Flow nhưng trang chưa ký được: hoặc chưa đăng nhập, hoặc app " +
        "đang tải. Đăng nhập rồi đợi load xong, sau đó bấm kiểm tra lại.",
    };
  }
  return { short: "Sẵn sàng", hint: "Tab Flow ký được — lệnh gửi Flow sẽ chạy." };
}
