// UI strings come from t() (i18n.js), e.g. t("mode.bus").
const MODES = [{ id: "tram", on: true }, { id: "metro", on: true }, { id: "train", on: true }, { id: "bus", on: true }, { id: "ferry", on: false }];
const ORDER = ["bus", "ferry", "train", "metro", "tram"];  // draw order, bottom to top
// Station rings show the most important mode served there (lines themselves use their own colours).
const STATION_RING = { train: "#C2185B", metro: "#6A1B9A", tram: "#00A261", ferry: "#00897B", bus: "#1565C0" };
// Theme: "light", "dark" or "auto" (follow the system); the page's colours follow data-theme on <html>.
const darkScheme = matchMedia("(prefers-color-scheme: dark)");
let theme = ["light", "dark"].includes(prefs.get("theme")) ? prefs.get("theme") : "auto";
const isDark = () => theme === "dark" || (theme === "auto" && darkScheme.matches);
const basemap = () => `https://tiles.openfreemap.org/styles/${isDark() ? "dark" : "positron"}`;
let dark = isDark();
if (theme !== "auto") document.documentElement.dataset.theme = theme;

let REGIONS = [], REGION = null, LOADED = new Set(), ROUTES = [], BY_ID = {}, CORRIDORS = {}, STATIONS = {}, DAYS = {}, BIN = 15;
// stops: selected station ids; match: show lines serving "any" or "all" of them.
// weekly: hide lines that don't run at least once in every week the build checked.
const state = { time: { on: false, day: "wd", from: 28, to: 36 }, stops: [], match: "any", route: null, weekly: true };
const isMac = /Mac|iPhone|iPad/.test(navigator.platform);
const MULTI_KEY = isMac ? "⌘" : "Ctrl";
let visible = new Uint8Array(0), hoverPopup, stopsData = { type: "FeatureCollection", features: [] };
// False while the basemap is being swapped (theme change), when the map has none of the layers below.
let layersReady = false;

// Read before MapLibre adds its own map= parameter to the URL.
const INITIAL_HASH = location.hash;
const map = new maplibregl.Map({
  container: "map",
  center: [11.97, 57.71], zoom: 11, hash: "map", attributionControl: false,
});
loadBasemap();
map.addControl(new maplibregl.NavigationControl(), "top-right");
/** The basemap for the current theme. Its filters compare properties with numbers even on features
 *  without them (e.g. ["<=", ["get", "ref_length"], 6] on roads without a ref), which works but warns
 *  for every such feature; checking that the property is there first keeps the same result quietly. */
function loadBasemap() {
  const guard = x => {
    if (!Array.isArray(x)) return x;
    const e = x.map(guard);
    const [op, a, b] = e;
    return ["<", "<=", ">", ">="].includes(op) && e.length === 3 && Array.isArray(a) && a[0] === "get" && a.length === 2 &&
      typeof b === "number" ? ["all", ["has", a[1]], e] : e;
  };
  map.setStyle(basemap(), { diff: false,
    transformStyle: (_, style) => ({ ...style, layers: style.layers.map(l => l.filter ? { ...l, filter: guard(l.filter) } : l) }) });
}
// The OpenFreeMap styles use a few icons their sprite doesn't have ("wood-pattern", "circle-11"); give
// those an empty image, which draws nothing, as missing ones do, but without a warning each.
map.on("styleimagemissing", e => {
  if (!map.hasImage(e.id)) map.addImage(e.id, { width: 1, height: 1, data: new Uint8Array(4) });
});
// Added once the language is known, and again when it changes (the attribution is translated).
let bottomControls = [];
function addBottomControls() {
  bottomControls.forEach(c => map.removeControl(c));
  bottomControls = [new maplibregl.AttributionControl({ customAttribution: t("attribution") }), new maplibregl.ScaleControl()];
  bottomControls.forEach(c => map.addControl(c, "bottom-right"));
}

