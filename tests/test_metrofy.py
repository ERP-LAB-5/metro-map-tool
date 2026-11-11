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
from metro_map_tool.metrofy import parts

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
                         {"metrofy", "title", "subtitle", "zones", "phases", "steps", "lines", "trips", "spine",
                          "legend", "details"})
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


def phased():
    """Three phases, two zones: alternatives stand one above the other."""
    return {
        "metrofy": 1, "title": "Phased", "spine": False,
        "zones": [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}],
        "phases": [{"id": "p1", "name": "One"}, {"id": "p2", "name": "Two"}, {"id": "p3", "name": "Three"}],
        "steps": [{"id": "p1-a", "label": "1a", "zone": "a", "phase": "p1"},
                  {"id": "p2-a", "label": "2a", "zone": "a", "phase": "p2"},
                  {"id": "p2-b", "label": "2b", "zone": "b", "phase": "p2"},
                  {"id": "p3-a", "label": "3a", "zone": "a", "phase": "p3"}],
        "lines": [{"id": "via-a", "name": "Via A", "status": "live", "steps": ["p1-a", "p2-a", "p3-a"]},
                  {"id": "via-b", "name": "Via B", "status": "planned", "steps": ["p3-a", "p2-b", "p1-a"]}],
        "trips": [{"id": "t", "name": "Via B", "line": "via-b", "stops": ["p1-a", "p2-b", "p3-a"]}],
    }


class Phases(unittest.TestCase):
    def test_a_phase_is_a_staircase_so_zone_changes_run_at_60_degrees(self):
        import math
        spec = place(phased())
        st = spec["stations"]
        self.assertEqual(spec["angles"], "60")
        dx = st["p2-b"]["gx"] - st["p2-a"]["gx"]
        dy = st["p2-b"]["gy"] - st["p2-a"]["gy"]
        self.assertGreater(dy, 0)
        self.assertAlmostEqual(math.degrees(math.atan2(dy, dx)), 60.0, delta=0.5)
        # the same zone in the next phase is straight across
        self.assertEqual(st["p1-a"]["gy"], st["p2-a"]["gy"])
        self.assertLess(st["p1-a"]["gx"], st["p2-a"]["gx"])

    def test_no_line_runs_steeper_than_60_degrees(self):
        import math
        doc = phased()
        doc["zones"].append({"id": "c", "name": "C"})
        doc["steps"].append({"id": "p1-c", "label": "1c", "zone": "c", "phase": "p1"})
        doc["lines"].append({"id": "low-high", "name": "Low to high", "steps": ["p1-c", "p2-a"]})
        spec = place(doc)
        st = spec["stations"]
        for ln in spec["lines"]:
            for a, b in zip(ln["stations"], ln["stations"][1:]):
                dx, dy = abs(st[b]["gx"] - st[a]["gx"]), abs(st[b]["gy"] - st[a]["gy"])
                angle = math.degrees(math.atan2(dy, dx))
                self.assertLessEqual(angle, 60.5, (ln["name"], a, b, angle))

    def test_the_legend_can_be_left_to_the_app(self):
        doc = phased()
        self.assertEqual(place(doc)["legend"], "bottom")
        doc["legend"] = False
        self.assertEqual(place(doc)["legend"], "hide")
        self.assertNotIn('id="legend"', parts(doc)[1]["svg"])

    def test_a_phase_is_a_named_band_around_its_steps(self):
        zones = {z["name"]: z["stations"] for z in place(phased())["zones"]}
        self.assertEqual(zones["Two"], ["p2-a", "p2-b"])

    def test_lines_split_and_join_and_run_forward(self):
        lines = {ln["name"]: ln["stations"] for ln in place(phased())["lines"]}
        self.assertEqual(lines["Via B"], ["p1-a", "p2-b", "p3-a"])
        receipt, _ = parts(phased())
        self.assertTrue(receipt["ok"], receipt["errors"])

    def test_one_station_per_phase_and_zone(self):
        doc = phased()
        doc["steps"].append({"id": "p2-a2", "label": "2a again", "zone": "a", "phase": "p2"})
        self.assertTrue(any("one station per phase and zone" in p for p in check(doc)))

    def test_a_step_names_a_phase_that_exists(self):
        doc = phased()
        doc["steps"][0]["phase"] = "p9"
        self.assertTrue(any("phase must be one of the phases' ids" in p for p in check(doc)))
        doc = small()
        doc["steps"][0]["phase"] = "p1"
        self.assertTrue(any("the map lists no phases" in p for p in check(doc)))

    def test_the_data_names_each_step_s_phase(self):
        d = parts(phased())[1]["data"]
        self.assertEqual(d["steps"]["p2-b"]["phase"], "Two")
        self.assertEqual([p["name"] for p in d["phases"]], ["One", "Two", "Three"])


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


