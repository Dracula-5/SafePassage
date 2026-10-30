# Gate 3 report: Instance B (Senses & Face), robustness

Run on 2026-10-31 on a new Windows machine, on branch `integration/gate3`.

## What was built

- **Reconnecting UI:** a banner when the stream drops, full reload on reconnect, and the server's `error` event shown.
- **`plan` SSE event**, through the new `v_plan_status` view.
- **PWA offline queue** in its own tested module (`driver-pwa/queue.js`), with stop progress and an alert when a route changes, read aloud.
- **Input validation sweep:** drills must have area, at most 1,000 vertices, and lie in the region; `/api/facilities` capped at 5,000 rows.
- **Operator token** (`OPERATOR_TOKEN`) on drills, plan triggers and approvals. Off by default.
- **Ingestion retry:** a failed poll retries after 2 minutes.
- **AePDS matching:** 100 of 136 skipped offices now match.
- **Chaos tests:** `tests/senses/chaos/test_chaos_sources.py`.

## Evidence

**Driver PWA in real Chrome**, with an emulated location, the form filled in, and a labelled drill approved:

```
registered through the form: f01fd84d-...
after first flush: 2:11:34 AM (1 new, 0 dup) | queued: 0
plan ('d1886d61-...', 'awaiting_approval')
approved: 200
assignment api: 200
assignment on screen: DRILL - NOT A REAL DISPATCH | 103 km · about 2.6 h · 0/8 stops · avoids active hazard zones | Pick up AP Civil Supplies PDS stock, Duggirala (Guntur) ...
speak button visible: True
problems: none
```

**Dashboard in real Chrome:** no console errors, exceptions or failed requests. `docs/images/dashboard.png` shows a route bending around the flooded zone at Tenali.

**PWA queue:** 6 of 6.

**Why the PWA's queue is safe.** A failed upload keeps its fixes, a success removes only what was sent so a fix arriving mid-upload survives, and a rejection for being too old drops the batch so it cannot block the queue.

## AePDS matching

186 office-commodity rows were skipped, which is 136 distinct offices. Their real names fell into spelling variants (97), a name shared by two districts (19), and urban circles (20).

Plain fuzzy matching was rejected. It suggested `Bogole → Ongole`, `Akiveedu → Galiveedu` and `Kovur → Kovvur`, and a wrong match puts real ration stock in the wrong town. Instead, offices match a spelling-insensitive key, only inside districts that exact matches show the office belongs to, and only when one answer remains.

```
offices 573; matched now 537; previously unmatched 136; of those now matched 100
lost (previously matched, now not): []
```

Live after the restart: 709 stock rows at 536 suppliers, up from 578 rows. Each of the 100 was read through by eye. `Kovur` now reaches the real Kovuru in Nellore. The remaining 36 stay skipped and are logged by name: examples are `Bogole`, `Mpt Urb` and `Cuddapah`.

## Self-audit

- (a) `audit_runtime.py`: 0 findings.
- (c) No secrets tracked.
- (d) Every source has a timeout, retry, breaker and failure path.
- `npx tsc --noEmit`, `npx eslint src` and `next build` all pass.

## Not verified

- **Real phone.** The PWA ran in real Chrome with an emulated location. Background GPS, screen-off behaviour and a real dead zone are untested.
- **Telugu voice.** The speech button works, but without a Gemini key the instructions stayed in English, and headless Chrome has no voices.
- **Overpass** is reported as falling back to the extract, as on the previous machine.
- **SACHET polygons** return 403 here too, so alerts use the area-name fallback.
