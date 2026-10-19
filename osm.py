"""OpenStreetMap helpers: line colours per region and routing trains along the rail network.

Both use the Overpass API. Results are cached on disk so a failed or slow Overpass request
never breaks a build: colours live in colors/<region>.json (committed), the rail network in
cache/osm-rail.json (not committed; refreshed monthly).
"""

import heapq
import json
import math
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict

from geo import metres

OVERPASS_URLS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]
USER_AGENT = "sweden-transit-map (+https://github.com/)"
# Sweden plus the cross-border stretches to Oslo, Copenhagen and Narvik.
RAIL_BBOX = (54.9, 10.0, 69.2, 24.3)
# OSM route types and the map modes they may be; light rail is a tram in some GTFS feeds and a train
# in others (e.g. Saltsjöbanan).
OSM_ROUTE_MODES = {"tram": ["tram"], "light_rail": ["tram", "train"], "subway": ["metro"], "train": ["train"]}
NAMED_COLOURS = {"black": "#000000", "white": "#FFFFFF", "red": "#E32118", "green": "#00A261", "blue": "#0079C2",
                 "yellow": "#FDDD04", "orange": "#FA8719", "purple": "#872387", "brown": "#9C5506", "grey": "#808080",
                 "gray": "#808080", "pink": "#E91E8C"}


def overpass(query, timeout=600, rounds=3):
    """Run an Overpass query, trying each mirror and backing off between rounds (Overpass rate-limits)."""
    last = None
    for attempt in range(rounds):
        for url in OVERPASS_URLS:
            try:
                req = urllib.request.Request(url, data=urllib.parse.urlencode({"data": query}).encode(),
                                             headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return json.load(resp)
            except Exception as e:  # noqa: BLE001 - any failure moves on to the next mirror
                last = e
        if attempt + 1 < rounds:
            time.sleep(30 * (attempt + 1))
    raise RuntimeError(f"Overpass failed: {last}")


def normalise_colour(c):
    c = c.strip().lower()
    if c in NAMED_COLOURS:
        return NAMED_COLOURS[c]
    if c.startswith("#") and len(c) == 4:
        c = "#" + "".join(ch * 2 for ch in c[1:])
    return c.upper() if c.startswith("#") and len(c) == 7 else None


def fetch_colour_relations(bbox=RAIL_BBOX):
    """Every tram, light rail, metro and train route relation with a colour in bbox (south, west,
    north, east), with its bounds. One query for the whole country, filtered per region locally."""
    s, w, n, e = bbox
    data = overpass(f'[out:json][timeout:300];relation["type"="route"]["route"~"^(tram|light_rail|subway|train)$"]'
                    f'["colour"]["ref"]({s},{w},{n},{e});out tags bb;', timeout=360, rounds=2)
    out = []
    for el in data["elements"]:
        tags, b = el.get("tags", {}), el.get("bounds")
        if b and tags.get("colour") and tags.get("ref"):
            out.append({"route": tags["route"], "ref": tags["ref"], "colour": tags["colour"],
                        "bounds": [b["minlat"], b["minlon"], b["maxlat"], b["maxlon"]]})
    return out


def colours_in_bbox(relations, bbox=None):
    """Line colours of the relations overlapping bbox (south, west, north, east): mode -> ref -> #RRGGBB."""
    votes = defaultdict(Counter)
    for rel in relations:
        if bbox and "bounds" in rel:
            s, w, n, e = rel["bounds"]
            if s > bbox[2] or n < bbox[0] or w > bbox[3] or e < bbox[1]:
                continue
        colour = normalise_colour(rel.get("colour", ""))
        if not colour:
            continue
        modes = OSM_ROUTE_MODES[rel["route"]]
        for ref in rel["ref"].split(";"):
            for mode in modes:
                votes[(mode, ref.strip())][colour] += 1
    out = defaultdict(dict)
    for (mode, ref), c in sorted(votes.items()):
        out[mode][ref] = c.most_common(1)[0][0]
    return dict(out)


def fetch_rail(path):
    """Download the rail network to path, without sidings, yards and spurs. Crossovers stay: station
    throats and junctions are often connected only through them."""
    s, w, n, e = RAIL_BBOX
    data = overpass(f'[out:json][timeout:500];way["railway"~"^(rail|light_rail)$"]["service"!~"^(siding|yard|spur)$"]'
                    f'({s},{w},{n},{e});out geom qt;', timeout=700)
    if len(data.get("elements", [])) < 1000:
        raise RuntimeError("Overpass returned an implausibly small rail network")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"elements": [{"geometry": el["geometry"]} for el in data["elements"] if "geometry" in el]}, f,
                  separators=(",", ":"))


