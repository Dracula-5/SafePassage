# Presentation guide

For whoever presents ReliefRoute. It shows all three roles, duty officer, driver and supplier, in about 8 minutes, from one laptop or three.

**What is real and what is simulated.** Hazard alerts, roads, facilities, population, government ration stock and the planning are all live. In presentation mode the **drivers, number plates and truck GPS** are simulated, because nobody can drive a real truck in Andhra Pradesh for the demo. Every place and every unit of stock is real: there are no made-up depots, so water appears only when a supplier reports some (step 2). Simulated trucks are marked on the map legend, carry a SIMULATED badge in Fleet, and say so on their driver screen. There is no banner across the duty officer's screens. Say so up front: it builds trust.

## Before you present

Do this 20 minutes ahead.

1. In the project folder, run `.\infra\dev\stack.ps1 demo`. Presentation mode stays on until you run `demo-off`, and `http://localhost:3000` is the address. After a restart, run `.\infra\dev\stack.ps1 present` instead: it starts Docker if needed, waits for the stack, opens the ngrok tunnel window and checks the hosted site. Each step prints a line.
   **Present from `http://localhost:3000` on this laptop.** The hosted site (`https://safepassage-frontend.vercel.app`) reaches this laptop through a free ngrok tunnel, which allows 20,000 requests a month. One open dashboard uses about 6,500 an hour, and one session on 2026-11-03 had already used 21,000. Keep the hosted link for people who must open it on their own device, and close hosted tabs you are not using.
2. Open three separate browser windows, because one browser can only hold one sign-in. Use a normal window, an incognito window and a second browser, or three laptops.
   - **Duty officer:** sign in and land on the dashboard. The Sources tab should be green, and *Trucks online* should say 42 or more.
   - **Supplier:** sign in. If the screen asks you to register, do it once and pick your site.
   - **Driver:** sign in. If you are outside Andhra Pradesh, open **Not in Andhra Pradesh? Simulate this truck's GPS**, choose **Vijayawada**, set the load capacity to `8000`, and press **Simulate my GPS**.
3. Rehearse one full drill, then end it from its popup on the map.
4. Duty officer and driver accounts sign up without codes and remain pending until an authorized duty officer approves them. On a fresh local stack, use `OPERATOR_TOKEN` from `infra\deploy\local.env` to approve the first duty officer with `POST /api/accounts/{account_id}/approve`; then approve drivers from the Team tab. Suppliers can use the portal immediately.

## The script

**1. The live picture (duty officer, 30 s).** Point at the numbers and the map. "This is Andhra Pradesh right now. These alerts come from NDMA's SACHET feed, GDACS, rainfall forecasts and NASA fire data. The Sources tab shows how fresh each one is, and if one goes down it turns red and says so. We never fill a gap with a guess." Open the **Fleet** tab. "The orange trucks are simulated for today. Everything they do goes through the same system a real phone would use."

**2. The supplier (supplier, 30 s).** Show the stock table. Report some water: item *Water*, a quantity such as `5000`, weight per unit `1`. "Planners can use this straight away." It says *Awaiting verification*, so on the duty officer's screen open **Team** and press **Verify supplier**. The supplier's screen changes to *Verified* within 5 seconds.

**3. Trigger a disaster (duty officer, 30 s).** Press **Draw a what-if drill**, click corners across a town, name it, keep severity *severe*, and press **Publish**. "Anyone can draw a disaster. It's labelled DRILL, but it goes through exactly the same pipeline as a real alert." Within about 20 seconds at least 10 trucks gather around it. Draw it near your supplier's site if you want their stock used: plans take the nearest stock. Vijayawada works well (`16.51, 80.63`).

**4. The agents reason (duty officer, 1 min).** Open the **Activity** tab and read the steps as they appear:
- **Sentinel:** Gemini's triage, which flags low census coverage by itself.
- **Needs:** census population, scaled by how much of the mandal is under water. Sphere humanitarian standards turn people into litres and kilos.
- **Procurement:** real government ration stock from AePDS. What can't be found is escalated, not invented.
- **Route Planner and Critic:** OR-Tools solves the routes, and Valhalla and Shapely prove they clear the hazard. "The LLM reasons. Solvers compute. No model ever produces a coordinate or a stock number."

