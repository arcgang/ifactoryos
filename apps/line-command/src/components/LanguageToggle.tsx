import { useTranslation } from "react-i18next";
import { LOCALE_STORAGE_KEY, type SupportedLocale } from "../i18n";

const LOCALE_OPTIONS: { value: SupportedLocale; flag: string; label: string }[] = [
  { value: "de-DE", flag: "🇩🇪", label: "Deutsch" },
  { value: "en-GB", flag: "🇬🇧", label: "English" },
];

export function LanguageToggle() {
  const { i18n, t } = useTranslation();

  function handleChange(locale: SupportedLocale) {
    localStorage.setItem(LOCALE_STORAGE_KEY, locale);
    void i18n.changeLanguage(locale);
  }

  const currentLocale = i18n.language as SupportedLocale;
  const current = LOCALE_OPTIONS.find((o) => o.value === currentLocale) ?? LOCALE_OPTIONS[1];

  return (
    <div className="lang-toggle" role="group" aria-label={t("lang_toggle.label")}>
      {LOCALE_OPTIONS.map((opt) => (
        <button
          key={opt.value}
          type="button"
          aria-pressed={opt.value === currentLocale}
          aria-label={opt.label}
          className={`lang-toggle__btn${opt.value === currentLocale ? " lang-toggle__btn--active" : ""}`}
          onClick={() => handleChange(opt.value)}
        >
          <span aria-hidden="true">{opt.flag}</span>
          <span>{opt.label}</span>
        </button>
      ))}
      <span className="sr-only">{t("lang_toggle.label")}: {current.label}</span>
    </div>
  );
}
