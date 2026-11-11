# SPDX-License-Identifier: GPL-3.0-or-later
"""Metrofy: a metro map made from code, for display (ADR 0015).

What these pin down: the input is refused in sentences, never half drawn;
placement is decided by Metrofy alone and is the same every time; a line
always runs forward through the process; a trip rides track that exists;
and the page is one self-contained file."""

import copy
import json
import re
import unittest
from pathlib import Path

from metro_map_tool import metro_map as mm
from metro_map_tool.metrofy import build, check, finalize, place
from metro_map_tool.metrofy import cli

EXAMPLE = Path(build.__file__).with_name("examples") / "shop-migration.json"
SCHEMA = Path(build.__file__).with_name("schema.json")


def example():
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def small():
    return {
        "metrofy": 1, "title": "Small",
        "zones": [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}],
        "steps": [{"id": "one", "label": "One", "zone": "a"},
                  {"id": "two", "label": "Two", "zone": "b"},
                  {"id": "three", "label": "Three", "zone": "a"}],
        "lines": [{"id": "s", "name": "Story", "status": "building", "steps": ["three", "one"]}],
        "trips": [{"id": "t", "name": "Trip", "line": "s", "stops": ["one", "two", "three"]}],
    }


class TheInput(unittest.TestCase):
    def test_the_example_is_clean(self):
        self.assertEqual(check(example()), [])

    def test_problems_are_sentences_naming_the_item(self):
        doc = small()
        doc["steps"][1]["zone"] = "nowhere"
        doc["lines"][0]["steps"] = ["one", "missing"]
        doc["lines"][0]["status"] = "done"
        doc["trips"][0]["line"] = "other"
        doc["colour"] = "#000000"
        problems = "\n".join(check(doc))
        for expected in ("step 2 ('two'): zone must be one of the zones' ids",
                         "line 1 ('s'): unknown step 'missing'",
                         "line 1 ('s'): status must be one of planned, building, live, dropped",
                         "trip 1 ('t'): line must be one of the lines' ids",
                         'unknown field "colour"'):
            self.assertIn(expected, problems)

    def test_a_parent_comes_before_its_children(self):
        doc = small()
        doc["steps"][0]["parent"] = "two"
        self.assertTrue(any("parent 'two' must be a step listed before it" in p for p in check(doc)))

    def test_a_line_through_one_step_is_refused_with_the_way_out(self):
        doc = small()
        doc["lines"][0]["steps"] = ["one", "one"]
        self.assertTrue(any("rides the process line instead" in p for p in check(doc)))

    def test_ids_are_plain(self):
        doc = small()
        doc["steps"][0]["id"] = "One Step"
        self.assertTrue(any("id must be lowercase" in p for p in check(doc)))

    def test_the_schema_names_the_same_fields_check_allows(self):
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        self.assertEqual(set(schema["properties"]),
                         {"metrofy", "title", "subtitle", "zones", "steps", "lines", "trips", "spine", "details"})
        self.assertEqual(schema["properties"]["lines"]["items"]["properties"]["status"]["enum"],
                         list(build.STATUS))


class Placement(unittest.TestCase):
    def test_steps_keep_process_order_in_their_zone_lane(self):
        spec = place(small())
        st = spec["stations"]
        self.assertEqual([st[s]["gx"] for s in ("one", "two", "three")], [0, 2, 4])
        self.assertEqual(st["one"]["gy"], st["three"]["gy"])
        lane = {ln["name"]: ln["rows"] for ln in spec["swimlanes"]}
        self.assertTrue(lane["A"][0] <= st["one"]["gy"] <= lane["A"][1])
        self.assertTrue(lane["B"][0] <= st["two"]["gy"] <= lane["B"][1])
        self.assertEqual(mm.validate_spec(spec), [])

    def test_a_line_runs_forward_whatever_order_it_was_written_in(self):
        story = place(small())["lines"][1]
        self.assertEqual(story["stations"], ["one", "three"])
        self.assertEqual(story["status"], "under-construction")

    def test_the_process_line_is_drawn_unless_asked_not_to(self):
        self.assertEqual(place(small())["lines"][0]["name"], build.SPINE_NAME)
        doc = small()
        doc["spine"] = False
        doc["trips"] = []
        self.assertNotIn(build.SPINE_NAME, [ln["name"] for ln in place(doc)["lines"]])

    def test_a_parent_is_an_interchange(self):
        spec = place(example())
        self.assertTrue(spec["stations"]["present"].get("interchange"))
        self.assertFalse(spec["stations"]["price"].get("interchange", False))

    def test_the_same_input_gives_the_same_page(self):
        a, b = finalize(example())[1], finalize(copy.deepcopy(example()))[1]
        self.assertTrue(a)
        self.assertEqual(a, b)


