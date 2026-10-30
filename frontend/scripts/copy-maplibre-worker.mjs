// MapLibre resolves its web worker relative to its own bundle URL, which does not exist under
// Next's bundler. Serve the installed version's worker as a static file instead (see LiveMap).
import { copyFileSync, mkdirSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";

const require = createRequire(import.meta.url);
const dist = dirname(require.resolve("maplibre-gl/dist/maplibre-gl.mjs"));
mkdirSync("public/maplibre", { recursive: true });
copyFileSync(join(dist, "maplibre-gl-worker.mjs"), "public/maplibre/maplibre-gl-worker.mjs");
console.log("copied maplibre-gl-worker.mjs to public/maplibre/");