// ---------- helpers ----------
const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const textColor = c => {
  const n = parseInt(c.slice(1), 16), r = n >> 16, g = (n >> 8) & 255, b = n & 255;
  return (0.299 * r + 0.587 * g + 0.114 * b) > 150 ? "#111" : "#fff";
};
// Mode icons (Material Design icons, Apache 2.0), drawn in the text colour.
const ICON_PATHS = {
  bus: "M4 16c0 .88.39 1.67 1 2.22V20c0 .55.45 1 1 1h1c.55 0 1-.45 1-1v-1h8v1c0 .55.45 1 1 1h1c.55 0 1-.45 1-1v-1.78c.61-.55 1-1.34 1-2.22V6c0-3.5-3.58-4-8-4s-8 .5-8 4v10zm3.5 1c-.83 0-1.5-.67-1.5-1.5S6.67 14 7.5 14s1.5.67 1.5 1.5S8.33 17 7.5 17zm9 0c-.83 0-1.5-.67-1.5-1.5s.67-1.5 1.5-1.5 1.5.67 1.5 1.5-.67 1.5-1.5 1.5zm1.5-6H6V6h12v5z",
  train: "M12 2c-4 0-8 .5-8 4v9.5C4 17.43 5.57 19 7.5 19L6 20.5v.5h2.23l2-2H14l2 2h2v-.5L16.5 19c1.93 0 3.5-1.57 3.5-3.5V6c0-3.5-3.58-4-8-4zM7.5 17c-.83 0-1.5-.67-1.5-1.5S6.67 14 7.5 14s1.5.67 1.5 1.5S8.33 17 7.5 17zm3.5-7H6V6h5v4zm2 0V6h5v4h-5zm3.5 7c-.83 0-1.5-.67-1.5-1.5s.67-1.5 1.5-1.5 1.5.67 1.5 1.5-.67 1.5-1.5 1.5z",
  tram: "M19 16.94V8.5c0-2.79-2.61-3.4-6.01-3.49l.76-1.51H17V2H7v1.5h4.75l-.76 1.52C7.86 5.11 5 5.73 5 8.5v8.44c0 1.45 1.19 2.66 2.59 2.97L6 21.5v.5h2.23l2-2H14l2 2h2v-.5L16.5 20h-.08c1.69 0 2.58-1.37 2.58-3.06zm-7 1.56c-.83 0-1.5-.67-1.5-1.5s.67-1.5 1.5-1.5 1.5.67 1.5 1.5-.67 1.5-1.5 1.5zm5-4.5H7V9h10v5z",
  metro: "M12 2c-4.42 0-8 .5-8 4v9.5C4 17.43 5.57 19 7.5 19L6 20.5v.5h12v-.5L16.5 19c1.93 0 3.5-1.57 3.5-3.5V6c0-3.5-3.58-4-8-4zM7.5 17c-.83 0-1.5-.67-1.5-1.5S6.67 14 7.5 14s1.5.67 1.5 1.5S8.33 17 7.5 17zm3.5-6H6V6h5v5zm5.5 6c-.83 0-1.5-.67-1.5-1.5s.67-1.5 1.5-1.5 1.5.67 1.5 1.5-.67 1.5-1.5 1.5zm1.5-6h-5V6h5v5z",
  ferry: "M20 21c-1.39 0-2.78-.47-4-1.32-2.44 1.71-5.56 1.71-8 0C6.78 20.53 5.39 21 4 21H2v2h2c1.38 0 2.74-.35 4-.99 2.52 1.29 5.48 1.29 8 0 1.26.65 2.62.99 4 .99h2v-2h-2zM3.95 19H4c1.6 0 3.02-.88 4-2 .98 1.12 2.4 2 4 2s3.02-.88 4-2c.98 1.12 2.4 2 4 2h.05l1.89-6.68c.08-.26.06-.54-.06-.78s-.34-.42-.6-.5L20 10.62V6c0-1.1-.9-2-2-2h-3V1H9v3H6c-1.1 0-2 .9-2 2v4.62l-1.29.42c-.26.08-.48.26-.6.5s-.15.52-.06.78L3.95 19zM6 6h12v3.97L12 8 6 9.97V6z",
};
const modeLabel = id => t(`mode.${id}`);
const modeIcon = mode => `<svg class="mode-icon" viewBox="0 0 24 24" role="img" aria-label="${modeLabel(mode)}"><title>${modeLabel(mode)}</title><path d="${ICON_PATHS[mode]}"/></svg>`;
const badge = r => `<span class="badge" style="background:${r.color};color:${textColor(r.color)}">${esc(r.label)}</span>`;
const modeOn = id => MODES.find(m => m.id === id).on;
const fmt = bin => {
  const m = bin * BIN, h = Math.floor(m / 60) % 24, mm = String(m % 60).padStart(2, "0");
  return `${String(h).padStart(2, "0")}:${mm}${m >= 1440 ? "⁺¹" : ""}`;
};
const fmtDate = d => new Date(`${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6)}T12:00`).toLocaleDateString(t("locale"), { weekday: "short", day: "numeric", month: "long" });

// Times are 15-minute bins from midnight (the build's bin_minutes); a service day shown runs from
// 04:00 (FIRST_BIN) to 04:00 the next day (DAY_BINS).
const DAY_BINS = 112, FIRST_BIN = 16;
/** Cumulative counts from a compact [first bin, counts...] histogram: cum[b] = total in bins < b. */
function cumulative(compact) {
  const cum = new Uint16Array(DAY_BINS + 1);
  for (let b = 0; b < DAY_BINS; b++) {
    const i = b - compact[0] + 1;
    cum[b + 1] = cum[b] + (i >= 1 && i < compact.length ? compact[i] : 0);
  }
  return cum;
}

/** Trips of route r running at some point in bins [from, to): started before `to`, not ended before `from`. */
function tripsInWindow(r, day = state.time.day, from = state.time.from, to = state.time.to) {
  const a = r.act[day];
  return a ? a.cs[to] - a.ce[from] : 0;
}

/** First and last bin with service on a day. */
const serviceSpan = a => [a.s[0], a.e[0] + a.e.length - 2];

// Line widths per zoom; parallel lines in a corridor are spaced one width plus a small gap, so the
// casing shows between them and each line in a bundle stays distinguishable.
const WIDTH = {
  tram:  [[9, 1.6], [12, 3], [14, 4.5], [17, 8]],
  metro: [[9, 2], [12, 3.4], [14, 4.8], [17, 8]],
  train: [[6, 1.5], [12, 3.2], [14, 4.5], [17, 7]],
  bus:   [[9, 0.6], [12, 1.4], [14, 2.6], [17, 5]],
  ferry: [[9, 1], [13, 2.5], [17, 4]],
};
const widthExpr = (mode, add = 0) => ["interpolate", ["linear"], ["zoom"], ...WIDTH[mode].flatMap(([z, w]) => [z, w + add])];
const GAP = { tram: 0.5, metro: 0.6, train: 0.9, bus: 0.9, ferry: 0.9 };
const offsetExpr = mode => ["interpolate", ["linear"], ["zoom"], ...WIDTH[mode].flatMap(([z, w]) => [z, ["*", ["get", "o"], w + (z >= 12 ? GAP[mode] : GAP[mode] / 2)]])];

