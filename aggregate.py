"""Build the all-of-Sweden view from the regions already built in site/data/<region>/.

Every region's lines are kept (ids prefixed with the region, since feeds may reuse ids) with
coarser geometry suited to a national map. Stations that several regions share (Samtrafiken stop
area ids are national, and close stations with matching names are merged as in each region) appear
once, so clicking one shows the lines of every region that serves it. Platforms are left out.
Buses and ferries start switched off in this view.
"""

import json
from collections import defaultdict
from pathlib import Path

import build
from geo import percentile_bbox, simplify
from stations import cluster_stations

SWEDEN_ID = "sweden"
TOLERANCE_M = 5  # extra simplification for the national view
DECIMALS = 4  # ~6-11 m: plenty for a national map, and much smaller files


def coarse(coords):
    return [[round(x, DECIMALS), round(y, DECIMALS)] for x, y in simplify([tuple(c) for c in coords], TOLERANCE_M)]


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def build_sweden(config, data_dir, log=print):
    data_dir = Path(data_dir)
    out_dir = data_dir / SWEDEN_ID
    staging = data_dir / f".{SWEDEN_ID}.new"
    staging.mkdir(parents=True, exist_ok=True)
    for old in staging.glob("*"):
        old.unlink()

    routes, corridors = [], defaultdict(list)
    stations = {}  # id -> feature, merged across regions
    regions_used, feed_versions = [], []
    for cfg in config["regions"]:
        rdir = data_dir / cfg["id"]
        if not (rdir / "routes.json").exists():
            continue
        regions_used.append(cfg["id"])
        meta = read_json(rdir / "routes.json")
        feed_versions.append(meta["feed_version"])
        base = len(routes)
        prefix = f"{cfg['id']}/"
        index = {r["id"]: i for i, r in enumerate(meta["routes"])}
        for r in meta["routes"]:
            routes.append({**r, "id": prefix + r["id"], "region": cfg["id"]})
        for mode in meta["modes"]:
            for coords, lines in read_json(rdir / f"{mode}.json")["corridors"]:
                corridors[mode].append([coarse(coords), [base + i for i in lines]])
        for f in read_json(rdir / "stops.geojson")["features"]:
            p = f["properties"]
            if p["kind"] != "station":
                continue
            merged = stations.get(p["id"])
            if merged is None:
                merged = stations[p["id"]] = {"type": "Feature", "geometry": f["geometry"], "properties": {
                    "id": p["id"], "kind": "station", "name": p["name"], "routes": [], "modes": []}}
            mp = merged["properties"]
            # Lines by index into routes (not by id): halves the size of this 50 000-station file.
            mp["routes"] = sorted(set(mp["routes"]) | {base + index[r] for r in p["routes"]})
            mp["modes"] = sorted(set(mp["modes"]) | set(p["modes"]))
            mp["rank"] = len(mp["routes"])

    if not routes:
        raise ValueError("no regions built yet")
    # Regions merge close, same-named stations on their own; do it again across regions, e.g.
    # where two regions picked different stations of a group as its representative.
    alias = cluster_stations({k: (f["properties"]["name"], tuple(f["geometry"]["coordinates"]), f["properties"]["rank"])
                                    for k, f in stations.items()})
    groups = defaultdict(list)
    for sid, head in alias.items():
        groups[head].append(sid)
    for head, ids in groups.items():
        hp = stations[head]["properties"]
        pts = [stations[s]["geometry"]["coordinates"] for s in ids]
        stations[head]["geometry"] = {"type": "Point", "coordinates": [
            round(sum(p[0] for p in pts) / len(pts), 6), round(sum(p[1] for p in pts) / len(pts), 6)]}
        for s in ids:
            if s != head:
                sp = stations.pop(s)["properties"]
                hp["routes"] = sorted(set(hp["routes"]) | set(sp["routes"]))
                hp["modes"] = sorted(set(hp["modes"]) | set(sp["modes"]))
        hp["rank"] = len(hp["routes"])
    if alias:
        for r in routes:
            r["stations"] = list(dict.fromkeys(alias.get(s, s) for s in r["stations"]))
    modes = {}
    for mode in build.MODE_ORDER:
        if corridors[mode]:
            modes[mode] = sum(1 for r in routes if r["mode"] == mode)
            build.write_lines(staging / f"{mode}.json", {}, "corridors", corridors[mode])
    build.write_lines(staging / "stops.geojson", {"type": "FeatureCollection"}, "features",
                      [stations[k] for k in sorted(stations)])
    # The extent comes from the stations themselves, between percentiles, so the few international
    # stations of the long-distance trains don't stretch the national view to central Europe.
    meta = {"id": SWEDEN_ID, "name": "All of Sweden", "area": f"{len(regions_used)} regions",
            "feed_version": max(feed_versions), "days": {}, "bin_minutes": build.BIN_MIN,
            "bbox": percentile_bbox([f["geometry"]["coordinates"] for f in stations.values()]),
            "modes": modes, "modes_off": ["bus", "ferry"], "lines": len(routes), "stations": len(stations),
            "regions": regions_used}
    build.write_lines(staging / "routes.json", meta, "routes", routes)

    if out_dir.exists():
        for old in out_dir.glob("*"):
            old.unlink()
        out_dir.rmdir()
    staging.rename(out_dir)
    size = sum(p.stat().st_size for p in out_dir.glob("*")) / 1e6
    log(f"All of Sweden: {len(routes)} lines from {len(regions_used)} regions, {len(stations)} stations, {size:.1f} MB")
    return meta


if __name__ == "__main__":
    root = Path(__file__).parent
    build_sweden(read_json(root / "regions.json"), root / "site" / "data")
