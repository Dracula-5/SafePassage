# Remaining work: draft GitHub issues

These are **drafts**. None has been created on GitHub. Each one comes from a gap already written down in [HANDOFF.md](HANDOFF.md), the [gate reports](gates/) or the [readiness audit](FINAL_TESTING_AND_DEPLOYMENT_READINESS.md), and says which. To file one, copy its title and body into a new issue, or save the body to a file and run `gh issue create --title "<title>" --body-file <file> --label <labels>`.

Labels used: `gate-3` (closed loop and edge cases), `gate-4` (polish, deploy, docs), `needs-owner` (only the project owner or a teammate can do it), `contract` (needs a contract request decided), `idea` (listed in HANDOFF §6 as an idea, not a commitment).

## Gate 3: closed loop and edge cases

### 1. Record a real phone's GPS run inside Andhra Pradesh, then tag Gates 2 and 3

Labels: `gate-3`, `needs-owner`

**Why.** Gates 2 and 3 are not tagged, because every closed-loop run so far used simulated vehicles, or the real driver screen in Chrome with an emulated location. Background GPS, screen-off behaviour and a real dead zone are untested.

**What to do.**
- A teammate inside Andhra Pradesh opens the site on a phone over HTTPS and signs up as a driver, and a duty officer approves them. The road graph covers only AP, and vehicles more than 200 km from the relief points are ignored.
- Run a drill near them and approve the plan.
- Drive or walk part of the route, including a stretch with the screen off and a stretch with no signal.

**Done when.**
- [ ] The phone receives its route, and its stops are marked done from real GPS.
- [ ] Leaving the route starts a replan.
- [ ] Going offline holds the route for the grace period and then reassigns it.
- [ ] The output is pasted into `docs/gates/A-G3.md` and `docs/gates/B-G3.md`.
- [ ] Tags `phase2-*` and `phase3-*` are pushed.

**Source.** HANDOFF §3 ("Real-phone GPS"), A-G3 and B-G3 "Not verified".

### 2. Verify Telugu voice on a real phone

Labels: `gate-3`, `needs-owner`

**Why.** The Telugu text is verified, with a check that every number survives translation. Speech depends on the phone's installed voices, and headless Chrome has none.

**Done when.**
- [ ] On an Android phone with a Telugu speech pack, the driver screen reads a dispatched route aloud in Telugu.
- [ ] It reads aloud again when the route changes.
- [ ] The result is noted in B-G3.

**Source.** HANDOFF §3 ("Telugu voice on a phone"), B-G3, B-G5 "Not verified".

### 3. Record a live Gemini run, including a replan, in the gate reports

Labels: `gate-3`

**Why.** A-G3 says the recorded runs used the labelled rule-based steps, because `GEMINI_API_KEY` was blank on that machine, and that replans had never run with the live LLM. If a live run has happened since, it is not in the gate reports.

**Done when.**
- [ ] With a key set, a drill's `agent_events` timeline shows Gemini triage and the brief, with no `RULE-BASED FALLBACK`.
- [ ] A replan also shows Gemini's steps.
- [ ] The Telugu instructions in the same run are produced by Gemini.
- [ ] The timeline is pasted into A-G3.

**Source.** A-G3 "Not verified" (Gemini), A-G2 "Still open".

### 4. Re-run the browser end-to-end suites and the drill-to-driver test in a clear area

Labels: `gate-3`

**Why.**
- The readiness audit rewrote the presentation-mode specs for the new screens but did not run them.
- The drill-to-driver test draws its drill over Vijayawada, where real hazards cut every route on the day of the audit.
- In the manual run, stop-by-stop GPS progress was shown for only 2 of 10 stops, because the simulator ran faster than the 300 m arrival circle allows.

**What to do.** Point the drill test at an area the live hazards don't cover, or pick the area at run time. Then run `npm run test:e2e` with `E2E_ROUTING=1`, and again with `E2E_DEMO=1`.

**Done when.**
- [ ] Both runs pass, or every skip is explained.
- [ ] One drill shows every stop reached from simulated GPS at a realistic speed.
- [ ] The results are recorded in the readiness audit or a new gate note.

**Source.** Readiness audit §6 (stage 9), §8 and §10.

### 5. Buy from another supplier when one is cut off after dispatch

Labels: `gate-3`, `idea`