// ---------- load ----------
map.on("load", async () => {
  const [index] = await Promise.all([fetch("data/regions.json").then(r => r.json()), i18nReady]);
  REGIONS = index.regions;
  addBottomControls();
  renderRegions();
  addLayers();
  wireControls();
  wireSettings();
  wireMap();
  const fromHash = (INITIAL_HASH.match(/region=([^&]+)/) || [])[1];
  await loadRegion(REGIONS.some(r => r.id === fromHash) ? fromHash : index.default, { initial: true });
});

// Region names come from the data, except the two made up by the build itself.
const TRANSLATED_REGIONS = ["sweden", "trains"];
const regionName = r => TRANSLATED_REGIONS.includes(r.id) ? t(`region.${r.id}`) : r.name;
// The national view's area is "<n> regions", n being how many regions the build combined.
const regionArea = r => r.id === "sweden" && parseInt(r.area) ? t("region.count", { n: parseInt(r.area) }) : r.area;

/** The region menu: alphabetical (Swedish collation, so Å/Ä/Ö come last), with All of Sweden pinned first. */
function renderRegions() {
  const collator = new Intl.Collator("sv", { sensitivity: "base", numeric: true });
  const sorted = [...REGIONS].sort((a, b) => (b.id === "sweden") - (a.id === "sweden") || collator.compare(regionName(a), regionName(b)));
  const select = document.getElementById("region"), current = select.value;
  select.innerHTML = sorted.map(r => {
    const name = regionName(r), area = regionArea(r);
    return `<option value="${r.id}">${esc(name)}${area && area !== name ? ` · ${esc(area)}` : ""}</option>`;
  }).join("");
  if (current) select.value = current;
}

/** The map's own sources and layers, on top of the basemap (again after every basemap swap). */
function addLayers() {
  if (map.getSource("stops")) return;  // already added for this style (several theme changes during one load)
  const casing = dark ? "#0d0f12" : "#ffffff";
  for (const mode of ORDER) {
    map.addSource(mode, { type: "geojson", data: { type: "FeatureCollection", features: [] } });
    map.addLayer({ id: `${mode}-casing`, type: "line", source: mode, layout: { "line-join": "round", "line-cap": "round" },
      paint: { "line-color": mode === "tram" ? (dark ? "#000" : "#555") : casing, "line-width": widthExpr(mode, 1.6),
               "line-offset": offsetExpr(mode), "line-opacity": 0.9 } });
    map.addLayer({ id: `${mode}-line`, type: "line", source: mode, layout: { "line-join": "round", "line-cap": "round" },
      paint: { "line-color": ["get", "color"], "line-width": widthExpr(mode), "line-offset": offsetExpr(mode),
               "line-opacity": mode === "bus" ? 0.85 : 1 } });
  }

  map.addSource("stops", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
  const stopColor = ["case", ...["train", "metro", "tram", "ferry"].flatMap(m => [["in", m, ["get", "modes"]], STATION_RING[m]]),
    STATION_RING.bus];
  const stationPaint = {
    "circle-radius": ["interpolate", ["linear"], ["zoom"], 8, 2.5, 12, 3.5, 16, 6],
    "circle-color": dark ? "#1d2126" : "#ffffff", "circle-stroke-color": stopColor,
    "circle-stroke-width": ["interpolate", ["linear"], ["zoom"], 8, 1.2, 14, 2],
  };
  map.addLayer({ id: "stations-bus", type: "circle", source: "stops", minzoom: 12, paint: stationPaint });
  map.addLayer({ id: "stations", type: "circle", source: "stops", minzoom: 9, paint: stationPaint });
  map.addLayer({ id: "platforms", type: "circle", source: "stops", minzoom: 16,
    paint: { "circle-radius": 3, "circle-color": dark ? "#9aa3ad" : "#616a75", "circle-stroke-color": dark ? "#1d2126" : "#fff", "circle-stroke-width": 1 } });
  map.addLayer({ id: "focus", type: "circle", source: "stops", filter: ["==", ["get", "id"], ""],
    paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], 8, 7, 16, 12], "circle-color": "rgba(0,0,0,0)",
             "circle-stroke-color": dark ? "#fff" : "#111", "circle-stroke-width": 3 } });
  map.addLayer({ id: "station-labels", type: "symbol", source: "stops", minzoom: 12, filter: ["==", ["get", "kind"], "station"],
    layout: { "text-field": ["get", "name"], "text-font": ["Noto Sans Regular"], "text-size": 11, "text-offset": [0, 0.9],
              "text-anchor": "top", "text-optional": true, "symbol-sort-key": ["-", 0, ["coalesce", ["get", "rank"], 0]] },
    paint: { "text-color": dark ? "#e8eaed" : "#333", "text-halo-color": dark ? "#15181c" : "#fff", "text-halo-width": 1.4 } });
  map.addLayer({ id: "platform-labels", type: "symbol", source: "stops", minzoom: 17,
    filter: ["all", ["==", ["get", "kind"], "platform"], ["!=", ["get", "platform"], ""]],
    layout: { "text-field": ["get", "platform"], "text-font": ["Noto Sans Bold"], "text-size": 10, "text-offset": [0, -1] },
    paint: { "text-color": dark ? "#e8eaed" : "#333", "text-halo-color": dark ? "#15181c" : "#fff", "text-halo-width": 1.2 } });

  // The selected line, drawn above everything at its own place in each bundle.
  map.addSource("selected", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
  map.addLayer({ id: "selected-casing", type: "line", source: "selected", layout: { "line-join": "round", "line-cap": "round" },
    paint: { "line-color": dark ? "#fff" : "#111", "line-width": ["interpolate", ["linear"], ["zoom"], 8, 5, 14, 8, 17, 12] } },
    "stations");
  map.addLayer({ id: "selected-line", type: "line", source: "selected", layout: { "line-join": "round", "line-cap": "round" },
    paint: { "line-color": ["get", "color"], "line-width": ["interpolate", ["linear"], ["zoom"], 8, 3, 14, 5.5, 17, 9] } },
    "stations");
  map.getSource("stops").setData(stopsData);
  layersReady = true;
}

