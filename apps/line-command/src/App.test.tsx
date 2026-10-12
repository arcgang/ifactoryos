import { render, screen } from "@testing-library/react";
import { App } from "./App";

test("renders the app shell heading", () => {
  render(<App />);
  expect(screen.getByRole("heading", { name: "Web" })).toBeTruthy();
});
