# Live demo script (2 to 3 minutes)

Everything on screen is live. The one exception is the what-if drill, which is labelled DRILL everywhere and exists to show the system on demand.

## Before judging

Do this 30 minutes ahead.

- [ ] Stack up (`.\infra\dev\stack.ps1 up`), `GEMINI_API_KEY` set, and `http://localhost:8000/health` says ok.
- [ ] `GET /api/brain/health` shows Valhalla and the LLM healthy.
- [ ] Dashboard loaded, source dots green. AePDS has stock (`/api/sources/health`).
- [ ] **At least two vehicles online inside Andhra Pradesh**, for the reassign beat. Use real phones through a tunnel if a teammate is in the state; otherwise `sim_driver` (its vehicles are named `TEST-SIM-GPS`, so say so).
- [ ] A real water check-in at `/portal` from a supplier or teammate. Otherwise water shows as an escalated shortfall, which is honest and worth pointing out. Do not run `infra/demo/seed_demo_water.py`: it is not real stock.
- [ ] Rehearse one full drill, then cancel it.
- [ ] Open three windows: dashboard, driver app on a phone, and a terminal.

## The script

**0:00 Live picture.** "This is Andhra Pradesh right now. These alerts come from NDMA's SACHET feed, GDACS and rainfall forecasts. The dots on the left show how fresh each source is. If one goes down it turns red and says so: we never fill in a gap with a guess."

**0:20 Trigger.** Click *Draw drill zone*, draw a flood across the Krishna near Vijayawada, set severity *severe*, *Publish drill*. "Judges can draw any disaster. It's labelled DRILL, but it goes through exactly the same pipeline as a real alert."

**0:35 The swarm reasons.** Select the new plan and read the timeline as it fills. Point out:
- **Sentinel:** Gemini's triage, and that it flags low census coverage by itself.
- **Needs:** real census population, scaled by how much of the mandal is under water. Sphere humanitarian standards turn that into litres and kilos.
- **Procurement:** real government ration stock from AePDS, split across mandals. What can't be found is escalated, not invented.
- **Route Planner and Critic:** OR-Tools solves the routes; Valhalla and Shapely prove they clear the hazard. "The LLM reasons. Solvers compute. No model ever produces a coordinate or a stock number."

**1:05 Human in the loop.** Show the brief, then the reasons this needs approval. Type a name and press *Approve dispatch*. "The plan re-checks the world after you approve, so a stale approval can't send a truck into a flood."

**1:20 Dispatch.** The route appears on the map. On the phone, show the stops and press *Read instructions aloud* for the Telugu voice. Show the PO PDF if there's time. "Every step is idempotent. Press approve twice, kill the worker: nothing is sent twice."

**1:40 Closed loop.** Draw a second drill across the road ahead of the truck, or have the driver leave the route. Without anyone pressing anything, the timeline shows the Route Monitor detecting it and the Replanner sending a new route, and the phone announces it. "Goods already on the truck stay on it. It didn't ask for approval because these goods were already approved."

**2:10 Driver offline.** Stop one vehicle's GPS. About five minutes later it's reassigned, so show a prepared example if time is short. "Goods still at the supplier go to another truck. Goods on the lost truck can't be replanned, so a human gets its last known position."

**2:30 Break it.** In the terminal, `docker compose stop valhalla`, then trigger a plan. It stops with "routing service unavailable, so no route can be verified as safe". "It refuses to guess. In a disaster, a confident wrong route is the worst outcome." Restart Valhalla: `docker compose start valhalla`.

**2:50 Close.** "Live data, real stock, verified routes, a human in control, and it repairs itself."

## If something fails on stage

| What happens | What to do |
|---|---|
| No active real alerts | Normal. Use the drill; it runs the same pipeline |
| SACHET returning 403 | It rate-limits. Alerts still appear, placed by area name with a *fallback* badge. Point at the badge: that's the design |
| A source goes red | Say so. It's the "data stale" edge case, working |
| Gemini overloaded | The fallback chain tries other models. If all fail, the plan continues with text labelled RULE-BASED and a required human review. Say so |
| No Telugu voice on the phone | The phone has no Telugu speech pack. Show the Telugu text instead |
| Plan stops at "no online vehicle" | GPS fixes are older than 2 minutes. Reopen the driver app or restart `sim_driver` |
| Plan fails with "no census population" | The drill missed every settlement with a census tag. Draw it over a town |
| Plan fails with "no safe relief point" | The drill is too large or too far from schools and shelters. Draw a smaller one |
| Planning is slow | Valhalla or Gemini is slow. Talk through the timeline: it's progressing live |
| Backend won't respond | Dashboard says "Backend unreachable". Run `.\infra\dev\stack.ps1 up`, which restarts everything, then reload |
| Venue Wi-Fi fails | Phone tether. The stack runs locally; only live sources and Gemini need internet, and stale data is flagged rather than breaking |

## Questions judges may ask

**Is any data fake?** No. Hazards, facilities, population, PDS stock and GPS are live or from official open data. Water has no public source, so it needs a supplier check-in. Drills and `sim_driver` vehicles are labelled wherever they appear.

**What if the LLM hallucinates?** It can't touch the numbers. Output is a bounded Pydantic model, and invalid output is sent back with the exact error, up to three times. Translations fail validation if any number changes.

**How accurate is the population?** It's an estimate, and the plan says so. OSM census tags cover about 2.7% of settlements. WorldPop would improve it.

**Why a human gate?** High-stakes dispatch needs accountability. Replans of goods already approved skip the gate, because a truck heading into a flood can't wait for a meeting.