/** Switch the theme: the page follows data-theme, the map gets the matching basemap. */
function applyTheme() {
  if (theme === "auto") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = theme;
  if (isDark() === dark) return;
  dark = isDark();
  layersReady = false;
  hoverPopup?.remove();
  map.once("style.load", () => { addLayers(); if (REGION) update(); });
  loadBasemap();
}

/** Load one region's lines, corridors and stops, replacing whatever was shown. */
async function loadRegion(id, { initial = false } = {}) {
  const meta = REGIONS.find(r => r.id === id) || REGIONS[0];
  document.getElementById("region").value = meta.id;
  document.getElementById("feed").textContent = t("loading");
  // Modes that start switched off (buses in the all-of-Sweden view) are only downloaded when switched on.
  const off = meta.modes_off || ["ferry"];
  MODES.forEach(m => { m.on = !off.includes(m.id); });
  const modes = ORDER.filter(m => meta.modes[m] && !off.includes(m));
  const [index, stops, ...modeData] = await Promise.all([
    fetch(`data/${meta.id}/routes.json`).then(r => r.json()),
    fetch(`data/${meta.id}/stops.geojson`).then(r => r.json()),
    ...modes.map(m => fetch(`data/${meta.id}/${m}.json`).then(r => r.json())),
  ]);
  REGION = index;
  ROUTES = index.routes; DAYS = index.days; BIN = index.bin_minutes; BY_ID = {}; STATIONS = {}; CORRIDORS = {};
  ROUTES.forEach((r, i) => {
    r.n = i; BY_ID[r.id] = i;
    for (const a of Object.values(r.act)) { a.cs = cumulative(a.s); a.ce = cumulative(a.e); }
  });
  ORDER.forEach(m => { CORRIDORS[m] = []; });
  LOADED = new Set(modes);
  modes.forEach((m, i) => { CORRIDORS[m] = modeData[i].corridors; });
  // Stations list their lines by id, or (in the all-of-Sweden view) by index.
  for (const f of stops.features) if (f.properties.kind === "station")
    STATIONS[f.properties.id] = { id: f.properties.id, name: f.properties.name, coord: f.geometry.coordinates,
      routes: f.properties.routes.map(id => typeof id === "number" ? id : BY_ID[id]) };
  stopsData = stops;
  if (layersReady) map.getSource("stops").setData(stops);
  renderRegionInfo();
  document.getElementById("search").value = "";
  state.stops = []; state.route = null; state.match = "any";
  buildModes();

  let focused = false;
  if (initial) {
    const h = INITIAL_HASH;
    const r = (h.match(/route=([^&]+)/) || [])[1], s = (h.match(/stop=([^&]+)/) || [])[1];
    if (s) state.stops = s.split(",").filter(id => STATIONS[id]);
    if (/match=all/.test(h)) state.match = "all";
    if (r && BY_ID[r] !== undefined) state.route = BY_ID[r];
    focused = !!(state.route !== null || state.stops.length);
    if (!focused && !/map=/.test(h)) fitRegion();
  } else fitRegion();
  update({ fit: focused });
}

/** The region's summary line and the time filter's note on which days the timetable is from. */
function renderRegionInfo() {
  document.getElementById("feed").textContent = t("feed", { lines: t("lines", { n: ROUTES.length }),
    stops: t("stops", { n: Object.keys(STATIONS).length }), version: REGION.feed_version });
  document.getElementById("time-hint").textContent = `${t("time.hint")} ${DAYS.wd
    ? t("time.basedOn", { wd: fmtDate(DAYS.wd), sa: fmtDate(DAYS.sa), su: fmtDate(DAYS.su) }) : t("time.typicalWeek")}`;
}

function fitRegion() {
  const [w, s, e, n] = REGION.bbox;
  fitPadded(new maplibregl.LngLatBounds([w, s], [e, n]));
}

// ---------- core update ----------
/** Lines serving the selected stops: any of them, or every one of them. Null when no stop is selected. */
function linesAtStops() {
  if (!state.stops.length) return null;
  const sets = state.stops.map(id => new Set(STATIONS[id].routes));
  if (state.match === "all") return new Set([...sets[0]].filter(n => sets.every(s => s.has(n))));
  return new Set(sets.flatMap(s => [...s]));
}

/** Lines the build found missing from at least one regular (non-holiday) week of the timetable. */
const occasional = r => r.occasional === true;

