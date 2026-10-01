import { describe, expect, it } from "vitest";
import { describeState } from "./AccountPanel";
import { flowTabColor, flowTabHint } from "../shell/StatusBar";
import type { AuthScanResult, WsStats } from "../api/client";

/**
 * Why these are tested at all: the states they distinguish are the ones that
 * cost a week. The bridge can be connected, its queue idle and its metrics
 * clean, while the page cannot sign a single call — and before the September
 * 2026 migration the UI had one label for both, so "extension connected" read
 * as "generation will work".
 *
 * Three states, three different fixes. Collapsing any two of them sends someone
 * to the wrong place.
 */

function scan(over: Partial<AuthScanResult> = {}): AuthScanResult {
  return {
    extension_connected: true,
    extension_version: "0.1.0",
    flow_tab_present: true,
    flow_tab_signed: true,
    has_paygate_tier: true,
    probe_error: null,
    ...over,
  };
}

function stats(over: Partial<WsStats> = {}): WsStats {
  return {
    connected: true,
    flow_tab_present: true,
    at_token_present: true,
    flow_probe_age_s: 3,
    page_unsigned: false,
    transport: "batch",
    extension_version: "0.1.0",
    last_failure: null,
    pending: 0,
    request_count: 0,
    success_count: 0,
    failed_count: 0,
    last_error: null,
    ...over,
  };
}

describe("describeState", () => {
  it("names the bridge when the extension is not connected", () => {
    const state = describeState(scan({ extension_connected: false }));
    expect(state.short).toContain("Extension");
    expect(state.hint).toContain("chrome://extensions");
  });

  it("names the tab when the bridge is up but no Flow tab exists", () => {
    const state = describeState(scan({ flow_tab_present: false, flow_tab_signed: false }));
    expect(state.hint).toContain("flow.google.com");
    // Must NOT send the user to chrome://extensions — the extension is fine.
    expect(state.hint).not.toContain("chrome://extensions");
  });

  it("distinguishes a tab that cannot sign from a tab that is missing", () => {
    const missing = describeState(scan({ flow_tab_present: false, flow_tab_signed: false }));
    const unsigned = describeState(scan({ flow_tab_signed: false }));
    expect(unsigned.short).not.toEqual(missing.short);
    expect(unsigned.hint).toContain("đăng nhập");
  });

  it("says ready only when the page can sign", () => {
    expect(describeState(scan()).short).toBe("Sẵn sàng");
    expect(describeState(scan({ flow_tab_signed: false })).short).not.toBe("Sẵn sàng");
  });

  it("treats no answer as unknown rather than broken", () => {
    // Null is "the agent did not answer", which is a different problem from
    // any of the three above and must not be reported as one of them.
    const state = describeState(null);
    expect(state.short).toContain("Chưa kiểm tra");
    expect(state.hint).toContain("agent");
  });
});

describe("flowTabColor", () => {
  it("is green only when the page can sign", () => {
    expect(flowTabColor(stats())).toContain("ok");
    expect(flowTabColor(stats({ at_token_present: false }))).not.toContain("ok");
  });

  it("is dim, not red, before any probe has run", () => {
    // Colouring "not asked yet" as broken sends people to fix a browser that
    // may be fine — and on a cold start nothing has been asked yet.
    const color = flowTabColor(stats({ flow_tab_present: null, at_token_present: null }));
    expect(color).toContain("dim");
    expect(color).not.toContain("danger");
  });

  it("is red when a tab exists and cannot sign", () => {
    expect(
      flowTabColor(stats({ flow_tab_present: true, at_token_present: false })),
    ).toContain("danger");
  });

  it("is red when no tab exists at all", () => {
    expect(
      flowTabColor(stats({ flow_tab_present: false, at_token_present: false })),
    ).toContain("danger");
  });
});

describe("flowTabHint", () => {
  it("gives a different instruction for each state", () => {
    const hints = new Set([
      flowTabHint(undefined),
      flowTabHint(stats({ flow_tab_present: false, at_token_present: false })),
      flowTabHint(stats({ at_token_present: false })),
      flowTabHint(stats()),
    ]);
    expect(hints.size).toBe(4);
  });
});
