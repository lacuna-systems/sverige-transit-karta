import unittest

import stations


class StationMerging(unittest.TestCase):
    def test_station_key(self):
        self.assertEqual(stations.station_key("Skövde resecentrum (tåg)"), "skövde")
        self.assertEqual(stations.station_key("Halmstad C"), stations.station_key("Halmstad Resecentrum"))
        self.assertEqual(stations.station_key("Station"), "station", "a name is never emptied")
        self.assertNotEqual(stations.station_key("Halmby 2"), stations.station_key("Halmby"))
        self.assertNotEqual(stations.station_key("Evenås västra"), stations.station_key("Evenås östra"))

    def test_name_variants(self):
        names = ["Nils Ericson Terminalen", "Göteborg Nils Ericsonterminal", "Göteborg C", "Göteborg Central",
                 "Göteborg Centralen", "Göteborg Centralstation", "Nils Ericsonsplatsen", "Alingsås station",
                 "Alingsåsterminalen", "T-Centralen", "Centralen"]
        towns = stations.town_names(names)
        self.assertIn("göteborg", towns)
        key = {n: stations.station_key(n, towns) for n in names}
        self.assertEqual(key["Nils Ericson Terminalen"], key["Göteborg Nils Ericsonterminal"])
        self.assertEqual(len({key[n] for n in names if n.startswith("Göteborg C")}), 1)
        self.assertEqual(key["Göteborg C"], "göteborg")
        self.assertNotEqual(key["Göteborg C"], key["Nils Ericson Terminalen"], "neighbouring but different stations")
        self.assertNotEqual(key["Nils Ericsonsplatsen"], key["Nils Ericson Terminalen"])
        self.assertEqual(key["Alingsås station"], key["Alingsåsterminalen"])
        self.assertEqual(key["T-Centralen"], "tcentralen", "never shortened below three letters")

    def test_genitive_town_names(self):
        names = ["Stockholm Central", "Stockholms central", "Stockholms Centralstation", "Stockholm Odenplan",
                 "Odenplan", "Borås C", "Borås resecentrum", "Borås Central"]
        towns = stations.town_names(names)
        key = {n: stations.station_key(n, towns) for n in names}
        self.assertEqual(len({key[n] for n in names[:3]}), 1, key)
        self.assertEqual(key["Stockholm Central"], "stockholm")
        self.assertEqual(key["Stockholm Odenplan"], key["Odenplan"])
        self.assertEqual(len({key[n] for n in names[5:]}), 1, "towns that end in s still match each other")

    def test_cluster_by_name_and_distance(self):
        dlat = 100 / 110540.0  # 100 m north
        places = {
            "a": ("Aröd", (12.0, 57.0), 3), "b": ("Aröd terminal", (12.0, 57.0 + dlat), 5),
            "c": ("Aröd station", (12.0, 57.0 + 2 * dlat), 1),  # 200 m from a, 100 m from b
            "d": ("Aröd", (12.0, 57.0 + 30 * dlat), 9),  # same name, 3 km away: another place
            "e": ("Berg", (12.0, 57.0 + dlat / 2), 1),
        }
        alias = stations.cluster_stations(places)
        self.assertEqual(alias, {"a": "b", "b": "b", "c": "b"}, "busiest member represents the group")
        # Chains are not allowed to grow past the limit: with 150 m, c is 200 m from a.
        alias = stations.cluster_stations(places, max_m=150)
        self.assertEqual(len(alias), 2, "only one pair fits within 150 m")
        self.assertNotEqual(alias.get("c"), alias.get("a"))


if __name__ == "__main__":
    unittest.main()
