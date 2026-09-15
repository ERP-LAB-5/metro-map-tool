# SPDX-License-Identifier: GPL-3.0-or-later
"""Exporting some of a map's swimlanes and phases.

The cut is made on a copy of the spec (ADR 0014). These pin what it leaves:
the chosen lanes stacked, the ruler narrowed to the chosen phases, and every
line, zone, join and ride holding only what is still on the map.
"""

import json
import unittest

from metro_map_tool import app as designer
from metro_map_tool import cut
from metro_map_tool import metro_map as mm


def plan():
    """Three lanes of two rows each, a monthly ruler over 2027, two phases.

    Main runs across all three lanes' worth of time in the top lane and drops
    into the bottom lane; Side stays in the middle lane."""
    return {
        "mode": "roadmap",
        "timeline": {"start": "2027-01-01", "end": "2027-12-31", "interval": "month"},
        "phases": [{"name": "Build", "from": "2027-01-01", "to": "2027-06-30"},
                   {"name": "Run", "from": "2027-07-01", "to": "2027-12-31"}],
        "swimlanes": [{"name": "Top", "rows": [0, 1]},
                      {"name": "Middle", "rows": [2, 3]},
                      {"name": "Bottom", "rows": [4, 5]}],
        "stations": {
            "a": {"label": "A", "gx": 1.5, "gy": 1},
            "b": {"label": "B", "gx": 4.5, "gy": 1},
            "c": {"label": "C", "gx": 8.5, "gy": 1},
            "d": {"label": "D", "gx": 10.5, "gy": 5},
            "s1": {"label": "S1", "gx": 2.5, "gy": 3},
            "s2": {"label": "S2", "gx": 9.5, "gy": 3}},
        "lines": [
            {"name": "Main", "color": "#0098d4", "stations": ["a", "b", "c", "d"],
             "notes": [{"at": 1, "text": "b to c"}, {"at": 2, "text": "c to d"}]},
            {"name": "Side", "color": "#e1251b", "stations": ["s1", "s2"]}],
        "zones": [{"name": "Z", "color": "#00a4a7", "stations": ["a", "s1"]}],
        "interchanges": [{"stations": ["c", "s2"]}],
        "scenarios": [{"name": "whole", "from": "a", "to": "d"},
                      {"name": "late", "from": "c", "to": "d"}],
    }


class LaneTest(unittest.TestCase):
    def test_all_of_everything_draws_exactly_the_map(self):
        spec = plan()
        for lanes, phases in ((None, None), (["Top", "Middle", "Bottom"], ["Run", "Build"])):
            got = cut.cut(spec, lanes, phases)
            self.assertEqual(mm.render(got, mm.Style()), mm.render(plan(), mm.Style()))

    def test_one_lane_keeps_its_stations_and_its_band_only(self):
        got = cut.cut(plan(), ["Top"])
        self.assertEqual(sorted(got["stations"]), ["a", "b", "c"])
        self.assertEqual([ln["name"] for ln in got["swimlanes"]], ["Top"])
        self.assertEqual([ln["name"] for ln in got["lines"]], ["Main"])
        self.assertEqual(got["interchanges"], [])
        self.assertEqual(mm.validate_spec(got), [])

    def test_lanes_not_chosen_close_up(self):
        got = cut.cut(plan(), ["Top", "Bottom"])
        self.assertEqual(got["stations"]["d"]["gy"], 3)            # was 5, Middle's 2 rows gone
        self.assertEqual(got["swimlanes"][1]["rows"], [2, 3])
        self.assertEqual(got["stations"]["a"]["gy"], 1)

    def test_a_line_cut_short_runs_on_past_the_map(self):
        got = cut.cut(plan(), ["Top"])
        main = got["lines"][0]
        self.assertEqual(main["stations"], ["a", "b", "c"])
        self.assertEqual(main["continues"], "end")
        self.assertEqual(main["notes"], [{"at": 1, "text": "b to c"}])

    def test_the_spec_given_is_not_touched(self):
        spec = plan()
        before = json.dumps(spec, sort_keys=True)
        cut.cut(spec, ["Bottom"], ["Run"])
        self.assertEqual(json.dumps(spec, sort_keys=True), before)


