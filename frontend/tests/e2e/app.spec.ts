// The web app in a real browser, used the way people use it. Every account and vehicle is labelled as a
// test, and the drill is labelled a drill. Needs OPERATOR_TOKEN from the running stack.
import { Browser, BrowserContext, expect, Page, test } from "@playwright/test";

const OPERATOR_CODE = process.env.OPERATOR_TOKEN ?? "";
const PASSWORD = "test account passphrase";
const RUN = Date.now().toString(36);
const VIJAYAWADA = { longitude: 80.75, latitude: 16.52 };
const DRILL_ZONE = [[80.6, 16.49], [80.66, 16.49], [80.66, 16.53], [80.6, 16.53], [80.6, 16.49]];

function email(role: string): string {
  return `e2e-${role}-${RUN}@example.org`;
}

async function signUp(page: Page, role: "operator" | "driver" | "supplier", name: string, address = email(role)) {
  await page.goto(`/signup?role=${role}`);
  await page.getByLabel("Full name").fill(name);
  await page.getByLabel("Email").fill(address);
  await page.getByLabel("Password").fill(PASSWORD);
  const signupResponse = page.waitForResponse((response) =>
    response.url().endsWith("/api/auth/signup") && response.request().method() === "POST");
  await page.getByRole("button", { name: "Create account" }).click();
  const response = await signupResponse;
  expect(response.status()).toBe(201);
  const account = await response.json() as { id: string; approved: boolean };
  if (!account.approved) {
    // Only where approval is enforced (production): a duty officer approves, then the person signs in.
    await expect(page.getByRole("status")).toContainText("pending approval");
    const approval = await page.request.post(`/api/accounts/${account.id}/approve`, {
      headers: { "X-Operator-Token": OPERATOR_CODE },
    });
    expect(approval.status()).toBe(200);
    await page.goto("/signin");
    await page.getByLabel("Email").fill(address);
    await page.getByLabel("Password").fill(PASSWORD);
    await page.getByRole("button", { name: "Sign in" }).click();
  } else {
    // Local development: the account is usable at once, and the page goes straight to its own screen.
    expect(role === "supplier" || account.approved).toBe(true);
  }
}

/** Whether the server enforces duty officer and driver approval (production). Local development does not. */
async function approvalEnforced(page: Page): Promise<boolean> {
  return (await (await page.request.get("/api/auth/options")).json() as { approval: boolean }).approval;
}

/** One dropped or non-JSON answer while the stack is busy is not a verdict on the plan: the caller polls again. */
async function listPlans<T>(page: Page, headers?: Record<string, string>): Promise<T[]> {
  try {
    const response = await page.request.get("/api/plans", headers ? { headers } : undefined);
    return response.ok() ? await response.json() as T[] : [];
  } catch {
    return [];
  }
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

test("the map's tile address only takes tile numbers", async ({ page }) => {
  for (const path of ["/tiles/99/1/1.png", "/tiles/7/91/abc.png", "/tiles/7/91/..%2f..%2fetc.png"]) {
    expect((await page.request.get(path)).status(), path).toBe(404);
  }
});

test("a wrong code keeps a stranger out of the operator role", async ({ page }) => {
  await signUp(page, "operator", "E2E Stranger", "guessing");
  await expect(page.locator(".alert.danger")).toContainText("not the operator code");
  await expect(page).toHaveURL(/\/signup/);
});

for (const role of ["operator", "driver"] as const) {
  test(`${role} signup stays pending until an authorized approver approves it`, async ({ page }) => {
    test.skip(!(await approvalEnforced(page)), "approval is only enforced in production");
    const address = email(role);
    await page.goto(`/signup?role=${role}`);
    await expect(page.getByLabel("Operator code")).toHaveCount(0);
    await expect(page.getByLabel("Enrolment code")).toHaveCount(0);
    await page.getByLabel("Full name").fill(`E2E ${role}`);
    await page.getByLabel("Email").fill(address);
    await page.getByLabel("Password").fill(PASSWORD);
    const signupResponse = page.waitForResponse((response) =>
      response.url().endsWith("/api/auth/signup") && response.request().method() === "POST");
    await page.getByRole("button", { name: "Create account" }).click();
    const response = await signupResponse;
    expect(response.status()).toBe(201);
    const account = await response.json() as { id: string; approved: boolean };
    expect(account.approved).toBe(false);
    await expect(page.getByRole("status")).toContainText("pending approval");
    await expect(page).toHaveURL(/\/signup/);

    const blockedLogin = await page.request.post("/api/auth/signin", { data: { email: address, password: PASSWORD } });
    expect(blockedLogin.status()).toBe(403);
    expect((await blockedLogin.json()).detail).toContain("pending approval");
    const unauthorizedApproval = await page.request.post(`/api/accounts/${account.id}/approve`);
    expect(unauthorizedApproval.status()).toBe(401);
    const approval = await page.request.post(`/api/accounts/${account.id}/approve`, {
      headers: { "X-Operator-Token": OPERATOR_CODE },
    });
    expect(approval.status()).toBe(200);

    await page.goto("/signin");
    await page.getByLabel("Email").fill(address);
    await page.getByLabel("Password").fill(PASSWORD);
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page).toHaveURL(role === "operator" ? /\/dashboard/ : /\/driver/);
  });
}

