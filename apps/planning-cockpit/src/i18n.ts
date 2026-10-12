import i18next from "i18next";
import LanguageDetector from "i18next-browser-languagedetector";
import { initReactI18next } from "react-i18next";

import deDE from "./locales/de-DE.json";
import enGB from "./locales/en-GB.json";

export const SUPPORTED_LANGUAGES = ["de-DE", "en-GB"] as const;
export type SupportedLanguage = (typeof SUPPORTED_LANGUAGES)[number];

export const LANGUAGE_STORAGE_KEY = "ifos_planning_cockpit_lang";

i18next
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources: {
      "de-DE": { translation: deDE },
      "en-GB": { translation: enGB },
    },
    fallbackLng: "en-GB",
    supportedLngs: SUPPORTED_LANGUAGES,
    detection: {
      order: ["localStorage", "navigator"],
      lookupLocalStorage: LANGUAGE_STORAGE_KEY,
      caches: ["localStorage"],
    },
    interpolation: {
      escapeValue: false,
    },
    react: {
      useSuspense: false,
    },
  });

export default i18next;