**Why.** Today, if a supplier is cut off once goods are on order, those goods are escalated to a person and Procurement is not re-run.

**Done when.**
- [ ] A hazard that cuts off a dispatched supplier leads to a replan that sources the same goods elsewhere, through the Critic and the approval rules.
- [ ] Goods are escalated only if no other stock exists.
- [ ] An integration test covers it.

**Source.** A-G3 "Not verified" (re-sourcing), HANDOFF §6.

### 6. Make crash recovery safe with two brain workers

Labels: `gate-3`, `idea`

**Why.** Crash recovery assumes a single brain worker. Running two needs real locks around resuming plans.

**Done when.**
- [ ] Two brain workers run together.
- [ ] Killing either one mid-plan or mid-dispatch leaves exactly one set of orders and routes.
- [ ] A chaos test covers it.

**Source.** A-G3 "Not verified" (two brain workers), HANDOFF §6.

## Gate 4: polish, deploy, docs

### 7. Deploy the production stack on a real VM with a domain and HTTPS

Labels: `gate-4`, `needs-owner`

**Why.** The production compose stack has only run on the development laptop, with no real domain or certificate. The readiness audit's first condition is that it runs once on the target machine.

**What to do.** Follow [deploy.md](deploy.md) on a VM with 8 GB of RAM, Docker, ports 80 and 443, and a domain pointing at it.

**Done when.**
- [ ] `https://<domain>/health` answers ok with a real certificate.
- [ ] The first duty officer has been approved with the operator token.
- [ ] A drill plans and dispatches.
- [ ] A backup has been taken and restored once.

**Source.** HANDOFF §3 ("Deploy"), A-G4-security "Not verified", readiness audit "Verdict" condition 1 and §11.

### 8. Give the Vercel site a permanent API

Labels: `gate-4`, `needs-owner`

**Why.** `https://safepassage-frontend.vercel.app` forwards every API call to `API_ORIGIN`. Today that points at the laptop through a tunnel, so the site works only while the laptop, Docker and the tunnel are all running. The free Render API has no workers, so it shows no live alerts, plans or routes.

**What to do.** Choose one, and set `API_ORIGIN` in Vercel (Production and Preview), then redeploy:
- the VM from issue 7;
- `render.yaml` on paid Render plans;
- a permanent tunnel to a machine that stays on.

**Done when.**
- [ ] `/health` and `/api/status` on the Vercel site answer from the chosen API, with the laptop switched off.

**Source.** [deploy-vercel-render.md](deploy-vercel-render.md).

### 9. Fix or remove the second Vercel project that fails every push

Labels: `gate-4`, `needs-owner`

**Why.** Every push brings a failed-deployment email from a Vercel project named `safepassage`, on another team, because its Root Directory is not `frontend`. The live site is the `safepassage-frontend` project. This was seen in the deployment emails of 2026-11-04 and is not recorded elsewhere in the repo.

**Done when.**
- [ ] That project has Root Directory `frontend` and `API_ORIGIN` set, or it is disconnected from the repository.
- [ ] A push no longer produces a failure email.

### 10. Review contract requests 002 to 010 and update `openapi.yaml`

Labels: `gate-4`, `contract`, `needs-owner`

**Why.** Ten requests in `contracts/requests/` are marked PENDING human review. The code already behaves as they describe, but `contracts/openapi.yaml` does not describe their endpoints until they are applied. Two files are both numbered 005: `B-account-approval.md` and `B-email-links.md`.

**Done when.**
- [ ] Each request is marked approved (and applied to `contracts/`) or rejected (and the code reverted).
- [ ] The duplicate 005 is renumbered.
- [ ] `openapi.yaml` matches the running API.

**Source.** HANDOFF §1 and §3, readiness audit condition 2.

### 11. Make `main` the default branch and retire the old ones

Labels: `gate-4`, `needs-owner`

**Why.**
- GitHub's default branch is `feat/brain`, which stopped at Gate 2 on 2026-10-31.
- `main` contains all of it plus 69 later commits.
- `integration/gate3`, `integration/gate2` and `feat/senses-face` are all already contained in `main`.
- HANDOFF §1 still describes `main` as Gate 2 only.

**Done when.**
- [ ] The repository's default branch is `main`.
- [ ] The merged branches are deleted, or kept and labelled as history.
- [ ] HANDOFF §1 describes the branches as they are.

**Source.** HANDOFF §3 ("Merge into main").

