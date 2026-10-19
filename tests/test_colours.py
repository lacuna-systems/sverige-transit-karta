import colorsys
import unittest

import colours


class ModeShades(unittest.TestCase):
    def test_shades_keep_hue_and_stand_apart(self):
        for base in ("#0079B4", "#F1C800", "#E10E1C", "#003A70"):
            shades = colours.mode_shades(base, 4)
            self.assertEqual(shades[0], base)
            self.assertEqual(len(set(shades)), 4)
            hue = lambda c: colorsys.rgb_to_hls(*(int(c[i:i + 2], 16) / 255 for i in (1, 3, 5)))[0]  # noqa: E731
            for s in shades:
                self.assertAlmostEqual(hue(s), hue(base), delta=0.035, msg=s)
            for s in shades[1:]:
                for bg in colours.MAP_BACKGROUNDS:
                    self.assertGreaterEqual(colours.contrast(s, bg), colours.SHADE_MIN_CONTRAST, f"{s} on {bg}")
            self.assertGreater(min(colours.contrast(a, b) for i, a in enumerate(shades) for b in shades[i + 1:]), 1.3)

    def test_contrast(self):
        self.assertAlmostEqual(colours.contrast("#000000", "#FFFFFF"), 21, places=1)
        self.assertEqual(colours.contrast("#123456", "#123456"), 1)


if __name__ == "__main__":
    unittest.main()