/** Which lines are shown: enabled mode, running every week, in the time window, and serving the selected stops. */
function computeVisible() {
  visible = new Uint8Array(ROUTES.length);
  const atStop = linesAtStops();
  for (const r of ROUTES)
    visible[r.n] = modeOn(r.mode) && !(state.weekly && occasional(r)) && (!state.time.on || tripsInWindow(r) > 0) &&
      (!atStop || atStop.has(r.n)) ? 1 : 0;
  if (state.route !== null) visible[state.route] = 1;
}

/** Rebuild every mode's features: each corridor once per visible line, offset side by side. */
let selectedFeatures = [];
function drawLines() {
  selectedFeatures = [];
  for (const mode of ORDER) {
    const features = [];
    if (modeOn(mode) || (state.route !== null && ROUTES[state.route].mode === mode)) {
      for (const [coords, lines] of CORRIDORS[mode]) {
        const shown = lines.filter(n => visible[n]);
        shown.forEach((n, i) => {
          const o = i - (shown.length - 1) / 2;
          features.push({ type: "Feature", geometry: { type: "LineString", coordinates: coords },
                          properties: { r: n, color: ROUTES[n].color, o } });
          if (n === state.route) selectedFeatures.push({ type: "Feature", geometry: { type: "LineString", coordinates: coords },
                                                         properties: { color: ROUTES[n].color, o } });
        });
      }
    }
    map.getSource(mode).setData({ type: "FeatureCollection", features });
  }
  map.getSource("selected").setData({ type: "FeatureCollection", features: selectedFeatures });
  if (state.route !== null) {
    // The highlight keeps the line's own place in each bundle.
    const offset = offsetExpr(ROUTES[state.route].mode);
    map.setPaintProperty("selected-line", "line-offset", offset);
    map.setPaintProperty("selected-casing", "line-offset", offset);
  }
  const dim = state.route !== null;
  for (const m of ORDER) {
    map.setPaintProperty(`${m}-line`, "line-opacity", dim ? 0.2 : m === "bus" ? 0.85 : 1);
    map.setPaintProperty(`${m}-casing`, "line-opacity", dim ? 0.15 : 0.9);
  }
}

/** Show only stops served by the visible lines (or by the selected line). */
function drawStops() {
  const filtering = state.time.on || state.stops.length || MODES.some(m => !m.on) || (state.weekly && ROUTES.some(occasional));
  const IS_STATION = ["==", ["get", "kind"], "station"];
  const IS_MAJOR = ["any", ["in", "train", ["get", "modes"]], ["in", "metro", ["get", "modes"]], ["in", "tram", ["get", "modes"]], ["in", "ferry", ["get", "modes"]]];
  let ids = null;
  if (state.route !== null) ids = ROUTES[state.route].stations;
  else if (filtering) {
    const s = new Set();
    for (const r of ROUTES) if (visible[r.n]) r.stations.forEach(id => s.add(id));
    ids = [...s];
  }
  const inSet = key => !ids ? true : ids.length ? ["match", ["get", key], ids, true, false] : false;
  if (state.route !== null) {
    map.setFilter("stations", ["all", IS_STATION, inSet("id")]);
    map.setLayoutProperty("stations-bus", "visibility", "none");
    map.setLayerZoomRange("stations", 0, 24);
  } else {
    map.setFilter("stations", ["all", IS_STATION, IS_MAJOR, inSet("id")]);
    map.setFilter("stations-bus", ["all", IS_STATION, ["!", IS_MAJOR], inSet("id")]);
    map.setLayoutProperty("stations-bus", "visibility", "visible");
    map.setLayerZoomRange("stations", 9, 24);
  }
  map.setFilter("station-labels", ["all", IS_STATION, inSet("id")]);
  map.setFilter("platforms", ["all", ["==", ["get", "kind"], "platform"], inSet("station")]);
  map.setFilter("focus", state.stops.length ? ["match", ["get", "id"], state.stops, true, false] : false);
}

function update({ fit = false } = {}) {
  if (!layersReady) return;  // redrawn once the new basemap has loaded
  computeVisible();
  drawLines();
  drawStops();
  renderTime();
  renderView();
  const occ = ROUTES.filter(r => modeOn(r.mode) && occasional(r)).length;
  document.getElementById("weekly-n").textContent = occ ? t(state.weekly ? "weekly.hides" : "weekly.wouldHide", { n: occ }) : "";
  const parts = location.hash.slice(1).split("&").filter(p => p && !/^(route|stop|match|region)=/.test(p));
  if (state.stops.length > 1 && state.match === "all") parts.unshift("match=all");
  if (state.stops.length) parts.unshift("stop=" + state.stops.join(","));
  if (state.route !== null) parts.unshift("route=" + ROUTES[state.route].id);
  parts.unshift("region=" + REGION.id);
  history.replaceState(null, "", "#" + parts.join("&"));
  if (fit) fitView();
}

// Coalesce rapid slider moves into one redraw.
let pending;
function scheduleUpdate() {
  renderTime();
  clearTimeout(pending);
  pending = setTimeout(() => update(), 120);
}

function fitView() {
  if (state.route === null) {
    if (state.stops.length === 1) map.flyTo({ center: STATIONS[state.stops[0]].coord, zoom: Math.max(map.getZoom(), 14) });
    else if (state.stops.length) {
      const b = new maplibregl.LngLatBounds();
      state.stops.forEach(id => b.extend(STATIONS[id].coord));
      fitPadded(b);
    }
    return;
  }
  const b = new maplibregl.LngLatBounds();
  for (const [coords, lines] of CORRIDORS[ROUTES[state.route].mode])
    if (lines.includes(state.route)) coords.forEach(c => b.extend(c));
  if (!b.isEmpty()) fitPadded(b);
}

