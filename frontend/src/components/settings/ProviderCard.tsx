import type { LLMProviderInfo, LLMProviderName } from "../../api/client";

/**
 * One provider card — clickable tile that shows provider identity +
 * connection status + selection state. Used in the OAuth Providers and
 * API Key Providers groups inside AiProvidersSection.
 *
 * Visual contract (matches the screenshot reference):
 *   - Logo dot + name on the left, status badge below
 *   - Right-edge indicator: filled when this card is the user's
 *     pending selection, hollow when not
 *   - Active border when selected; muted border otherwise
 *   - Disabled visual (lower opacity, no pointer) when the provider
 *     can't be activated yet (CLI missing for OAuth) — clicking still
 *     selects so the panel below can guide the user through setup.
 */

interface ProviderCardProps {
  provider: LLMProviderInfo;
  /** True when this card is the user's pending selection (highlighted). */
  selected: boolean;
  /** True when this card matches the currently-applied config (badge). */
  current: boolean;
  /** Click handler — flips selection. Always fires; the section above
   * decides whether to render setup guidance vs. test flow. */
  onSelect(name: LLMProviderName): void;
}

const PROVIDER_META: Record<
  LLMProviderName,
  { name: string; vendor: string }
> = {
  claude:  { name: "Claude Code",   vendor: "Anthropic CLI" },
  gemini:  { name: "Gemini",        vendor: "Google" },
  openai:  { name: "OpenAI Codex",  vendor: "ChatGPT CLI" },
};

/**
 * The second line of the card: vendor + the identity actually in use.
 *
 * This used to be a fixed string per provider — claude "· OAuth", openai
 * "· API key". Those were true on the machine they were written on and
 * became lies the moment anyone signed in differently. The whole point of
 * the backend's `authMode` is that this line is now measured, so a user
 * who runs `codex login` sees the card change.
 */
function taglineFor(p: LLMProviderInfo): string {
  const vendor = PROVIDER_META[p.name].vendor;
  switch (p.authMode) {
    case "oauth":
      return `${vendor} · OAuth`;
    case "apikey":
      return `${vendor} · API key`;
    default:
      return `${vendor} · not signed in`;
  }
}

/**
 * Order matters here. "Connected" used to be tested first, which meant an
 * installed-but-signed-out CLI reported Connected and the "Not signed in"
 * branch below could never be reached — `available` only ever proved that
 * `--version` answers, which it does when signed out too.
 */
function statusLabel(p: LLMProviderInfo): string {
  if (p.lastError === "not_authenticated") return "Not signed in";
  if (p.available && p.configured) return "Connected";
  if (p.requiresKey && !p.configured) return "API key needed";
  return "Setup needed";
}

function statusKind(p: LLMProviderInfo): "ok" | "warn" {
  if (p.lastError === "not_authenticated") return "warn";
  return p.available && p.configured ? "ok" : "warn";
}

export function ProviderCard({ provider, selected, current, onSelect }: ProviderCardProps) {
  const meta = PROVIDER_META[provider.name];
  const kind = statusKind(provider);

  return (
    <button
      type="button"
      className={`provider-card${selected ? " provider-card--selected" : ""}${
        kind === "warn" ? " provider-card--unconfigured" : ""
      }`}
      onClick={() => onSelect(provider.name)}
      aria-pressed={selected}
    >
      <div className="provider-card__head">
        <span className="provider-card__name">{meta.name}</span>
        <span className="provider-card__tagline">{taglineFor(provider)}</span>
      </div>
      <div className="provider-card__foot">
        <span className={`provider-card__status provider-card__status--${kind}`}>
          <span className="provider-card__status-dot" aria-hidden="true">●</span>
          {statusLabel(provider)}
        </span>
        {current && !selected && (
          <span className="provider-card__current-badge">Active</span>
        )}
      </div>
      <span
        className={`provider-card__radio${selected ? " provider-card__radio--on" : ""}`}
        aria-hidden="true"
      />
    </button>
  );
}
