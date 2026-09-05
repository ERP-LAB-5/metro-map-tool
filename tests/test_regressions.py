# SPDX-License-Identifier: GPL-3.0-or-later
"""What must not have changed.

The importers moved onto a plugin seam and the renderer did not change at all.
These pin both of those down, because a refactor that quietly redraws every
existing map is the worst way to find out.
"""

import json
import pathlib
import subprocess
import sys
import unittest

from metro_map_tool import metro_map as mm

ROOT = pathlib.Path(__file__).resolve().parent.parent
SHARED = ROOT / "metro_map_tool" / "shared-maps"


class ShippedMapsTest(unittest.TestCase):
    def test_every_shipped_map_still_draws(self):
        for path in sorted(SHARED.glob("*.json")):
            with self.subTest(map=path.stem):
                spec = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(mm.validate_spec(spec), [])
                svg = mm.render(spec, mm.style_from(spec.get("style") or {}))
                self.assertIn("<svg", svg)


class ProvenanceTest(unittest.TestCase):
    def test_an_origin_does_not_earn_a_newer_format(self):
        # origin changes no pixel, so an older copy of the tool draws the same
        # SVG — locking maps out of it for a field it never reads would be wrong
        spec = {"stations": {"a": {"label": "A", "gx": 0, "gy": 0,
                                   "origin": "jira:A"},
                             "b": {"label": "B", "gx": 1, "gy": 0,
                                   "origin": "jira:B"}},
                "lines": [{"name": "L", "color": "#000000",
                           "stations": ["a", "b"], "origin": "jira:lane/L"}],
                "source": {"name": "jira", "at": "x", "tool": "t", "options": {}}}
        self.assertEqual(mm.needs_format(spec), 1)

    def test_an_origin_changes_nothing_that_is_drawn(self):
        plain = {"stations": {"a": {"label": "A", "gx": 0, "gy": 0},
                              "b": {"label": "B", "gx": 1, "gy": 0}},
                 "lines": [{"name": "L", "color": "#000000",
                            "stations": ["a", "b"]}]}
        marked = json.loads(json.dumps(plain))
        for sid, st in marked["stations"].items():
            st["origin"] = f"jira:{sid}"
        marked["lines"][0]["origin"] = "jira:lane/L"
        marked["source"] = {"name": "jira", "at": "x", "tool": "t", "options": {}}
        style = mm.style_from({})
        self.assertEqual(mm.render(plain, style), mm.render(marked, style))

    def test_the_spec_validates_with_provenance_on_it(self):
        spec = {"stations": {"a": {"label": "A", "gx": 0, "gy": 0,
                                   "origin": "jira:A"},
                             "b": {"label": "B", "gx": 1, "gy": 0,
                                   "origin": "jira:B"}},
                "lines": [{"name": "L", "color": "#000000",
                           "stations": ["a", "b"], "origin": "jira:lane/L"}],
                "source": {"name": "jira", "at": "x", "tool": "t", "options": {}}}
        self.assertEqual(mm.validate_spec(spec), [])


class CliTest(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-m", "metro_map_tool.metro_map",
                               *args], cwd=ROOT, capture_output=True, text=True)

    def test_a_failed_import_names_the_source_not_None(self):
        done = self.run_cli("--from", "nosuchsource", "-o", "-")
        self.assertEqual(done.returncode, 2)
        self.assertIn("no source called", done.stderr)
        self.assertNotIn("None:", done.stderr)

    def test_import_flags_without_a_source_are_refused_not_ignored(self):
        done = self.run_cli("--model", "x.json",
                            str(SHARED / "roadmap-example.json"), "-o", "-")
        self.assertEqual(done.returncode, 2)
        self.assertIn("--from", done.stderr)

    def test_sources_lists_the_built_ins(self):
        done = self.run_cli("--sources")
        self.assertEqual(done.returncode, 0)
        for name in ("git", "github", "jira"):
            self.assertIn(name, done.stdout)

    def test_describe_explains_one_source_and_names_its_credentials(self):
        done = self.run_cli("--from", "jira", "--describe")
        self.assertEqual(done.returncode, 0)
        self.assertIn("project", done.stdout)
        self.assertIn("JIRA_API_TOKEN", done.stdout)

    def test_from_git_is_still_spelled_the_old_way(self):
        done = self.run_cli("--from-git", ".", "--commits", "5", "-o", "-")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("<svg", done.stdout)


if __name__ == "__main__":
    unittest.main()
