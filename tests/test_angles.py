# SPDX-License-Identifier: GPL-3.0-or-later
"""Free angles: an opt-in spec field that draws each hop as one straight leg.

Octilinear stays the default and every existing map draws exactly as before;
"angles": "free" is for generated maps that want to be compact (Metrofy)."""

import re
import unittest

from metro_map_tool import metro_map as mm


def spec(**extra):
    s = {"stations": {"a": {"label": "A", "gx": 0, "gy": 0}, "b": {"label": "B", "gx": 1, "gy": 2}},
         "lines": [{"name": "L", "color": "#0098d4", "stations": ["a", "b"]}]}
    s.update(extra)
    return s


def route_d(svg):
    return re.search(r'<path class="route[^"]*" d="([^"]+)"', svg).group(1)


class FreeAngles(unittest.TestCase):
    def test_octilinear_bends_a_hop_that_is_not_on_a_45(self):
        d = route_d(mm.render(spec(), mm.Style()))
        self.assertGreaterEqual(d.count("L") + d.count("Q"), 2)    # a straight leg, then the diagonal

    def test_free_draws_the_hop_as_one_straight_leg(self):
        d = route_d(mm.render(spec(angles="free"), mm.Style()))
        self.assertEqual(d.count("Q"), 0)
        self.assertEqual(d.count("L"), 1)

    def test_free_is_format_4_and_the_default_is_unchanged(self):
        self.assertEqual(mm.needs_format(spec(angles="free")), 4)
        self.assertEqual(mm.needs_format(spec()), 1)
        self.assertEqual(mm.needs_format(spec(angles="octilinear")), 1)

    def test_only_the_two_values_are_accepted(self):
        self.assertEqual(mm.validate_spec(spec(angles="free")), [])
        self.assertTrue(any("spec.angles must be one of" in e for e in mm.validate_spec(spec(angles="curvy"))))

    def test_parallel_free_lines_are_still_bundled(self):
        s = spec(angles="free")
        s["lines"].append({"name": "M", "color": "#9b0058", "stations": ["a", "b"]})
        svg = mm.render(s, mm.Style())
        ds = re.findall(r'<path class="route[^"]*" d="([^"]+)"', svg)
        self.assertEqual(len(ds), 2)
        self.assertNotEqual(ds[0], ds[1])                          # offset from each other, not on top


class Miters(unittest.TestCase):
    def test_nearly_parallel_legs_on_different_tracks_do_not_shoot_off(self):
        # two legs a few degrees apart, pushed onto different tracks
        legs = [((0.0, 0.0), (100.0, 0.0)), ((100.0, 6.0), (200.0, 9.0))]
        pts = mm.join_legs(legs)
        self.assertTrue(all(-10 <= p[0] <= 210 and -10 <= p[1] <= 20 for p in pts), pts)

    def test_a_real_corner_is_still_joined_at_its_crossing(self):
        legs = [((0.0, 0.0), (100.0, 0.0)), ((100.0, 5.0), (100.0, 100.0))]
        self.assertIn((100.0, 0.0), [tuple(round(c, 6) for c in p) for p in mm.join_legs(legs)])


if __name__ == "__main__":
    unittest.main()
