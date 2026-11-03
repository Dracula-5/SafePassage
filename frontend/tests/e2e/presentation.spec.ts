// Presentation mode in a real browser. Needs a stack started with `stack.ps1 demo`, E2E_DEMO=1, and
// OPERATOR_TOKEN. Accounts and the truck are labelled as tests.
import { expect, Page, test } from "@playwright/test";

const OPERATOR_CODE = process.env.OPERATOR_TOKEN ?? "";
const PASSWORD = "test account passphrase";
const RUN = Date.now().toString(36);

test.skip(!process.env.E2E_DEMO, "needs presentation mode: set E2E_DEMO=1");

function email(role: string): string {
  return `e2e-demo-${role}-${RUN}@example.org`;
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

async function dispatchedPlan(page: Page, hazard: string, headers: Record<string, string> = {}): Promise<string> {
  const decided = new Set<string>();
  let planId = "";
  await expect.poll(async () => {
    const plans = await listPlans<{ id: string; status: string; evacuation: boolean; hazard_ids: string[] }>(page, headers);
    const plan = plans.find((p) => p.hazard_ids.includes(hazard) && !p.evacuation);
    planId = plan?.id ?? "";
    if (plan?.status === "awaiting_approval" && !decided.has(plan.id)) {
      decided.add(plan.id);
      const response = await page.request.post(`/api/plans/${plan.id}/approve`, {
        headers,
        data: { decision: "approved", decided_by: "E2E" },
      });
      expect(response.ok()).toBe(true);
    }
    return plan?.status;
  }, { timeout: 300_000 }).toBe("dispatched");
  return planId;
}

async function signUp(page: Page, role: "operator" | "driver" | "supplier", name: string, who: string = role) {
  await page.goto(`/signup?role=${role}`);
  await page.getByLabel("Full name").fill(name);
  await page.getByLabel("Email").fill(email(who));
  await page.getByLabel("Password").fill(PASSWORD);
  const signupResponse = page.waitForResponse((response) =>
    response.url().endsWith("/api/auth/signup") && response.request().method() === "POST");
  await page.getByRole("button", { name: "Create account" }).click();
  const response = await signupResponse;
  expect(response.status()).toBe(201);
  const account = await response.json() as { id: string; approved: boolean };
  if (!account.approved) {
    // Only where approval is enforced (production). Local development signs the account straight in.
    await expect(page.getByRole("status")).toContainText("pending approval");
    const approval = await page.request.post(`/api/accounts/${account.id}/approve`, {
      headers: { "X-Operator-Token": OPERATOR_CODE },
    });
    expect(approval.status()).toBe(200);
    await page.goto("/signin");
    await page.getByLabel("Email").fill(email(who));
    await page.getByLabel("Password").fill(PASSWORD);
    await page.getByRole("button", { name: "Sign in" }).click();
  } else {
    expect(role === "supplier" || account.approved).toBe(true);
  }
}

test("the map comes through this site, with warehouses once zoomed in", async ({ browser }) => {
  const page = await browser.newPage();
  const tiles: number[] = [];
  const elsewhere: string[] = [];
  page.on("response", (response) => {
    if (response.url().includes("/tiles/")) tiles.push(response.status());
    else if (/openstreetmap|cartocdn/.test(response.url())) elsewhere.push(response.url());
  });
  await signUp(page, "operator", "E2E Demo Map", "map");
  await expect(page).toHaveURL(/\/dashboard/);
  await expect.poll(() => tiles.length).toBeGreaterThan(4);
  expect(tiles.every((status) => status === 200)).toBe(true);
  expect(elsewhere).toEqual([]);
  // At state zoom the warehouses would cover the hazards: they wait until the map is about a district across.
  await expect(page.getByText("Zoom in to see warehouses", { exact: false })).toBeVisible();
  for (let i = 0; i < 2; i++) {
    await page.getByRole("button", { name: "Zoom in" }).click();
    await page.waitForTimeout(600);
  }
  await expect(page.getByText(/\d+ (warehouses and godowns|facilities) in view/)).toBeVisible();
  await page.close();
});

test("a duty officer sees that the fleet is simulated, and can add a driver", async ({ page, context }) => {
  await signUp(page, "operator", "E2E Demo Officer");
  await expect(page).toHaveURL(/\/dashboard/);
  // No banner any more (the owner's decision): the trucks themselves say they are simulated.
  await expect(page.getByText(/\d+ simulated/)).toBeVisible();
  await expect(page.getByText("Presentation mode", { exact: true })).toHaveCount(0);
  await page.getByRole("link", { name: "Fleet & Drivers" }).click();
  await expect(page.locator("tbody tr").first()).toBeVisible();
  await expect.poll(() => page.locator("tbody tr .badge.sim").count()).toBeGreaterThanOrEqual(42);

  await page.getByText("Presentation tools: add a simulated driver").click();
  await page.getByLabel("Driver's name").fill("E2E Added Driver");
  await page.getByLabel("Vehicle number").fill(`AP 16 E2E ${RUN}`);
  await page.getByRole("button", { name: "Add simulated driver" }).click();
  await expect(page.getByText("E2E Added Driver added at", { exact: false })).toBeVisible();
  const added = page.locator("tbody tr", { hasText: `AP 16 E2E ${RUN}` });
  await expect(added).toBeVisible({ timeout: 15_000 });
  await expect(added.locator(".badge.sim")).toBeVisible();
  await expect(added.getByText("Online")).toBeVisible({ timeout: 20_000 });

  await added.click();  // the details open in a drawer
  const drawer = page.getByRole("dialog");
  const [screen] = await Promise.all([context.waitForEvent("page"), drawer.getByRole("link", { name: "Driver screen" }).click()]);
  await expect(screen.getByText("This truck and its GPS are simulated", { exact: false })).toBeVisible();
  await expect(screen.getByText("No route yet")).toBeVisible();
  await screen.close();

  await drawer.getByRole("button", { name: "Remove from fleet" }).click();
  await page.getByRole("button", { name: "Remove truck" }).click();
  await expect(added).toHaveCount(0);
});

test("a duty officer can look up any driver or supplier", async ({ browser }) => {
  const page = await browser.newPage();
  await page.goto("/signin");
  await page.getByLabel("Email").fill(email("operator"));
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/dashboard/);
  await page.getByRole("link", { name: "Directory" }).click();
  await expect(page).toHaveURL(/\/directory/);

  await page.getByRole("button", { name: "Drivers" }).click();
  await expect(page.locator("tbody tr").first()).toBeVisible();
  expect(await page.locator("tbody tr").count()).toBeGreaterThanOrEqual(42);
  await page.getByLabel("Search").fill("Ramesh Naidu");
  await expect(page.locator("tbody tr")).toHaveCount(1);
  await expect(page.locator("tbody tr").getByText("SIMULATED")).toBeVisible();

  await page.getByRole("button", { name: "Suppliers" }).click();
  // Only real suppliers: presentation mode makes no depots, and the old made-up ones are not listed.
  await page.getByLabel("Search").fill("TEST-SIM");
  await expect(page.locator("tbody tr")).toHaveCount(0);
  await page.getByLabel("Search").fill("PDS stock");
  await expect(page.locator("tbody tr").first()).toBeVisible();

  await page.getByRole("button", { name: "Warehouses" }).click();
  await page.getByLabel("Search").fill("godown");
  await expect(page.locator("tbody tr").first()).toContainText(/godown/i);

  await page.getByRole("link", { name: "Team & Access" }).click();
  await expect(page.getByRole("row", { name: new RegExp(email("operator")) })).toBeVisible();
  await page.close();
});

