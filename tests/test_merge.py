# SPDX-License-Identifier: GPL-3.0-or-later
"""Re-syncing must not undo an afternoon's arranging.

Every test here is a way that could go wrong quietly, which is the kind of
wrong that loses somebody's work without anyone noticing until much later.
"""

import copy
import unittest

from metro_map_tool.sources.merge import merge

STAMP = {"name": "jira", "at": "2026-01-01T00:00:00Z", "tool": "t", "options": {}}


def imported(**over):
    spec = {
        "stations": {"a": {"label": "Alpha", "gx": 0, "gy": 0, "origin": "jira:A"},
                     "b": {"label": "Beta", "gx": 1, "gy": 1, "origin": "jira:B"}},
        "lines": [{"name": "One", "color": "#111111", "stations": ["a", "b"],
                   "origin": "jira:lane/One"}],
        "legend": "bottom",
    }
    spec.update(over)
    return spec


class FirstImportTest(unittest.TestCase):
    def test_with_no_model_the_import_is_the_map(self):
        spec, notes = merge(None, imported(), source="jira", stamp=STAMP)
        self.assertEqual(spec["source"], STAMP)
        self.assertEqual(notes, [])


class ResyncTest(unittest.TestCase):
    def setUp(self):
        self.mine, _ = merge(None, imported(), source="jira", stamp=STAMP)
        self.mine = copy.deepcopy(self.mine)
        self.mine["stations"]["a"].update(gx=9, gy=9, label="My words")
        self.mine["lines"][0].update(color="#ff00aa", name="My line")

    def test_position_colour_and_wording_all_survive(self):
        spec, _ = merge(self.mine, imported(), source="jira", stamp=STAMP)
        self.assertEqual(spec["stations"]["a"]["gx"], 9)
        self.assertEqual(spec["stations"]["a"]["label"], "My words")
        self.assertEqual(spec["lines"][0]["color"], "#ff00aa")
        self.assertEqual(spec["lines"][0]["name"], "My line")

    def test_drift_upstream_is_reported_rather_than_applied(self):
        _, notes = merge(self.mine, imported(), source="jira", stamp=STAMP)
        self.assertTrue(any("still says" in n for n in notes))

    def test_refresh_is_the_way_to_ask_for_the_upstream_value(self):
        spec, _ = merge(self.mine, imported(), source="jira",
                        refresh=["label", "gx"], stamp=STAMP)
        self.assertEqual(spec["stations"]["a"]["label"], "Alpha")
        self.assertEqual(spec["stations"]["a"]["gx"], 0)
        self.assertEqual(spec["stations"]["a"]["gy"], 9)   # not asked for

    def test_the_authors_station_id_is_what_survives(self):
        # renaming in the designer keeps the origin, and the merge must follow
        mine = copy.deepcopy(self.mine)
        mine["stations"]["my-own-id"] = mine["stations"].pop("a")
        mine["lines"][0]["stations"] = ["my-own-id", "b"]
        spec, _ = merge(mine, imported(), source="jira", stamp=STAMP)
        self.assertIn("my-own-id", spec["stations"])
        self.assertNotIn("a", spec["stations"])
        self.assertEqual(spec["lines"][0]["stations"], ["my-own-id", "b"])

    def test_something_gone_from_upstream_is_kept_and_reported(self):
        thinner = imported()
        thinner["stations"].pop("b")
        thinner["lines"][0]["stations"] = ["a"]
        spec, notes = merge(self.mine, thinner, source="jira", stamp=STAMP)
        self.assertIn("b", spec["stations"])
        self.assertTrue(any("no longer upstream" in n for n in notes))

    def test_prune_is_what_removes_it_and_it_leaves_nothing_dangling(self):
        thinner = imported()
        thinner["stations"].pop("b")
        thinner["lines"][0]["stations"] = ["a"]
        spec, _ = merge(self.mine, thinner, source="jira", prune=True, stamp=STAMP)
        self.assertNotIn("b", spec["stations"])
        for line in spec["lines"]:
            self.assertNotIn("b", line["stations"])

    def test_another_sources_work_is_never_touched(self):
        mine = copy.deepcopy(self.mine)
        mine["stations"]["g1"] = {"label": "A commit", "gx": 5, "gy": 3,
                                  "origin": "git:abc123"}
        mine["stations"]["hand"] = {"label": "Drawn by me", "gx": 6, "gy": 3}
        spec, _ = merge(mine, imported(), source="jira", prune=True, stamp=STAMP)
        self.assertIn("g1", spec["stations"])
        self.assertIn("hand", spec["stations"])

    def test_a_sketch_is_a_template_and_its_placeholders_are_replaced(self):
        sketch = {"stations": {"p1": {"label": "Placeholder", "gx": 0, "gy": 0}},
                  "lines": [{"name": "One", "color": "#abcdef",
                             "stations": ["p1"]}]}
        spec, _ = merge(sketch, imported(), source="jira", stamp=STAMP)
        self.assertNotIn("p1", spec["stations"])
        self.assertEqual(spec["lines"][0]["color"], "#abcdef")   # the choice stays

    def test_a_timeline_grows_to_hold_what_arrived_and_never_shrinks(self):
        mine = copy.deepcopy(self.mine)
        mine["timeline"] = {"start": "2026-03-01", "end": "2026-04-01",
                            "interval": "month"}
        wider = imported(timeline={"start": "2026-01-01", "end": "2026-09-01",
                                   "interval": "month"})
        spec, notes = merge(mine, wider, source="jira", stamp=STAMP)
        self.assertEqual(spec["timeline"]["start"], "2026-01-01")
        self.assertEqual(spec["timeline"]["end"], "2026-09-01")
        self.assertTrue(any("widened" in n for n in notes))

        narrower = imported(timeline={"start": "2026-03-10", "end": "2026-03-20",
                                      "interval": "month"})
        spec, _ = merge(mine, narrower, source="jira", stamp=STAMP)
        self.assertEqual(spec["timeline"]["start"], "2026-03-01")
        self.assertEqual(spec["timeline"]["end"], "2026-04-01")

    def test_two_stops_claiming_one_origin_is_resolved_and_said(self):
        mine = copy.deepcopy(self.mine)
        mine["stations"]["copy"] = dict(mine["stations"]["a"])
        _, notes = merge(mine, imported(), source="jira", stamp=STAMP)
        self.assertTrue(any("both claim" in n for n in notes))

    def test_track_notes_are_dropped_loudly_when_the_hops_move(self):
        mine = copy.deepcopy(self.mine)
        mine["lines"][0]["notes"] = [{"at": 0, "text": "6 weeks"}]
        longer = imported()
        longer["stations"]["c"] = {"label": "Gamma", "gx": 2, "gy": 1,
                                   "origin": "jira:C"}
        longer["lines"][0]["stations"] = ["a", "c", "b"]
        spec, notes = merge(mine, longer, source="jira", stamp=STAMP)
        self.assertNotIn("notes", spec["lines"][0])
        self.assertTrue(any("note" in n for n in notes))


if __name__ == "__main__":
    unittest.main()
