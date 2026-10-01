/** Whether a node card should show a failure reason, and how loudly.
 *
 * Pulled out of the card so it can be tested without a DOM — the suite runs in
 * node, and the same shape is already used for `watermarkFallback` and
 * `applyLoadedTabState`.
 *
 * The decision is worth a function of its own because getting it wrong is
 * invisible: this logic lived inside `VideoBody` and nowhere else, so an image,
 * character, storyboard, upload or post-production node that failed rendered the
 * red status strip with no text on it. The worst case is a refusal whose whole
 * purpose is to say what to change — set an image node to OpenAI, leave a
 * reference image wired, press ▶ — where the only explanation appeared in a toast
 * that dismisses itself on a timer.
 */

export interface NodeErrorView {
  /** The code to hand to `errorLabel`. */
  code: string;
  /** A hard failure, as opposed to a partial result worth keeping. */
  isError: boolean;
}

export function nodeErrorView(
  data: { type?: string; status?: string; error?: string } | null | undefined,
): NodeErrorView | null {
  const code = data?.error;
  if (!code) return null;
  // `note` never dispatches, so it has no failure of its own to report. Every
  // other type can fail, including the post-production ones.
  if (data?.type === "note") return null;
  return { code, isError: data?.status === "error" };
}
