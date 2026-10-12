import { useTranslation } from "react-i18next";

export interface BilingualStrings {
  "de-DE": string;
  "en-GB": string;
}

/**
 * Returns the resolved string for the active locale when BOTH translations
 * are present, or null when either is absent (AC-3 gate).
 *
 * Callers must render a "Translation pending" placeholder when null is returned.
 */
export function useBilingualContent(strings: BilingualStrings | null | undefined): string | null {
  const { i18n } = useTranslation();

  if (!strings) return null;

  const deDEValue = strings["de-DE"]?.trim();
  const enGBValue = strings["en-GB"]?.trim();

  if (!deDEValue || !enGBValue) {
    return null;
  }

  const lang = i18n.language;
  if (lang === "de-DE" || lang.startsWith("de")) {
    return deDEValue;
  }
  return enGBValue;
}
