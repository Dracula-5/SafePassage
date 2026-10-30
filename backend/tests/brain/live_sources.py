"""Test-only helpers that pull live hazard polygons from the public NDMA SACHET feed."""

import xml.etree.ElementTree as ET

import httpx
from shapely import Polygon, box

from app.routing.valhalla import Location
from app.solvers.geometry import local_projection

SACHET_RSS = "https://sachet.ndma.gov.in/cap_public_website/rss/rss_india.xml"
SACHET_POLYGON = "https://sachet.ndma.gov.in/cap_public_website/FetchPolygonXMLFile?identifier={}"


def parse_cap_polygons(xml_text: str) -> list[Polygon]:
    """SACHET polygon files hold <polygon> elements of 'lat,lon lat,lon ...' (CAP 1.2 order)."""
    polys = []
    for el in ET.fromstring(xml_text).iter("polygon"):
        pts = [tuple(map(float, pair.split(","))) for pair in (el.text or "").split()]
        if len(pts) >= 4:
            poly = Polygon([(lon, lat) for lat, lon in pts])
            if poly.is_valid and poly.area > 0:
                polys.append(poly)
    return polys


async def live_hazards(client: httpx.AsyncClient, limit: int = 60):
    """Yield (identifier, sender, title, polygon) for recent SACHET alerts, largest polygon first."""
    rss = await client.get(SACHET_RSS, timeout=30)
    rss.raise_for_status()
    for item in list(ET.fromstring(rss.content).iter("item"))[:limit]:
        guid = item.findtext("guid")
        resp = await client.get(SACHET_POLYGON.format(guid), timeout=30)
        if resp.status_code != 200 or "<polygon>" not in resp.text:
            continue
        for poly in sorted(parse_cap_polygons(resp.text), key=lambda p: -p.area):
            yield guid, item.findtext("author"), item.findtext("title"), poly


def endpoints_around(poly: Polygon, offset_m: float = 4000) -> list[tuple[Location, Location]]:
    """Point pairs offset_m beyond the polygon's bounds on opposite sides (W-E and S-N)."""
    fwd, inv = local_projection([poly])
    x0, y0, x1, y1 = box(*poly.bounds).bounds
    mx0, my0 = fwd.transform(x0, y0)
    mx1, my1 = fwd.transform(x1, y1)
    cx, cy = (mx0 + mx1) / 2, (my0 + my1) / 2
    pairs = [((mx0 - offset_m, cy), (mx1 + offset_m, cy)), ((cx, my0 - offset_m), (cx, my1 + offset_m))]
    out = []
    for a, b in pairs:
        (alon, alat), (blon, blat) = inv.transform(*a), inv.transform(*b)
        out.append((Location(lon=alon, lat=alat), Location(lon=blon, lat=blat)))
    return out


def ring_points(poly: Polygon, offset_m: float, count: int) -> list[Location]:
    """`count` points evenly spaced on a circle around the polygon, `offset_m` beyond its extent."""
    import math

    fwd, inv = local_projection([poly])
    c = poly.centroid
    cx, cy = fwd.transform(c.x, c.y)
    x0, y0 = fwd.transform(poly.bounds[0], poly.bounds[1])
    x1, y1 = fwd.transform(poly.bounds[2], poly.bounds[3])
    radius = math.hypot(x1 - x0, y1 - y0) / 2 + offset_m
    out = []
    for k in range(count):
        t = math.tau * k / count
        lon, lat = inv.transform(cx + radius * math.cos(t), cy + radius * math.sin(t))
        out.append(Location(lon=lon, lat=lat))
    return out
