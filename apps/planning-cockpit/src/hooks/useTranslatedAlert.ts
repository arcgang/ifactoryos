import i18next from "../i18n";
import { useTranslation } from "react-i18next";

/**
 * Returns a translated string only when the key resolves in BOTH de-DE and
 * en-GB bundles.  When either translation is absent the returned value is the
 * `missingFallback` string (default: the value of errors.missingTranslation).
 * This satisfies AC-3: content with only one language translation is suppressed
 * at the display layer.
 */
export function useTranslatedAlert(
  key: string,
  interpolation?: Record<string, string | number>,
): { value: string; isComplete: boolean } {
  const { t } = useTranslation();

  const deValue = i18next.getFixedT("de-DE")(key, interpolation ?? {});
  const enValue = i18next.getFixedT("en-GB")(key, interpolation ?? {});

  const deResolved = deValue !== key;
  const enResolved = enValue !== key;
  const isComplete = deResolved && enResolved;

  const missingFallback =
    i18next.getFixedT("en-GB")("errors.missingTranslation") ??
    "Translation pending";

  return {
    value: isComplete ? t(key, interpolation ?? {}) : missingFallback,
    isComplete,
  };
}
