"""Build the map data for one region from one or more GTFS feeds.

Usage (normally run through update.py, which also downloads the feeds):
    python3 build.py <region-id> <unzipped-gtfs-dir> [<unzipped-gtfs-dir> ...]

Writes site/data/<region>/{tram,metro,train,bus,ferry}.json (shared corridors),
stops.geojson and routes.json (lines, their stops and when they run). Close stations with
matching names are merged into one (see stations.py), and paths bend onto them. Region settings come
from regions.json, line colours from colors/<region>.json, and rail geometry for feeds without
usable train shapes from cache/osm-rail.json when present.
"""

import csv
import heapq
import json
import math
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

import netex
import osm
from colours import MODE_COLOURS, mode_shades
from geo import (
    converge,
    length_and_gap,
    metres,
    percentile_bbox,
    point_to_polyline_m,
    project_stops,
    simplify,
    smooth_bridge,
)
from stations import cluster_stations, merged_positions

ROOT = Path(__file__).parent
MODE_ORDER = ["tram", "metro", "train", "bus", "ferry"]
ID_COLUMNS = ("route_id", "agency_id", "trip_id", "service_id", "shape_id", "stop_id", "parent_station")
SHAPE_TOLERANCE_M = 0.5  # applied while loading shapes, to keep memory down on very dense feeds
SIMPLIFY_TOLERANCE_M = 1.0  # applied to the geometry written out
SUSPECT_GAP_M = 1500  # shape steps longer than this may be straight-line placeholders
MAX_GAP_M = 8000  # steps longer than this are certainly placeholders
CORRIDOR_MATCH_M = 40  # stop-to-stop paths closer than this are drawn as one shared corridor
BRIDGE_MAX_M = 150  # gaps between consecutive stretches of a line up to this length are bridged
BRIDGE_STOP_M = 250  # ...when both loose ends are this close to the stop they meet at
RAIL_BRIDGE_MAX_M = 3000  # trains may bridge longer gaps, along the rail network
BIN_MIN = 15  # time-of-day resolution of the activity histograms
DAY_BINS = 28 * 60 // BIN_MIN  # GTFS times run past midnight; cover service until 04:00 next day
WEEKS_CHECKED = 8  # weeks of the timetable checked for lines that don't run every week
HOLIDAY_WEEK_SHARE = 0.9  # a week is a holiday week when fewer than this share of the reference lines run
DEBUG_GAPS = bool(os.environ.get("DEBUG_GAPS"))  # log every gap left open


def mode_of(route_type):
    """Map basic and extended GTFS route types to a map mode; None for modes the map leaves out."""
    t = int(route_type)
    if t == 0 or 900 <= t <= 906:
        return "tram"
    if t == 1 or 400 <= t <= 405:
        return "metro"
    if t == 2 or 100 <= t <= 117:
        return "train"
    if t in (3, 11) or 200 <= t <= 209 or 700 <= t <= 714 or t == 800:
        return "bus"
    if t == 4 or 1000 <= t <= 1021 or t == 1200:
        return "ferry"
    return None  # dial-a-ride (715, 1501), cable cars, funiculars, ...


def minutes(hms):
    h, m, _ = hms.split(":")
    return int(h) * 60 + int(m)


def natural(label):
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", label)]


def write_lines(path, head, key, items):
    """JSON with one array item per line, so weekly data commits diff as small changes in git."""
    with open(path, "w", encoding="utf-8") as f:
        if head:
            f.write(json.dumps(head, ensure_ascii=False, separators=(",", ":"))[:-1] + f',"{key}":[')
        else:
            f.write(f'{{"{key}":[')
        for i, item in enumerate(items):
            f.write(("\n" if i == 0 else ",\n") + json.dumps(item, ensure_ascii=False, separators=(",", ":")))
        f.write("\n]}\n")