test("a duty officer finds a numbered truck near a place and asks that driver to go", async ({ browser }) => {
  const page = await browser.newPage();
  await page.goto("/signin");
  await page.getByLabel("Email").fill(email("operator"));
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/dashboard/);

  await expect.poll(() => page.locator(".truck-number").count()).toBeGreaterThanOrEqual(42);  // a number on every round button
  await page.getByRole("link", { name: "Fleet & Drivers" }).click();
  await page.getByLabel("Choose a drill or severe alert").selectOption({ index: 1 });
  await expect(page.getByText("Nearest first", { exact: false })).toBeVisible();
  await expect(page.locator("tbody tr small").filter({ hasText: /km away|inside the area/ }).first()).toBeVisible();

  // A truck with a real person answers for itself, so this picks the nearest one that answers by simulation.
  const fleet: { vehicle_id: string; has_person: boolean }[] = await (await page.request.get("/api/demo/fleet")).json();
  const vehicles: { id: string; fleet_number: number }[] = await (await page.request.get("/api/vehicles")).json();
  const people = new Set(fleet.filter((t) => t.has_person).map((t) => String(vehicles.find((v) => v.id === t.vehicle_id)?.fleet_number)));
  const free = page.locator("tbody tr", { has: page.getByRole("button", { name: "Send request" }) });
  let nearest = free.first();
  for (let i = 0; people.has((await nearest.locator(".truck-no").textContent())!); i++) nearest = free.nth(i + 1);
  const number = (await nearest.locator(".truck-no").textContent())!;
  await nearest.getByRole("button", { name: "Send request" }).click();
  await expect(page.getByText(`Request sent to truck ${number}`, { exact: false })).toBeVisible();
  const asked = page.locator("tbody tr").filter({ has: page.locator(".truck-no", { hasText: new RegExp(`^${number}$`) }) });
  await expect(asked.getByText("Accepted")).toBeVisible({ timeout: 40_000 });  // a simulated driver answers by itself
  await expect(page.getByRole("button", { name: "Make a plan with accepted drivers" })).toBeVisible();
  await page.close();
});

