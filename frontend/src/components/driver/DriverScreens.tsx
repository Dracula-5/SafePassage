"use client";

// The driver's six screens. Each one has a single purpose; the bottom bar moves between them. Everything shown comes
// from the trip hook (real GPS state, the real assignment). Nothing is invented: no delivery confirmation exists in
// the backend, so none is offered, and stops complete by themselves as the truck reaches them.

import { ReactNode } from "react";

import { Account } from "@/lib/api";
import { plate, tonnes } from "@/lib/format";
import { signOut } from "@/lib/session";

import { Icon } from "../ui";
import styles from "./driver.module.css";
import { stopVerb, Trip } from "./useTrip";

export type DriverView = "home" | "assignments" | "route" | "stops" | "status" | "profile";

export function parseDriverView(hash: string): DriverView {
  const name = hash.replace(/^#\/?/, "");
  return name === "assignments" || name === "route" || name === "stops" || name === "status" || name === "profile" ? name : "home";
}

export function goDriver(view: DriverView): void {
  window.location.hash = view === "home" ? "#/" : `#/${view}`;
}

const clock = (iso: string) => new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

const TABS: { view: DriverView; label: string; icon: string }[] = [
  { view: "home", label: "Home", icon: "home" },
  { view: "assignments", label: "Assignments", icon: "orders" },
  { view: "stops", label: "Stops", icon: "pin" },
  { view: "status", label: "Status", icon: "signal" },
  { view: "profile", label: "Profile", icon: "user" },
];

export function DriverNav({ view, trip }: { view: DriverView; trip: Trip }) {
  const active = view === "route" ? "assignments" : view;
  const needsLook = !trip.online || trip.gpsFailed || trip.request?.status === "sent";
  return (
    <nav className={styles.nav} aria-label="Driver sections">
      {TABS.map((t) => (
        <button key={t.view} type="button" className={`${styles.navItem} ${active === t.view ? styles.navOn : ""}`} aria-current={active === t.view ? "page" : undefined} onClick={() => goDriver(t.view)}>
          <Icon name={t.icon} size={26} />
          <span>{t.label}</span>
          {needsLook && (t.view === "status" ? !trip.online || trip.gpsFailed : t.view === "assignments" ? trip.request?.status === "sent" : false) && <i className={styles.navDot} aria-label="Needs attention" />}
        </button>
      ))}
    </nav>
  );
}

function Banners({ trip, view }: { trip: Trip; view: DriverView }) {
  const { vehicle, online, queued, routeChanged, assignment } = trip;
  return (
    <>
      {vehicle.simulated && <div className={`${styles.notice} ${styles.nWarn}`}><span><span className={styles.tag}>SIMULATED</span> This truck and its GPS are simulated for a presentation. No real vehicle is moving.</span></div>}
      {!online && <div className={`${styles.notice} ${styles.nWarn}`}>No signal. Your position is being saved ({queued} waiting) and sends by itself when you are back in range.</div>}
      {routeChanged && (
        <div className={`${styles.notice} ${styles.nRoute}`} role="alert">
          <span>New route. Follow the stops.{view !== "stops" && <> <button type="button" className="link" style={{ color: "inherit", textDecoration: "underline", minHeight: 0 }} onClick={() => { trip.dismissRouteChange(); goDriver("stops"); }}>Open stops</button></>}</span>
        </div>
      )}
      {assignment?.is_drill && <div className={`${styles.notice} ${styles.nDrill}`}>DRILL. This is not a real dispatch.</div>}
      {assignment?.route.kind === "evacuation" && <div className={`${styles.notice} ${styles.nGood}`}><span><b>Evacuation.</b> You are carrying people. The way out avoids the active hazard zones.</span></div>}
    </>
  );
}

/** A duty officer asking this driver to go somewhere. Only the driver answers: a watching officer just sees it. */
function RequestCard({ trip }: { trip: Trip }) {
  const { request } = trip;
  if (!request) return null;
  return (
    <section className={styles.card} role="alert" style={{ background: "var(--warn-bg)", borderColor: "#f6dfb0" }}>
      <small>Request from {request.requested_by}</small>
      <h2 style={{ fontSize: 22 }}>{request.place}</h2>
      {request.status === "sent" ? (
        <>
          <p>Please go to the edge of this area and wait there. Stay out of the marked zone.</p>
          {!trip.watching ? (
            <div className={styles.bigRow}>
              <button type="button" className={`primary ${styles.big}`} onClick={() => trip.answer(true)}>Accept</button>
              <button type="button" className={styles.big} onClick={() => trip.answer(false)}>Decline</button>
            </div>
          ) : <p className="muted small">Waiting for the driver to answer.</p>}
        </>
      ) : <p>Accepted. Wait at the edge of this area. Your route appears here once a plan is approved.</p>}
    </section>
  );
}

function Heading({ title, subtitle }: { title: string; subtitle?: string }) {
  return <header><h1 className={styles.title}>{title}</h1>{subtitle && <p className={styles.sub}>{subtitle}</p>}</header>;
}

export function HomeScreen({ trip, account }: { trip: Trip; account: Account | null }) {
  const { vehicle, assignment, request, noPhone, gps, gpsFailed, online, queued, sentAt } = trip;
  const route = assignment?.route;
  const done = route?.completed_stops ?? 0;
  const next = route?.stops[done];
  const first = (account?.name ?? vehicle.driver_name ?? "driver").split(" ")[0];
  return (
    <>
      <Heading title={`Hi, ${first}`} subtitle={`Truck ${vehicle.fleet_number} · ${plate(vehicle.label)}`} />
      <Banners trip={trip} view="home" />
      {request && !route && <RequestCard trip={trip} />}
      {!route ? (
        <section className={`${styles.card} ${styles.waiting}`}>
          <span className={styles.pulse} aria-hidden="true" />
          <h1>No route yet</h1>
          <p className="muted">Keep this screen open. Your route appears here as soon as a duty officer approves it.</p>
          {!noPhone && <small className="muted">{sentAt ? `Position last sent at ${sentAt}` : "Waiting to send your first position"}</small>}
        </section>
      ) : next ? (
        <section className={styles.hero}>
          <small>Next stop · {stopVerb(next.kind, route.kind)}</small>
          <h1>{next.name}</h1>
          <p>By {clock(next.eta)}</p>
        </section>
      ) : <section className={`${styles.hero} ${styles.heroDone}`}><h1>Route complete</h1><p>Thank you.</p></section>}
      <div className={styles.tiles}>
        <div className={styles.tile}><small>GPS</small><b className={gpsFailed ? styles.bad : styles.good}>{gps}</b></div>
        <div className={styles.tile}><small>Network</small><b className={online ? styles.good : styles.bad}>{online ? "Online" : "No signal"}</b></div>
        <div className={styles.tile}><small>Waiting to send</small><b>{noPhone ? "–" : queued}</b></div>
      </div>
      {route && (
        <section className={styles.card}>
          <div className={styles.cardRow}><b>{done} of {route.stops.length} stops</b><span className="muted">{(route.distance_m / 1000).toFixed(0)} km · {(route.duration_s / 3600).toFixed(1)} h</span></div>
          <div className="progress" aria-hidden="true"><span style={{ width: `${(100 * done) / Math.max(1, route.stops.length)}%` }} /></div>
          <div className={styles.bigRow}>
            <button type="button" className={styles.big} onClick={() => goDriver("stops")}>See all stops</button>
            <button type="button" className={styles.big} onClick={() => goDriver("route")}>Route details</button>
          </div>
        </section>
      )}
    </>
  );
}

export function AssignmentsScreen({ trip }: { trip: Trip }) {
  const { assignment, request } = trip;
  const route = assignment?.route;
  return (
    <>
      <Heading title="My assignments" subtitle="What a duty officer has asked this truck to do." />
      <Banners trip={trip} view="assignments" />
      {request && <RequestCard trip={trip} />}
      {route ? (
        <button type="button" className={styles.listItem} onClick={() => goDriver("route")} aria-label={`Open the ${route.kind === "evacuation" ? "evacuation" : "delivery"} route`}>
          <span className={styles.bubble}><Icon name={route.kind === "evacuation" ? "evac" : "truck"} size={26} /></span>
          <span className={styles.listText}>
            <b>{route.kind === "evacuation" ? "Evacuation" : "Supply delivery"}{assignment?.is_drill ? " · DRILL" : ""}</b>
            <span>{route.stops.length} stops · {(route.distance_m / 1000).toFixed(0)} km · {route.completed_stops} done</span>
          </span>
          <Icon name="chevron" size={22} />
        </button>
      ) : !request && (
        <section className={`${styles.card} ${styles.waiting}`}>
          <Icon name="orders" size={40} />
          <h2>No assignments</h2>
          <p className="muted">When a duty officer approves a plan that uses this truck, it appears here.</p>
        </section>
      )}
    </>
  );
}

export function RouteScreen({ trip }: { trip: Trip }) {
  const { assignment } = trip;
  const route = assignment?.route;
  return (
    <>
      <div className={styles.cardRow} style={{ justifyContent: "flex-start" }}>
        <button type="button" className="ghost" style={{ minHeight: 48, minWidth: 48, padding: 0 }} aria-label="Back to assignments" onClick={() => goDriver("assignments")}><Icon name="back" /></button>
        <h1 className={styles.title}>Route details</h1>
      </div>
      <Banners trip={trip} view="route" />
      {!route || !assignment ? (
        <section className={`${styles.card} ${styles.waiting}`}><Icon name="route" size={40} /><h2>No route</h2><p className="muted">There is no route for this truck right now.</p></section>
      ) : (
        <>
          <section className={styles.card}>
            <h2>{route.kind === "evacuation" ? "Evacuation route" : "Delivery route"}</h2>
            <div className={styles.facts}>
              <div className={styles.fact}><span>Distance</span><b>{(route.distance_m / 1000).toFixed(0)} km</b></div>
              <div className={styles.fact}><span>Driving time</span><b>{(route.duration_s / 3600).toFixed(1)} h</b></div>
              {route.kind !== "evacuation" && <div className={styles.fact}><span>Load</span><b>{(route.load_kg / 1000).toFixed(1)} t</b></div>}
              <div className={styles.fact}><span>Stops</span><b>{route.completed_stops} of {route.stops.length} done</b></div>
              {route.min_hazard_clearance_m !== null && <div className={styles.fact}><span>Keeps clear of hazards by</span><b>{route.min_hazard_clearance_m.toFixed(0)} m</b></div>}
            </div>
            <p className={styles.safe}>This route avoids active hazard zones. Do not take shortcuts.</p>
          </section>
          <section className={styles.card}>
            <div className={styles.cardRow}>
              <h2>Instructions</h2>
              {assignment.instructions.text && <button type="button" className="primary" style={{ minHeight: 48 }} onClick={() => trip.speak(assignment.instructions.text, assignment.instructions.language)}><Icon name="sound" size={20} /> Read aloud</button>}
            </div>
            {assignment.instructions.text
              ? <p className={styles.instructions}>{assignment.instructions.text}</p>
              : <p className="muted">Preparing your instructions in your language. The stops are ready now.</p>}
          </section>
          <button type="button" className={styles.big} onClick={() => goDriver("stops")}>See the stops</button>
        </>
      )}
    </>
  );
}

export function StopsScreen({ trip }: { trip: Trip }) {
  const route = trip.assignment?.route;
  const done = route?.completed_stops ?? 0;
  return (
    <>
      <Heading title="Pickup & delivery stops" subtitle={route ? `${done} of ${route.stops.length} done` : undefined} />
      <Banners trip={trip} view="stops" />
      {!route ? (
        <section className={`${styles.card} ${styles.waiting}`}><Icon name="pin" size={40} /><h2>No stops yet</h2><p className="muted">Your stops appear here once a plan is approved.</p></section>
      ) : (
        <section className={styles.card}>
          <div className="progress" aria-hidden="true"><span style={{ width: `${(100 * done) / Math.max(1, route.stops.length)}%` }} /></div>
          <ol className={styles.stops}>
            {route.stops.map((s, i) => (
              <li key={`${s.facility_id}-${i}`} className={i < done ? "done" : i === done ? "current" : ""} aria-current={i === done ? "step" : undefined}>
                <b>{stopVerb(s.kind, route.kind)}</b>
                <span>{s.name}</span>
                <small>by {clock(s.eta)}{i < done ? " · done" : i === done ? " · next" : ""}</small>
              </li>
            ))}
          </ol>
          <p className={styles.safe}>Stops are marked done by themselves as the truck reaches them.</p>
        </section>
      )}
    </>
  );
}

export function StatusScreen({ trip }: { trip: Trip }) {
  const { noPhone, gps, gpsFailed, online, queued, sentAt, vehicle, watching } = trip;
  return (
    <>
      <Heading title="GPS & connection" subtitle="Whether your position is reaching the duty officer." />
      <Banners trip={trip} view="status" />
      <section className={styles.card}>
        <div className={styles.facts}>
          <div className={styles.fact}><span>GPS</span><b className={gpsFailed ? styles.bad : styles.good}>{gps}</b></div>
          <div className={styles.fact}><span>Network</span><b className={online ? styles.good : styles.bad}>{online ? "Online" : "No signal"}</b></div>
          <div className={styles.fact}><span>Positions waiting to send</span><b>{noPhone ? "–" : queued}</b></div>
          <div className={styles.fact}><span>Last position sent</span><b>{noPhone ? "–" : sentAt ?? "Not yet"}</b></div>
          <div className={styles.fact}><span>This screen</span><b>{watching ? "Watching only" : vehicle.simulated ? "Simulated truck" : "Your phone"}</b></div>
        </div>
      </section>
      <section className={styles.card}>
        <h2>How this works</h2>
        <p className="muted">{noPhone
          ? "This screen does not send a position. It only shows the truck's route."
          : "Your position is sent every 5 seconds. With no signal it is saved on this phone and sent by itself when you are back in range. Keep this screen open."}</p>
      </section>
    </>
  );
}

export function ProfileScreen({ trip, account, extra }: { trip: Trip; account: Account | null; extra?: ReactNode }) {
  const { vehicle, watching } = trip;
  return (
    <>
      <Heading title="Profile" />
      <section className={styles.card}>
        <div className={styles.facts}>
          <div className={styles.fact}><span>Driver</span><b>{watching ? vehicle.driver_name ?? "Not given" : account?.name ?? vehicle.driver_name ?? "Not given"}</b></div>
          {!watching && account && <div className={styles.fact}><span>Email</span><b>{account.email}</b></div>}
          <div className={styles.fact}><span>Truck number</span><b>{vehicle.fleet_number}</b></div>
          <div className={styles.fact}><span>Number plate</span><b>{plate(vehicle.label)}</b></div>
          <div className={styles.fact}><span>Load capacity</span><b>{tonnes(vehicle.capacity_kg)}</b></div>
        </div>
      </section>
      {extra}
      {!watching && <button type="button" className={`danger ${styles.big}`} onClick={signOut}><Icon name="logout" size={20} /> Sign out</button>}
    </>
  );
}
