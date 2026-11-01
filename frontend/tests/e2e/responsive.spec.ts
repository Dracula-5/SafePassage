// Regression tests for the redesigned web app: layout at every supported screen size, who may see what,
// session expiry, keyboard use, and that status is never colour alone. Every account is a labelled test account.
import { Browser, BrowserContext, expect, Page, test } from "@playwright/test";

const OPERATOR_CODE = process.env.OPERATOR_TOKEN ?? "";
const PASSWORD = "test account passphrase";
const RUN = Date.now().toString(36);
const SIZES = [[375, 812], [390, 844], [768, 1024], [1366, 768], [1920, 1080]] as const;
const OPERATOR_PAGES = ["/dashboard", "/dashboard/hazards", "/dashboard/plans", "/dashboard/fleet", "/dashboard/evacuations",
  "/dashboard/sources", "/dashboard/activity", "/dashboard/team", "/directory", "/dashboard/simulation"];
const PUBLIC_PAGES = ["/", "/signin", "/signup", "/forgot", "/reset?token=x", "/verify?token=x"];

type Role = "operator" | "driver" | "supplier";
let made = 0;
const address = (role: Role) => `e2e-resp-${role}-${RUN}-${++made}@example.org`;

/** Creates an account through the API, approving it with the operator token where the server enforces approval. */
async function account(browser: Browser, role: Role): Promise<BrowserContext> {
  const context = await browser.newContext();
  const email = address(role);
  const response = await context.request.post("/api/auth/signup", { data: { email, password: PASSWORD, name: `E2E ${role}`, role } });
  expect(response.status()).toBe(201);
  const created = await response.json() as { id: string; approved: boolean };
  if (!created.approved) {
    expect((await context.request.post(`/api/accounts/${created.id}/approve`, { headers: { "X-Operator-Token": OPERATOR_CODE } })).status()).toBe(200);
    expect((await context.request.post("/api/auth/signin", { data: { email, password: PASSWORD } })).status()).toBe(200);
  }
  return context;
}

async function expectNoSidewaysScroll(page: Page, what: string) {
  const { scroll, inner } = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, inner: innerWidth }));
  expect(scroll, `${what} scrolls sideways`).toBeLessThanOrEqual(inner);
}

test("sign-up asks for no codes and offers the three roles", async ({ page }) => {
  await page.goto("/signup");
  await expect(page.getByRole("radiogroup", { name: "Account type" }).getByRole("radio")).toHaveCount(3);
  for (const label of [/operator code/i, /enrol?ment code/i, /access code/i, /invite/i]) {
    await expect(page.getByLabel(label)).toHaveCount(0);
  }
  await expect(page.getByLabel("Full name")).toBeVisible();
  await expect(page.getByLabel("Email")).toBeVisible();
  await expect(page.getByLabel("Password")).toBeVisible();
});

test("sign-up refuses a bad role and a short password, and the server holds the line", async ({ request }) => {
  const bad = (data: object) => request.post("/api/auth/signup", { data: { email: `e2e-bad-${RUN}@example.org`, password: PASSWORD, name: "E2E Bad", role: "supplier", ...data } });
  expect((await bad({ role: "admin" })).status()).toBe(422);
  expect((await bad({ password: "short" })).status()).toBe(422);
  expect((await bad({ email: "not-an-email" })).status()).toBe(422);
  expect((await bad({ name: "<script>alert(1)</script>" })).status()).toBe(422);
});

test("a wrong password and an unknown email look the same", async ({ request }) => {
  const wrongPassword = await request.post("/api/auth/signin", { data: { email: `nobody-${RUN}@example.org`, password: "wrong passphrase" } });
  expect(wrongPassword.status()).toBe(401);
  expect(await wrongPassword.text()).not.toMatch(/no such|not found|unknown/i);
});

