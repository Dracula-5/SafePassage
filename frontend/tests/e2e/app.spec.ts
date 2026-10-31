// The web app in a real browser, used the way people use it. Every account and vehicle is labelled as a
// test, and the drill is labelled a drill. Needs OPERATOR_TOKEN and ENROLMENT_CODE from the running stack.
import { Browser, BrowserContext, expect, Page, test } from "@playwright/test";

const OPERATOR_CODE = process.env.OPERATOR_TOKEN ?? "";
const DRIVER_CODE = process.env.ENROLMENT_CODE ?? "";
const PASSWORD = "test account passphrase";
const RUN = Date.now().toString(36);
const VIJAYAWADA = { longitude: 80.75, latitude: 16.52 };
const DRILL_ZONE = [[80.6, 16.49], [80.66, 16.49], [80.66, 16.53], [80.6, 16.53], [80.6, 16.49]];

function email(role: string): string {
  return `e2e-${role}-${RUN}@example.org`;
}

async function signUp(page: Page, role: "operator" | "driver" | "supplier", name: string, code?: string, address = email(role)) {
  await page.goto(`/signup?role=${role}`);
  await page.getByLabel("Full name").fill(name);
  await page.getByLabel("Email").fill(address);
  await page.getByLabel("Password").fill(PASSWORD);
  if (code) await page.getByLabel(role === "operator" ? "Operator code" : "Enrolment code").fill(code);
  await page.getByRole("button", { name: "Create account" }).click();
}

async function person(browser: Browser, phone = false): Promise<BrowserContext> {
  return browser.newContext(phone ? { viewport: { width: 390, height: 844 }, permissions: ["geolocation"], geolocation: VIJAYAWADA } : {});
}

test("a stranger can see the live picture but nothing private", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("Active alerts")).toBeVisible();
  await page.goto("/dashboard");
  await expect(page).toHaveURL(/\/signin\?next=/);
  for (const path of ["/api/plans", "/api/vehicles", "/api/overview", "/api/accounts"]) {
    expect((await page.request.get(path)).status(), path).toBe(401);
  }
});

test("a wrong code keeps a stranger out of the operator role", async ({ page }) => {
  await signUp(page, "operator", "E2E Stranger", "guessing");
  await expect(page.locator(".alert.danger")).toContainText("not the operator code");
  await expect(page).toHaveURL(/\/signup/);
});

test("sign in, wrong password, and sign out", async ({ browser }) => {
  const first = await person(browser);
  const page = await first.newPage();
  await signUp(page, "supplier", "E2E Traders");
  await expect(page).toHaveURL(/\/supplier/);
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/signin/);

  await page.getByLabel("Email").fill(email("supplier"));
  await page.getByLabel("Password").fill("not the password");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.locator(".alert.danger")).toContainText("wrong email or password");

  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/supplier/);
  await first.close();
});

test("the dashboard stays live without being reloaded", async ({ browser }) => {
  const context = await person(browser);
  const page = await context.newPage();
  const refreshes: number[] = [];
  page.on("response", (r) => { if (r.url().endsWith("/api/overview") && r.ok()) refreshes.push(Date.now()); });
  await signUp(page, "operator", "E2E Officer", OPERATOR_CODE);
  await expect(page).toHaveURL(/\/dashboard/);
  await expect(page.locator(".live-pill")).toContainText("Live");
  await expect.poll(() => refreshes.length, { timeout: 25_000 }).toBeGreaterThanOrEqual(4);
  const gaps = refreshes.slice(1).map((at, i) => at - refreshes[i]);
  expect(Math.max(...gaps.slice(-2))).toBeLessThan(7_000);
  await context.close();
});

/** The link in the newest message to this address with this subject, from a Mailpit test mail server. */
async function linkInMail(mailbox: string, to: string, subject: string): Promise<string> {
  let link = "";
  await expect.poll(async () => {
    const search = await (await fetch(`${mailbox}/api/v1/search?query=${encodeURIComponent(`to:${to} subject:"${subject}"`)}`)).json();
    const id = search.messages?.[0]?.ID;
    if (!id) return "";
    const message = await (await fetch(`${mailbox}/api/v1/message/${id}`)).json();
    link = message.Text.match(/https?:\S+token=\S+/)?.[0] ?? "";
    return link;
  }, { timeout: 20_000 }).not.toBe("");
  return link;
}