function fitPadded(b) {
  const wide = innerWidth > 640;
  map.fitBounds(b, { padding: wide ? { top: 60, bottom: 60, right: 80, left: 410 } : { top: 60, bottom: innerHeight * 0.55, left: 40, right: 40 }, maxZoom: 15 });
}

// ---------- sidebar ----------
// A mode's legend dot shows the colours its lines are drawn in: solid when they share one, else a
// pie of the most common ones (by number of lines), so the legend always matches the map.
function modeSwatch(mode) {
  const counts = {};
  for (const r of ROUTES) if (r.mode === mode) counts[r.color] = (counts[r.color] || 0) + 1;
  const top = Object.entries(counts).sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : 1)).slice(0, 4);
  if (top.length === 1) return top[0][0];
  const total = top.reduce((s, [, n]) => s + n, 0);
  let at = 0;
  return `conic-gradient(${top.map(([c, n]) => `${c} ${(at / total) * 360}deg ${((at += n) / total) * 360}deg`).join(", ")})`;
}
function buildModes() {
  const el = document.getElementById("modes");
  // Only the modes this region actually has.
  el.innerHTML = MODES.filter(m => ROUTES.some(r => r.mode === m.id)).map(m => {
    const n = ROUTES.filter(r => r.mode === m.id).length;
    return `<button class="mode" data-mode="${m.id}" aria-pressed="${m.on}"><span class="dot" style="background:${modeSwatch(m.id)}"></span>${modeLabel(m.id)} <span class="n">${n}</span></button>`;
  }).join("");
  el.onclick = e => {
    const b = e.target.closest(".mode"); if (!b) return;
    const m = MODES.find(x => x.id === b.dataset.mode);
    m.on = !m.on; b.setAttribute("aria-pressed", m.on);
    if (m.on && !LOADED.has(m.id)) {
      const region = REGION.id;
      b.classList.add("loading");
      fetch(`data/${region}/${m.id}.json`).then(r => r.json()).then(d => {
        if (REGION.id !== region) return;  // switched region while loading
        CORRIDORS[m.id] = d.corridors; LOADED.add(m.id);
        b.classList.remove("loading");
        update();
      });
    }
    update();
  };
}

function renderTime() {
  const time = state.time;
  document.getElementById("time").classList.toggle("off", !time.on);
  document.getElementById("time-on").checked = time.on;
  document.getElementById("time-label").textContent = `${fmt(time.from)} – ${fmt(time.to)}`;
  const span = DAY_BINS - FIRST_BIN, fill = document.getElementById("time-fill");
  fill.style.left = `${(time.from - FIRST_BIN) / span * 100}%`;
  fill.style.width = `${(time.to - time.from) / span * 100}%`;
  document.querySelectorAll("#days button").forEach(b => b.setAttribute("aria-pressed", b.dataset.day === time.day));
}

const rowHtml = r => {
  const n = state.time.on && tripsInWindow(r);
  const notes = [];
  // With several stops and "any", say how many of them the line serves.
  if (state.stops.length > 1 && state.match === "any") notes.push(t("stopsServed", { k: state.stops.filter(id => STATIONS[id].routes.includes(r.n)).length, n: state.stops.length }));
  if (state.time.on) notes.push(t("trips", { n }));
  const count = notes.length ? `<span class="count">${notes.join(" · ")}</span>` : "";
  return `<div class="row" data-route="${r.n}">${badge(r)}<span class="name" title="${esc(r.name)}">${esc(r.name)}</span>${count}</div>`;
};

function groupedRows(routes) {
  let html = "", group = null;
  for (const r of routes) {
    if (r.mode !== group) { group = r.mode; html += `<div class="group">${modeLabel(group)}</div>`; }
    html += rowHtml(r);
  }
  return html;
}

