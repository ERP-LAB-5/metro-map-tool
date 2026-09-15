# SPDX-License-Identifier: GPL-3.0-or-later
"""The roadmap and decision records kept in docs/.

The roadmap is a map this tool draws, so it is held to what a shipped map is:
it validates, draws without a warning, and the committed picture is what its
spec draws. The ADR index is held to the files beside it.
"""

import json
import re
import unittest
from pathlib import Path

from metro_map_tool import metro_map as mm

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"


class RoadmapTest(unittest.TestCase):
    def setUp(self):
        self.spec = json.loads((DOCS / "roadmap" / "roadmap.json").read_text(encoding="utf-8"))

    def test_the_roadmap_is_a_map_without_problems(self):
        self.assertEqual(mm.validate_spec(self.spec), [])
        self.assertEqual(mm.spec_warnings(self.spec), [])

    def test_the_roadmap_picture_is_what_its_spec_draws(self):
        drawn = mm.render(self.spec, mm.style_from(self.spec.get("style") or {}))
        committed = (DOCS / "roadmap" / "roadmap.svg").read_text(encoding="utf-8")
        # the version in the stamp moves with every release; the drawing must not
        unstamp = lambda svg: re.sub(r"metro-map \d+(\.\d+)*", "metro-map X", svg)
        self.assertEqual(unstamp(drawn), unstamp(committed),
                         "re-draw it: metro-map docs/roadmap/roadmap.json "
                         "-o docs/roadmap/roadmap.svg")


class DecisionRecordTest(unittest.TestCase):
    def test_every_adr_is_in_the_index_and_numbered_once(self):
        adrs = sorted(p.name for p in (DOCS / "adr").glob("[0-9][0-9][0-9][0-9]-*.md"))
        index = (DOCS / "adr" / "README.md").read_text(encoding="utf-8")
        numbers = [name[:4] for name in adrs]
        self.assertEqual(len(numbers), len(set(numbers)), adrs)
        for name in adrs:
            self.assertIn(f"({name})", index)

    def test_every_adr_has_its_sections(self):
        for path in (DOCS / "adr").glob("[0-9][0-9][0-9][0-9]-*.md"):
            text = path.read_text(encoding="utf-8")
            for heading in ("## Status", "## Date", "## Context", "## Decision",
                            "## Consequences", "## Carried out in"):
                self.assertIn(heading, text, path.name)


if __name__ == "__main__":
    unittest.main()