test("an approved duty officer approves a pending driver in the Team panel", async ({ browser }) => {
  const probe = await browser.newContext();
  const probing = await probe.newPage();
  const enforced = await approvalEnforced(probing);
  await probe.close();
  test.skip(!enforced, "approval is only enforced in production");
  const officerContext = await person(browser);
  const officer = await officerContext.newPage();
  await signUp(officer, "operator", "E2E Approval Officer", email("approval-officer"));
  await expect(officer).toHaveURL(/\/dashboard/);

  const driverContext = await person(browser);
  const driver = await driverContext.newPage();
  const address = email("team-driver");
  await driver.goto("/signup?role=driver");
  await driver.getByLabel("Full name").fill("E2E Pending Driver");
  await driver.getByLabel("Email").fill(address);
  await driver.getByLabel("Password").fill(PASSWORD);
  const signupResponse = driver.waitForResponse((response) =>
    response.url().endsWith("/api/auth/signup") && response.request().method() === "POST");
  await driver.getByRole("button", { name: "Create account" }).click();
  const account = await (await signupResponse).json() as { id: string; approved: boolean };
  expect(account.approved).toBe(false);
  await expect(driver.getByRole("status")).toContainText("pending approval");

  await officer.getByRole("link", { name: "Team & Access" }).click();
  const member = officer.getByRole("row", { name: new RegExp(address) });
  await expect(member).toBeVisible();
  await member.getByRole("button", { name: "Approve account" }).click();
  await expect(member.getByText("Pending approval")).toHaveCount(0);

  await driver.goto("/signin");
  await driver.getByLabel("Email").fill(address);
  await driver.getByLabel("Password").fill(PASSWORD);
  await driver.getByRole("button", { name: "Sign in" }).click();
  await expect(driver).toHaveURL(/\/driver/);
  await driverContext.close();
  await officerContext.close();
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

test("a supplier finds a site from two letters, and is told when nothing matches", async ({ page }) => {
  await signUp(page, "supplier", "E2E Search", email("search"));
  const search = page.getByLabel("Find your warehouse or site").locator("visible=true");
  const picker = page.locator(".picker");
  const route = "**/portal/api/facilities*";

  // A slow reply to earlier typing must not replace the answer to later typing.
  await page.route(route, async (r) => {
    if (new URL(r.request().url()).searchParams.get("q") === "pd") await new Promise((done) => setTimeout(done, 1500));
    await r.continue();
  });
  await search.fill("pd");
  await expect(picker.getByRole("status")).toHaveText("Searching…");
  await search.fill("pdsnothingnamedthis");
  await expect(picker).toContainText("No matching warehouses found");
  await page.waitForTimeout(2000);
  await expect(picker).toContainText("No matching warehouses found");
  await page.unroute(route);

  await search.fill("vi");
  await expect(picker.getByRole("button").first()).toBeVisible();

  await page.route(route, (r) => r.abort());
  await search.fill("vij");
  await expect(picker.getByRole("alert")).toContainText("Search is unavailable");
  await page.unroute(route);

  await search.fill("vija");
  const first = picker.getByRole("button").first();
  const name = (await first.innerText()).split("\n")[0].trim();
  await first.click();
  await expect(page.locator(".alert.ok")).toContainText(`Selected: ${name.slice(0, 20)}`);
});

test("the dashboard stays live without being reloaded", async ({ browser }) => {
  const context = await person(browser);
  const page = await context.newPage();
  const refreshes: number[] = [];
  page.on("response", (r) => { if (r.url().endsWith("/api/overview") && r.ok()) refreshes.push(Date.now()); });
  await signUp(page, "operator", "E2E Officer", email("live-officer"));
  await expect(page).toHaveURL(/\/dashboard/);
  await expect(page.getByTestId("connection-status")).toContainText("Live");
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
  await signUp(page, "supplier", "E2E Mail", address);
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
  test.skip(Boolean(process.env.E2E_DEMO), "this truck must be the only one: a simulated fleet can win the route");
  test.setTimeout(420_000);

  const phone = await person(browser, true);
  const driver = await phone.newPage();
  await signUp(driver, "driver", "E2E Driver");
  await expect(driver).toHaveURL(/\/driver/);
  await driver.getByLabel("Vehicle number").fill(`TEST-SIM-GPS-E2E-${RUN}`);
  await driver.getByLabel("Load capacity (kg)").fill("6000");
  await driver.getByRole("button", { name: "Register and start" }).click();
  await expect(driver.getByText("No route yet")).toBeVisible();
  await expect(driver.getByText("±", { exact: false })).toBeVisible();

  const desk = await person(browser);
  const officer = await desk.newPage();
  await signUp(officer, "operator", "E2E Officer", email("drill-officer"));
  await expect(officer).toHaveURL(/\/dashboard/);

  const drill = await officer.request.post("/api/drill", { data: {
    geometry: { type: "Polygon", coordinates: [DRILL_ZONE] }, hazard_type: "flood", severity: "severe", label: `E2E ${RUN}`, ttl_minutes: 30,
  } });
  expect(drill.status()).toBe(201);
  const hazard = (await drill.json()).id as string;

  // This drill's own plan: an older one may be waiting too.
  let planId = "";
  await expect.poll(async () => {
    const plans = await listPlans<{ id: string; status: string; hazard_ids: string[] }>(officer);
    planId = plans.find((p) => p.hazard_ids.includes(hazard) && p.status === "awaiting_approval")?.id ?? "";
    return planId;
  }, { timeout: 240_000 }).not.toBe("");
  await officer.getByRole("link", { name: "Plans & Approvals" }).click();
  await officer.locator(`[data-plan="${planId}"]`).click();
  await expect(officer.getByText(`Recorded as E2E Officer (${email("drill-officer")})`)).toBeVisible();
  await officer.getByRole("button", { name: "Approve dispatch" }).click();
  await officer.getByRole("button", { name: "Yes, dispatch" }).click();  // approving asks once more before any truck is sent

  await expect(driver.getByText("Next stop")).toBeVisible({ timeout: 90_000 });
  await expect(driver.getByText("DRILL. This is not a real dispatch.")).toBeVisible();
  await expect(officer.getByTestId("plan-detail")).toContainText(`by E2E Officer <${email("drill-officer")}>`);

  expect((await officer.request.delete(`/api/drill/${hazard}`)).status()).toBe(204);
  await phone.close();
  await desk.close();
});