test("an address is confirmed, and a forgotten password reset, by email", async ({ page }) => {
  const mailbox = process.env.E2E_MAILPIT_URL;
  test.skip(!mailbox, "needs a mail server: set E2E_MAILPIT_URL");
  const address = email("mail");
  await signUp(page, "supplier", "E2E Mail", undefined, address);
  await expect(page).toHaveURL(/\/supplier/);

  await page.goto(await linkInMail(mailbox!, address, "Confirm your ReliefRoute email"));
  await expect(page.getByText("Your email is confirmed.")).toBeVisible();
  await page.goto("/supplier");
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/signin/);

  await page.getByRole("link", { name: "Forgot your password?" }).click();
  // Next keeps the page you left mounted but hidden, so the sign-in form's Email field stays in the DOM.
  // Wait for the new page first: until then the old field is the visible one, and typing there is lost.
  await expect(page.getByRole("button", { name: "Send reset link" })).toBeVisible();
  await page.getByLabel("Email").locator("visible=true").fill(address);
  await page.getByRole("button", { name: "Send reset link" }).click();
  await expect(page.getByText("a link is on its way")).toBeVisible();

  const newPassword = "a brand new passphrase";
  await page.goto(await linkInMail(mailbox!, address, "Reset your ReliefRoute password"));
  await page.getByLabel("New password").fill(newPassword);
  await page.getByRole("button", { name: "Save password" }).click();
  await expect(page.getByText("Password changed.")).toBeVisible();

  await page.goto("/signin");
  await page.getByLabel("Email").fill(address);
  await page.getByLabel("Password").fill(newPassword);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/supplier/);
});

test("a drill goes from the map to the driver's phone", async ({ browser }) => {
  test.skip(!process.env.E2E_ROUTING, "needs the road map: set E2E_ROUTING=1");
  test.setTimeout(420_000);

  const phone = await person(browser, true);
  const driver = await phone.newPage();
  await signUp(driver, "driver", "E2E Driver", DRIVER_CODE);
  await expect(driver).toHaveURL(/\/driver/);
  await driver.getByLabel("Vehicle number").fill(`TEST-SIM-GPS-E2E-${RUN}`);
  await driver.getByLabel("Load capacity (kg)").fill("6000");
  await driver.getByRole("button", { name: "Register and start" }).click();
  await expect(driver.getByText("No route yet")).toBeVisible();
  await expect(driver.getByText("±", { exact: false })).toBeVisible();

  const desk = await person(browser);
  const officer = await desk.newPage();
  await officer.goto("/signin");
  await officer.getByLabel("Email").fill(email("operator"));
  await officer.getByLabel("Password").fill(PASSWORD);
  await officer.getByRole("button", { name: "Sign in" }).click();
  await expect(officer).toHaveURL(/\/dashboard/);

  const drill = await officer.request.post("/api/drill", { data: {
    geometry: { type: "Polygon", coordinates: [DRILL_ZONE] }, hazard_type: "flood", severity: "severe", label: `E2E ${RUN}`, ttl_minutes: 30,
  } });
  expect(drill.status()).toBe(201);
  const hazard = (await drill.json()).id as string;

  // This drill's own plan: an older one may be waiting too.
  let planId = "";
  await expect.poll(async () => {
    const plans: { id: string; status: string; hazard_ids: string[] }[] = await (await officer.request.get("/api/plans")).json();
    planId = plans.find((p) => p.hazard_ids.includes(hazard) && p.status === "awaiting_approval")?.id ?? "";
    return planId;
  }, { timeout: 240_000 }).not.toBe("");
  await officer.locator(`[data-plan="${planId}"]`).click();
  await expect(officer.getByText(`Recorded as E2E Officer (${email("operator")})`)).toBeVisible();
  await officer.getByRole("button", { name: "Approve dispatch" }).click();

  await expect(driver.getByText("Next stop")).toBeVisible({ timeout: 90_000 });
  await expect(driver.getByText("DRILL. This is not a real dispatch.")).toBeVisible();
  await expect(officer.locator(".plan-detail")).toContainText(`by E2E Officer <${email("operator")}>`);

  expect((await officer.request.delete(`/api/drill/${hazard}`)).status()).toBe(204);
  await phone.close();
  await desk.close();
});
