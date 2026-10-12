import { useTranslation } from "react-i18next";
import type { SupportedLanguage } from "../i18n";
import { SUPPORTED_LANGUAGES } from "../i18n";

const LANGUAGE_LABELS: Record<SupportedLanguage, { flag: string; label: string }> = {
  "de-DE": { flag: "🇩🇪", label: "Deutsch" },
  "en-GB": { flag: "🇬🇧", label: "English" },
};

export function LanguageSwitcher() {
  const { i18n, t } = useTranslation();
  const current = i18n.language as SupportedLanguage;
  const currentMeta = LANGUAGE_LABELS[current] ?? LANGUAGE_LABELS["en-GB"];

  function handleChange(lang: SupportedLanguage) {
    void i18n.changeLanguage(lang);
  }

  return (
    <div
      role="group"
      aria-label={t("languageSwitcher.label")}
      style={{ display: "flex", alignItems: "center", gap: "8px" }}
    >
      <span aria-hidden="true">{currentMeta.flag}</span>
      <span>{currentMeta.label}</span>
      <select
        aria-label={t("languageSwitcher.label")}
        value={current}
        onChange={(e) => handleChange(e.target.value as SupportedLanguage)}
        style={{ marginLeft: "4px" }}
      >
        {SUPPORTED_LANGUAGES.map((lang) => {
          const meta = LANGUAGE_LABELS[lang];
          return (
            <option key={lang} value={lang}>
              {meta.flag} {meta.label}
            </option>
          );
        })}
      </select>
    </div>
  );
}
