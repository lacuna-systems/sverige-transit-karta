import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import netex
import osm


def rail_file(tmp, ways):
    path = Path(tmp) / "rail.json"
    path.write_text(json.dumps({"elements": [{"geometry": [{"lon": x, "lat": y} for x, y in w]} for w in ways]}))
    return path


class RailRouterTest(unittest.TestCase):
    def test_routes_along_track_not_straight(self):
        # An L-shaped track: the path must go via the corner.
        with tempfile.TemporaryDirectory() as tmp:
            way = [(12.0 + i * 0.002, 57.7) for i in range(11)] + [(12.02, 57.7 + i * 0.002) for i in range(1, 11)]
            router = osm.RailRouter(rail_file(tmp, [way]))
            path = router.route((12.0, 57.7), (12.02, 57.72))
            self.assertIsNotNone(path)
            self.assertTrue(any(abs(x - 12.02) < 1e-6 and abs(y - 57.7) < 1e-6 for x, y in path), "passes the corner")

    def test_heals_small_gaps(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = [(12.0 + i * 0.001, 57.7) for i in range(10)]
            b = [(12.0092 + i * 0.001, 57.7) for i in range(10)]  # starts ~17 m after a ends
            router = osm.RailRouter(rail_file(tmp, [a, b]))
            self.assertIsNotNone(router.route((12.0, 57.7), (12.0182, 57.7)))

    def test_rejects_long_detours_and_far_stations(self):
        with tempfile.TemporaryDirectory() as tmp:
            # A long U-shaped track between two points 1 km apart: far longer than 1.5x + 5 km.
            u = [(12.0, 57.7 + i * 0.01) for i in range(30)] + [(12.016, 57.7 + i * 0.01) for i in range(29, -1, -1)]
            router = osm.RailRouter(rail_file(tmp, [u]))
            self.assertIsNone(router.route((12.0, 57.7), (12.016, 57.7)))
            self.assertIsNone(router.route((13.0, 58.0), (12.0, 57.7)), "station far from any track")


class ColoursTest(unittest.TestCase):
    def test_normalise(self):
        self.assertEqual(osm.normalise_colour("#0079c2"), "#0079C2")
        self.assertEqual(osm.normalise_colour("#fff"), "#FFFFFF")
        self.assertEqual(osm.normalise_colour("green"), "#00A261")
        self.assertIsNone(osm.normalise_colour("not a colour"))

    def test_colours_vote_and_map_modes(self):
        relations = [
            {"route": "subway", "ref": "13", "colour": "#e32118"},
            {"route": "subway", "ref": "13", "colour": "#e32118"},
            {"route": "subway", "ref": "13", "colour": "red"},
            {"route": "light_rail", "ref": "27", "colour": "#A05EA6"},
            {"route": "tram", "ref": "1;2", "colour": "#FFFFFF"},
        ]
        out = osm.colours_in_bbox(relations)
        self.assertEqual(out["metro"]["13"], "#E32118")
        self.assertEqual(out["tram"]["27"], "#A05EA6")
        self.assertEqual(out["train"]["27"], "#A05EA6", "light rail may be a train in the feed")
        self.assertEqual(out["tram"]["2"], "#FFFFFF")

    def test_colours_in_bbox_only_uses_overlapping_relations(self):
        # Line "1" is a white tram in Gothenburg and a green tram in Norrköping: each region gets its own.
        relations = [
            {"route": "tram", "ref": "1", "colour": "#FFFFFF", "bounds": [57.6, 11.8, 57.8, 12.1]},
            {"route": "tram", "ref": "1", "colour": "#00A261", "bounds": [58.5, 16.1, 58.7, 16.3]},
        ]
        gothenburg = osm.colours_in_bbox(relations, (57.5, 11.5, 58.0, 12.5))
        norrkoping = osm.colours_in_bbox(relations, (58.4, 15.9, 58.8, 16.5))
        self.assertEqual(gothenburg["tram"]["1"], "#FFFFFF")
        self.assertEqual(norrkoping["tram"]["1"], "#00A261")


NETEX_LINE = """<?xml version="1.0"?><PublicationDelivery xmlns="http://www.netex.org.uk/netex"><dataObjects>
<Line version="1" id="SE:012:Line:{rid}"><Name>X</Name>{presentation}<PublicCode>{code}</PublicCode></Line>
</dataObjects></PublicationDelivery>"""
NETEX_SHARED = """<?xml version="1.0"?><PublicationDelivery xmlns="http://www.netex.org.uk/netex"><dataObjects>
<GroupOfLines version="1" id="SE:012:GroupOfLines:1"><Name>Stadsbuss Malmö</Name><members>
<LineRef ref="SE:012:Line:9011012000600000"/><LineRef ref="SE:012:Line:9011012000700000"/></members></GroupOfLines>
<GroupOfLines version="1" id="SE:012:GroupOfLines:2"><Name>Pågatåg</Name><members>
<LineRef ref="SE:012:Line:9011012081700000"/></members></GroupOfLines>
</dataObjects></PublicationDelivery>"""


class NetexTest(unittest.TestCase):
    def make_zip(self, tmp):
        path = Path(tmp) / "netex.zip"
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("_shared_data.xml", NETEX_SHARED)
            z.writestr("line_6.xml", NETEX_LINE.format(rid="9011012000600000", presentation="", code="6"))
            z.writestr("line_7.xml", NETEX_LINE.format(
                rid="9011012000700000", code="7",
                presentation="<Presentation><Colour>ff0000</Colour><TextColour>FFFFFF</TextColour></Presentation>"))
        return path

    def test_extract_groups_and_presentation(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = netex.extract(self.make_zip(tmp))
        self.assertEqual(data["groups"]["9011012000600000"], "Stadsbuss Malmö")
        self.assertEqual(data["groups"]["9011012081700000"], "Pågatåg")
        self.assertEqual(data["colours"], {"9011012000700000": "#FF0000"})

    def test_route_colours_presentation_beats_category(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = netex.extract(self.make_zip(tmp))
        cfg = {"category_colours": {"Stadsbuss": "#3EA827", "Pågatåg": "#716CB8"}}
        out = netex.route_colours(cfg, data)
        self.assertEqual(out["9011012000600000"], "#3EA827")
        self.assertEqual(out["9011012000700000"], "#FF0000")
        self.assertEqual(out["9011012081700000"], "#716CB8")

    def test_group_colour_prefers_longest_prefix(self):
        table = {"Stadsbuss": "#1", "Stadsbuss Malmö": "#2"}
        self.assertEqual(netex.group_colour("Stadsbuss Malmö", table), "#2")
        self.assertEqual(netex.group_colour("Stadsbuss Lund", table), "#1")
        self.assertIsNone(netex.group_colour("Närtrafik", table))

    def test_api_key_from_env_file(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict("os.environ", {}, clear=True), \
                mock.patch.object(netex, "ROOT", Path(tmp)):
            (Path(tmp) / ".env").write_text('OTHER=1\nTRAFIK_NETEX_KEY="0123456789abcdef0123456789abcdef"Tr\n')
            self.assertEqual(netex.api_key(), "0123456789abcdef0123456789abcdef")
        with mock.patch.dict("os.environ", {"TRAFIKLAB_NETEX_KEY": "fromenv"}):
            self.assertEqual(netex.api_key(), "fromenv")

    def test_download_errors_hide_the_key(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch("urllib.request.urlopen", side_effect=OSError("boom key=SECRETKEY")), \
                self.assertRaises(RuntimeError) as ctx:
            netex.download("sl", Path(tmp) / "x.zip", "SECRETKEY")
        self.assertNotIn("SECRETKEY", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