class PhaseTest(unittest.TestCase):
    def test_a_later_phase_narrows_the_ruler_and_moves_the_columns(self):
        got = cut.cut(plan(), phases=["Run"])
        self.assertEqual(sorted(got["stations"]), ["c", "d", "s2"])
        self.assertEqual(got["timeline"]["start"], "2027-07-01")
        self.assertEqual(got["stations"]["c"]["gx"], 2.5)            # September, now column 2
        self.assertEqual(mm.spec_timeline(got).columns, 6)
        self.assertEqual([ph["name"] for ph in got["phases"]], ["Run"])
        self.assertEqual(got["lines"][0]["continues"], "start")
        self.assertEqual(mm.validate_spec(got), [])

    def test_lanes_and_phases_cut_together(self):
        got = cut.cut(plan(), ["Middle"], ["Build"])
        self.assertEqual(sorted(got["stations"]), ["s1"])
        self.assertEqual(got["zones"][0]["stations"], ["s1"])

    def test_a_ride_keeps_going_only_while_its_ends_are_there(self):
        got = cut.cut(plan(), phases=["Run"])
        self.assertEqual([sc["name"] for sc in got["scenarios"]], ["late"])

    def test_the_picture_is_drawn_from_the_cut(self):
        svg = mm.render(cut.cut(plan(), phases=["Run"]), mm.Style())
        self.assertIn(">Run</text>", svg)
        self.assertNotIn(">Build</text>", svg)
        self.assertNotIn(">A</text>", svg)


class RefusalTest(unittest.TestCase):
    def test_names_that_are_not_there_are_said(self):
        with self.assertRaises(ValueError) as said:
            cut.cut(plan(), ["Nowhere"], ["Never"])
        self.assertIn("no swimlane is called 'Nowhere'", str(said.exception))
        self.assertIn("no phase is called 'Never'", str(said.exception))

    def test_choosing_nothing_is_refused(self):
        self.assertIn("choose at least one swimlane", " ".join(cut.problems(plan(), [], None)))

    def test_phases_on_a_metro_map_are_refused(self):
        spec = plan()
        spec["mode"] = "metro"
        self.assertTrue(cut.problems(spec, None, ["Run"]))


class EndpointTest(unittest.TestCase):
    def setUp(self):
        designer.app.config["TESTING"] = True
        self.client = designer.app.test_client()

    def test_render_takes_a_cut(self):
        got = self.client.post("/api/render", json={
            "spec": plan(), "cut": {"swimlanes": ["Top"]}}).get_json()
        self.assertIn(">A</text>", got["svg"])
        self.assertNotIn(">S1</text>", got["svg"])

    def test_a_bad_cut_is_a_400_with_the_reason(self):
        got = self.client.post("/api/render", json={
            "spec": plan(), "cut": {"phases": ["Never"]}})
        self.assertEqual(got.status_code, 400)
        self.assertIn("no phase is called 'Never'", " ".join(got.get_json()["errors"]))


class CommandLineTest(unittest.TestCase):
    def test_lane_and_phase_flags_cut_the_drawing(self):
        import tempfile, os, contextlib, io
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "plan.json")
            out = os.path.join(tmp, "plan.svg")
            with open(src, "w") as fh:
                json.dump(plan(), fh)
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(mm.main([src, "--lane", "Top", "--phase", "Run", "-o", out]), 0)
                self.assertEqual(mm.main([src, "--lane", "Nope", "-o", out]), 2)
            svg = open(out).read()
        self.assertIn(">C</text>", svg)
        self.assertNotIn(">B</text>", svg)


if __name__ == "__main__":
    unittest.main()