**5. Rules decide, a person handles what's unclear (duty officer, 30 s).** A clear plan is approved by rules and dispatched by itself, and the plan says so. To show it, draw the drill over a small inland town: Gooty (`15.12, 77.63`) went out in about 30 seconds, with nobody pressing anything. "The rules are plain code. The AI can add a person, but never remove one." To show a person deciding, open a real alert's plan: a real alert with simulated trucks always stops at **Your decision is needed**, with the reasons listed, and **Approve dispatch** sends it. So does a drill whose routes cross a wide warning, such as a cyclone zone. Size and severity alone do not: a big job and an extreme drill both go out by themselves, because only what is unclear needs a person. Every rule is in `backend/app/agents/approval.py`. `AUTO_APPROVE=false` in `infra\deploy\local.env` sends every plan to a person. The routes, with each truck and driver, appear on the map and in the panel a few seconds later.

**6. The driver (driver, 1 min).** The signed-in driver's screen fills in by itself. A truck with a real person is used before simulated ones, and it moves next to the drill the moment you publish, so it got a route in all 3 clean test drills. Show the DRILL banner, the stops and their times, then press **Read aloud**. Telugu arrives within about a minute of the stops, and the screen says so meanwhile. To show any other truck, press **Driver screen** on its route card: it opens that driver's view in a new tab. About one simulated truck in three has Telugu instructions.

A big job does not wait for anyone either. What the trucks could not carry shows as **Still to send**, and when they finish the rules send a **NEXT WAVE** plan by themselves, through the same checks, for what is still needed and nothing twice.

**7. Delivery (all three, 1 min).** Trucks move on the map every 5 seconds, and each stop ticks off by itself from their position. If your supplier is in the plan, their **Orders** list shows *Prepare for pickup*, then *On the truck*, then *Delivered*, and *Reserved* in their stock has moved. A full route takes about 5 minutes, so the first stops happen while you talk.

**8. It repairs itself (duty officer, 1 min).** On the **Fleet** tab, trucks on a route are at the top. Press **Leave route** on one. Within about 20 seconds the Activity tab shows the deviation and the Replanner sending a new route, with nobody pressing anything. "Goods already on the truck stay on it. It didn't ask for approval because those goods were already approved."

Press **Lose GPS** on another truck for the offline story. It goes offline after 2 minutes, and its goods move to another truck after 5, so start this early or just describe it. **Restore GPS** brings it back.

The driver can report a problem too. On a driver's screen, under the next stop, press **Report a problem** (on a duty officer's view of a truck it reads **Report a problem for this driver**) and choose:
- **Heavy traffic** or **Road blocked:** the same truck keeps its deliveries and gets a new route that avoids the road about 1.5 km ahead, in about 15 seconds.
- **Vehicle breakdown** or **Accident:** goods still at suppliers go to another truck, and a load already on board is flagged for a person to recover.

The Activity tab shows who reported what, with the driver's note. On a real phone, a minute with signal but no GPS fix brings up **We can't see your GPS**. **Yes, I'm on my route** keeps the truck's goods with it for 5 more minutes, and the log says so; **No, I have a problem** opens the same report.

**9. Add a driver live (duty officer, 30 s).** On the **Fleet** tab, open **Add a driver**, type a name and number plate, and choose a town. The truck appears on the map within seconds. **Directory**, at the top of the screen, lists every driver, every supplier with its stock, and everyone with an account, and you can search them.

**10. Ask a particular driver (all roles, 1 min).** Click a drill on the map and press **Find nearest drivers**. The **Fleet** tab sorts by distance, and each truck's number matches its round button on the map. Press **Send request** on the driver you want. A signed-in driver sees it on their screen and presses **Accept**; a simulated driver accepts by itself in about 10 seconds. An accepted truck moves to the edge of the area. Press **Make a plan with accepted drivers**: that driver goes first in the new plan. Requests appear in the app only, not by SMS. A driver on a route or offline can't be asked.

