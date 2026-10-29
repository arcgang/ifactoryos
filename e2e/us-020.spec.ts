import { test, expect } from "@playwright/test";
import { login } from "./fixtures";

// US-020: Acceptance Criterion 1 (partial) — browser-observable side: after a tracked action completes
// the UI reflects success and the action is registered. Full event-layer timing (<1 s) is an
// infrastructure concern not visible in the browser; we verify the action completes successfully
// and the UI confirms it, which is the trigger for the emission.

// US-020: Acceptance Criterion 2 — WAU calculation exclusion of login-only sessions is a
// backend/analytics concern with no browser-visible surface; covered by a comment below.

// ---------------------------------------------------------------------------
// Journey 1: Shift Lead submits a handover — action completes, success state visible
// ---------------------------------------------------------------------------
test("US-020: Shift Lead submits a handover and sees confirmation", async ({ page }) => {
  await login(page, "approver"); // Shift Lead maps to approver persona

  // Navigate to Line Command handover section
  await page.getByRole("link", { name: /line command/i }).click();
  await page.getByRole("link", { name: /handover/i }).click();

  // Fill in a handover form
  await page.getByLabel(/station/i).fill("STATION-01");
  await page.getByLabel(/notes|summary|comments/i).fill("End-of-shift handover for US-020 smoke test");

  // Submit
  await page.getByRole("button", { name: /submit handover/i }).click();

  // Assert success feedback visible — event is emitted on this completion
  await expect(
    page.getByRole("status").or(page.getByText(/handover submitted/i)).or(page.getByText(/success/i))
  ).toBeVisible();
});

// ---------------------------------------------------------------------------
// Journey 2: Assembly Associate acknowledges an alert — action completes, acknowledgment recorded
// ---------------------------------------------------------------------------
test("US-020: Assembly Associate acknowledges an alert and sees acknowledged state", async ({ page }) => {
  await login(page, "cardholder"); // Assembly Associate maps to cardholder persona

  await page.getByRole("link", { name: /line command/i }).click();
  await page.getByRole("link", { name: /alerts/i }).click();

  // Expect the alerts list or empty state to load
  const alertsHeading = page.getByRole("heading", { name: /alerts/i });
  await expect(alertsHeading).toBeVisible();

  // If there are alerts, acknowledge the first one; otherwise assert empty state
  const firstAlert = page.getByRole("listitem").first();
  const hasAlerts = await firstAlert.isVisible().catch(() => false);

  if (hasAlerts) {
    await firstAlert.getByRole("button", { name: /acknowledge/i }).click();
    // Badge or status should update
    await expect(
      firstAlert.getByText(/acknowledged/i).or(firstAlert.getByRole("status"))
    ).toBeVisible();
  } else {
    // Empty state is acceptable — no alerts to acknowledge
    await expect(
      page.getByText(/no alerts/i).or(page.getByText(/all clear/i)).or(page.getByText(/0 alerts/i))
    ).toBeVisible();
  }
});

// ---------------------------------------------------------------------------
// Journey 3: Assembly Associate starts an AR session — event payload includes VIN, device type, station
// (AC 3) — we verify the form captures these required fields before submission
// ---------------------------------------------------------------------------
test("US-020: AR session start form captures VIN, device type, and station ID", async ({ page }) => {
  await login(page, "cardholder");

  await page.getByRole("link", { name: /line command/i }).click();
  await page.getByRole("link", { name: /ar|augmented reality|workbench/i }).click();

  const arHeading = page.getByRole("heading", { name: /ar|augmented reality|workbench/i });
  await expect(arHeading).toBeVisible();

  // The session-start form must expose VIN, device type, and station ID fields (AC 3)
  await expect(page.getByLabel(/vin/i)).toBeVisible();
  await expect(page.getByLabel(/device type/i)).toBeVisible();
  await expect(page.getByLabel(/station/i)).toBeVisible();

  // Device type choices must include HoloLens 2 and iPad (AC 3)
  const deviceSelect = page.getByLabel(/device type/i);
  await deviceSelect.click();
  await expect(
    page.getByRole("option", { name: /hololens 2/i }).or(page.getByText(/hololens 2/i))
  ).toBeVisible();
  await expect(
    page.getByRole("option", { name: /ipad/i }).or(page.getByText(/ipad/i))
  ).toBeVisible();
});

// ---------------------------------------------------------------------------
// Journey 4: Assembly Associate submits a torque log — action completes, row appears in log
// ---------------------------------------------------------------------------
test("US-020: Assembly Associate submits a torque log and sees it in the log list", async ({ page }) => {
  await login(page, "cardholder");

  await page.getByRole("link", { name: /line command/i }).click();
  await page.getByRole("link", { name: /torque/i }).click();

  const torqueHeading = page.getByRole("heading", { name: /torque/i });
  await expect(torqueHeading).toBeVisible();

  // Fill torque log fields
  await page.getByLabel(/station/i).fill("STATION-02");
  await page.getByLabel(/vin/i).fill("1HGBH41JXMN109186");
  await page.getByLabel(/torque value|value/i).fill("85");
  await page.getByLabel(/unit/i).selectOption({ label: /nm|newton/i }).catch(async () => {
    // unit might be a text input
    await page.getByLabel(/unit/i).fill("Nm");
  });

  await page.getByRole("button", { name: /submit|log torque/i }).click();

  // Confirmation or new row visible
  await expect(
    page.getByText(/torque log submitted/i)
      .or(page.getByText(/success/i))
      .or(page.getByRole("row", { name: /1HGBH41JXMN109186/i }))
  ).toBeVisible();
});

// ---------------------------------------------------------------------------
// Journey 5: Login-only session — no meaningful actions performed
// (AC 2: such sessions are excluded from WAU — this is a backend analytics rule
//  with no browser-visible confirmation UI; we document the expected behaviour here
//  but cannot assert it through the browser. The test below verifies the user can
//  reach Line Command without performing any tracked action, confirming the
//  "login-only" path is reachable and the app does not force an action.)
// ---------------------------------------------------------------------------
test("US-020: Login-only session — user reaches Line Command dashboard without performing any tracked action", async ({ page }) => {
  await login(page, "cardholder");

  // Navigate to Line Command without triggering any tracked action
  await page.getByRole("link", { name: /line command/i }).click();

  // The dashboard should load and show navigation options but we do NOT interact further
  const dashboard = page
    .getByRole("heading", { name: /line command/i })
    .or(page.getByRole("main"))
    .or(page.getByText(/welcome/i));

  await expect(dashboard).toBeVisible();

  // Verify that action links (handover, alerts, torque, AR) are present but we leave them untouched
  await expect(page.getByRole("link", { name: /handover/i })).toBeVisible();
  await expect(page.getByRole("link", { name: /alerts/i })).toBeVisible();

  // NOTE: AC 2 — this login-only session must NOT be counted as an active day in WAU.
  // That exclusion logic lives in the instrumentation/analytics service and cannot be
  // asserted via the browser UI; it must be validated through backend event-store queries
  // or analytics dashboards outside the scope of this Playwright spec.
});
