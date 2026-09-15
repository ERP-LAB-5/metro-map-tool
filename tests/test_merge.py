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


def synced(label_a="Alpha", date_a="2026-03-01", gx_a=2.0, branch="Side"):
    """An import that says what it decided, the way the Jira mapping does."""
    def snap(st):
        return dict(st, upstream={"label": st["label"], "date": st.get("date"),
                                  "gx": st["gx"], "gy": st["gy"]})
    spec = {
        "stations": {
            "a": snap({"label": label_a, "date": date_a, "gx": gx_a, "gy": 1,
                       "origin": "jira:A"}),
            "b": snap({"label": "Beta", "date": "2026-04-01", "gx": 3.0, "gy": 0,
                       "origin": "jira:B"})},
        "junctions": {
            "j-x-fork": {"gx": gx_a - 1, "gy": 0, "origin": "jira:X#fork",
                         "upstream": {"gx": gx_a - 1, "gy": 0}},
            "j-x-join": {"gx": gx_a + 1, "gy": 0, "origin": "jira:X#join",
                         "upstream": {"gx": gx_a + 1, "gy": 0}}},
        "lines": [{"name": "One", "color": "#111111", "origin": "jira:ONE",
                   "stations": ["j-x-fork", "j-x-join", "b"],
                   "branches": [{"name": branch, "origin": "jira:X",
                                 "stations": ["j-x-fork", "a", "j-x-join"],
                                 "upstream": {"name": branch}}],
                   "notes": [{"at": 1, "text": "Hand-over", "origin": "jira:N",
                              "upstream": {"text": "Hand-over"}}],
                   "upstream": {"name": "One", "status": None}}],
    }
    return spec


class ThreeWayTest(unittest.TestCase):
    """Jira changed it, or somebody did — and only the first is applied."""

    def setUp(self):
        self.mine, _ = merge(None, synced(), source="jira", stamp=STAMP)
        self.mine = copy.deepcopy(self.mine)

    def resync(self, mine=None, **upstream):
        return merge(mine or self.mine, synced(**upstream), source="jira", stamp=STAMP)

    def test_what_nobody_touched_follows_upstream(self):
        spec, notes = self.resync(label_a="Alpha v2", date_a="2026-05-01", gx_a=4.0)
        a = spec["stations"]["a"]
        self.assertEqual((a["label"], a["date"], a["gx"]), ("Alpha v2", "2026-05-01", 4.0))
        self.assertEqual(spec["junctions"]["j-x-join"]["gx"], 5.0)
        self.assertTrue(any("now \"Alpha v2\"" in n for n in notes))

    def test_what_the_map_changed_is_kept_and_the_clash_is_said(self):
        self.mine["stations"]["a"]["label"] = "My words"
        spec, notes = self.resync(label_a="Alpha v2")
        self.assertEqual(spec["stations"]["a"]["label"], "My words")
        self.assertTrue(any("changed both" in n for n in notes))

    def test_what_only_the_map_changed_stays_without_a_word(self):
        self.mine["stations"]["a"]["label"] = "My words"
        spec, notes = self.resync()
        self.assertEqual(spec["stations"]["a"]["label"], "My words")
        self.assertFalse(any("jira:A" in n for n in notes))

    def test_a_dragged_stop_stays_put_while_its_date_still_updates(self):
        self.mine["stations"]["a"]["gx"] = 9.0
        spec, notes = self.resync(date_a="2026-05-01", gx_a=4.0)
        self.assertEqual(spec["stations"]["a"]["gx"], 9.0)
        self.assertEqual(spec["stations"]["a"]["date"], "2026-05-01")
        self.assertTrue(any("stays where it is" in n for n in notes))

    def test_the_new_upstream_is_the_base_for_next_time(self):
        self.mine["stations"]["a"]["label"] = "My words"
        spec, _ = self.resync(label_a="Alpha v2")
        _, notes = merge(spec, synced(label_a="Alpha v2"), source="jira", stamp=STAMP)
        self.assertEqual(notes, [])

    def test_a_renamed_branch_and_line_keep_their_names(self):
        self.mine["lines"][0]["name"] = "My line"
        self.mine["lines"][0]["branches"][0]["name"] = "My branch"
        spec, _ = self.resync(branch="Side v2")
        self.assertEqual(spec["lines"][0]["name"], "My line")
        self.assertEqual(spec["lines"][0]["branches"][0]["name"], "My branch")

    def test_an_untouched_branch_name_follows(self):
        spec, _ = self.resync(branch="Side v2")
        self.assertEqual(spec["lines"][0]["branches"][0]["name"], "Side v2")

    def test_junctions_the_import_no_longer_makes_go_and_hand_made_ones_stay(self):
        self.mine["junctions"]["mine"] = {"gx": 7, "gy": 5}
        plain = synced()
        plain["junctions"] = {}
        plain["lines"][0]["stations"] = ["b"]
        plain["lines"][0]["branches"] = []
        spec, _ = merge(self.mine, plain, source="jira", stamp=STAMP)
        self.assertEqual(set(spec["junctions"]), {"mine"})

    def test_hand_drawn_branches_and_notes_survive(self):
        line = self.mine["lines"][0]
        line["branches"].append({"name": "Mine", "stations": ["b", "a"]})
        line["notes"].append({"at": 0, "text": "my note"})
        spec, _ = self.resync()
        got = spec["lines"][0]
        self.assertIn("Mine", [b["name"] for b in got["branches"]])
        self.assertIn("my note", [n["text"] for n in got["notes"]])
        self.assertEqual([n["text"] for n in got["notes"]].count("Hand-over"), 1)

    def test_a_map_from_before_snapshots_keeps_its_values_once(self):
        old = copy.deepcopy(self.mine)
        for st in old["stations"].values():
            st.pop("upstream")
        old["stations"]["a"]["label"] = "Older words"
        spec, _ = self.resync(label_a="Alpha v2")
        spec, _ = merge(old, synced(label_a="Alpha v2"), source="jira", stamp=STAMP)
        self.assertEqual(spec["stations"]["a"]["label"], "Older words")
        self.assertEqual(spec["stations"]["a"]["upstream"]["label"], "Alpha v2")



