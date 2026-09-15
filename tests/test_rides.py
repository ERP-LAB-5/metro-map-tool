# SPDX-License-Identifier: GPL-3.0-or-later
"""Rides routed from a start to an end, and swimlanes.

A ride used to be every stop typed out in order. These pin down what the track
now decides instead: which way it goes, where it waits, where it rides through,
and how it gets on and off the map. Swimlanes are here too, as the other thing
3.5 draws that an older copy of the tool could not.
"""

import copy
import json
import re
import unittest

from metro_map_tool import metro_map as mm


def network():
    """Main runs a–d past both edges, with a loop via x between two junctions;
    Cross leaves Main at Bravo and rejoins at Charlie, down in the Field rows."""
    return {
        "stations": {
            "a": {"label": "Alpha", "gx": 1, "gy": 1},
            "b": {"label": "Bravo", "gx": 3, "gy": 1},
            "c": {"label": "Charlie", "gx": 5, "gy": 1},
            "d": {"label": "Delta", "gx": 7, "gy": 1},
            "x": {"label": "X-ray", "gx": 4, "gy": 2},
            "p": {"label": "Papa", "gx": 3, "gy": 3},
            "q": {"label": "Quebec", "gx": 5, "gy": 3}},
        "junctions": {"j1": {"gx": 2, "gy": 1}, "j2": {"gx": 6, "gy": 1}},
        "lines": [
            {"name": "Main", "color": "#0098d4", "continues": "both",
             "stations": ["a", "j1", "b", "c", "j2", "d"],
             "branches": [{"name": "Loop", "stations": ["j1", "x", "j2"]}]},
            {"name": "Cross", "color": "#e1251b", "stations": ["b", "p", "q", "c"]}],
    }


def with_rides(*rides, **extra):
    spec = network()
    spec["scenarios"] = list(rides)
    spec.update(extra)
    return spec


def route_of(spec, index=0):
    return mm.ride_report(spec, mm.Style())[index]


class RoutingTest(unittest.TestCase):
    def test_start_to_end_takes_the_straight_way(self):
        got = route_of(with_rides({"name": "r", "from": "a", "to": "d"}))
        self.assertEqual(got["route"], ["a", "j1", "b", "c", "j2", "d"])
        self.assertEqual([s["id"] for s in got["stops"]], ["a", "b", "c", "d"])
        self.assertEqual(got["problems"], [])

    def test_a_via_sends_it_down_the_branch(self):
        got = route_of(with_rides({"name": "r", "from": "a", "to": "d", "via": ["x"]}))
        self.assertEqual(got["route"], ["a", "j1", "x", "j2", "d"])

    def test_a_junction_can_be_the_via(self):
        got = route_of(with_rides({"name": "r", "from": "b", "to": "x", "via": ["j1"]}))
        self.assertEqual(got["route"][:2], ["b", "j1"])
        self.assertEqual(got["route"][-1], "x")

    def test_it_stays_aboard_rather_than_changing_for_a_similar_way(self):
        got = route_of(with_rides({"name": "r", "from": "b", "to": "c"}))
        self.assertEqual(got["route"], ["b", "c"])                 # Main, not Cross
        self.assertFalse(any(s["change"] for s in got["stops"]))

    def test_a_change_of_line_is_named_where_it_happens(self):
        got = route_of(with_rides({"name": "r", "from": "a", "to": "q"}))
        changes = {s["id"]: s["change"] for s in got["stops"] if s["change"]}
        self.assertEqual(list(changes.values()), ["Cross"])

    def test_a_stop_marked_to_pass_is_ridden_through(self):
        got = route_of(with_rides({"name": "r", "from": "a", "to": "d", "pass": ["b"]}))
        jumps = {s["id"]: s["jump"] for s in got["stops"]}
        self.assertTrue(jumps["b"])
        self.assertFalse(jumps["c"])

    def test_it_can_arrive_from_past_the_map_and_leave_past_it(self):
        spec = with_rides(
            {"name": "in", "from": {"line": "Main", "edge": "start"}, "to": "b"},
            {"name": "out", "from": "c", "to": {"line": "Main", "edge": "end"}})
        arriving, leaving = mm.ride_report(spec, mm.Style())
        self.assertTrue(arriving["route"][0].startswith("@"))
        self.assertGreater(arriving["stops"][0]["at"], 0)         # rolls in first
        self.assertTrue(leaving["route"][-1].startswith("@"))
        self.assertLess(leaving["stops"][-1]["at"], 1)            # rolls out after
        self.assertEqual(arriving["problems"] + leaving["problems"], [])

    def test_an_end_that_does_not_run_past_the_map_is_a_problem(self):
        got = route_of(with_rides({"name": "r", "from": "a",
                                   "to": {"line": "Cross", "edge": "end"}}))
        self.assertIsNone(got["d"] or None)
        self.assertTrue(got["problems"])

    def test_no_track_between_two_points_is_said_not_crashed_on(self):
        spec = with_rides({"name": "r", "from": "a", "to": "lonely"})
        spec["stations"]["lonely"] = {"label": "Lonely", "gx": 9, "gy": 9}
        got = route_of(spec)
        self.assertIn("no track joins", got["problems"][0])
        self.assertTrue(any("no track joins" in w for w in mm.spec_warnings(spec)))

    def test_a_ride_without_an_end_yet_is_a_warning_not_an_error(self):
        spec = with_rides({"name": "new", "dwell": 1.5})
        self.assertEqual(mm.validate_spec(spec), [])
        self.assertTrue(any("choose where" in w for w in mm.spec_warnings(spec)))

    def test_stops_along_the_path_run_forward_to_the_end(self):
        got = route_of(with_rides({"name": "r", "from": "a", "to": "d", "via": ["x"]}))
        ats = [s["at"] for s in got["stops"]]
        self.assertEqual(ats, sorted(ats))
        self.assertEqual((ats[0], ats[-1]), (0.0, 1.0))


