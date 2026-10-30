# Gate 4 report: Instance B (Senses & Face), polish, deploy, docs

Run on 2026-10-31, on branch `integration/gate3`.

## Done

- **`README.md`:** setup for Windows, macOS and Linux, Mermaid architecture diagram, and a data-source table that states what is live and what was never wired.
- **`docs/senses.md`** and **`docs/demo-script.md`:** a timed script for the edge cases, a table of what to do when something fails on stage, and likely questions.
- **`infra/dev/stack.ps1`:** one command to run everything on Windows.
- **`infra/deploy/`:** backend image and a compose file that runs the whole backend on one VM.
- **Final audit:** 0 findings.

## Deployment checks

Nothing is deployed. These checks ran locally:

```
docker compose ... config --services      db redis migrate valhalla api brain ingestion
docker build -f infra/deploy/backend.Dockerfile     exit 0, 808 MB
run: imports ok; 7 migrations; driver app True True; user 1000
/data/documents owned by relief and writable (a first build would have failed to write PO PDFs)
next build                                exit 0
```

The image was built and started, but the full production stack was not run: there is not enough free disk on this machine for a second Valhalla and database.

## Blocked, needing a person

| Item | Needs |
|---|---|
| Deploy backend | A VM with 8 GB RAM, or Fly.io/Railway accounts |
| Deploy dashboard | A Vercel account, and the backend's URL |
| Real phone test | Someone inside Andhra Pradesh |

A public deployment must set `OPERATOR_TOKEN`, or anyone can approve dispatches.

## Demo water stock

`infra/demo/seed_demo_water.py` was **not run**, and stays clearly labelled. Water shows as an escalated shortfall until a real supplier checks in at `/portal`, which is the honest state under the zero-fake-data rule. The script remains in the repo in case the owner chooses to use it, disclosed.