### 12. Water stock from a real supplier

Labels: `gate-4`, `needs-owner`

**Why.** There is no public source for drinking-water stock, so water reaches a plan only through a supplier check-in. The labelled demo seed (`infra/demo/seed_demo_water.py`, "DEMO SEED (not real stock)") conflicts with the zero-fake-data rule unless it is disclosed.

**Done when.**
- [ ] A real supplier, or a teammate reporting stock they actually hold, checks in water at `/supplier`.
- [ ] A duty officer verifies that supplier.
- [ ] No DEMO SEED supplier is in the database used for judging.

**Source.** HANDOFF §3 ("Real water stock"), Gate 2 HANDOFF §3 on `feat/brain`.

### 13. Real SMS, WhatsApp and email delivery

Labels: `gate-4`, `needs-owner`

**Why.** Twilio is a Trial account, which can only reach numbers verified with Twilio, so no real message has gone out; drills send nothing by design. Email works against a real SMTP server, but delivery through a real provider, including spam handling, is untested.

**Done when.**
- [ ] A dispatched (non-drill) route sends its driver an SMS or WhatsApp message through a paid or verified Twilio sender.
- [ ] Sign-up confirmation and password-reset emails arrive through a real provider set in `SMTP_HOST` and `MAIL_FROM`.

**Source.** HANDOFF §3, B-G5 "Not verified".

### 14. A valid NASA FIRMS key

Labels: `gate-4`, `needs-owner`

**Why.** The FIRMS poller is built, but it stays off until a valid `FIRMS_MAP_KEY` is set. NASA rejected the key recorded in the handoff; real keys are 32 characters.

**Done when.**
- [ ] With a valid key, the Sources tab shows FIRMS as current.

**Source.** HANDOFF §3, B-G5 "Not verified".

### 15. Show the proposed routes and orders before a duty officer approves

Labels: `gate-4`, `contract`

**Why.** Before approving, a duty officer sees the demand, the safety result and the summary, but not the proposed routes or orders. Those are written to the database only at dispatch. Changing what the plan detail returns touches the frozen contract.

**Done when.**
- [ ] A contract request is raised and decided.
- [ ] A plan waiting for approval shows its routes on the map and its orders in the panel.

**Source.** Readiness audit §10 (Medium).

### 16. Langfuse dashboards and LLM cost tuning

Labels: `gate-4`

**Why.** Tracing is built (optional, with a 3-second limit, and it never affects a plan), but A-G4 lists the dashboards as not done for lack of keys, and cost tuning as needing a live LLM.

**Done when.**
- [ ] With Langfuse keys set, a drill's LLM calls appear as one trace.
- [ ] Token use per plan is written down in A-G4.

**Source.** A-G4 "Not done".

### 17. Small fixes from the readiness audit

Labels: `gate-4`

**Done when.**
- [ ] `/health` is public and reports "degraded": decide whether it should say less to anonymous callers.
- [ ] `stack.ps1 status` lists the API, workers and web server, not only the containers.

**Source.** Readiness audit §10 (Low).

### 18. A better population source

Labels: `idea`, `contract`

**Why.** OSM census tags cover about 2.7% of settlements. The Gate 2 overcount, where Vijayawada's 1,048,240 people counted in full and demand came to about 1,135 t, is fixed: a city or town is now scaled by the share of its mandal inside the zone, and two integration tests cover it. Three gaps remain:
- the scaling assumes even density;
- a zone covering a whole mandal still counts the whole city;
- rural areas are undercounted.

**Done when.**
- [ ] A contract request for a gridded source such as WorldPop is decided.
- [ ] If it is approved, Needs uses it, with coverage still shown on every plan.

**Source.** HANDOFF §5 and §6, A-G2 "Not yet done" item 5 and its addendum.

### 19. An independent security review

Labels: `gate-4`, `needs-owner`

**Why.** The security work was reviewed by the people who built it.

**Known gaps from the readiness audit:**
- the supplier portal is open by design;
- a registered phone can fake its GPS;
- WhatsApp messages can be replayed;
- there is no encryption at rest beyond the disk;
- there is no network-level DoS protection.

**Done when.**
- [ ] Someone outside the build reviews `docs/security.md` and the deployed stack.
- [ ] Their findings are filed as issues.

**Source.** A-G4-security "Not verified", readiness audit §9.
