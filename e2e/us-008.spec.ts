import { test, expect } from "@playwright/test";
import { login } from "./fixtures";

// NOTE: No seeded data is available for this story. Journeys assert on
// structural elements (headings, labels, chart containers, buttons, audit
// trail entries) rather than specific station names or IDs.
// AC-3 (audit trail write-back with user ID / station ID / alert ID /
// timestamp) cannot be fully verified in the browser because the audit trail
// may live in a back-end log not surfaced in the UI; the journey verifies the
// dispatch confirmation UI and any in-app confirmation message instead.

test("US-008: Shift Lead opens Station Detail from Command Center and sees weld current and voltage traces", async ({
  page,
}) => {
  await login(page, "approver"); // Shift Lead maps to the "approver" persona

  // Navigate to the Command Center (assumed root or dedicated route)
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: /command center/i })
  ).toBeVisible();

  // Find the first weld cell card / row and open its detail
  const weldCellLink = page
    .getByRole("link", { name: /weld cell|station/i })
    .first();
  await expect(weldCellLink).toBeVisible();
  await weldCellLink.click();

  // Station Detail page should be open
  await expect(
    page.getByRole("heading", { name: /station detail/i })
  ).toBeVisible();

  // Live trace section must be present
  await expect(
    page.getByText(/weld current/i)
  ).toBeVisible();
  await expect(
    page.getByText(/weld voltage/i)
  ).toBeVisible();

  // The time range label should indicate the last 60 minutes
  await expect(page.getByText(/60\s*min(utes)?|last\s*60/i)).toBeVisible();

  // A chart / trace container must be rendered
  const chartContainer = page
    .locator("[data-testid='weld-trace-chart'], [aria-label*='trace'], canvas, svg")
    .first();
  await expect(chartContainer).toBeVisible();

  // Predictive alert threshold reference line label should be visible
  await expect(
    page.getByText(/threshold|alert threshold|reference/i)
  ).toBeVisible();
});

test("US-008: Active predictive alert highlights threshold crossing with timestamp and predicted time to failure", async ({
  page,
}) => {
  await login(page, "approver");

  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: /command center/i })
  ).toBeVisible();

  // Open the first station that shows an active alert badge
  const alertBadge = page
    .getByText(/alert|anomaly/i)
    .first();
  await expect(alertBadge).toBeVisible();

  // Navigate via the closest parent link / button to the station detail
  const stationLink = page
    .getByRole("link", { name: /weld cell|station/i })
    .first();
  await stationLink.click();

  await expect(
    page.getByRole("heading", { name: /station detail/i })
  ).toBeVisible();

  // Highlighted crossing point annotation on the trace
  await expect(
    page.getByText(/threshold crossing|crossing point|anomaly detected/i)
  ).toBeVisible();

  // Timestamp of the crossing must be displayed
  await expect(
    page.getByText(/\d{1,2}:\d{2}|\d{4}-\d{2}-\d{2}/i)
  ).toBeVisible();

  // Predicted time to failure must be surfaced
  await expect(
    page.getByText(/time to failure|predicted failure|TTF/i)
  ).toBeVisible();
});

test("US-008: Dispatch Maintenance from Station Detail logs event and shows confirmation", async ({
  page,
}) => {
  await login(page, "approver");

  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: /command center/i })
  ).toBeVisible();

  // Open first available station detail
  const stationLink = page
    .getByRole("link", { name: /weld cell|station/i })
    .first();
  await expect(stationLink).toBeVisible();
  await stationLink.click();

  await expect(
    page.getByRole("heading", { name: /station detail/i })
  ).toBeVisible();

  // Click the Dispatch Maintenance button
  const dispatchBtn = page.getByRole("button", {
    name: /dispatch maintenance/i,
  });
  await expect(dispatchBtn).toBeVisible();
  await dispatchBtn.click();

  // A confirmation dialog or modal should appear
  const confirmDialog = page.getByRole("dialog");
  await expect(confirmDialog).toBeVisible();
  await expect(
    confirmDialog.getByText(/confirm|are you sure|dispatch/i)
  ).toBeVisible();

  // Confirm the action
  const confirmBtn = confirmDialog.getByRole("button", {
    name: /confirm|yes|dispatch/i,
  });
  await expect(confirmBtn).toBeVisible();
  await confirmBtn.click();

  // Success feedback must be displayed (toast, banner, or status message)
  await expect(
    page.getByText(/dispatched|dispatch successful|maintenance requested/i)
  ).toBeVisible();
});