**11. Evacuation (duty officer, 2 min).** While a drill or severe alert is active, a request arrives by itself every 10 to 15 minutes, with the first about 2 minutes after the fleet starts. Or click a zone on the map and press **Request evacuation**. The **Evacuations** tab shows how many people, from which real settlement, and which truck is going. On the map **orange routes carry goods and green routes carry people**, and trucks are white with their number. The truck collects people at a real place just outside the zone, then takes them to a safe point. "With people on board, the way out avoids every active zone, even forecasts that goods can drive through, and it's checked before the truck leaves." Draw a drill across the green line once people are on board, and the truck gets a new way out by itself.

**12. Break it (duty officer, 30 s, optional).** Run `docker stop reliefroute-prod-valhalla-1` in a terminal, then draw a drill. It stops with "routing service unavailable, so no route can be verified as safe". "It refuses to guess. In a disaster, a confident wrong route is the worst outcome." Then run `docker start reliefroute-prod-valhalla-1`.

**Close.** "Live data, real stock, verified routes, a human in control, and it repairs itself."

## If something fails on stage

| What happens | What to do |
|---|---|
| No active real alerts | Normal. Use the drill; it runs the same pipeline |
| SACHET returning 403 | It rate-limits. Alerts still appear, placed by area name with a *using backup* badge. Point at the badge: that's the design |
| A source goes red | Say so. It's the "data stale" edge case, working |
| Trucks show offline | The simulator stopped. Run `.\infra\dev\stack.ps1 demo` again, which keeps all the data |
| A plan failed, and you've fixed the cause | Select it and press **Plan again** |
| The driver's screen stays on *No route yet* | Check the truck is SIMULATED on the Fleet tab, with a capacity of 8 t: a much smaller truck can't carry a part of an order. Otherwise press **Driver screen** on a route card |
| Gemini overloaded | Each call gives up after 45 seconds and tries the next model. If all fail, the plan continues with text labelled RULE-BASED and a required human review. Say so |
| No Telugu voice on the laptop | It has no Telugu speech pack. Show the Telugu text instead |
| Plan fails with "no census population" | The drill missed every settlement with a census tag. Draw it over a town |
| Plan fails with "no safe relief point" | The drill is too large or too far from schools and shelters. Draw a smaller one |
| Planning is slow | Valhalla or Gemini is slow. Talk through the Activity tab: it's progressing live |
| Trucks drive too fast or too slowly | Set `DEMO_SPEED_MPS` in `infra\deploy\local.env` (1200 by default) and run `stack.ps1 demo` again |
| Backend won't respond | The dashboard says "Cannot reach the server". Run `.\infra\dev\stack.ps1 demo`, then reload |
| Venue Wi-Fi fails | Phone tether. The stack runs locally; only live sources and Gemini need internet, and stale data is flagged rather than breaking |

## Questions people may ask

**Is any data fake?** In this presentation, the drivers, plates and truck GPS are simulated, and the trucks are marked as simulated. Every warehouse, godown and unit of stock is real. The ration godowns' points are approximate: the government does not publish shop locations, so each mandal's stock sits at a point inside that mandal's official boundary. Hazards, roads, facilities, population and ration stock are live or official open data. In real use the trucks are real phones, and the simulated fleet is switched off.

**Would a simulated truck ever be sent to a real disaster?** Not without someone choosing it. A plan that uses one always waits for a person, and the mode is off unless it's turned on.

**What if the LLM hallucinates?** It can't touch the numbers. Output is a bounded Pydantic model, and invalid output is sent back with the exact error, up to three times. Translations fail validation if any number changes.

**How accurate is the population?** It's an estimate, and the plan says so. OSM census tags cover about 2.7% of settlements. WorldPop would improve it.

**Why isn't every plan checked by a person?** Clear plans shouldn't wait. A person decides anything unclear: an extreme real alert, stale data, an unverified supplier, simulated trucks on a real alert, over 300 t, or a route close to a hazard. Replans of goods already approved skip the gate too, because a truck heading into a flood can't wait for a meeting.

**Do messages go out?** Drills are silent by design: a real supplier can't tell a drill from an order in time. SMS and WhatsApp need a paid Twilio number, which this demo doesn't use. Each message is still recorded.

## Ending presentation mode

Run `.\infra\dev\stack.ps1 demo-off`. It removes the simulated trucks and their stock and turns the mode off. A driver who simulated their own truck keeps it, with the phone taking over again.
