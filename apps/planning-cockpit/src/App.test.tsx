import "../src/i18n";
import { render, screen } from "@testing-library/react";
import { App } from "./App";

test("renders the app shell heading in the active language", async () => {
  const { default: i18next } = await import("./i18n");
  await i18next.changeLanguage("en-GB");

  render(<App />);
  expect(screen.getByRole("heading", { name: "Planning Cockpit" })).toBeTruthy();
});

test("renders German heading when language is de-DE", async () => {
  const { default: i18next } = await import("./i18n");
  await i18next.changeLanguage("de-DE");

  render(<App />);
  expect(screen.getByRole("heading", { name: "Planungs-Cockpit" })).toBeTruthy();
});