test("US-008: Station Detail 60-minute window is visible without an active alert (empty / baseline state)", async ({
  page,
}) => {
  await login(page, "approver");

  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: /command center/i })
  ).toBeVisible();

  // Attempt to open a station that has no active alert
  // (navigate directly via a known route pattern if no alert-free card is
  //  distinguishable — fall back to the first available station)
  const stationLinks = page.getByRole("link", { name: /weld cell|station/i });
  await expect(stationLinks.first()).toBeVisible();

  // Try to find a card without an alert badge; if none, use the first card
  const count = await stationLinks.count();
  let opened = false;
  for (let i = 0; i < count; i++) {
    const link = stationLinks.nth(i);
    const parentText = await link.locator("..").textContent();
    if (parentText && !/alert|anomaly/i.test(parentText)) {
      await link.click();
      opened = true;
      break;
    }
  }
  if (!opened) {
    await stationLinks.first().click();
  }

  await expect(
    page.getByRole("heading", { name: /station detail/i })
  ).toBeVisible();

  // Traces and the 60-minute window label must still be present
  await expect(page.getByText(/weld current/i)).toBeVisible();
  await expect(page.getByText(/weld voltage/i)).toBeVisible();
  await expect(page.getByText(/60\s*min(utes)?|last\s*60/i)).toBeVisible();

  // Threshold reference line label must still appear even without active alert
  await expect(
    page.getByText(/threshold|reference/i)
  ).toBeVisible();

  // Dispatch Maintenance button should still be present (Shift Lead can dispatch proactively)
  await expect(
    page.getByRole("button", { name: /dispatch maintenance/i })
  ).toBeVisible();
});

test("US-008: Dispatch Maintenance action is recorded in the audit trail section on Station Detail", async ({
  page,
}) => {
  // NOTE: This journey covers AC-3 as far as it is observable in the browser.
  // Full server-side audit log verification (user ID, station ID, alert ID in
  // the back-end store) is out of scope for a browser smoke test.

  await login(page, "approver");

  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: /command center/i })
  ).toBeVisible();

  const stationLink = page
    .getByRole("link", { name: /weld cell|station/i })
    .first();
  await expect(stationLink).toBeVisible();
  await stationLink.click();

  await expect(
    page.getByRole("heading", { name: /station detail/i })
  ).toBeVisible();

  // Dispatch and confirm
  const dispatchBtn = page.getByRole("button", {
    name: /dispatch maintenance/i,
  });
  await expect(dispatchBtn).toBeVisible();
  await dispatchBtn.click();

  const confirmDialog = page.getByRole("dialog");
  await expect(confirmDialog).toBeVisible();

  const confirmBtn = confirmDialog.getByRole("button", {
    name: /confirm|yes|dispatch/i,
  });
  await confirmBtn.click();

  // Wait for success feedback
  await expect(
    page.getByText(/dispatched|dispatch successful|maintenance requested/i)
  ).toBeVisible();

  // Audit trail section should now show the new dispatch entry
  const auditSection = page
    .getByRole("region", { name: /audit trail|history|activity log/i })
    .or(page.locator("[data-testid='audit-trail']"));

  await expect(auditSection).toBeVisible();

  // The entry should reference a dispatch action and include a timestamp
  await expect(
    auditSection.getByText(/dispatch|dispatched/i).first()
  ).toBeVisible();

  await expect(
    auditSection.getByText(/\d{1,2}:\d{2}|\d{4}-\d{2}-\d{2}/).first()
  ).toBeVisible();
});
