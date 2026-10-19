import json
import os
import tempfile
import unittest
from collections import defaultdict
from pathlib import Path

import build
import colours
from tests.gtfs_fixture import AGENCY, LAT, make_feed, station_lon

CFG = {"id": "test", "name": "Test region", "include_agencies": f"^{AGENCY}$"}


def run_build(cfg=CFG, colours=None, feeds=1):
    tmp = tempfile.TemporaryDirectory()
    dirs = [make_feed(Path(tmp.name) / f"feed{i}") for i in range(feeds)]
    out = Path(tmp.name) / "out"
    meta = build.build_region(cfg, dirs, out, colours or {}, None, log=lambda m: None)
    data = {p.name: json.loads(p.read_text(encoding="utf-8")) for p in out.iterdir()}
    return tmp, out, meta, data


class ModeOf(unittest.TestCase):
    def test_basic_and_extended_types(self):
        cases = {"0": "tram", "900": "tram", "1": "metro", "401": "metro", "2": "train", "101": "train",
                 "106": "train", "3": "bus", "700": "bus", "200": "bus", "800": "bus", "4": "ferry", "1000": "ferry"}
        for route_type, mode in cases.items():
            self.assertEqual(build.mode_of(route_type), mode, route_type)

    def test_left_out_modes(self):
        for route_type in ("1501", "715", "6", "7", "1400"):
            self.assertIsNone(build.mode_of(route_type), route_type)


class Helpers(unittest.TestCase):
    def test_natural_sort(self):
        self.assertEqual(sorted(["10", "2", "1", "X", "4E"], key=build.natural), ["1", "2", "4E", "10", "X"])

    def test_minutes_past_midnight(self):
        self.assertEqual(build.minutes("25:10:00"), 25 * 60 + 10)

    def test_write_lines_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            for head in ({}, {"id": "x", "n": 1}):
                path = Path(tmp) / "f.json"
                build.write_lines(path, head, "items", [[1, 2], {"a": "å"}])
                data = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(data["items"], [[1, 2], {"a": "å"}])
                for k, v in head.items():
                    self.assertEqual(data[k], v)
                self.assertGreaterEqual(path.read_text(encoding="utf-8").count("\n"), 3, "one item per line")


