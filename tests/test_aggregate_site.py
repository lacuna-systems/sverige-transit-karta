import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import aggregate
import build
from tests.gtfs_fixture import AGENCY, make_feed

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "site" / "data"


class Aggregate(unittest.TestCase):
    def test_two_regions_merge(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "data"
            feed = make_feed(Path(tmp) / "feed")
            config = {"default": "a", "regions": [
                {"id": "a", "name": "A", "include_agencies": f"^{AGENCY}$"},
                {"id": "b", "name": "B", "modes": ["bus"]}]}
            for cfg in config["regions"]:
                build.build_region(cfg, [feed], data / cfg["id"], {}, None, log=lambda m: None)
            meta = aggregate.build_sweden(config, data, log=lambda m: None)
            routes = json.loads((data / "sweden" / "routes.json").read_text())["routes"]
            stops = json.loads((data / "sweden" / "stops.geojson").read_text())["features"]

        self.assertEqual(meta["modes_off"], ["bus", "ferry"])
        self.assertEqual(len(routes), 4 + 3, "region a: trams 1, 2 and buses 10, 20; region b: buses 10, 20 and 99")
        self.assertTrue(all(r["id"].startswith(r["region"] + "/") for r in routes))
        # Station S1 is served in both regions and appears once, listing lines of both by index.
        s1 = [f["properties"] for f in stops if f["properties"]["id"] == "S1"]
        self.assertEqual(len(s1), 1)
        self.assertEqual({routes[i]["region"] for i in s1[0]["routes"]}, {"a", "b"})
        self.assertTrue(all(f["properties"]["kind"] == "station" for f in stops), "no platforms nationally")


class Site(unittest.TestCase):
    SITE = ROOT / "site"

    def test_script_parses(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        for script in ("i18n.js", "app.js"):
            result = subprocess.run([node, "--check", str(self.SITE / script)], capture_output=True, text=True,
                                    check=False)
            self.assertEqual(result.returncode, 0, f"{script}: {result.stderr}")

    def test_local_files_exist(self):
        # Everything the page loads from the site itself must be there, so it can be served as is.
        html = (self.SITE / "index.html").read_text(encoding="utf-8")
        refs = re.findall(r'(?:src|href)="(?!https?:|data:|#)([^"]+)"', html)
        self.assertIn("app.js", refs)
        self.assertIn("i18n.js", refs)
        self.assertIn("style.css", refs)
        for ref in refs:
            self.assertTrue((self.SITE / ref).is_file(), ref)

    def test_only_allowed_external_hosts(self):
        html = (self.SITE / "index.html").read_text(encoding="utf-8")
        # Scripts and stylesheets the page loads (plain links, e.g. attributions, don't count).
        hosts = set(re.findall(r'<script[^>]*src="https://([^/"]+)', html)) | \
            set(re.findall(r'<link[^>]*rel="stylesheet"[^>]*href="https://([^/"]+)', html))
        self.assertLessEqual(hosts, {"unpkg.com"})


class Translations(unittest.TestCase):
    SITE = ROOT / "site"
    PLACEHOLDER = re.compile(r"\{(\w+)\}")

    @classmethod
    def setUpClass(cls):
        cls.langs = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in (cls.SITE / "i18n").glob("*.json")}
        cls.en = cls.langs["en"]

    def placeholders(self, value):
        forms = value.values() if isinstance(value, dict) else [value]
        return {name for form in forms for name in self.PLACEHOLDER.findall(form)}

    def test_languages_match_english(self):
        self.assertIn("sv", self.langs)
        for lang, strings in self.langs.items():
            with self.subTest(lang=lang):
                self.assertEqual(set(strings), set(self.en), "same keys as en.json")
                for key, value in strings.items():
                    self.assertEqual(type(value), type(self.en[key]), key)
                    if isinstance(value, dict):
                        self.assertIn("other", value, key)
                    self.assertEqual(self.placeholders(value), self.placeholders(self.en[key]), key)

    def test_used_keys_exist(self):
        js = (self.SITE / "app.js").read_text(encoding="utf-8")
        html = (self.SITE / "index.html").read_text(encoding="utf-8")
        used = set(re.findall(r'\bt\("([\w.]+)"', js)) | set(re.findall(r'data-i18n(?:-[\w-]+)?="([\w.]+)"', html))
        # Keys built from a prefix and an id.
        used |= {f"mode.{m}" for m in ("tram", "metro", "train", "bus", "ferry")}
        used |= {f"{p}.{d}" for p in ("day", "noService") for d in ("wd", "sa", "su")}
        used |= {"region.sweden", "region.trains"}
        self.assertLessEqual(used, set(self.en), "keys used by the page but missing from en.json")
        self.assertGreater(len(used), 40)


class Config(unittest.TestCase):
    def test_regions_config(self):
        cfg = json.loads((ROOT / "regions.json").read_text(encoding="utf-8"))
        ids = [r["id"] for r in cfg["regions"]]
        self.assertEqual(len(ids), len(set(ids)), "unique region ids")
        self.assertIn(cfg["default"], ids)
        self.assertNotIn(aggregate.SWEDEN_ID, ids, "reserved for the all-of-Sweden view")
        for r in cfg["regions"]:
            self.assertTrue(r["feeds"], r["id"])
            for feed in r["feeds"]:
                self.assertRegex(feed["slug"], r"^[a-z0-9-]+$")
                self.assertIsInstance(feed["mdb"], int)
            for mode in r.get("modes", []):
                self.assertIn(mode, build.MODE_ORDER)
            for colour in r.get("category_colours", {}).values():
                self.assertRegex(colour, r"^#[0-9A-F]{6}$")

    def test_colour_files(self):
        for path in (ROOT / "colors").glob("*.json"):
            data = json.loads(path.read_text(encoding="utf-8"))
            for mode in ("tram", "metro", "train"):
                for colour in data.get(mode, {}).values():
                    self.assertRegex(colour, r"^#[0-9A-F]{6}$", path.name)


@unittest.skipUnless((DATA / "regions.json").exists(), "no built data")
class BuiltData(unittest.TestCase):
    """Integrity of the committed map data: every reference resolves and files stay small enough for git."""

    def test_regions(self):
        index = json.loads((DATA / "regions.json").read_text(encoding="utf-8"))
        self.assertTrue(index["regions"])
        for region in index["regions"]:
            with self.subTest(region=region["id"]):
                rdir = DATA / region["id"]
                routes = json.loads((rdir / "routes.json").read_text(encoding="utf-8"))["routes"]
                ids = {r["id"] for r in routes}
                self.assertEqual(len(ids), len(routes), "unique route ids")
                self.assertEqual(len(routes), region["lines"])
                for r in routes:
                    self.assertRegex(r["color"], r"^#[0-9A-F]{6}$")
                    self.assertIn(r["mode"], build.MODE_ORDER)
                for mode in region["modes"]:
                    for coords, lines in json.loads((rdir / f"{mode}.json").read_text(encoding="utf-8"))["corridors"]:
                        self.assertGreaterEqual(len(coords), 2)
                        self.assertTrue(all(0 <= i < len(routes) and routes[i]["mode"] == mode for i in lines))
                stops = json.loads((rdir / "stops.geojson").read_text(encoding="utf-8"))["features"]
                stations = {f["properties"]["id"] for f in stops if f["properties"]["kind"] == "station"}
                self.assertEqual(len(stations), region["stations"])
                for f in stops:
                    for ref in f["properties"]["routes"]:
                        self.assertTrue(ref in ids if isinstance(ref, str) else 0 <= ref < len(routes))
                for r in routes:
                    self.assertTrue(set(r["stations"]) <= stations, r["id"])

    def test_file_sizes(self):
        for path in DATA.rglob("*"):
            if path.is_file():
                self.assertLess(path.stat().st_size, 50_000_000, f"{path} would trigger GitHub's large-file warning")


if __name__ == "__main__":
    unittest.main()