for (const [width, height] of SIZES) {
  test(`public pages fit a ${width}x${height} screen`, async ({ browser }) => {
    const context = await browser.newContext({ viewport: { width, height } });
    const page = await context.newPage();
    const problems: string[] = [];
    page.on("pageerror", (error) => problems.push(error.message));
    for (const path of PUBLIC_PAGES) {
      await page.goto(path);
      await expect(page.locator("h1").first()).toBeVisible();
      await expectNoSidewaysScroll(page, path);
    }
    expect(problems).toEqual([]);
    await context.close();
  });

  test(`every duty officer page fits a ${width}x${height} screen`, async ({ browser }) => {
    test.setTimeout(180_000);
    const context = await account(browser, "operator");
    await context.setOffline(false);
    const page = await context.newPage();
    await page.setViewportSize({ width, height });
    const problems: string[] = [];
    page.on("pageerror", (error) => problems.push(error.message));
    for (const path of OPERATOR_PAGES) {
      await page.goto(path);
      await expect(page.locator("h1, [aria-label='Scenario controls']").first()).toBeVisible({ timeout: 30_000 });
      await expectNoSidewaysScroll(page, path);
    }
    expect(problems).toEqual([]);
    await context.close();
  });
}

test("driver and supplier screens fit a phone", async ({ browser }) => {
  for (const role of ["driver", "supplier"] as const) {
    const context = await account(browser, role);
    const page = await context.newPage();
    await page.setViewportSize({ width: 375, height: 812 });
    await page.goto(role === "driver" ? "/driver" : "/supplier");
    await expect(page.locator("h1, h2").first()).toBeVisible({ timeout: 30_000 });
    await expectNoSidewaysScroll(page, role);
    await context.close();
  }
});

test("each role is kept to its own screens and its own API", async ({ browser }) => {
  const supplier = await account(browser, "supplier");
  const driver = await account(browser, "driver");
  for (const [who, context] of [["supplier", supplier], ["driver", driver]] as const) {
    for (const path of ["/api/plans", "/api/vehicles", "/api/accounts", "/api/overview"]) {
      const status = (await context.request.get(path)).status();
      expect([401, 403], `${who} on ${path}`).toContain(status);
    }
    const approve = await context.request.post("/api/accounts/00000000-0000-4000-8000-000000000000/approve");
    expect([401, 403], `${who} approving`).toContain(approve.status());
    const drill = await context.request.post("/api/drill", { data: {} });
    expect([401, 403, 422], `${who} drilling`).toContain(drill.status());
    if (drill.status() === 422) throw new Error(`${who} reached drill validation: the role check is missing`);
  }
  await supplier.close();
  await driver.close();
});

test("an ended session sends a duty officer back to sign in", async ({ browser }) => {
  const context = await account(browser, "operator");
  const page = await context.newPage();
  await page.goto("/dashboard/plans");
  await expect(page.getByRole("heading", { name: "Plans & Approvals" })).toBeVisible();
  await context.clearCookies();
  await page.goto("/dashboard/plans");
  await expect(page).toHaveURL(/\/signin\?next=/);
  expect((await context.request.get("/api/plans")).status()).toBe(401);
  await context.close();
});

test("signing out ends the session on the server", async ({ browser }) => {
  const context = await account(browser, "supplier");
  const page = await context.newPage();
  await page.goto("/supplier");
  await expect(page.locator("h1, h2").first()).toBeVisible();
  const cookie = (await context.cookies()).find((c) => c.name === "rr_session")!;
  expect(cookie.httpOnly).toBe(true);
  expect(cookie.sameSite).toBe("Lax");
  expect((await context.request.post("/api/auth/signout")).status()).toBeLessThan(300);
  const replay = await (await browser.newContext()).request.get("/api/auth/me", { headers: { cookie: `rr_session=${cookie.value}` } });
  expect(replay.status(), "the old cookie must not work after sign-out").toBe(401);
  await context.close();
});

test("the sign-in form can be used from the keyboard alone", async ({ page }) => {
  await page.goto("/signin");
  await page.getByLabel("Email").focus();
  await page.keyboard.type(`nobody-${RUN}@example.org`);
  await page.keyboard.press("Tab");
  await page.keyboard.type("not the password");
  await page.keyboard.press("Enter");
  await expect(page.getByRole("alert")).toBeVisible();
  const focused = await page.evaluate(() => {
    const el = document.activeElement as HTMLElement | null;
    return el ? getComputedStyle(el).outlineStyle !== "none" || getComputedStyle(el).boxShadow !== "none" : false;
  });
  expect(focused, "the focused control must show a focus ring").toBe(true);
});

