import { useTranslation } from "react-i18next";

export interface TranslatedAlertResult {
  /** true when both de-DE and en-GB bundles have the key */
  ready: boolean;
  /** the localised string in the active language, or undefined when not ready */
  text: string | undefined;
}

/**
 * Resolves a translation key against both supported bundles before surfacing
 * alert content. When either locale is missing the key the result has
 * ready=false so the caller can render the "Translation pending" placeholder
 * instead of partial content (AC-1a65ae08).
 */
export function useTranslatedAlert(
  key: string,
  interpolation?: Record<string, string | number>,
): TranslatedAlertResult {
  const { t, i18n } = useTranslation();

  const hasDE = i18n.exists(key, { lng: "de-DE" });
  const hasEN = i18n.exists(key, { lng: "en-GB" });
  const ready = hasDE && hasEN;

  return {
    ready,
    text: ready ? t(key, interpolation) : undefined,
  };
}
