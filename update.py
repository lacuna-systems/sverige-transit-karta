"""Download the GTFS feeds, refresh OSM colours and rail geometry, and rebuild every region.

Usage:
    python3 update.py                    # all regions in regions.json, 4 at a time
    python3 update.py sl skane           # just these
    python3 update.py --jobs 2           # fewer parallel builds (each can use ~1 GB of memory)
    python3 update.py --netex            # also download Trafiklab NeTEx Regional (line categories/colours)
    python3 update.py --netex-from DIR   # read NeTEx from DIR/<region>.zip instead of downloading

GTFS comes from Trafiklab's GTFS Regional API when TRAFIKLAB_GTFS_REGIONAL_KEY is set (falling
back to the Mobility Database mirror on any error), otherwise straight from the keyless mirror.
NeTEx needs TRAFIKLAB_NETEX_KEY (or TRAFIK_NETEX_KEY in .env); its results are stored in
colors/<region>.json, so runs without --netex keep using the last download.

A region that fails keeps its previous data; the run fails only if every region failed. The
all-of-Sweden view is rebuilt from the regions' data at the end of every run.
"""

import argparse
import gzip
import json
import os
import shutil
import sys
import tempfile
import time
import traceback
import urllib.request
import zipfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import aggregate
import build
import netex
import osm
from geo import percentile_bbox

ROOT = Path(__file__).parent
DATA = ROOT / "site" / "data"
CACHE = ROOT / "cache"
GTFS_FILES = ("agency.txt", "routes.txt", "trips.txt", "stops.txt", "stop_times.txt", "shapes.txt",
              "calendar.txt", "calendar_dates.txt", "feed_info.txt")
RAIL_MAX_AGE_DAYS = 30
NETEX_CALL_SPACING_S = 7  # Trafiklab allows 10 calls a minute


def download(url, dest, attempts=3):
    for i in range(attempts):
        try:
            # Trafiklab answers 406 unless the request accepts gzip; a zip is rarely re-compressed, but
            # undo it if the server does.
            req = urllib.request.Request(url, headers={"User-Agent": osm.USER_AGENT, "Accept-Encoding": "gzip"})
            with urllib.request.urlopen(req, timeout=600) as resp, open(dest, "wb") as f:
                body = gzip.GzipFile(fileobj=resp) if resp.headers.get("Content-Encoding") == "gzip" else resp
                shutil.copyfileobj(body, f, 1 << 20)
            if zipfile.is_zipfile(dest):
                return
            raise RuntimeError("response is not a zip file")
        except Exception:
            if i == attempts - 1:
                raise
            time.sleep(10 * (i + 1))


def fetch_feed(feed, workdir, source, log):
    """Download one feed and extract only the GTFS tables the build reads. Returns the directory."""
    zpath = workdir / f"{feed['slug']}.zip"
    key = os.environ.get("TRAFIKLAB_GTFS_REGIONAL_KEY")
    urls = []
    if key and source in ("auto", "trafiklab"):
        urls.append(f"https://opendata.samtrafiken.se/gtfs/{feed['slug']}/{feed['slug']}.zip?key={key}")
    if source in ("auto", "mirror"):
        urls.append(f"https://files.mobilitydatabase.org/mdb-{feed['mdb']}/latest.zip")
    error = None
    for url in urls:
        try:
            download(url, zpath)
            break
        except Exception as e:  # noqa: BLE001 - try the next source
            error = e
            log(f"  download failed from {url.split('?')[0]}: {e}")
    else:
        raise RuntimeError(f"could not download {feed['slug']}: {error}")
    out = workdir / feed["slug"]
    out.mkdir()
    with zipfile.ZipFile(zpath) as z:
        for name in GTFS_FILES:
            if name in z.namelist():
                with z.open(name) as src, open(out / name, "wb") as dst:
                    shutil.copyfileobj(src, dst, 1 << 20)
    zpath.unlink()
    return out