class LaneFoldTest(unittest.TestCase):
    """Swimlanes an import made follow it; the author's own stay theirs."""

    def fresh(self, name="Platform", rows=(0, 2)):
        spec = imported()
        spec["swimlanes"] = [{"name": name, "rows": list(rows), "origin": "jira:P-1#lane",
                              "upstream": {"name": name, "rows": list(rows)}}]
        return spec

    def test_an_untouched_lane_follows_and_a_renamed_one_keeps_its_name(self):
        mine, _ = merge(None, self.fresh(), source="jira", stamp=STAMP)
        mine = copy.deepcopy(mine)
        spec, _ = merge(mine, self.fresh(rows=(0, 3)), source="jira", stamp=STAMP)
        self.assertEqual(spec["swimlanes"][0]["rows"], [0, 3])
        mine["swimlanes"][0]["name"] = "My area"
        spec, notes = merge(mine, self.fresh(name="Platform v2"), source="jira", stamp=STAMP)
        self.assertEqual(spec["swimlanes"][0]["name"], "My area")
        self.assertTrue(any("changed both" in n for n in notes))

    def test_a_hand_drawn_lane_survives_and_a_gone_one_goes_only_with_prune(self):
        mine, _ = merge(None, self.fresh(), source="jira", stamp=STAMP)
        mine = copy.deepcopy(mine)
        mine["swimlanes"].append({"name": "Mine", "rows": [5, 6]})
        empty = imported()
        spec, _ = merge(mine, empty, source="jira", stamp=STAMP)
        self.assertEqual([l["name"] for l in spec["swimlanes"]], ["Platform", "Mine"])
        spec, _ = merge(mine, empty, source="jira", prune=True, stamp=STAMP)
        self.assertEqual([l["name"] for l in spec["swimlanes"]], ["Mine"])

if __name__ == "__main__":
    unittest.main()