class ValidationTest(unittest.TestCase):
    def test_the_new_fields_are_checked(self):
        spec = with_rides({"name": "r", "from": "nowhere", "to": {"edge": "sideways"},
                           "via": "x", "pass": ["j1"], "dwell": 99, "hidden": "yes"})
        said = " ".join(mm.validate_spec(spec))
        for part in ("'nowhere'", '"edge"', "via must be a list", "pass 'j1'",
                     "dwell", "hidden"):
            self.assertIn(part, said)


class AnimationTest(unittest.TestCase):
    def test_a_routed_ride_waits_at_its_stops(self):
        svg = mm.render(with_rides({"name": "r", "from": "a", "to": "d", "dwell": 2}),
                        mm.Style())
        frames = re.findall(r"([\d.]+)% \{ offset-distance: ([\d.]+)%", svg)
        distances = [d for _, d in frames]
        # each stop appears twice in a row: arrive, then leave after the wait
        self.assertTrue(any(a == b for a, b in zip(distances, distances[1:])))
        self.assertIn("@keyframes ride0", svg)

    def test_a_hidden_ride_is_left_out_of_the_file(self):
        svg = mm.render(with_rides({"name": "r", "from": "a", "to": "d", "hidden": True}),
                        mm.Style())
        self.assertNotIn("traveller", svg)

    def test_a_ride_in_the_old_shape_draws_exactly_as_before(self):
        old = with_rides({"name": "r", "stations": ["b", "c"], "duration": 6})
        svg = mm.render(old, mm.Style())
        self.assertIn("@keyframes ride {", svg)
        self.assertNotIn("@keyframes ride0", svg)
        self.assertEqual(mm.needs_format(old), 2)                  # junctions, not rides

    def test_routed_rides_need_format_3(self):
        self.assertEqual(mm.needs_format(with_rides({"name": "r", "from": "a", "to": "d"})), 3)


class SwimlaneTest(unittest.TestCase):
    def lanes(self, *lanes):
        return with_rides(swimlanes=list(lanes))

    def test_a_lane_draws_a_named_band_and_needs_format_3(self):
        spec = self.lanes({"name": "Core", "rows": [1, 2]}, {"name": "Field", "rows": [3, 3],
                                                            "color": "#00a4a7"})
        self.assertEqual(mm.validate_spec(spec), [])
        svg = mm.render(spec, mm.Style())
        self.assertIn('id="swimlanes"', svg)
        self.assertIn(">Core</text>", svg)
        self.assertIn("--lc: #00a4a7", svg)
        self.assertEqual(mm.needs_format(spec), 3)

    def test_the_name_gutter_widens_the_drawing(self):
        plain = mm.render(network(), mm.Style())
        laned = mm.render(self.lanes({"name": "A long lane name", "rows": [1, 3]}), mm.Style())
        width = lambda svg: float(re.search(r'width="([\d.]+)"', svg).group(1))
        self.assertGreater(width(laned), width(plain))

    def test_a_bad_lane_is_refused_with_a_sentence(self):
        said = " ".join(mm.validate_spec(self.lanes({"name": "", "rows": [3, 1],
                                                     "color": "red"})))
        for part in ("non-empty name", "comes after", "#rrggbb"):
            self.assertIn(part, said)

    def test_overlap_and_strays_are_warnings(self):
        spec = self.lanes({"name": "One", "rows": [1, 2]}, {"name": "Two", "rows": [2, 2]})
        said = " ".join(mm.spec_warnings(spec))
        self.assertIn("overlap", said)
        self.assertIn("outside every swimlane", said)          # Papa and Quebec on row 3


class ShippedMapsTest(unittest.TestCase):
    def test_nothing_shipped_earns_the_new_format(self):
        import pathlib
        root = pathlib.Path(mm.__file__).resolve().parent / "shared-maps"
        for path in sorted(root.glob("*.json")):
            spec = json.loads(path.read_text(encoding="utf-8"))
            self.assertLess(mm.needs_format(copy.deepcopy(spec)), 3, path.name)


if __name__ == "__main__":
    unittest.main()