class Finalize(unittest.TestCase):
    def test_the_example_passes_every_gate(self):
        receipt, page = finalize(example())
        self.assertTrue(receipt["ok"], receipt["errors"])
        self.assertEqual([g["gate"] for g in receipt["gates"]], ["input", "spec", "trips and lanes", "render"])
        self.assertEqual(receipt["counts"], {"zones": 3, "steps": 8, "lines": 4, "trips": 2})
        for sid in example()["steps"]:
            self.assertIn(f'data-station="{sid["id"]}"', page)

    def test_a_trip_off_the_track_is_refused_not_drawn_half(self):
        doc = small()
        doc["spine"] = False
        doc["trips"][0]["stops"] = ["one", "two"]          # no line joins them without the spine
        receipt, page = finalize(doc)
        self.assertFalse(receipt["ok"])
        self.assertEqual(page, "")
        self.assertTrue(any("no line runs between 'one' and 'two'" in e for e in receipt["errors"]))

    def test_a_bad_input_stops_at_the_first_gate(self):
        receipt, page = finalize({"metrofy": 1})
        self.assertFalse(receipt["ok"])
        self.assertEqual([g["gate"] for g in receipt["gates"]], ["input"])
        self.assertEqual(page, "")

    def test_the_page_stands_alone(self):
        page = finalize(example())[1]
        self.assertNotRegex(page, r'<(script|link|img)[^>]+(src|href)="(https?:)?//')
        self.assertIn('id="scenarios"', page)
        self.assertIn("prefers-color-scheme: dark", page)
        data = json.loads(re.search(r'<script id="metrofy-data" type="application/json">(.*?)</script>',
                                    page, re.S).group(1))
        self.assertEqual(data["steps"]["present"]["zone"], "Both")
        self.assertEqual(data["lines"][1]["name"], "Browse by maker")
        self.assertEqual(data["trips"][0]["line"], "Browse by maker")

    def test_text_in_the_input_cannot_break_out_of_the_page(self):
        doc = small()
        doc["title"] = "<script>alert(1)</script>"
        doc["steps"][0]["details"] = {"note": "</script><script>alert(2)</script>"}
        page = finalize(doc)[1]
        self.assertNotIn("<script>alert(1)", page)
        self.assertNotIn("</script><script>alert(2)", page)


class CommandLine(unittest.TestCase):
    def test_finalize_writes_the_page_and_exits_by_the_receipt(self):
        import tempfile, io, contextlib
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "map.html"
            with contextlib.redirect_stdout(io.StringIO()) as said:
                self.assertEqual(cli.main(["finalize", str(EXAMPLE), str(out)]), 0)
            self.assertTrue(json.loads(said.getvalue())["ok"])
            self.assertTrue(out.read_text(encoding="utf-8").startswith("<!doctype html>"))
            bad = Path(d) / "bad.json"
            bad.write_text(json.dumps({"metrofy": 2}), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(["finalize", str(bad), str(Path(d) / "no.html")]), 1)
            self.assertFalse((Path(d) / "no.html").exists())


if __name__ == "__main__":
    unittest.main()
