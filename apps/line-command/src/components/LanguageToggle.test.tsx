import { render, screen, act } from "@testing-library/react";
import { describe, expect, it, beforeEach } from "vitest";
import { I18nextProvider } from "react-i18next";
import i18n from "../i18n";
import { LanguageToggle } from "./LanguageToggle";
import { LOCALE_STORAGE_KEY } from "../i18n";

function renderToggle() {
  return render(
    <I18nextProvider i18n={i18n}>
      <LanguageToggle />
    </I18nextProvider>,
  );
}

beforeEach(async () => {
  localStorage.clear();
  await i18n.changeLanguage("en-GB");
});

describe("LanguageToggle — AC-2 language switch", () => {
  it("renders buttons for both supported locales", () => {
    renderToggle();
    expect(screen.getByRole("button", { name: "Deutsch" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "English" })).toBeTruthy();
  });

  it("marks the active locale button as pressed and the inactive one as not pressed", () => {
    renderToggle();
    const deutsch = screen.getByRole("button", { name: "Deutsch" });
    const english = screen.getByRole("button", { name: "English" });
    expect(deutsch.getAttribute("aria-pressed")).toBe("false");
    expect(english.getAttribute("aria-pressed")).toBe("true");
  });

  it("switches i18n language to de-DE when Deutsch button is clicked", async () => {
    renderToggle();
    const deutsch = screen.getByRole("button", { name: "Deutsch" });
    await act(async () => { deutsch.click(); });
    expect(i18n.language).toBe("de-DE");
  });

  it("persists de-DE to localStorage when Deutsch button is clicked", async () => {
    renderToggle();
    const deutsch = screen.getByRole("button", { name: "Deutsch" });
    await act(async () => { deutsch.click(); });
    expect(localStorage.getItem(LOCALE_STORAGE_KEY)).toBe("de-DE");
  });

  it("switches i18n language to en-GB when English button is clicked after being on de-DE", async () => {
    await i18n.changeLanguage("de-DE");
    renderToggle();
    const english = screen.getByRole("button", { name: "English" });
    await act(async () => { english.click(); });
    expect(i18n.language).toBe("en-GB");
  });

  it("persists en-GB to localStorage when English button is clicked", async () => {
    await i18n.changeLanguage("de-DE");
    renderToggle();
    const english = screen.getByRole("button", { name: "English" });
    await act(async () => { english.click(); });
    expect(localStorage.getItem(LOCALE_STORAGE_KEY)).toBe("en-GB");
  });
});
