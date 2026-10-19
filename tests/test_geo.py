import math
import unittest

import geo


class Distances(unittest.TestCase):
    def test_metres_one_degree_of_latitude(self):
        self.assertAlmostEqual(geo.metres((12.0, 57.0), (12.0, 58.0)), 110540, delta=10)

    def test_point_to_polyline(self):
        line = [(11.9, 57.7), (11.91, 57.7)]
        self.assertAlmostEqual(geo.point_to_polyline_m((11.905, 57.701), line), 110.5, delta=1)

    def test_length_and_gap(self):
        total, gap = geo.length_and_gap([(11.9, 57.7), (11.9, 57.701), (11.9, 57.704)])
        self.assertAlmostEqual(total, 442, delta=2)
        self.assertAlmostEqual(gap, 331.6, delta=1)


class Simplify(unittest.TestCase):
    def test_straight_line_collapses_to_ends(self):
        pts = [(11.9 + i * 0.001, 57.7) for i in range(50)]
        self.assertEqual(geo.simplify(pts, 1.0), [pts[0], pts[-1]])

    def test_keeps_corners(self):
        pts = [(11.9, 57.7), (11.91, 57.7), (11.92, 57.7), (11.92, 57.71), (11.92, 57.72)]
        self.assertIn((11.92, 57.7), geo.simplify(pts, 1.0))

    def test_max_step_bounds_long_steps(self):
        pts = [(11.9 + i * 0.001, 57.7) for i in range(200)]  # ~12 km straight line, a point every ~60 m
        out = geo.simplify(pts, 1.0, max_step_m=1000)
        self.assertLess(geo.length_and_gap(out)[1], 1100)
        self.assertLess(len(out), len(pts))


class ProjectStops(unittest.TestCase):
    def test_monotonic_positions(self):
        pts = [(11.9 + i * 0.001, 57.7, None, i * 59.5) for i in range(21)]
        dists = geo.project_stops(pts, [(11.9, 57.7001), (11.91, 57.7001), (11.92, 57.6999)])
        self.assertAlmostEqual(dists[0], 0, delta=1)
        self.assertAlmostEqual(dists[1], 595, delta=2)
        self.assertAlmostEqual(dists[2], 1190, delta=2)

    def test_loop_revisits_later(self):
        # Out and back along the same street: the second visit to the start must project to the end.
        out = [(11.9 + i * 0.001, 57.7) for i in range(11)]
        coords = out + out[-2::-1]
        pts, d = [], 0.0
        for i, c in enumerate(coords):
            if i:
                d += geo.metres(coords[i - 1], c)
            pts.append((c[0], c[1], None, d))
        dists = geo.project_stops(pts, [(11.9, 57.7), (11.91, 57.7), (11.9, 57.7)])
        self.assertLess(dists[0], dists[1])
        self.assertLess(dists[1], dists[2])


class PercentileBbox(unittest.TestCase):
    def test_outliers_are_ignored(self):
        points = [(12.0 + i * 0.001, 57.0 + i * 0.001) for i in range(100)] + [(2.35, 48.85)]  # ... and Paris
        west, south, east, north = geo.percentile_bbox(points)
        self.assertGreater(west, 12.0)
        self.assertGreater(south, 57.0)
        self.assertLess(east, 12.1)
        self.assertLess(north, 57.1)


class SmoothBridge(unittest.TestCase):
    def test_bridge_follows_both_stretches(self):
        # Two parallel eastbound stretches offset 30 m sideways (a line switching to the other
        # direction's platform): the bridge must leave and arrive heading east, not cut across.
        dy = 30 / 110540.0
        c1 = [(12.0, 57.7), (12.001, 57.7)]
        c2 = [(12.0012, 57.7 + dy), (12.0025, 57.7 + dy)]
        path = geo.smooth_bridge(c1, c1[-1], c2, c2[0])
        self.assertEqual(path[0], c1[-1])
        self.assertEqual(path[-1], c2[0])
        self.assertGreater(len(path), 2)
        xs = [p[0] for p in path]
        self.assertEqual(xs, sorted(xs), "keeps heading east")
        # Leaves far flatter (closer to c1's direction) than the straight chord would.
        kx = 111320.0 * math.cos(math.radians(57.7))
        slope = lambda a, b: (b[1] - a[1]) * 110540.0 / ((b[0] - a[0]) * kx)  # noqa: E731
        self.assertLess(slope(path[0], path[1]), 0.5 * slope(path[0], path[-1]))

    def test_doubling_back_falls_back_to_a_straight_join(self):
        c1 = [(12.0, 57.7), (12.001, 57.7)]
        c2 = [(12.0011, 57.7), (12.0, 57.7001)]  # heads back west
        path = geo.smooth_bridge(c1, c1[-1], c2[::-1], c2[0])
        self.assertTrue(len(path) >= 2 and path[0] == c1[-1] and path[-1] == c2[0])


class Converge(unittest.TestCase):
    def test_converge_bends_only_the_end(self):
        line = [(12.0 + i * 0.001, 57.7) for i in range(11)]  # ~600 m eastwards, points every ~60 m
        target = (12.0, 57.7 + 30 / 110540.0)  # 30 m north of the start
        out = geo.converge(line, target, at_end=False)
        self.assertEqual(out[0], target)
        self.assertEqual(out[-1], line[-1], "the far end stays put")
        self.assertTrue(all(abs(p[1] - 57.7) < 1e-9 for p in out if p[0] >= 12.002), "unchanged beyond the bend")
        ys = [p[1] for p in out]
        self.assertEqual(ys, sorted(ys, reverse=True), "eases monotonically back onto the path")
        self.assertGreater(len(out), len(line), "points added along the bend")
        back = geo.converge(line[::-1], target, at_end=True)
        self.assertEqual(back[::-1], out, "the same bend from either end")


if __name__ == "__main__":
    unittest.main()