def stops_bbox(dirs):
    """Rough extent of a region's stops as (south, west, north, east), used to look up line colours
    before building."""
    points = []
    for row in build.Feeds(dirs).rows("stops.txt"):
        try:
            points.append((float(row["stop_lon"]), float(row["stop_lat"])))
        except (KeyError, ValueError):
            continue
    west, south, east, north = percentile_bbox(points, decimals=6)
    return (south, west, north, east)


def colour_relations(log=print):
    """All coloured rail route relations in Sweden from OSM, fetched once per run; on failure the
    copy from the last successful run is used (or none, keeping each region's cached colours)."""
    path = CACHE / "osm-colours.json"
    try:
        relations = osm.fetch_colour_relations()
        CACHE.mkdir(exist_ok=True)
        path.write_text(json.dumps(relations), encoding="utf-8")
        log(f"OSM colours: {len(relations)} coloured route relations")
        return relations
    except Exception as e:  # noqa: BLE001
        fallback = "using the last copy" if path.exists() else "keeping cached colours"
        log(f"OSM colours: Overpass unavailable ({e}); {fallback}")
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def refresh_colours(region_id, bbox, relations):
    """Update colors/<region>.json with the OSM colours of lines in the region's area. Refs OSM no
    longer returns are kept (a relation briefly broken in OSM shouldn't drop a colour)."""
    if relations is None:
        return
    fresh = osm.colours_in_bbox(relations, bbox)
    path = ROOT / "colors" / f"{region_id}.json"
    old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    merged = dict(old)
    for mode in ("tram", "metro", "train"):
        if mode in old or mode in fresh:
            merged[mode] = {**old.get(mode, {}), **fresh.get(mode, {})}
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(merged, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def ensure_rail_network(log=print):
    """Refresh the cached OSM rail network when it is missing or older than RAIL_MAX_AGE_DAYS."""
    path = CACHE / "osm-rail.json"
    if not path.exists() or time.time() - path.stat().st_mtime > RAIL_MAX_AGE_DAYS * 86400:
        CACHE.mkdir(exist_ok=True)
        log("Fetching OSM rail network…")
        try:
            osm.fetch_rail(path)
        except Exception as e:  # noqa: BLE001
            log(f"  rail network unavailable ({e}); "
                f"{'using the cached copy' if path.exists() else 'trains without shapes stay straight'}")
    return path if path.exists() else None


_ROUTER = {}


def worker_router(path):
    """One rail router per worker process, loaded on first use."""
    if path and path not in _ROUTER:
        _ROUTER[path] = osm.RailRouter(path)
    return _ROUTER.get(path)


def run_region(cfg, source, rail_path, relations):
    """Download, colour and build one region (runs in a worker process)."""
    log = lambda m: print(f"[{cfg['id']}] {m}", flush=True)  # noqa: E731
    start = time.time()
    with tempfile.TemporaryDirectory(prefix=f"gtfs-{cfg['id']}-") as tmp:
        staging = DATA / f".{cfg['id']}.new"
        try:
            dirs = [fetch_feed(feed, Path(tmp), source, log) for feed in cfg["feeds"]]
            refresh_colours(cfg["id"], stops_bbox(dirs), relations)
            shutil.rmtree(staging, ignore_errors=True)
            router = worker_router(str(rail_path)) if rail_path else None
            meta = build.build_region(cfg, dirs, staging, build.load_colours(cfg["id"]), router, log=log)
            shutil.rmtree(DATA / cfg["id"], ignore_errors=True)
            staging.rename(DATA / cfg["id"])
            details = f"{meta['lines']} lines, {meta['stations']} stops, feed {meta['feed_version']}"
            return "ok", details, time.time() - start
        except Exception as e:  # noqa: BLE001 - one region failing must not stop the others
            log(traceback.format_exc())
            shutil.rmtree(staging, ignore_errors=True)
            return "failed", f"{type(e).__name__}: {e}", time.time() - start


def update_netex(regions, from_dir, log=print):
    """Refresh colors/<region>.json["netex"] for regional authorities, from Trafiklab or local zips."""
    key = None if from_dir else netex.api_key()
    if not from_dir and not key:
        log("NeTEx: no TRAFIKLAB_NETEX_KEY set; keeping the stored NeTEx data")
        return
    with tempfile.TemporaryDirectory(prefix="netex-") as tmp:
        for i, cfg in enumerate(r for r in regions if len(r["feeds"]) == 1):
            local = Path(from_dir) / f"{cfg['id']}.zip" if from_dir else None
            if local and not local.exists():
                continue
            try:
                if not local and i:
                    time.sleep(NETEX_CALL_SPACING_S)
                netex.refresh_region(cfg, tmp, key, log=lambda m, rid=cfg["id"]: log(f"[{rid}] {m}"), local_zip=local)
            except Exception as e:  # noqa: BLE001 - keep the previous NeTEx data for this region
                log(f"[{cfg['id']}] NeTEx failed ({e}); keeping the stored data")


def write_index(config):
    """site/data/regions.json: every region that has data, for the region picker."""
    regions = []
    for cfg in [{"id": aggregate.SWEDEN_ID}] + config["regions"]:
        path = DATA / cfg["id"] / "routes.json"
        if path.exists():
            with open(path, encoding="utf-8") as f:
                meta = json.loads(f.readline().rsplit(',"routes":[', 1)[0] + "}")
            regions.append({k: meta[k] for k in ("id", "name", "area", "feed_version", "bbox", "modes", "modes_off",
                                                 "lines", "stations") if k in meta})
    (DATA / "regions.json").write_text(json.dumps({"default": config["default"], "regions": regions},
                                                  ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("regions", nargs="*", help="region ids (default: all)")
    parser.add_argument("--source", choices=("auto", "trafiklab", "mirror"), default="auto")
    parser.add_argument("--jobs", type=int, default=min(4, os.cpu_count() or 1), help="regions built in parallel")
    parser.add_argument("--netex", action="store_true", help="download Trafiklab NeTEx Regional for line colours")
    parser.add_argument("--netex-from", metavar="DIR", help="read NeTEx zips from DIR/<region>.zip instead")
    args = parser.parse_args()

    config = json.loads((ROOT / "regions.json").read_text(encoding="utf-8"))
    wanted = [r for r in config["regions"] if not args.regions or r["id"] in args.regions]
    unknown = set(args.regions) - {r["id"] for r in config["regions"]}
    if unknown:
        sys.exit(f"unknown region(s): {', '.join(sorted(unknown))}")

    if args.netex or args.netex_from:
        update_netex(wanted, args.netex_from)
    rail_path = ensure_rail_network()
    relations = colour_relations()

    with ProcessPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        futures = [(cfg, pool.submit(run_region, cfg, args.source, rail_path, relations)) for cfg in wanted]
        results = [(cfg, *f.result()) for cfg, f in futures]

    try:
        meta = aggregate.build_sweden(config, DATA)
        results.append(({"name": "All of Sweden"}, "ok", f"{meta['lines']} lines, {meta['stations']} stops", 0))
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        results.append(({"name": "All of Sweden"}, "failed", f"{type(e).__name__}: {e}", 0))
    write_index(config)

    summary = ["| Region | Result | Details | Time |", "|---|---|---|---|"]
    summary += [f"| {c['name']} | {'✅' if s == 'ok' else '❌'} {s} | {d} | {t:.0f} s |" for c, s, d, t in results]
    print("\n".join(summary))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write("## Transit data update\n\n" + "\n".join(summary) + "\n")
    if results and all(s == "failed" for _, s, _, _ in results[:-1]):
        sys.exit(1)


if __name__ == "__main__":
    main()