test("a signed-in driver answers a request on their own screen", async ({ browser }) => {
  const phone = await browser.newContext({ viewport: { width: 390, height: 844 }, permissions: ["geolocation"], geolocation: { longitude: 76.98, latitude: 31.77 } });
  const driver = await phone.newPage();
  await signUp(driver, "driver", "E2E Demo Answerer", "answerer");
  await expect(driver).toHaveURL(/\/driver/);
  await driver.getByLabel("Vehicle number").fill(`AP 16 E2F A${RUN}`);
  await driver.getByLabel("Load capacity (kg)").fill("8000");
  await driver.getByRole("button", { name: "Register and start" }).click();
  await driver.getByRole("button", { name: "Profile" }).click();
  await driver.getByText("Not in Andhra Pradesh?").click();
  await driver.getByLabel("Start from").selectOption("Guntur");
  await driver.getByRole("button", { name: "Simulate my GPS" }).click();
  await driver.getByRole("button", { name: "Home" }).click();
  await expect(driver.getByText("This truck and its GPS are simulated", { exact: false })).toBeVisible();
  await driver.waitForTimeout(12_000);

  const headers = { "X-Operator-Token": OPERATOR_CODE };
  const vehicles: { id: string; label: string }[] = await (await driver.request.get("/api/vehicles", { headers })).json();
  const mine = vehicles.find((v) => v.label.includes(`A${RUN}`))!;
  const alerts: { features: { id: string; properties: { severity: string } }[] } = await (await driver.request.get("/api/hazards")).json();
  const place = alerts.features.find((f) => f.properties.severity === "severe" || f.properties.severity === "extreme")!;
  const sent = await driver.request.post("/api/driver-requests", { headers, data: { vehicle_id: mine.id, hazard_id: place.id } });
  expect(sent.status()).toBe(201);

  await expect(driver.getByText("Request from", { exact: false })).toBeVisible({ timeout: 15_000 });
  await driver.getByRole("button", { name: "Accept" }).click();
  await expect(driver.getByText("Accepted. Wait at the edge of this area.", { exact: false })).toBeVisible({ timeout: 10_000 });
  await phone.close();
});