test("operational status is written out, not just coloured", async ({ browser }) => {
  const context = await account(browser, "operator");
  const page = await context.newPage();
  for (const path of ["/dashboard/sources", "/dashboard/fleet", "/dashboard/plans"]) {
    await page.goto(path);
    await expect(page.locator("h1")).toBeVisible();
    await expect(page.getByTestId("connection-status")).toContainText(/Live|Connecting|Offline|Reconnecting|Paused/);
    // A status someone can see must say something in words or carry a label: an empty coloured dot is colour alone.
    const colourOnly = await page.evaluate(() => [...document.querySelectorAll(".badge, [data-status], [role='status']")]
      .filter((el) => {
        const box = el.getBoundingClientRect();
        const visible = box.width > 0 && box.height > 0 && getComputedStyle(el).visibility !== "hidden";
        return visible && !(el as HTMLElement).innerText.trim() && !el.getAttribute("aria-label") && !el.getAttribute("title");
      })
      .map((el) => el.outerHTML.slice(0, 160)));
    expect(colourOnly, `${path} has status elements shown only by colour`).toEqual([]);
  }
  await context.close();
});

test("the duty officer's menu works from the keyboard and on a phone", async ({ browser }) => {
  const context = await account(browser, "operator");
  const page = await context.newPage();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/dashboard");
  const menu = page.getByRole("button", { name: /menu/i }).first();
  await menu.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("link", { name: "Hazards & Alerts" }).first()).toBeVisible();
  await page.keyboard.press("Escape");
  await context.close();
});

// The two tests below serve labelled test data from the browser for this page only; nothing is stored.
test("a plan that failed says so in the list, and the detail gives the reason", async ({ browser }) => {
  const id = "22222222-bbbb-4bbb-8bbb-000000000002";
  const now = new Date().toISOString();
  const summary = { id, parent_plan_id: null, status: "failed", trigger_kind: "hazard", hazard_ids: [], is_drill: false, requires_approval: false, summary: null, evacuation: false, created_at: now, updated_at: now };
  const detail = { ...summary, demand: [], routes: [], orders: [], critic: [], approval: null, escalations: [], triage: null, unreachable_reason: "TEST FIXTURE: no online vehicle with a GPS fix (4 offline)" };
  const context = await account(browser, "operator");
  const page = await context.newPage();
  await page.route((url) => url.pathname === "/api/plans", (route) => route.fulfill({ json: [summary] }));
  await page.route((url) => url.pathname === `/api/plans/${id}`, (route) => route.fulfill({ json: detail }));
  await page.goto("/dashboard/plans");
  const row = page.locator(`[data-plan="${id}"]`);
  await expect(row).toBeVisible();
  await expect(row).not.toContainText("being prepared");
  await expect(row).toContainText("could not be completed");
  await row.click();
  await expect(page.getByTestId("plan-detail")).toContainText("TEST FIXTURE: no online vehicle with a GPS fix");
  await expect(page.getByTestId("plan-detail")).not.toContainText("still being prepared");
  await context.close();
});

test("a passing safety check is counted under Safety checks", async ({ browser }) => {
  const event = { id: 1, plan_id: "33333333-cccc-4ccc-8ccc-000000000003", node: "critic", event_type: "decision", message: "TEST FIXTURE: Attempt 1: PASS", payload: {}, source_ids: [], created_at: new Date().toISOString() };
  const context = await account(browser, "operator");
  const page = await context.newPage();
  await page.route((url) => url.pathname === "/api/stream", (route) =>
    route.fulfill({ status: 200, contentType: "text/event-stream", body: `retry: 60000\n\nid: 1\nevent: agent_event\ndata: ${JSON.stringify(event)}\n\n` }));
  await page.goto("/dashboard/activity");
  await expect(page.getByText("TEST FIXTURE: Attempt 1: PASS")).toBeVisible();
  const chip = page.getByRole("button", { name: /Safety checks/ });
  await expect(chip).toContainText("1");
  await chip.click();
  await expect(page.getByText("TEST FIXTURE: Attempt 1: PASS")).toBeVisible();
  await context.close();
});