class RailRouter:
    """Shortest paths along OSM railway lines, used where a feed has no usable train shapes."""

    SNAP_M = 1500

    def __init__(self, path):
        with open(path, encoding="utf-8") as f:
            ways = json.load(f)["elements"]
        self.coords, index = [], {}
        self.edges = defaultdict(list)
        for way in ways:
            prev = None
            for p in way["geometry"]:
                key = (round(p["lon"], 7), round(p["lat"], 7))
                node = index.get(key)
                if node is None:
                    node = index[key] = len(self.coords)
                    self.coords.append(key)
                if prev is not None and prev != node:
                    w = metres(self.coords[prev], key)
                    self.edges[prev].append((node, w))
                    self.edges[node].append((prev, w))
                prev = node
        self.grid = defaultdict(list)
        for i, (lon, lat) in enumerate(self.coords):
            self.grid[(int(lon * 50), int(lat * 100))].append(i)
        self.heal_gaps()
        self.cache = {}

    def heal_gaps(self, max_m=30):
        """OSM rail ways are sometimes split with a small gap instead of a shared node (at junctions,
        bridges or edits in progress). Connect every dead end to the nearest other track node nearby."""
        for u in [i for i, e in self.edges.items() if len(e) == 1]:
            near = {v for v, _ in self.edges[u]} | {u}
            p = self.coords[u]
            cx, cy = int(p[0] * 50), int(p[1] * 100)
            best, best_d = None, max_m
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for v in self.grid.get((cx + dx, cy + dy), ()):
                        if v not in near:
                            d = metres(p, self.coords[v])
                            if d < best_d:
                                best, best_d = v, d
            if best is not None:
                self.edges[u].append((best, best_d))
                self.edges[best].append((u, best_d))

    def snap(self, p):
        cx, cy = int(p[0] * 50), int(p[1] * 100)
        best, best_d = None, self.SNAP_M
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for i in self.grid.get((cx + dx, cy + dy), ()):
                    d = metres(p, self.coords[i])
                    if d < best_d:
                        best, best_d = i, d
        return best

    def route(self, a, b):
        """Track geometry from a to b as (lon, lat) points, or None when no plausible path exists."""
        key = (a, b)
        if key not in self.cache:
            self.cache[key] = self._route(a, b)
        return self.cache[key]

    def _route(self, a, b):
        sa, sb = self.snap(a), self.snap(b)
        if sa is None or sb is None:
            return None
        if sa == sb:
            return [a, b]
        target = self.coords[sb]
        limit = 1.5 * metres(a, b) + 5000
        dist, prev = {sa: 0.0}, {}
        heap = [(metres(self.coords[sa], target), 0.0, sa)]
        while heap:
            _, d, u = heapq.heappop(heap)
            if u == sb:
                break
            if d > dist.get(u, math.inf) or d > limit:
                continue
            for v, w in self.edges[u]:
                nd = d + w
                if nd < dist.get(v, math.inf):
                    dist[v], prev[v] = nd, u
                    heapq.heappush(heap, (nd + metres(self.coords[v], target), nd, v))
        if sb not in prev or dist[sb] > limit:
            return None
        path, node = [sb], sb
        while node != sa:
            node = prev[node]
            path.append(node)
        return [a] + [self.coords[i] for i in reversed(path)] + [b]