class Parts(unittest.TestCase):
    """The map for an app to show in its own layout: the SVG and its data."""

    def test_parts_are_the_svg_and_data_keyed_as_the_svg_marks_them(self):
        from metro_map_tool.metrofy import parts
        receipt, made = parts(example())
        self.assertTrue(receipt["ok"])
        svg, d = made["svg"], made["data"]
        self.assertTrue(svg.lstrip().startswith("<svg"))
        for sid in d["steps"]:
            self.assertIn(f'data-station="{sid}"', svg)
        for i, ln in enumerate(d["lines"]):                  # a route's l<i> is data["lines"][i]
            self.assertRegex(svg, rf'<path class="route[^"]*\bl{i}\b[^"]*"[^>]*stroke="{ln["color"]}"')
        self.assertEqual(d["lines"][0]["name"], build.SPINE_NAME)
        self.assertEqual([t["id"] for t in d["trips"]], ["maker-as-metaobject", "maker-as-tag"])
        self.assertEqual(d["trips"][0]["color"], d["lines"][1]["color"])   # a trip takes its story's colour
        self.assertEqual(d["steps"]["classify"]["parent"], "present")
        # the station ids too, so an app can walk a trip stop by stop
        self.assertEqual(d["trips"][0]["stations"], ["present", "classify", "describe"])
        self.assertEqual(d["lines"][1]["stations"], ["present", "classify", "describe"])

    def test_the_page_is_made_from_the_same_parts(self):
        from metro_map_tool.metrofy import parts
        _, made = parts(example())
        self.assertIn(made["svg"], finalize(example())[1])

    def test_failed_parts_are_empty(self):
        from metro_map_tool.metrofy import parts
        receipt, made = parts({"metrofy": 1})
        self.assertFalse(receipt["ok"])
        self.assertEqual(made, {})


class CommandLine(unittest.TestCase):
    def test_finalize_writes_the_page_and_exits_by_the_receipt(self):
        import tempfile, io, contextlib
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "map.html"
            with contextlib.redirect_stdout(io.StringIO()) as said:
                self.assertEqual(cli.main(["finalize", str(EXAMPLE), str(out)]), 0)
            self.assertTrue(json.loads(said.getvalue())["ok"])
            self.assertTrue(out.read_text(encoding="utf-8").startswith("<!doctype html>"))
            with contextlib.redirect_stdout(io.StringIO()):
                cli.main(["finalize", str(EXAMPLE), str(out), "--parts", str(Path(d) / "parts.json")])
            made = json.loads((Path(d) / "parts.json").read_text(encoding="utf-8"))
            self.assertEqual(sorted(made), ["data", "svg"])
            bad = Path(d) / "bad.json"
            bad.write_text(json.dumps({"metrofy": 2}), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(["finalize", str(bad), str(Path(d) / "no.html")]), 1)
            self.assertFalse((Path(d) / "no.html").exists())


if __name__ == "__main__":
    unittest.main()
