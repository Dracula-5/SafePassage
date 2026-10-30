"""Serves the Driver PWA (repo-root driver-pwa/) at /driver/ so the app and API share one origin."""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, RedirectResponse

router = APIRouter(include_in_schema=False)
APP_DIR = Path(__file__).resolve().parents[3] / "driver-pwa"
TYPES = {".html": "text/html", ".js": "text/javascript", ".webmanifest": "application/manifest+json", ".svg": "image/svg+xml"}


@router.get("/driver")
async def driver_root() -> RedirectResponse:
    return RedirectResponse("/driver/")


@router.get("/driver/{path:path}")
async def driver_file(path: str = "") -> FileResponse:
    target = (APP_DIR / (path or "index.html")).resolve()
    if APP_DIR not in target.parents or not target.is_file() or target.suffix not in TYPES:
        raise HTTPException(404, "not found")
    headers = {"Service-Worker-Allowed": "/driver/"} if target.name == "sw.js" else {}
    return FileResponse(target, media_type=TYPES[target.suffix], headers=headers)