class Feeds:
    """Reads the same GTFS table across several feeds; ids get a per-feed prefix when combining feeds."""

    def __init__(self, dirs):
        self.dirs = [Path(d) for d in dirs]

    def has(self, name):
        return any((d / name).exists() for d in self.dirs)

    def rows(self, name):
        for i, d in enumerate(self.dirs):
            if not (d / name).exists():
                continue
            prefix = f"{i}:" if len(self.dirs) > 1 else ""
            with open(d / name, newline="", encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    if prefix:
                        for col in ID_COLUMNS:
                            if row.get(col):
                                row[col] = prefix + row[col]
                    yield row


def load_shapes(feeds, needed):
    """Shape points as (lon, lat, feed distance or None, geometric distance in metres), lightly simplified."""
    raw, done = defaultdict(list), {}

    def finish(sid):
        pts = sorted(raw.pop(sid))
        has_dist = all(p[3] is not None for p in pts)
        out, gd = [], 0.0
        for i, (_, lon, lat, d) in enumerate(pts):
            if i:
                gd += metres(out[-1][:2], (lon, lat))
            out.append((lon, lat, d if has_dist else None, gd))
        done[sid] = simplify(out, SHAPE_TOLERANCE_M, xy=lambda p: p[:2], max_step_m=1000)

    current = None
    for p in feeds.rows("shapes.txt"):
        sid = p["shape_id"]
        if sid not in needed:
            continue
        if sid != current and current in raw:
            finish(current)
        if sid in done:  # shape rows not grouped: re-open it
            raw[sid] = [(i, q[0], q[1], q[2]) for i, q in enumerate(done.pop(sid))]
        current = sid
        d = p.get("shape_dist_traveled")
        raw[sid].append((int(p["shape_pt_sequence"]), float(p["shape_pt_lon"]), float(p["shape_pt_lat"]),
                         float(d) if d else None))
    for sid in list(raw):
        finish(sid)
    return done


def parse_date(yyyymmdd):
    return date(int(yyyymmdd[:4]), int(yyyymmdd[4:6]), int(yyyymmdd[6:]))


def read_service_dates(feeds):
    """service_id -> set of YYYYMMDD dates, from calendar.txt (weekly patterns) and calendar_dates.txt (exceptions)."""
    service_dates = defaultdict(set)
    weekdays = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
    for c in feeds.rows("calendar.txt"):
        if not any(c.get(d) == "1" for d in weekdays):
            continue
        day, end = parse_date(c["start_date"]), parse_date(c["end_date"])
        while day <= end:
            if c[weekdays[day.weekday()]] == "1":
                service_dates[c["service_id"]].add(day.strftime("%Y%m%d"))
            day += timedelta(1)
    for c in feeds.rows("calendar_dates.txt"):
        if c["exception_type"] == "1":
            service_dates[c["service_id"]].add(c["date"])
        else:
            service_dates[c["service_id"]].discard(c["date"])
    return service_dates


def representative_days(service_dates, trip_service):
    """The feed's first day with service, and its busiest Tue-Thu, Saturday and Sunday in the first
    four weeks (which skips holiday weeks with reduced service): (first day or None, {"wd": YYYYMMDD, ...})."""
    service_trips = Counter(trip_service.values())
    date_trips = Counter()
    for sid, dates in service_dates.items():
        for d in dates:
            date_trips[d] += service_trips[sid]
    first = min((d for d, n in date_trips.items() if n), default=None)
    if not first:
        return None, {}
    start, days = parse_date(first), {}
    for day_type, wdays in (("wd", range(1, 4)), ("sa", [5]), ("su", [6])):
        cands = [start + timedelta(i) for i in range(28) if (start + timedelta(i)).weekday() in wdays]
        best = max(cands, key=lambda d: (date_trips[d.strftime("%Y%m%d")], -d.toordinal()))
        days[day_type] = best.strftime("%Y%m%d")
    return start, days


def weeks_with_service(service_dates, trip_route, week_starts):
    """route -> indices of the weeks (Monday-Sunday, starting at week_starts) it runs in at least
    once. trip_route: trip -> (route, service_id)."""
    week_of = {}
    for dates in service_dates.values():
        for d in dates:
            if d not in week_of:
                i = (parse_date(d) - week_starts[0]).days // 7 if week_starts else -1
                week_of[d] = i if 0 <= i < len(week_starts) else None
    service_weeks = {sid: {week_of[d] for d in dates} - {None} for sid, dates in service_dates.items()}
    route_weeks = defaultdict(set)
    for rid, sid in trip_route.values():
        route_weeks[rid] |= service_weeks.get(sid, set())
    return route_weeks


def regular_weeks(route_weeks):
    """Weeks that are not holiday weeks (school breaks, Christmas) or past the end of the feed. The
    reference lines are those running in most of the weeks that have any service; a week is regular
    when at least HOLIDAY_WEEK_SHARE of them run in it."""
    served = {w for ws in route_weeks.values() for w in ws}
    reference = [ws for ws in route_weeks.values() if len(ws) * 2 > len(served)]
    return {w for w in served if sum(w in ws for ws in reference) >= HOLIDAY_WEEK_SHARE * len(reference)}


def is_occasional(weeks, regular):
    """A line is occasional when it skips a regular week between its first and last week of service,
    or runs in fewer than two weeks (or only in holiday weeks). A line that runs every week until it
    stops is not: some feeds only publish a couple of weeks ahead for some lines."""
    weeks_regular = weeks & regular  # gaps only count in regular weeks; the minimum counts any week
    return len(weeks) < 2 or not weeks_regular or any(
        w not in weeks_regular for w in regular if min(weeks_regular) < w < max(weeks_regular))


def mark_occasional(route_list, route_weeks, regular):
    """Flag occasional lines. A timetable change often gives a line a new route_id part-way through
    the weeks checked, so routes with the same mode and line number whose stops mostly overlap are
    judged together."""
    by_label = defaultdict(list)
    for r in route_list:
        by_label[(r["mode"], r["label"])].append(r)
    for group in by_label.values():
        for r in group:
            weeks, stations = set(route_weeks[r["id"]]), set(r["stations"])
            for other in group:
                o = set(other["stations"])
                if other is not r and len(stations & o) * 2 >= min(len(stations), len(o)):
                    weeks |= route_weeks[other["id"]]
            if is_occasional(weeks, regular):
                r["occasional"] = True


def known_colour(info, route_id, colours, netex_colours):
    """A line's colour from the feed's own route_color, then OSM (by line number), then NeTEx (by
    route id); None when none of them has one."""
    if re.fullmatch(r"[0-9A-F]{6}", info["color"]):
        return f"#{info['color']}"
    mode, short = info["mode"], info["short"]
    # Rail modes are classified differently by feeds and OSM (Roslagsbanan is a "tram" in SL's
    # feed but a train in OSM), so they may borrow a colour from another rail mode.
    rail = ("train", "tram", "metro")
    lookup = [mode] + ([m for m in rail if m != mode] if mode in rail else [])
    # Variants like SL's 27S or 43X take the colour of their base line when they have none of their own.
    refs = [short] + ([re.sub(r"[A-Z]+$", "", short)] if re.fullmatch(r"\d+[A-Z]+", short) else [])
    return next((colours[m][ref] for ref in refs for m in lookup if ref in colours.get(m, {})), None) or \
        netex_colours.get(route_id.split(":")[-1])


def shade_brand_colours(route_list, brand, brand_users):
    """One brand colour shared by several modes becomes one shade per mode, so trams, trains and
    buses stay distinguishable: the mode with the most lines keeps the brand colour itself.
    brand_users: brand key -> mode -> indices into route_list."""
    for key, by_mode in brand_users.items():
        modes = sorted(by_mode, key=lambda m: (-len(by_mode[m]), MODE_ORDER.index(m)))
        for m, shade in zip(modes, mode_shades(brand[key], len(modes))):
            for i in by_mode[m]:
                route_list[i]["color"] = shade


def same_path(c1, c2):
    """Whether two stop-to-stop paths run along each other closely enough to share one corridor."""
    l1, l2 = length_and_gap(c1)[0], length_and_gap(c2)[0]
    if not (0.75 * l2 <= l1 <= 1.33 * l2 + 50):
        return False
    # The ends must line up too (in either direction); otherwise a line drawn along the shared
    # geometry would jump at its stops, e.g. where the other direction stops on another road.
    ends = min(max(metres(c1[0], c2[0]), metres(c1[-1], c2[-1])), max(metres(c1[0], c2[-1]), metres(c1[-1], c2[0])))
    if ends > 1.5 * CORRIDOR_MATCH_M:
        return False
    step = max(1, len(c1) // 8)
    return all(point_to_polyline_m(p, c2) < CORRIDOR_MATCH_M for p in c1[::step] + [c1[-1]])


def compact(hist):
    """[first non-empty bin, counts...] trimmed to the active span, or None."""
    nz = [i for i, v in enumerate(hist) if v]
    return [nz[0]] + hist[nz[0]:nz[-1] + 1] if nz else None


def build_region(cfg, gtfs_dirs, out_dir, colours=None, router=None, log=print):
    """Build one region's map data into out_dir from its GTFS feed directories; returns its metadata.
    colours: the region's colors/<id>.json; router: an osm.RailRouter for trains without shapes."""
    feeds = Feeds(gtfs_dirs)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    colours = colours or {}
    include = re.compile(cfg["include_agencies"]) if cfg.get("include_agencies") else None
    exclude = re.compile(cfg["exclude_agencies"]) if cfg.get("exclude_agencies") else None
    allowed_modes = set(cfg.get("modes", MODE_ORDER))
    # Routes known to be invalid (e.g. depot runs), by GTFS route_id; multi-feed prefixes are ignored.
    excluded_routes = set(cfg.get("exclude_routes", []))

    agencies = {a["agency_id"]: a["agency_name"] for a in feeds.rows("agency.txt")}
    routes = {}
    for r in feeds.rows("routes.txt"):
        agency = agencies.get(r.get("agency_id", ""), next(iter(agencies.values()), ""))
        mode = mode_of(r["route_type"])
        if mode not in allowed_modes or (include and not include.search(agency)) or \
                (exclude and exclude.search(agency)):
            continue
        if r["route_id"].split(":")[-1] in excluded_routes:
            continue
        routes[r["route_id"]] = {"short": r.get("route_short_name") or "", "long": r.get("route_long_name") or "",
                                 "mode": mode, "agency": agency, "color": (r.get("route_color") or "").upper()}

    trip_route, trip_shape, trip_service = {}, {}, {}
    route_shape_trips = defaultdict(Counter)
    for t in feeds.rows("trips.txt"):
        rid = t["route_id"]
        if rid in routes:
            # Trips without a shape are grouped per route, so each still contributes its stop pattern.
            shape = t.get("shape_id") or f"noshape:{rid}:{t['trip_id']}"
            trip_route[t["trip_id"]] = rid
            trip_shape[t["trip_id"]] = shape
            trip_service[t["trip_id"]] = t["service_id"]
            route_shape_trips[rid][shape] += 1

    stops = {s["stop_id"]: s for s in feeds.rows("stops.txt")}

    def stop_xy(stop_id):
        return (float(stops[stop_id]["stop_lon"]), float(stops[stop_id]["stop_lat"]))

    # Ordered stop lists per trip (only one representative trip per shape is kept).
    shape_rep_trip = {}
    for tid, sid in trip_shape.items():
        shape_rep_trip.setdefault(sid, tid)
    rep_trips = set(shape_rep_trip.values())
    trip_stops = defaultdict(list)
    stop_routes = defaultdict(set)
    trip_span = {}  # trip -> (first departure, last arrival) in minutes after midnight
    for st in feeds.rows("stop_times.txt"):
        rid = trip_route.get(st["trip_id"])
        if rid is None or not (st["departure_time"] or st["arrival_time"]):
            continue
        stop_routes[st["stop_id"]].add(rid)
        t0 = minutes(st["departure_time"] or st["arrival_time"])
        t1 = minutes(st["arrival_time"] or st["departure_time"])
        span = trip_span.get(st["trip_id"])
        trip_span[st["trip_id"]] = (min(span[0], t0), max(span[1], t1)) if span else (t0, t1)
        if st["trip_id"] in rep_trips:
            dist = st.get("shape_dist_traveled")
            trip_stops[st["trip_id"]].append((int(st["stop_sequence"]), st["stop_id"], float(dist) if dist else None))
    for v in trip_stops.values():
        v.sort()

    # Trip-less noshape groups would otherwise create one "variant" per trip; keep one per stop pattern.
    for shapes in route_shape_trips.values():
        patterns = {}
        for sid in list(shapes):
            if not sid.startswith("noshape:"):
                continue
            pattern = tuple(s[1] for s in trip_stops[shape_rep_trip[sid]])
            if pattern in patterns:
                shapes[patterns[pattern]] += shapes.pop(sid)
            else:
                patterns[pattern] = sid

    shape_pts = load_shapes(feeds, set(trip_shape.values())) if feeds.has("shapes.txt") else {}

    # Stations that are really one place (same name once suffixes like "station" are dropped, and
    # close together, e.g. a bus terminal and the railway station beside it, or the same station
    # from two feeds) become one, drawn at the average of their positions.
    used = {}
    for stop_id, rids in stop_routes.items():
        parent = stops[stop_id].get("parent_station") or stop_id
        if parent in stops:
            used.setdefault(parent, set()).update(rids)
    station_alias = cluster_stations({s: (stops[s]["stop_name"], stop_xy(s), len(r)) for s, r in used.items()})
    merged_pos = merged_positions(station_alias, stop_xy)
    if merged_pos:
        log(f"  merged {len(station_alias)} stations into {len(merged_pos)}")

    def station_of(stop_id):
        parent = stops[stop_id].get("parent_station") or stop_id
        return station_alias.get(parent, parent)

    def station_name(stop_id):
        return stops[station_of(stop_id)]["stop_name"]

    def route_stations(rid):
        return {station_of(s[1]) for sid in route_shape_trips[rid] for s in trip_stops[shape_rep_trip[sid]]}

    # Each line is named after its own most frequent variant, before any merging below.
    for rid, info in routes.items():
        if rid in route_shape_trips:
            info["main_shape"] = route_shape_trips[rid].most_common(1)[0][0]

    # Västtrafik lists trains twice: unnumbered "TÅG" routes for the current timetable and
    # numbered lines from the next timetable change. Fold each TÅG route into the numbered line
    # that covers its stations, preferring the closest in size when several cover it.
    merged_into = {}

    def merge(rid, into):
        """Fold route rid (its trips, variants and stops) into route `into`."""
        merged_into[rid] = into
        route_shape_trips[into].update(route_shape_trips.pop(rid))
        for rids in stop_routes.values():
            if rid in rids:
                rids.discard(rid)
                rids.add(into)
        del routes[rid]

    numbered = {r: route_stations(r) for r, i in routes.items()
                if i["mode"] == "train" and i["short"] != "TÅG" and r in route_shape_trips}
    for rid, info in list(routes.items()):
        if info["mode"] != "train" or info["short"] != "TÅG" or rid not in route_shape_trips or not numbered:
            continue
        mine = route_stations(rid)
        overlap, _, best = max((len(mine & st) / min(len(mine), len(st)), len(mine & st) / len(mine | st), n)
                               for n, st in numbered.items())
        if overlap < 0.7:
            continue
        log(f"  timetable route {station_name(min(mine))}… ({len(mine)} stops) -> line {routes[best]['short']}")
        merge(rid, best)

    # Operators like SJ publish one "route" per train-number series. With group_by_termini, routes
    # of the same operator and mode between the same two end stations become one line, labelled
    # with the operator's short name.
    if cfg.get("group_by_termini"):
        groups, labels = {}, cfg.get("agency_labels", {})
        for rid in sorted((r for r in routes if r in route_shape_trips),
                          key=lambda r: (-sum(route_shape_trips[r].values()), r)):
            info = routes[rid]
            seq = trip_stops[shape_rep_trip[info["main_shape"]]]
            key = (info["agency"], info["mode"], tuple(sorted({station_name(seq[0][1]), station_name(seq[-1][1])})))
            if key in groups:
                merge(rid, groups[key])
            else:
                groups[key] = rid
                info["short"] = labels.get(info["agency"], info["agency"])
        log(f"  grouped operator routes into {len(groups)} lines by end stations")

    def resolve(rid):
        while rid in merged_into:
            rid = merged_into[rid]
        return rid

    def slice_shape(pts, d0, d1, k):
        """Points of a shape between two distances (field k: 2 = feed, 3 = geometric), with interpolated ends."""
        out = []
        for p, q in zip(pts, pts[1:]):
            if q[k] < d0:
                continue
            if not out:
                out.append(interp(p, q, d0, k))
            if q[k] < d1:
                out.append((q[0], q[1]))
            else:
                out.append(interp(p, q, d1, k))
                break
        return out

    def interp(a, b, d, k):
        span = b[k] - a[k]
        t = 0.0 if span <= 0 else max(0.0, min(1.0, (d - a[k]) / span))
        return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)

    def rounded(coords):
        return [[round(x, 5), round(y, 5)] for x, y in simplify(coords, SIMPLIFY_TOLERANCE_M)]

    # Pass 1: one candidate geometry per stop-to-stop segment, so the many near-identical shape
    # variants of a route (short turns, skipped stops) collapse into one network. Where variants
    # disagree, keep the most detailed one (smallest longest-step).
    route_segs = {}
    pool = defaultdict(dict)  # mode -> (station_a, station_b) -> (gap, coords from a to b)
    for rid, info in routes.items():
        shapes = route_shape_trips.get(rid)
        if not shapes:
            continue
        segs = {}
        for sid, _ in shapes.most_common():
            pts = shape_pts.get(sid, [])
            seq = trip_stops[shape_rep_trip[sid]]
            if len(seq) < 2:
                continue
            # Stop positions along the shape: the feed's own distances when both shape and stop
            # times carry them, otherwise each stop projected onto the shape.
            if len(pts) >= 2 and all(d is not None for _, _, d in seq) and pts[0][2] is not None:
                dists, k = [d for _, _, d in seq], 2
            elif len(pts) >= 2:
                dists, k = project_stops(pts, [stop_xy(s) for _, s, _ in seq]), 3
            else:
                dists, k = None, None
            for i, ((_, a, _), (_, b, _)) in enumerate(zip(seq, seq[1:])):
                key = (a, b) if a < b else (b, a)
                coords = slice_shape(pts, dists[i], dists[i + 1], k) if dists and dists[i + 1] > dists[i] else []
                if len(coords) < 2:
                    coords = [stop_xy(a), stop_xy(b)]
                gap = length_and_gap(coords)[1]
                if key not in segs or gap < segs[key][0]:
                    segs[key] = (gap, coords, station_of(a), station_of(b))
        mode_pool = pool[info["mode"]]
        for gap, coords, sa, sb in segs.values():
            if sa == sb:
                continue
            pkey, oriented = ((sa, sb), coords) if sa < sb else ((sb, sa), coords[::-1])
            if pkey not in mode_pool or gap < mode_pool[pkey][0]:
                mode_pool[pkey] = (gap, oriented)
        if segs:
            route_segs[rid] = segs

    # Pass 2: feeds draw some stretches (mostly regional rail) as long straight jumps between
    # stations, and some feeds have no shapes at all. Replace those with detailed geometry for the
    # same station pair from another line, a chain of detailed segments, or (for trains) a path
    # along the OpenStreetMap rail network.
    graphs = {}
    for mode, segs in pool.items():
        edges = graphs[mode] = defaultdict(list)
        for (u, v), (gap, coords) in segs.items():
            w = length_and_gap(coords)[0]
            edges[u].append((v, w, gap, coords))
            edges[v].append((u, w, gap, coords[::-1]))

    def detailed_path(mode, sa, sb, max_gap):
        """Shortest chain of segments from station sa to sb using only segments finer than max_gap."""
        edges = graphs[mode]
        dist, prev, heap = {sa: 0.0}, {}, [(0.0, sa)]
        while heap:
            d, u = heapq.heappop(heap)
            if u == sb:
                break
            if d > dist.get(u, math.inf):
                continue
            for v, w, gap, coords in edges[u]:
                if gap < max_gap and d + w < dist.get(v, math.inf):
                    dist[v], prev[v] = d + w, (u, coords)
                    heapq.heappush(heap, (d + w, v))
        if sb not in prev:
            return None, math.inf
        chain, node = [], sb
        while node != sa:
            node, coords = prev[node]
            chain = list(coords) + chain[1:] if chain else list(coords)
        return chain, dist[sb]

    # A step longer than SUSPECT_GAP_M may be a straight-line placeholder (or just straight track),
    # so it is only replaced by geometry that is clearly more detailed.
    repaired = routed = unresolved = 0
    for rid, segs in route_segs.items():
        mode = routes[rid]["mode"]
        for key, (gap, coords, sa, sb) in segs.items():
            if gap <= SUSPECT_GAP_M or sa == sb:
                continue
            limit = min(gap / 2, MAX_GAP_M)
            pkey = (sa, sb) if sa < sb else (sb, sa)
            best = pool[mode].get(pkey)
            if best and best[0] < limit:
                segs[key] = (best[0], best[1] if pkey[0] == sa else best[1][::-1], sa, sb)
                repaired += 1
                continue
            chain, chain_len = detailed_path(mode, sa, sb, limit)
            straight = metres(stop_xy(sa), stop_xy(sb))
            if chain and chain_len <= 1.5 * straight + 2000:
                segs[key] = (limit, chain, sa, sb)
                repaired += 1
                continue
            # Trains without real geometry (no shape at all, or a long placeholder) follow the OSM rail network.
            placeholder = gap > MAX_GAP_M or len(coords) == 2
            track = router.route(coords[0], coords[-1]) if router and mode == "train" and placeholder else None
            if track:
                segs[key] = (length_and_gap(track)[1], track, sa, sb)
                routed += 1
            elif gap > MAX_GAP_M:
                unresolved += 1
    log(f"  straight-line placeholders: {repaired} replaced from the feed, {routed} routed along OSM rail, "
        f"{unresolved} long ones left straight")

    # Paths converge on merged stations: each end at one is bent onto the station's new position,
    # so all of its lines meet at the drawn dot (and segments of a line still join up there).
    for segs in route_segs.values():
        for key, (gap, coords, sa, sb) in segs.items():
            if sa in merged_pos:
                coords = converge(coords, merged_pos[sa], at_end=False)
            if sb in merged_pos:
                coords = converge(coords, merged_pos[sb], at_end=True)
            segs[key] = (gap, coords, sa, sb)

    service_dates = read_service_dates(feeds)
    start, days = representative_days(service_dates, trip_service)
    # Lines that don't run every week are flagged (the map can hide them), judged on the first
    # WEEKS_CHECKED full weeks of the feed without its holiday weeks.
    week_starts = []
    if start:
        monday = start + timedelta((7 - start.weekday()) % 7)
        week_starts = [monday + timedelta(7 * i) for i in range(WEEKS_CHECKED)]
    route_weeks = weeks_with_service(service_dates, {t: (resolve(trip_route[t]), trip_service[t]) for t in trip_span},
                                     week_starts)
    regular = regular_weeks(route_weeks)
    holiday_weeks = [w.strftime("%Y%m%d") for i, w in enumerate(week_starts) if i not in regular]
    if regular:
        log(f"  weeks: {len(regular)} of {len(week_starts)} regular from {week_starts[0]}; "
            f"holiday or partial: {', '.join(holiday_weeks) or 'none'}")

    # Trip starts and ends per 15-minute bin of each representative day, per (merged) route.
    # Trips running during bins [a, b) = starts before b minus ends before a.
    activity = defaultdict(lambda: {k: ([0] * DAY_BINS, [0] * DAY_BINS) for k in days})
    for tid, (t0, t1) in trip_span.items():
        sdates = service_dates.get(trip_service[tid], ())
        rid = resolve(trip_route[tid])
        for day_type, d in days.items():
            if d in sdates:
                starts, ends = activity[rid][day_type]
                starts[min(DAY_BINS - 1, t0 // BIN_MIN)] += 1
                ends[min(DAY_BINS - 1, t1 // BIN_MIN)] += 1

    netex_colours = netex.route_colours(cfg, colours.get("netex", {}))
    brand = cfg.get("brand_colours", {})
    brand_users = defaultdict(lambda: defaultdict(list))  # brand key -> mode -> indices into route_list
    route_list = []
    for rid in route_segs:
        info, shapes = routes[rid], route_shape_trips[rid]

        # Name the route after the endpoints of its most frequent variant.
        main_seq = [s[1] for s in trip_stops[shape_rep_trip[info["main_shape"]]]]
        origin, destination = station_name(main_seq[0]), station_name(main_seq[-1])
        terminus = f"{origin} (loop)" if origin == destination else f"{origin} – {destination}"

        # Ordered union of stations across all variants, most common variant first.
        station_ids, seen = [], set()
        for sid, _ in shapes.most_common():
            for _, stop_id, _ in trip_stops[shape_rep_trip[sid]]:
                if station_of(stop_id) not in seen:
                    seen.add(station_of(stop_id))
                    station_ids.append(station_of(stop_id))

        mode, short = info["mode"], info["short"]
        label = short if short and short != "TÅG" else info["long"] or info["agency"]
        # Colour priority: the feed, OSM, NeTEx, then the operator's brand colour (by agency, mode
        # or "*"; shaded per mode below), then the mode's default.
        color = known_colour(info, rid, colours, netex_colours)
        if not color:
            key = next((k for k in (info["agency"], mode, "*") if k in brand), None)
            if key == mode:
                color = brand[key]
            elif key:
                brand_users[key][mode].append(len(route_list))  # shade per mode, below
        color = color or MODE_COLOURS[mode]
        act = {k: {"s": compact(activity[rid][k][0]), "e": compact(activity[rid][k][1])} for k in days}
        route_list.append({"id": rid, "mode": mode, "label": label, "name": terminus, "color": color,
                           "agency": info["agency"], "trips": sum(shapes.values()), "stations": station_ids,
                           "act": {k: v for k, v in act.items() if v["s"]}})

    mark_occasional(route_list, route_weeks, regular)
    shade_brand_colours(route_list, brand, brand_users)

    # A route's index in this list is its place in every bundle of parallel lines, so the same
    # lines keep the same side-by-side order along a shared corridor.
    route_list.sort(key=lambda r: (MODE_ORDER.index(r["mode"]), natural(r["label"]), r["name"], r["id"]))
    route_index = {r["id"]: i for i, r in enumerate(route_list)}

    def bridge_gaps(mode, corridors, seg_corridor):
        """Find places where consecutive stretches of a line don't meet (typically where a shared
        corridor's geometry comes from another line's platforms across the street) and join them
        when the gap is short and both loose ends sit at the shared stop. Returns bridge corridors."""
        bridges, unresolved, seen = defaultdict(set), 0, set()
        for rid in sorted(r for r in route_segs if routes[r]["mode"] == mode):
            for sid in sorted(route_shape_trips[rid]):
                seq = [s for _, s, _ in trip_stops[shape_rep_trip[sid]]]
                for a, b, c in zip(seq, seq[1:], seq[2:]):
                    k1, k2 = (a, b) if a < b else (b, a), (b, c) if b < c else (c, b)
                    c1, c2 = seg_corridor.get((rid, k1)), seg_corridor.get((rid, k2))
                    if c1 is None or c2 is None or c1 == c2 or (rid, c1, c2) in seen:
                        continue
                    seen.add((rid, c1, c2))
                    stop = merged_pos.get(station_of(b)) or stop_xy(b)
                    # Loose end of each stretch: whichever end lies nearer the shared stop.
                    e1 = min((corridors[c1][0][0], corridors[c1][0][-1]), key=lambda p: metres(p, stop))
                    e2 = min((corridors[c2][0][0], corridors[c2][0][-1]), key=lambda p: metres(p, stop))
                    gap = metres(e1, e2)
                    if gap <= 1:
                        continue
                    near_stop = metres(e1, stop) <= BRIDGE_STOP_M and metres(e2, stop) <= BRIDGE_STOP_M
                    path = None
                    if gap <= BRIDGE_MAX_M and near_stop:
                        path = smooth_bridge(corridors[c1][0], e1, corridors[c2][0], e2)
                    elif mode == "train" and router and gap <= RAIL_BRIDGE_MAX_M:
                        path = router.route(tuple(e1), tuple(e2))
                    if path:
                        key = tuple(sorted((tuple(path[0]), tuple(path[-1]))))
                        bridges[key].add(route_index[rid])
                        bridge_paths[key] = path
                    else:
                        unresolved += 1
                        if DEBUG_GAPS:
                            log(f"    gap {gap:.0f} m at {stops[b]['stop_name']} on {routes[rid]['short']} "
                                f"(ends {metres(e1, stop):.0f} m / {metres(e2, stop):.0f} m from the stop)")
        return bridges, unresolved

    # Shared corridors: stop-to-stop paths of different lines (and both directions) between the
    # same two stations collapse into one geometry when they run within CORRIDOR_MATCH_M of each
    # other. The client draws each corridor once per visible line, offset side by side.
    for old in out_dir.glob("*.json"):
        old.unlink()
    modes_present, gap_stats = {}, {"bridged": 0, "unresolved": 0}
    for mode in MODE_ORDER:
        corridors, by_pair, seg_corridor, bridge_paths = [], defaultdict(list), {}, {}
        # Busiest lines first: they define each corridor's geometry and direction.
        for rid in sorted((r for r in route_segs if routes[r]["mode"] == mode),
                          key=lambda r: (-sum(route_shape_trips[r].values()), r)):
            idx = route_index[rid]
            for key, (_, coords, sa, sb) in route_segs[rid].items():
                pair = (sa, sb) if sa < sb else (sb, sa)
                match = next((c for c in by_pair[pair] if same_path(coords, corridors[c][0])), None)
                if match is None:
                    corridors.append((coords, set()))
                    match = len(corridors) - 1
                    by_pair[pair].append(match)
                corridors[match][1].add(idx)
                seg_corridor[(rid, key)] = match
        if not corridors:
            continue
        bridges, unresolved = bridge_gaps(mode, corridors, seg_corridor)
        for key in sorted(bridges):
            corridors.append((bridge_paths[key], bridges[key]))
        gap_stats["bridged"] += len(bridges)
        gap_stats["unresolved"] += unresolved
        modes_present[mode] = sum(1 for r in route_list if r["mode"] == mode)
        items = [[rounded(c), sorted(r)] for c, r in corridors]
        path = out_dir / f"{mode}.json"
        write_lines(path, {}, "corridors", items)
        widest = max(len(r) for _, r in corridors)
        log(f"  {mode:6} {modes_present[mode]:4} lines {len(corridors):6} corridors (widest {widest:2}) "
            f"{path.stat().st_size / 1e6:6.2f} MB; gaps: {len(bridges)} bridged, {unresolved} left")

    # Stations (parent stops) aggregate the routes of their platforms; platforms are kept for close zooms.
    station_routes = defaultdict(set)
    stop_features = []
    for stop_id in sorted(stop_routes):
        rids = {resolve(r) for r in stop_routes[stop_id]} & route_index.keys()
        if not rids:
            continue
        s = stops[stop_id]
        parent = station_of(stop_id)
        station_routes[parent] |= rids
        if parent != stop_id:
            stop_features.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [
                round(float(s["stop_lon"]), 6), round(float(s["stop_lat"]), 6)]},
                "properties": {"id": stop_id, "kind": "platform", "name": s["stop_name"],
                               "platform": s.get("platform_code") or "", "station": parent,
                               "routes": sorted(rids)}})
    for sid in sorted(station_routes):
        rids, s = station_routes[sid], stops[sid]
        modes = {route_list[route_index[r]]["mode"] for r in rids}
        lon, lat = merged_pos.get(sid, (float(s["stop_lon"]), float(s["stop_lat"])))
        stop_features.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [
            round(lon, 6), round(lat, 6)]},
            "properties": {"id": sid, "kind": "station", "name": s["stop_name"], "routes": sorted(rids),
                           "modes": sorted(modes), "rank": len(rids)}})
    path = out_dir / "stops.geojson"
    write_lines(path, {"type": "FeatureCollection"}, "features", stop_features)
    log(f"  stops  {len(station_routes)} stations, {len(stop_features) - len(station_routes)} platforms, "
        f"{path.stat().st_size / 1e6:.2f} MB")

    if not station_routes:
        raise ValueError("no lines left after filtering")

    versions = sorted({f.get("feed_version", "") for f in feeds.rows("feed_info.txt")} - {""})
    meta = {"id": cfg["id"], "name": cfg["name"], "area": cfg.get("area", ""),
            "feed_version": versions[-1] if versions else "", "days": days,
            "weeks_checked": len(regular), "weeks_from": week_starts[0].strftime("%Y%m%d") if week_starts else "",
            "holiday_weeks": holiday_weeks, "bin_minutes": BIN_MIN,
            # Extent of the stations actually served, ignoring far-off outliers.
            "bbox": percentile_bbox([stop_xy(s) for s in station_routes]), "modes": modes_present,
            "lines": len(route_list), "stations": len(station_routes), "osm_rail": routed > 0, "gaps": gap_stats}
    write_lines(out_dir / "routes.json", meta, "routes", route_list)
    return meta


def load_config(region_id):
    cfg = json.loads((ROOT / "regions.json").read_text(encoding="utf-8"))
    return next(r for r in cfg["regions"] if r["id"] == region_id)


def load_colours(region_id):
    path = ROOT / "colors" / f"{region_id}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def load_router():
    path = ROOT / "cache" / "osm-rail.json"
    return osm.RailRouter(path) if path.exists() else None


if __name__ == "__main__":
    region = load_config(sys.argv[1])
    print(build_region(region, sys.argv[2:], ROOT / "site" / "data" / region["id"],
                       load_colours(region["id"]), load_router()))
