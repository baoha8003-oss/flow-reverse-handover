import { describe, expect, it } from "vitest";

import {
  WATERMARK_BOX,
  watermarkCorner,
  withWatermarkCorner,
} from "./watermarkFallback";

/** The setting that makes the CPU watermark pass reachable from a board.
 *
 * MI-GAN paints a hole someone points at; without a box there is nothing for
 * it to do. The box existed only in imported workflow files, so a board built
 * on the canvas could never get the third chance at a watermark — only the
 * detector, and it declines: measured, "No watermark detected (3%)" on a frame
 * that plainly carried a Veo logo.
 */

describe("the fallback corner", () => {
  it("writes the packaged box at the corner picked", () => {
    const next = withWatermarkCorner({}, "br");
    expect(next).toEqual({
      veo_logo_x_pct: 78,
      veo_logo_y_pct: 88,
      veo_logo_w_pct: WATERMARK_BOX.w,
      veo_logo_h_pct: WATERMARK_BOX.h,
    });
  });

  it("keeps every other setting on the node", () => {
    // The backend shallow-merges `data`, so this object is sent whole. Losing
    // a key here is losing it on the node — the subtitles, the music, the
    // aspect conversion all live in the same dict.
    const next = withWatermarkCorner(
      { enable_sub: true, sub_margin_v: 347, aspect_output: "9:16" },
      "tr",
    );
    expect(next.enable_sub).toBe(true);
    expect(next.sub_margin_v).toBe(347);
    expect(next.aspect_output).toBe("9:16");
  });

  it("clears all four keys for none, so the backend emits no box", () => {
    // `_logo_box` returns None unless all four are numbers, and a stale pair
    // left behind would keep pointing the model at a corner the user just
    // switched off.
    const next = withWatermarkCorner(
      { veo_logo_x_pct: 78, veo_logo_y_pct: 2, veo_logo_w_pct: 20, veo_logo_h_pct: 10 },
      "none",
    );
    expect(Object.keys(next)).toEqual([]);
  });

  it("moves the box rather than adding a second one", () => {
    const next = withWatermarkCorner(
      { veo_logo_x_pct: 78, veo_logo_y_pct: 2, veo_logo_w_pct: 20, veo_logo_h_pct: 10 },
      "bl",
    );
    expect(next.veo_logo_x_pct).toBe(2);
    expect(next.veo_logo_y_pct).toBe(88);
  });
});

describe("reading a box back", () => {
  it("recognises the packaged workflow's own box", () => {
    // `nguoi_que_tao_tu_anh.json`: 78 / 2 / 20 / 10 — top right.
    expect(watermarkCorner({ veo_logo_x_pct: 78.0, veo_logo_y_pct: 2.0 })).toBe("tr");
  });

  it("places a custom box in the quadrant it is actually in", () => {
    expect(watermarkCorner({ veo_logo_x_pct: 4, veo_logo_y_pct: 91 })).toBe("bl");
  });

  it("is none when there is no box", () => {
    expect(watermarkCorner({})).toBe("none");
    expect(watermarkCorner({ veo_logo_x_pct: 78 })).toBe("none");
  });

  it("is none when the value is not a number", () => {
    // Imported JSON is not validated on the way in, and the packaged files
    // write `""` for unset (`veo_logo_image: ""`). `Number("")` is 0, so this
    // read back as "top left" — a box the backend does not see, because its
    // own `_number` refuses anything that is not an int or a float.
    expect(watermarkCorner({ veo_logo_x_pct: "", veo_logo_y_pct: "" })).toBe("none");
    expect(watermarkCorner({ veo_logo_x_pct: "78", veo_logo_y_pct: "2" })).toBe("none");
    expect(watermarkCorner({ veo_logo_x_pct: true, veo_logo_y_pct: true })).toBe("none");
  });
});