function renderView() {
  const view = document.getElementById("view");
  const time = state.time;
  if (state.route !== null) {
    const r = ROUTES[state.route], a = r.act[time.day];
    // Trips running per hour on the selected day, with the chosen window highlighted.
    let hist = "";
    if (a) {
      const hour = 60 / BIN, startOf = h => FIRST_BIN + h * hour;
      const perHour = Array.from({ length: 24 }, (_, h) => tripsInWindow(r, time.day, startOf(h), startOf(h + 1)));
      const peak = Math.max(...perHour, 1);
      hist = `<div class="hist">${perHour.map((v, h) =>
        `<div class="${time.on && startOf(h) < time.to && startOf(h + 1) > time.from ? "in" : ""}" style="height:${v / peak * 100}%" title="${t("hist.bar", { from: fmt(startOf(h)), to: fmt(startOf(h + 1)), trips: t("trips", { n: v }) })}"></div>`).join("")}</div>
        <div class="ticks"><span>04</span><span>10</span><span>16</span><span>22</span><span>04</span></div>`;
    }
    const hours = a ? t("time.window", { day: t(`day.${time.day}`), from: fmt(Math.max(serviceSpan(a)[0], 0)), to: fmt(Math.min(serviceSpan(a)[1] + 1, DAY_BINS)) })
      : t(`noService.${time.day}`);
    const backTo = state.stops.length === 1 ? esc(STATIONS[state.stops[0]].name) : state.stops.length ? t("back.selected", { n: state.stops.length }) : t("back.all");
    view.innerHTML = `<div class="head"><button class="back" data-back>← ${backTo}</button>
      <div class="title">${badge(r)}<span>${esc(r.name)}</span></div>
      <div class="sub">${modeLabel(r.mode)} · ${esc(r.agency)}${r.region ? ` (${esc(regionName(REGIONS.find(x => x.id === r.region) || { name: r.region }))})` : ""} · ${t("stops", { n: r.stations.length })} · ${hours}</div>${hist}</div>
      <ol class="stations" style="--line:${r.color === "#FFFFFF" ? "#333" : r.color}">${
        r.stations.filter(s => STATIONS[s]).map(s => `<li data-station="${s}">${esc(STATIONS[s].name)}</li>`).join("")}</ol>`;
    return;
  }
  if (state.stops.length) {
    const multi = state.stops.length > 1;
    const atStops = [...linesAtStops()];
    const shown = atStops.filter(n => visible[n]).sort((a, b) => a - b).map(n => ROUTES[n]);
    const hidden = atStops.length - shown.length;
    const when = time.on ? ` · ${t("time.window", { day: t(`day.${time.day}`).toLocaleLowerCase(t("locale")), from: fmt(time.from), to: fmt(time.to) })}` : "";
    const what = !multi ? "atStops.here" : state.match === "all" ? "atStops.all" : "atStops.any";
    const title = multi ? t("stopsSelected", { n: state.stops.length }) : esc(STATIONS[state.stops[0]].name);
    const chips = multi ? `<div class="chips">${state.stops.map(id =>
      `<span class="chip">${esc(STATIONS[id].name)}<button data-remove="${id}" aria-label="${esc(t("remove", { name: STATIONS[id].name }))}">×</button></span>`).join("")}</div>
      <div class="days match" id="match"><button data-match="any" aria-pressed="${state.match === "any"}">${t("match.any")}</button><button data-match="all" aria-pressed="${state.match === "all"}">${t("match.all")}</button></div>` : "";
    const empty = t(multi && state.match === "all" && !atStops.length ? "empty.all" : "empty.filters");
    view.innerHTML = `<div class="head"><button class="back" data-back>← ${t("back.all")}</button>
      <div class="title"><span>${title}</span></div>${chips}
      <div class="sub" style="margin-top:6px">${t(what, { n: shown.length })}${when}${hidden ? ` · ${t("hiddenByFilters", { n: hidden })}` : ""}</div>
      <div class="hint2">${t(multi ? "multi.edit" : "multi.add", { key: MULTI_KEY })}</div></div>
      ${groupedRows(shown) || `<div class="empty">${empty}</div>`}`;
    return;
  }
  const q = document.getElementById("search").value.trim().toLowerCase();
  const routes = ROUTES.filter(r => visible[r.n] && (!q || r.label.toLowerCase().startsWith(q) || r.name.toLowerCase().includes(q)));
  let html = "";
  if (q.length >= 3) {
    const st = Object.values(STATIONS).filter(s => s.name.toLowerCase().includes(q)).slice(0, 8);
    if (st.length) html += `<div class="group">${t("group.stops")}</div>` + st.map(s =>
      `<div class="row" data-station="${s.id}"><span class="name">${esc(s.name)}</span><span class="count">${t("lines", { n: s.routes.length })}</span></div>`).join("");
  }
  html += groupedRows(routes);
  view.innerHTML = html || `<div class="empty">${t("empty.filters")}</div>`;
}

// ---------- interactions ----------
/** Plain click selects just this stop; with Ctrl/⌘ held it is added to (or removed from) the selection. */
function focusStop(id, { fly = true, add = false } = {}) {
  if (add) state.stops = state.stops.includes(id) ? state.stops.filter(s => s !== id) : [...state.stops, id];
  else state.stops = [id];
  state.route = null;
  document.getElementById("panel").classList.remove("min");
  update();
  if (fly && !add) map.flyTo({ center: STATIONS[id].coord, zoom: Math.max(map.getZoom(), 14) });
}
const multiKey = e => e.ctrlKey || e.metaKey;

async function selectRoute(n, fit = true) {
  state.route = n;
  const mode = ROUTES[n].mode;
  if (!LOADED.has(mode)) {  // a line of a mode not downloaded yet (e.g. a bus in the all-of-Sweden view)
    const region = REGION.id;
    const d = await fetch(`data/${region}/${mode}.json`).then(r => r.json());
    if (REGION.id !== region) return;
    CORRIDORS[mode] = d.corridors; LOADED.add(mode);
  }
  update({ fit });
}

function back() {
  if (state.route !== null) state.route = null;
  else state.stops = [];
  update();
}

