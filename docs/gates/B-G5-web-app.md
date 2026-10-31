# Web app report: one app, email accounts, live every 5 seconds

Run on 2026-10-31, on branch `integration/gate3`, at the owner's request. It covers what changed and the evidence. How to deploy it is in [deploy.md](../deploy.md), and how it is protected is in [security.md](../security.md).

## What changed

- **One web app.** Sign-in, `/dashboard`, `/driver` and `/supplier` are all in the Next.js app, behind one address. The separate driver app and the portal page are gone.
- **Email accounts.** Sign-up, sign-in, sign-out, and roles. Operator and driver accounts need a code, because a replan assigns vehicles with no human.
- **Live every 5 seconds.** The dashboard, supplier screen and sign-in page refresh on that cycle, and the driver sends GPS and reads its route on it. Truck and plan changes also arrive through the stream.
- **New interface.** A shared design for every screen, with a live summary bar, tabbed panels, and a phone-first driver screen.

**What "live" means here.** The screen refreshes every 5 seconds from real data, and nothing is animated or estimated. Alert feeds change only when their publishers do: SACHET is polled every 2 minutes, because polling faster gets the IP blocked. Each source shows its true age on the Sources tab.

## Evidence

```
457 passed, 3 skipped, 4 warnings in 556.75s
SKIPPED [3] tests\brain\live_sources.py:37: SACHET polygon endpoint is rate-limiting this IP
```

**In real Chrome through `http://localhost:3000`, on the production images, as four separate people: 25 of 25 passed.** There were no script errors or policy violations on any screen.

- A stranger is sent to sign-in, and a wrong operator code is refused on screen.
- The dashboard refreshed 3 times in 16 seconds, and the heavy hazard data loaded once.
- The truck's position changed on the operator's data with no reload.
- A drill was approved by clicking, recorded as `Test Officer <test-officer@example.org>`, and the route reached the phone by itself.
- Signing out ended access.

**First live Gemini run on this machine.** Triage and the plan brief came from the model, and a real Telugu translation reached the phone with every number kept:

```
డ్రిల్ - ఇది నిజమైన డిస్పాచ్ కాదు. వాహనం TEST-SIM-GPS-CHROME.
1. AP Civil Supplies PDS stock, Agiripalli (Eluru) వద్ద 22:12 IST లోపు పికప్ చేసుకోండి: 5992 RICE_KG.
```

A plan took about a minute to reach approval, because the fallback chain worked through models that were retired or rate-limited.

## Bugs found by using it

1. **Updates did not reach Caddy.** `up` does not notice a changed Caddyfile, so `/driver` returned 404. The update steps now restart it.
2. **A parked truck could look offline.** GPS can go quiet when nothing moves, so the phone now asks for a fresh reading every 5 seconds.
3. **An empty instructions box.** The route reaches the phone about a minute before its translation. The screen now says so.
4. **A sleeping phone stops sending GPS.** The driver screen now keeps the screen awake.
5. **Langfuse refused every trace.** It no longer takes the old format, and said so inside a 207 response that looked like success. The tracer now sends OpenTelemetry and notices refusals. A real trace was written through the app's tracer and read back from Langfuse.

## Not verified

- **Sending email.** No mail provider is set up, so addresses are not proven. See [security.md](../security.md).
- **A real phone.** The driver screen ran in Chrome with a phone-sized window and an emulated location.
- **Telugu voice.** The text is verified; speech depends on the phone.
- **Twilio sending.** The key is valid, but it is a Trial account, which can only message numbers verified with Twilio. Drills send nothing by design, so no real message has gone out.
- **FIRMS.** NASA rejected the key in `local.env`.
