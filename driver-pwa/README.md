# Driver PWA (owner: Instance B)

Static app served by the backend at `/driver/` (same origin as the API, so one HTTPS URL works on
a phone). Geolocation needs HTTPS on phones: expose the backend with a tunnel, e.g.
`cloudflared tunnel --url http://localhost:8000`, and open `https://<tunnel>/driver/`.

- Registration: `POST /api/vehicles`; the device token is kept in localStorage.
- GPS: `watchPosition`, one fix per 10 s, queued in localStorage and flushed every 15 s in batches
  of up to 500 (`POST /api/telemetry`). Offline: fixes keep queueing; resends are de-duplicated by
  the server.
- Assignment: `GET /api/vehicles/{id}/assignment` every 20 s; instructions read aloud with the Web
  Speech API in the driver's language (te-IN / hi-IN) when the phone has that voice.