function wireControls() {
  const from = document.getElementById("time-from"), to = document.getElementById("time-to");
  const onSlide = e => {
    let a = +from.value, b = +to.value;
    if (a >= b) { if (e.target === from) a = b - 1; else b = a + 1; }
    from.value = a; to.value = b;
    Object.assign(state.time, { from: a, to: b, on: true });
    scheduleUpdate();
  };
  from.addEventListener("input", onSlide);
  to.addEventListener("input", onSlide);
  // The inputs are stacked; put whichever thumb is nearer the pointer on top so they can always be separated.
  document.querySelector(".dual").addEventListener("pointerdown", e => {
    const rect = e.currentTarget.getBoundingClientRect();
    const v = FIRST_BIN + (e.clientX - rect.left) / rect.width * (DAY_BINS - FIRST_BIN);
    const fromNearer = Math.abs(v - from.value) < Math.abs(v - to.value) || (+from.value === +to.value - 1 && v < +from.value);
    from.style.zIndex = fromNearer ? 2 : 1;
    to.style.zIndex = fromNearer ? 1 : 2;
  }, { capture: true });
  document.getElementById("time-on").addEventListener("change", e => { state.time.on = e.target.checked; update(); });
  document.getElementById("weekly").addEventListener("change", e => { state.weekly = e.target.checked; update(); });
  document.getElementById("days").addEventListener("click", e => {
    const b = e.target.closest("button"); if (!b) return;
    state.time.day = b.dataset.day; state.time.on = true; update();
  });
  document.getElementById("search").addEventListener("input", () => renderView());
  document.getElementById("region").addEventListener("change", e => loadRegion(e.target.value));
  document.getElementById("toggle").onclick = () => { document.getElementById("panel").classList.toggle("min"); renderToggle(); };
  document.getElementById("view").addEventListener("click", e => {
    if (e.target.closest("[data-back]")) return back();
    const rm = e.target.closest("[data-remove]");
    if (rm) { state.stops = state.stops.filter(id => id !== rm.dataset.remove); return update(); }
    const m = e.target.closest("[data-match]");
    if (m) { state.match = m.dataset.match; return update(); }
    const st = e.target.closest("[data-station]");
    if (st) return focusStop(st.dataset.station, { add: multiKey(e) });
    const r = e.target.closest("[data-route]");
    if (r) selectRoute(+r.dataset.route);
  });
  renderTime();
  renderToggle();
}

const renderToggle = () => {
  document.getElementById("toggle").textContent = t(document.getElementById("panel").classList.contains("min") ? "panel.show" : "panel.hide");
};

/** The settings menu: language and theme, each "auto" or a fixed choice that is remembered. */
function wireSettings() {
  const settings = document.getElementById("settings"), lang = document.getElementById("lang"), themes = document.getElementById("theme");
  const renderSettings = () => {
    lang.querySelector("[data-i18n-auto]").textContent = t("settings.languageAuto", { name: LANGUAGES[i18n.detected] });
    lang.value = i18n.choice;
    themes.value = theme;
  };
  lang.addEventListener("change", () => setLanguage(lang.value));
  themes.addEventListener("change", () => { theme = themes.value; prefs.set("theme", theme); applyTheme(); });
  darkScheme.addEventListener("change", applyTheme);
  // Close the menu on a click elsewhere or Escape.
  document.addEventListener("click", e => { if (!settings.contains(e.target)) settings.open = false; });
  settings.addEventListener("keydown", e => { if (e.key === "Escape") { settings.open = false; settings.querySelector("summary").focus(); } });
  document.addEventListener("uilanguage", () => {
    renderSettings();
    renderToggle();
    renderRegions();
    addBottomControls();
    if (!REGION) return;
    renderRegionInfo();
    buildModes();
    update();
  });
  renderSettings();
}

function wireMap() {
  const lineLayers = () => [...ORDER].reverse().filter(modeOn).map(m => `${m}-line`);
  const stopLayers = ["stations", "stations-bus", "platforms"];
  const near = e => [[e.point.x - 4, e.point.y - 4], [e.point.x + 4, e.point.y + 4]];
  hoverPopup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 10, className: "tip" });

  map.on("mousemove", e => {
    if (!layersReady) return;
    const st = map.queryRenderedFeatures(near(e), { layers: stopLayers });
    if (st.length) {
      map.getCanvas().style.cursor = "pointer";
      const p = st[0].properties;
      const name = p.kind === "platform" && p.platform ? t("tip.platform", { name: p.name, p: p.platform }) : p.name;
      hoverPopup.setLngLat(e.lngLat).setHTML(`<div class="tip"><b>${esc(name)}</b><br>${t("tip.stopClick")}<br>${t("tip.stopAdd", { key: MULTI_KEY })}</div>`).addTo(map);
      return;
    }
    const feats = map.queryRenderedFeatures(near(e), { layers: lineLayers() });
    const rs = [...new Set(feats.map(f => f.properties.r))].sort((a, b) => a - b).map(n => ROUTES[n]);
    if (!rs.length) { map.getCanvas().style.cursor = ""; hoverPopup.remove(); return; }
    map.getCanvas().style.cursor = "pointer";
    const shown = rs.slice(0, 12);
    hoverPopup.setLngLat(e.lngLat).setHTML(`<div class="tip">${shown.map(r => `<div class="tip-line">${modeIcon(r.mode)}${badge(r)} ${esc(r.name)}</div>`).join("")}${rs.length > 12 ? `<div class="tip-line">${t("tip.more", { n: rs.length - 12 })}</div>` : ""}</div>`).addTo(map);
  });
  map.on("mouseout", () => hoverPopup.remove());

  map.on("click", e => {
    if (!layersReady) return;
    const st = map.queryRenderedFeatures(near(e), { layers: stopLayers });
    if (st.length) {
      const p = st[0].properties;
      return focusStop(p.kind === "platform" ? p.station : p.id, { fly: false, add: multiKey(e.originalEvent) });
    }
    const feats = map.queryRenderedFeatures(near(e), { layers: lineLayers() });
    if (feats.length) return selectRoute(feats[0].properties.r, false);
    if (state.route !== null || state.stops.length) back();
  });
}