class BuildRegion(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.out, cls.meta, cls.data = run_build()
        cls.routes = {r["label"]: r for r in cls.data["routes.json"]["routes"]}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_modes_and_filters(self):
        self.assertEqual(self.meta["modes"], {"tram": 2, "bus": 2})
        self.assertNotIn("D", self.routes, "dial-a-ride is left out")
        self.assertNotIn("99", self.routes, "other agencies are filtered out")

    def test_route_names_and_stations(self):
        self.assertEqual(self.routes["1"]["name"], "Station 1 – Station 4")
        self.assertEqual(self.routes["1"]["stations"], ["S1", "S2", "S3", "S4"])
        self.assertEqual(self.routes["1"]["agency"], AGENCY)

    def test_shared_corridor_bundles_both_trams(self):
        corridors = self.data["tram.json"]["corridors"]
        widest = max(len(lines) for _, lines in corridors)
        self.assertEqual(widest, 2, "trams 1 and 2 share S1-S3")
        index = {r["label"]: i for i, r in enumerate(self.data["routes.json"]["routes"])}
        shared = [c for c, lines in corridors if set(lines) == {index["1"], index["2"]}]
        self.assertEqual(len(shared), 2, "S1-S2 and S2-S3")

    def test_corridor_indexes_valid(self):
        n = len(self.data["routes.json"]["routes"])
        for mode in self.meta["modes"]:
            for coords, lines in self.data[f"{mode}.json"]["corridors"]:
                self.assertGreaterEqual(len(coords), 2)
                self.assertTrue(all(0 <= i < n for i in lines))

    def test_geometry_follows_shape(self):
        # Tram 1's corridors stay on the shape (a few metres north of the street centre).
        for coords, _ in self.data["tram.json"]["corridors"]:
            for _, lat in coords:
                self.assertAlmostEqual(lat, 57.70008, delta=0.0002)

    def test_bus_without_shape_is_straight_placeholder(self):
        coords = self.data["bus.json"]["corridors"][0][0]
        self.assertEqual(len(coords), 2)

    def test_service_days(self):
        days = self.data["routes.json"]["days"]
        self.assertEqual(set(days), {"wd", "sa", "su"})
        self.assertNotEqual(days["wd"], "20261007", "a date removed by calendar_dates is never the busiest")

    def test_activity_counts(self):
        t1, t2 = self.routes["1"]["act"], self.routes["2"]["act"]
        self.assertEqual(sum(t1["wd"]["s"][1:]), 6)
        self.assertNotIn("sa", t1, "tram 1 runs weekdays only")
        self.assertEqual(sum(t2["sa"]["s"][1:]), 1)
        # The 25:00 bus trip sits in the weekday's bins after midnight.
        self.assertEqual(self.routes["10"]["act"]["wd"]["s"][0], 25 * 60 // build.BIN_MIN)

    def test_stops(self):
        feats = self.data["stops.geojson"]["features"]
        stations = {f["properties"]["id"]: f["properties"] for f in feats if f["properties"]["kind"] == "station"}
        platforms = [f["properties"] for f in feats if f["properties"]["kind"] == "platform"]
        self.assertEqual(set(stations), {"S1", "S2", "S3", "S4"})
        self.assertEqual(len(stations["S2"]["routes"]), 2)
        self.assertTrue(all(p["station"] in stations for p in platforms))

    def test_no_gaps_left(self):
        self.assertEqual(self.meta["gaps"]["unresolved"], 0)

    def test_deterministic(self):
        tmp, out, _, _ = run_build()
        try:
            for name in os.listdir(self.out):
                self.assertEqual((self.out / name).read_bytes(), (out / name).read_bytes(), name)
        finally:
            tmp.cleanup()


class WeeklyAndExcluded(unittest.TestCase):
    def test_weeks_with_service(self):
        tmp, _, meta, data = run_build()
        try:
            occasional = {r["label"]: r.get("occasional", False) for r in data["routes.json"]["routes"]}
            # The fixture's weekday service runs 4 weeks from 2026-10-05; the other 4 weeks
            # checked are past the end of the feed, so they are not regular weeks.
            self.assertEqual(meta["weeks_checked"], 4)
            self.assertEqual(meta["weeks_from"], "20261005")
            self.assertEqual(len(meta["holiday_weeks"]), build.WEEKS_CHECKED - 4)
            self.assertFalse(occasional["1"], "weekday line runs every week")
            self.assertFalse(occasional["2"])
            self.assertFalse(occasional["20"], "runs in weeks 1-2, then stops: not a gap")
        finally:
            tmp.cleanup()

    def test_timetable_change_with_new_route_id_is_not_occasional(self):
        # Line 20 (B20) runs on the first two Saturdays; after a "timetable change" route B20N,
        # also numbered 20 and serving the same stops, takes over in week 3. Alone, B20N would
        # be a one-week line.
        with tempfile.TemporaryDirectory() as tmp:
            d = make_feed(Path(tmp) / "feed")
            with open(d / "routes.txt", "a", encoding="utf-8") as f:
                f.write("B20N,A,20,,700\n")
            with open(d / "calendar.txt", "a", encoding="utf-8") as f:
                f.write("LATE,1,1,1,1,1,0,0,20261019,20261025\n")
            with open(d / "trips.txt", "a", encoding="utf-8") as f:
                f.write("B20N,LATE,B20N_0,\n")
            with open(d / "stop_times.txt", "a", encoding="utf-8") as f:
                f.write("B20N_0,11:00:00,11:00:00,S3E,1,\nB20N_0,11:02:00,11:02:00,S4E,2,\n")
            build.build_region(CFG, [d], Path(tmp) / "out", {}, None, log=lambda m: None)
            routes = json.loads((Path(tmp) / "out" / "routes.json").read_text(encoding="utf-8"))["routes"]
            twenties = [r for r in routes if r["label"] == "20"]
            self.assertEqual(len(twenties), 2)
            self.assertFalse(any(r.get("occasional") for r in twenties), "together they run every week")

    def test_line_ending_early_is_not_occasional_but_gaps_are(self):
        # B20 runs on Saturdays in weeks 1-2 only: it stops, it doesn't skip a week, so it is
        # weekly. B30 runs in weeks 1 and 3 only, skipping week 2: occasional.
        with tempfile.TemporaryDirectory() as tmp:
            d = make_feed(Path(tmp) / "feed")
            with open(d / "routes.txt", "a", encoding="utf-8") as f:
                f.write("B30,A,30,,700\n")
            with open(d / "calendar_dates.txt", "a", encoding="utf-8") as f:
                f.write("GAP,20261006,1\nGAP,20261020,1\n")
            with open(d / "trips.txt", "a", encoding="utf-8") as f:
                f.write("B30,GAP,B30_0,\n")
            with open(d / "stop_times.txt", "a", encoding="utf-8") as f:
                f.write("B30_0,12:00:00,12:00:00,S1E,1,\nB30_0,12:02:00,12:02:00,S2E,2,\n")
            build.build_region(CFG, [d], Path(tmp) / "out", {}, None, log=lambda m: None)
            routes = json.loads((Path(tmp) / "out" / "routes.json").read_text(encoding="utf-8"))["routes"]
            occ = {r["label"]: r.get("occasional", False) for r in routes}
            self.assertFalse(occ["20"])
            self.assertTrue(occ["30"])

    def test_exclude_routes(self):
        tmp, _, _, data = run_build({**CFG, "exclude_routes": ["T2", "B20"]})
        try:
            ids = {r["id"] for r in data["routes.json"]["routes"]}
            self.assertNotIn("T2", ids)
            self.assertNotIn("B20", ids)
            self.assertIn("T1", ids)
            stations = [f for f in data["stops.geojson"]["features"] if f["properties"]["kind"] == "station"]
            self.assertFalse(any("T2" in f["properties"]["routes"] for f in stations), "no stop lists an excluded line")
        finally:
            tmp.cleanup()

    def test_exclude_routes_ignores_feed_prefix(self):
        tmp, _, _, data = run_build({**CFG, "exclude_routes": ["T2"]}, feeds=2)
        try:
            self.assertFalse(any(r["id"].endswith("T2") for r in data["routes.json"]["routes"]))
        finally:
            tmp.cleanup()


class StationMerge(unittest.TestCase):
    def test_close_stations_with_the_same_name_become_one(self):
        # "Station 3 resecentrum", 60 m north of Station 3, is served by bus 40 towards Station 4.
        with tempfile.TemporaryDirectory() as tmp:
            d = make_feed(Path(tmp) / "feed")
            lat5 = LAT + 60 / 110540.0
            with open(d / "stops.txt", "a", encoding="utf-8") as f:
                f.write(f"S5,Station 3 resecentrum,{lat5},{station_lon(2)},1,\n"
                        f"S5A,Station 3 resecentrum,{lat5},{station_lon(2)},0,S5\n")
            with open(d / "routes.txt", "a", encoding="utf-8") as f:
                f.write("B40,A,40,,700\n")
            with open(d / "trips.txt", "a", encoding="utf-8") as f:
                f.write("B40,WK,B40_0,\n")
            with open(d / "stop_times.txt", "a", encoding="utf-8") as f:
                f.write("B40_0,12:00:00,12:00:00,S5A,1,\nB40_0,12:05:00,12:05:00,S4E,2,\n")
            build.build_region(CFG, [d], Path(tmp) / "out", {}, None, log=lambda m: None)
            out = Path(tmp) / "out"
            features = json.loads((out / "stops.geojson").read_text(encoding="utf-8"))["features"]
            stations = {f["properties"]["id"]: f for f in features if f["properties"]["kind"] == "station"}
            self.assertNotIn("S5", stations, "merged into Station 3")
            s3 = stations["S3"]
            self.assertEqual(s3["properties"]["name"], "Station 3", "named after the busiest member")
            lon, lat = s3["geometry"]["coordinates"]
            self.assertAlmostEqual(lat, (LAT + lat5) / 2, places=6, msg="drawn at the average position")
            routes = json.loads((out / "routes.json").read_text(encoding="utf-8"))["routes"]
            b40 = next(r for r in routes if r["label"] == "40")
            self.assertIn("S3", b40["stations"])
            # Every path touching Station 3 ends exactly on the merged point.
            ends = [tuple(p) for mode in ("tram", "bus") for c, _ in
                    json.loads((out / f"{mode}.json").read_text(encoding="utf-8"))["corridors"] for p in (c[0], c[-1])]
            near = [p for p in ends if build.metres(p, (lon, lat)) < 100]
            self.assertTrue(near)
            self.assertTrue(all(build.metres(p, (lon, lat)) < 1.5 for p in near), near)


class Colours(unittest.TestCase):
    def test_priority_osm_then_netex_then_default(self):
        colours = {"tram": {"1": "#111111"},
                   "netex": {"groups": {"T1": "Spårvagn", "T2": "Spårvagn", "B10": "Stadsbuss Testby"}, "colours": {}}}
        cfg = {**CFG, "category_colours": {"Spårvagn": "#222222", "Stadsbuss": "#333333"}}
        tmp, _, _, data = run_build(cfg, colours)
        try:
            by_label = {r["label"]: r["color"] for r in data["routes.json"]["routes"]}
            self.assertEqual(by_label["1"], "#111111", "OSM wins over NeTEx")
            self.assertEqual(by_label["2"], "#222222", "NeTEx category when OSM has none")
            self.assertEqual(by_label["10"], "#333333", "category matched by name prefix")
        finally:
            tmp.cleanup()

    def test_brand_colour_after_osm_and_netex(self):
        colours = {"tram": {"1": "#111111"}}
        cfg = {**CFG, "brand_colours": {"*": "#0079B4", "bus": "#555555"}}
        tmp, _, _, data = run_build(cfg, colours)
        try:
            by_label = {r["label"]: r["color"] for r in data["routes.json"]["routes"]}
            self.assertEqual(by_label["1"], "#111111", "OSM wins over the brand colour")
            self.assertEqual(by_label["2"], "#0079B4", "brand colour when nothing else has one")
            self.assertEqual(by_label["10"], "#555555", "a mode-specific brand colour beats '*'")
        finally:
            tmp.cleanup()

    def test_brand_colour_shaded_per_mode(self):
        cfg = {**CFG, "brand_colours": {"*": "#0079B4"}}
        tmp, _, _, data = run_build(cfg)
        try:
            by_mode = defaultdict(set)
            for r in data["routes.json"]["routes"]:
                by_mode[r["mode"]].add(r["color"])
            self.assertEqual(set(by_mode), {"tram", "bus"})
            self.assertTrue(all(len(c) == 1 for c in by_mode.values()), "one shade per mode")
            self.assertNotEqual(by_mode["tram"], by_mode["bus"], "modes get different shades")
            self.assertIn("#0079B4", by_mode["tram"] | by_mode["bus"], "the busiest mode keeps the brand colour")
        finally:
            tmp.cleanup()

    def test_default_mode_colour(self):
        tmp, _, _, data = run_build()
        try:
            self.assertTrue(all(r["color"] == colours.MODE_COLOURS[r["mode"]] for r in data["routes.json"]["routes"]))
        finally:
            tmp.cleanup()


class MultiFeed(unittest.TestCase):
    def test_ids_prefixed_per_feed_and_grouping(self):
        cfg = {"id": "multi", "name": "Multi", "modes": ["tram"], "group_by_termini": True,
               "agency_labels": {AGENCY: "TT"}}
        tmp, _, meta, data = run_build(cfg, feeds=2)
        try:
            routes = data["routes.json"]["routes"]
            self.assertTrue(all(":" in r["id"] for r in routes), "ids are prefixed per feed")
            # Tram 1 (S1-S4) and tram 2 (S1-S3) differ in end stations; the second feed's copies merge in.
            self.assertEqual(len(routes), 2)
            self.assertTrue(all(r["label"] == "TT" for r in routes))
            self.assertEqual(sum(sum(r["act"]["wd"]["s"][1:]) for r in routes), 18)
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
