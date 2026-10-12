import i18n from "i18next";
import LanguageDetector from "i18next-browser-languagedetector";
import { initReactI18next } from "react-i18next";

import deDE from "./locales/de-DE.json";
import enGB from "./locales/en-GB.json";

export const SUPPORTED_LOCALES = ["de-DE", "en-GB"] as const;
export type SupportedLocale = (typeof SUPPORTED_LOCALES)[number];

export const LOCALE_STORAGE_KEY = "lc_locale";

i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources: {
      "de-DE": { translation: deDE },
      "en-GB": { translation: enGB },
    },
    fallbackLng: "en-GB",
    supportedLngs: ["de-DE", "en-GB"],
    detection: {
      order: ["localStorage", "navigator"],
      lookupLocalStorage: LOCALE_STORAGE_KEY,
      caches: ["localStorage"],
    },
    interpolation: {
      escapeValue: false,
    },
    missingKeyHandler: (lngs, _ns, key) => {
      console.warn(`[i18n] Missing translation key "${key}" for locale(s): ${lngs.join(", ")}`);
    },
    saveMissing: true,
  });

export default i18n;
