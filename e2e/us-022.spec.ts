import { test, expect } from "@playwright/test";
import { login } from "./fixtures";

// US-022: Console-level RBAC enforced
//
// Note: AC-3 (role propagation within 5 minutes without restart) cannot be
// demonstrated end-to-end in a browser automation test because it requires
// waiting for an identity-provider sync cycle and observing timing guarantees.
// That criterion is skipped here with an explanatory comment.

// ---------------------------------------------------------------------------
// AC-1 (partial): Production Planner is denied access to Line Command
// ---------------------------------------------------------------------------
test("US-022: Production Planner is forbidden from accessing Line Command", async ({
  page,
}) => {
  // Sign in as a Production Planner (mapped to the 'program_admin' persona
  // which has the Production Planner role in the seeded identity provider)
  await login(page, "program_admin");

  // Attempt to navigate directly to Line Command
  await page.goto("/line-command");

  // The user should see a 403 / Access Denied page or an error message
  await expect(
    page.getByRole("heading", {
      name: /403|Forbidden|Access Denied|Not Authorized/i,
    })
  ).toBeVisible();

  // The page must not render Line Command content
  await expect(page.getByText(/Line Command/i)).not.toBeVisible();
});

// ---------------------------------------------------------------------------
// AC-1 (partial): Production Planner is denied access to Quality Studio
// ---------------------------------------------------------------------------
test("US-022: Production Planner is forbidden from accessing Quality Studio", async ({
  page,
}) => {
  await login(page, "program_admin");

  // Attempt to navigate directly to Quality Studio
  await page.goto("/quality-studio");

  // Expect a 403 / access-denied indicator
  await expect(
    page.getByRole("heading", {
      name: /403|Forbidden|Access Denied|Not Authorized/i,
    })
  ).toBeVisible();

  await expect(page.getByText(/Quality Studio/i)).not.toBeVisible();
});

// ---------------------------------------------------------------------------
// AC-1 (audit trail): Unauthorised access attempt is recorded
// ---------------------------------------------------------------------------
test("US-022: Unauthorised access attempt by Production Planner appears in audit trail", async ({
  page,
}) => {
  await login(page, "program_admin");

  // Trigger an unauthorised navigation to Line Command so an audit record
  // is created
  await page.goto("/line-command");
  await expect(
    page.getByRole("heading", {
      name: /403|Forbidden|Access Denied|Not Authorized/i,
    })
  ).toBeVisible();

  // Navigate to the audit trail (accessible to admins / system pages)
  await page.goto("/admin/audit-trail");

  // The audit trail page must load
  await expect(
    page.getByRole("heading", { name: /Audit Trail|Audit Log/i })
  ).toBeVisible();

  // There should be at least one row mentioning an unauthorised / forbidden
  // access attempt for Line Command
  await expect(
    page.getByText(/Line Command/i, { exact: false }).first()
  ).toBeVisible();

  await expect(
    page.getByText(/403|Forbidden|Unauthoris|Unauthorized/i, {
      exact: false,
    }).first()
  ).toBeVisible();
});

// ---------------------------------------------------------------------------
// AC-2: Assembly Associate can access AR Workbench in Line Command but
//        cannot see Command Center OEE data or Handover Builder
// ---------------------------------------------------------------------------
test("US-022: Assembly Associate accesses AR Workbench but is denied Command Center OEE and Handover Builder", async ({
  page,
}) => {
  // The 'cardholder' persona maps to the Assembly Associate role in the
  // seeded identity provider
  await login(page, "cardholder");

  // Navigate to Line Command landing page
  await page.goto("/line-command");

  // The Assembly Associate should land successfully in Line Command
  await expect(
    page.getByRole("heading", { name: /Line Command/i })
  ).toBeVisible();

  // AR Workbench should be accessible
  const arWorkbenchLink = page.getByRole("link", {
    name: /AR Workbench/i,
  });
  await expect(arWorkbenchLink).toBeVisible();
  await arWorkbenchLink.click();

  // AR Workbench page loads with work instructions / torque logging
  await expect(
    page.getByRole("heading", { name: /AR Workbench/i })
  ).toBeVisible();

  // Work instructions section visible
  await expect(
    page.getByText(/Work Instructions|AR Work Instructions/i, { exact: false })
  ).toBeVisible();

  // Torque logging section visible
  await expect(
    page.getByText(/Torque Log|Torque Logging/i, { exact: false })
  ).toBeVisible();

  // Command Center OEE section must NOT be present for this role
  await expect(
    page.getByText(/Command Center|OEE/i, { exact: false })
  ).not.toBeVisible();

  // Handover Builder must NOT be present for this role
  await expect(
    page.getByText(/Handover Builder/i, { exact: false })
  ).not.toBeVisible();
});

// ---------------------------------------------------------------------------
// AC-3: SKIPPED – cannot be demonstrated in a browser automation test.
// Verifying that role changes propagate within 5 minutes without a platform
// restart requires timing/infrastructure-level observation that is outside
// the scope of a Playwright end-to-end smoke journey.
// ---------------------------------------------------------------------------