test("a duty officer requests an evacuation and follows it", async ({ browser }) => {
  test.setTimeout(240_000);  // going round a big rain alert takes the router over a minute
  const page = await browser.newPage();
  const headers = { "X-Operator-Token": OPERATOR_CODE };
  const zones: { features: { id: string; properties: { is_drill: boolean; severity: string } }[] } = await (await page.request.get("/api/hazards")).json();
  const candidates = zones.features.filter((f) => f.properties.is_drill || f.properties.severity === "severe" || f.properties.severity === "extreme");
  let asked = 0;
  for (const zone of candidates) {  // a zone with nobody living in it is refused
    asked = (await page.request.post("/api/evacuations", { headers, data: { hazard_id: zone.id, people: 33 } })).status();
    if (asked === 201) break;
  }
  expect(asked).toBe(201);

  await page.goto("/signin");
  await page.getByLabel("Email").fill(email("operator"));
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/dashboard/);
  await page.getByRole("link", { name: "Evacuations" }).click();
  const request = page.locator("section", { hasText: "33 people from" }).first();
  await expect(request).toBeVisible();
  await expect(request.getByText(/Avoids \d+ active zone/)).toBeVisible({ timeout: 200_000 });
  await expect(request.getByRole("link", { name: /driver's screen/i })).toBeVisible();
  await page.close();
});

test("a driver far from Andhra Pradesh can simulate their GPS", async ({ browser }) => {
  const phone = await browser.newContext({
    viewport: { width: 390, height: 844 }, permissions: ["geolocation"], geolocation: { longitude: 76.98, latitude: 31.77 },
  });
  const page = await phone.newPage();
  await signUp(page, "driver", "E2E Demo Driver");
  await expect(page).toHaveURL(/\/driver/);
  await page.getByLabel("Vehicle number").fill(`AP 16 E2F ${RUN}`);
  await page.getByLabel("Load capacity (kg)").fill("10");
  await page.getByRole("button", { name: "Register and start" }).click();
  await expect(page.getByText("No route yet")).toBeVisible();

  await page.getByRole("button", { name: "Profile" }).click();

  await page.getByText("Not in Andhra Pradesh?").click();
  await page.getByLabel("Start from").selectOption("Ongole");
  await page.getByLabel("Load capacity (kg)").fill("8000");
  await page.getByRole("button", { name: "Simulate my GPS" }).click();
  await page.getByRole("button", { name: "Home" }).click();
  await expect(page.getByText("This truck and its GPS are simulated", { exact: false })).toBeVisible();
  await expect(page.getByText("Simulated", { exact: true })).toBeVisible();

  await page.reload();  // the choice stays, and the browser does not go back to the phone's GPS
  await expect(page.getByText("This truck and its GPS are simulated", { exact: false })).toBeVisible();
  await phone.close();
});

test("a supplier watches their order go from a drill to delivered", async ({ browser }) => {
  test.skip(!process.env.E2E_ROUTING, "needs the road map: set E2E_ROUTING=1");
  test.setTimeout(840_000);

  const shop = await browser.newContext();
  const supplier = await shop.newPage();
  await signUp(supplier, "supplier", "E2E Demo Depot");
  await expect(supplier).toHaveURL(/\/supplier/);
  expect((await supplier.request.post("/api/supplier/me", { data: { name: "E2E Demo Depot", lon: 80.62, lat: 16.56 } })).status()).toBe(201);
  const stock = { lines: [{ item_code: "WATER_L", quantity: 400000, unit_weight_kg: 1 }] };
  expect((await supplier.request.post("/api/supplier/stock", { data: stock })).status()).toBe(200);
  await supplier.reload();
  await expect(supplier.getByRole("heading", { name: "No orders yet" })).toBeVisible();

  const desk = await browser.newContext();
  const officer = await desk.newPage();
  await officer.goto("/signin");
  await officer.getByLabel("Email").fill(email("operator"));
  await officer.getByLabel("Password").fill(PASSWORD);
  await officer.getByRole("button", { name: "Sign in" }).click();
  await expect(officer).toHaveURL(/\/dashboard/);

  const zone = [[80.6, 16.49], [80.66, 16.49], [80.66, 16.53], [80.6, 16.53], [80.6, 16.49]];
  const drill = await officer.request.post("/api/drill", { data: {
    geometry: { type: "Polygon", coordinates: [zone] }, hazard_type: "flood", severity: "severe", label: `E2E demo ${RUN}`, ttl_minutes: 30,
  } });
  expect(drill.status()).toBe(201);
  const hazard = (await drill.json()).id as string;

  const planId = await dispatchedPlan(officer, hazard);
  await officer.getByRole("link", { name: "Plans & Approvals" }).click();
  await officer.locator(`[data-plan="${planId}"]`).click();
  const card = officer.getByTestId("route-card").first();  // routes are only written when a plan is dispatched
  await expect(card.locator(".badge.sim")).toBeVisible({ timeout: 60_000 });
  await expect(card.getByRole("link", { name: "Driver screen" })).toBeVisible();

  await expect(supplier.getByText("Prepare for pickup").or(supplier.getByText("On the truck")).first()).toBeVisible({ timeout: 60_000 });
  await expect(supplier.locator("tr", { hasText: "Water" }).filter({ hasText: "DRILL" }).first()).toBeVisible();
  await expect(supplier.getByText("Delivered", { exact: true }).first()).toBeVisible({ timeout: 600_000 });

  expect((await officer.request.delete(`/api/drill/${hazard}`)).status()).toBe(204);
  await shop.close();
  await desk.close();
});

test("a big clear job goes out by itself, and the rules keep what is left", async ({ browser }) => {
  test.skip(!process.env.E2E_ROUTING, "needs the road map: set E2E_ROUTING=1");
  test.setTimeout(300_000);

  const desk = await browser.newContext();
  const officer = await desk.newPage();
  await officer.goto("/signin");
  await officer.getByLabel("Email").fill(email("operator"));
  await officer.getByLabel("Password").fill(PASSWORD);
  await officer.getByRole("button", { name: "Sign in" }).click();
  await expect(officer).toHaveURL(/\/dashboard/);

  const zone = [[78, 15.79], [78.08, 15.79], [78.08, 15.86], [78, 15.86], [78, 15.79]];
  const drill = await officer.request.post("/api/drill", { data: {
    geometry: { type: "Polygon", coordinates: [zone] }, hazard_type: "flood", severity: "severe", label: `E2E clear ${RUN}`, ttl_minutes: 30,
  } });
  expect(drill.status()).toBe(201);
  const hazard = (await drill.json()).id as string;

  let planId = "";
  await expect.poll(async () => {
    const plans = await listPlans<{ id: string; status: string; trigger_kind: string; hazard_ids: string[] }>(officer);
    const plan = plans.find((p) => p.hazard_ids.includes(hazard) && p.trigger_kind === "drill");
    planId = plan?.id ?? "";
    return plan?.status;
  }, { timeout: 180_000 }).toBe("dispatched");

  await officer.locator(`[data-plan="${planId}"]`).click();
  await expect(officer.getByText("Approved by the rules", { exact: false })).toBeVisible();
  await expect(officer.getByTestId("route-card").first()).toBeVisible();
  await expect(officer.getByRole("button", { name: "Approve dispatch" })).toHaveCount(0);
  await expect(officer.getByText("Still to send", { exact: true })).toBeVisible();
  await expect(officer.getByText("The rules send the next wave", { exact: false })).toBeVisible();
  await expect(officer.getByText("Needs a person", { exact: true })).toHaveCount(0);

  expect((await officer.request.delete(`/api/drill/${hazard}`)).status()).toBe(204);
  await desk.close();
});

test("a signed-in driver with simulated GPS gets a route ahead of the simulated fleet", async ({ browser }) => {
  test.skip(!process.env.E2E_ROUTING, "needs the road map: set E2E_ROUTING=1");
  test.setTimeout(420_000);

  const phone = await browser.newContext({ permissions: ["geolocation"], geolocation: { longitude: 76.98, latitude: 31.77 } });
  const driver = await phone.newPage();
  await signUp(driver, "driver", "E2E Demo Presenter", "presenter");
  await expect(driver).toHaveURL(/\/driver/);
  await driver.getByLabel("Vehicle number").fill(`AP 16 E2G ${RUN}`);
  await driver.getByLabel("Load capacity (kg)").fill("8000");
  await driver.getByRole("button", { name: "Register and start" }).click();
  await driver.getByRole("button", { name: "Profile" }).click();
  await driver.getByText("Not in Andhra Pradesh?").click();
  await driver.getByLabel("Start from").selectOption("Guntur");
  await driver.getByRole("button", { name: "Simulate my GPS" }).click();
  await driver.getByRole("button", { name: "Home" }).click();
  await expect(driver.getByText("This truck and its GPS are simulated", { exact: false })).toBeVisible();
  await driver.waitForTimeout(12_000);  // the simulator picks the truck up on its next round

  const headers = { "X-Operator-Token": OPERATOR_CODE };
  const zone = [[80.42, 16.28], [80.47, 16.28], [80.47, 16.33], [80.42, 16.33], [80.42, 16.28]];  // over its home town
  const drill = await driver.request.post("/api/drill", { headers, data: {
    geometry: { type: "Polygon", coordinates: [zone] }, hazard_type: "flood", severity: "severe", label: `E2E presenter ${RUN}`, ttl_minutes: 30,
  } });
  expect(drill.status()).toBe(201);
  const hazard = (await drill.json()).id as string;

  await dispatchedPlan(driver, hazard, headers);
  await expect(driver.getByText("Next stop")).toBeVisible({ timeout: 120_000 });
  await expect(driver.getByText("DRILL. This is not a real dispatch.")).toBeVisible();

  expect((await driver.request.delete(`/api/drill/${hazard}`, { headers })).status()).toBe(204);
  await phone.close();
});
