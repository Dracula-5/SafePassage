// Map tiles, fetched by this server. OpenStreetMap answers 403 to a browser that hides its Referer (privacy
// settings, some extensions) and to an address it has limited, and a blank map on stage can't be argued with.
// From here it always sees a proper User-Agent, and if it refuses anyway, a second free map takes over.

const SOURCES = [
  (z: string, x: string, y: string) => `https://tile.openstreetmap.org/${z}/${x}/${y}.png`,
  (z: string, x: string, y: string) => `https://a.basemaps.cartocdn.com/rastertiles/voyager/${z}/${x}/${y}.png`,
];
const AGENT = "ReliefRoute/1.0 (disaster relief logistics; https://github.com/Dracula-5/SafePassage)";
const TIMEOUT_MS = 6000;
const MAX_ZOOM = 19;
const ONE_DAY_S = 86_400;

export async function GET(_: Request, { params }: { params: Promise<{ z: string; x: string; y: string }> }) {
  const { z, x, y: file } = await params;
  const y = file.replace(/\.png$/, "");
  // Only numbers reach the URLs above, so nobody can point this server at anything else.
  if (![z, x, y].every((part) => /^\d{1,7}$/.test(part)) || Number(z) > MAX_ZOOM) return new Response(null, { status: 404 });
  for (const source of SOURCES) {
    try {
      const tile = await fetch(source(z, x, y), { headers: { "User-Agent": AGENT }, cache: "no-store", signal: AbortSignal.timeout(TIMEOUT_MS) });
      if (tile.ok) {
        return new Response(await tile.arrayBuffer(), { headers: { "Content-Type": "image/png", "Cache-Control": `public, max-age=${ONE_DAY_S}` } });
      }
    } catch {
      // Too slow or unreachable: the next map may answer.
    }
  }
  return new Response(null, { status: 502 });
}
