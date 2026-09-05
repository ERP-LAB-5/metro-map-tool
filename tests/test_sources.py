# SPDX-License-Identifier: GPL-3.0-or-later
"""What each importer makes of a recorded payload.

These run against fixtures rather than the live systems, which is the whole
reason fetch and build are separate: the mapping is the part that goes wrong,
and it should be checkable without a token, a network or somebody's real Jira.
"""

import json
import pathlib
import unittest

from metro_map_tool import metro_map as mm
from metro_map_tool.sources import coerce, get, payload

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def imported(name, opts):
    src = get(name)
    values, errors = coerce(src, opts)
    assert not errors, errors
    return src.build(payload(src, values, None), values, None)


class GitHubTest(unittest.TestCase):
    def setUp(self):
        self.spec, self.notes = imported("github", {
            "repo": "acme/platform",
            "from_file": str(FIXTURES / "github-issues.json")})

    def test_draws_without_complaint(self):
        self.assertEqual(mm.validate_spec(self.spec), [])
        self.assertEqual(mm.spec_warnings(self.spec), [])
        self.assertIn("<svg", mm.render(self.spec, mm.style_from(self.spec["style"])))

    def test_labels_name_the_lines_with_the_prefix_stripped(self):
        self.assertEqual([ln["name"] for ln in self.spec["lines"]],
                         ["auth", "billing", "platform", "Unlabelled"])

    def test_a_pull_request_is_not_an_issue(self):
        # the issues endpoint returns PRs too, and a PR is not planned work
        self.assertNotIn("gh-108", self.spec["stations"])

    def test_an_issue_with_no_milestone_has_no_date_and_is_said_so(self):
        self.assertNotIn("gh-107", self.spec["stations"])
        self.assertTrue(any("no date" in n for n in self.notes))

    def test_a_closed_issue_is_marked(self):
        self.assertTrue(self.spec["stations"]["gh-101"]["label"].startswith("✓"))

    def test_a_milestone_crosses_the_lanes_it_touches(self):
        beta = [c for c in self.spec["interchanges"] if c["label"] == "Beta"][0]
        rows = {self.spec["stations"][s]["gy"] for s in beta["stations"]}
        self.assertGreater(len(rows), 1)

    def test_a_release_stands_in_its_own_column(self):
        # a stop hidden under a capsule loses its label, so nothing else may
        # share the release's exact gx
        for capsule in self.spec["interchanges"]:
            gx = self.spec["stations"][capsule["stations"][0]]["gx"]
            covered = set(capsule["stations"])
            for sid, st in self.spec["stations"].items():
                if sid not in covered:
                    self.assertNotEqual(st["gx"], gx, sid)

    def test_everything_carries_an_origin(self):
        for sid, st in self.spec["stations"].items():
            self.assertTrue(st["origin"].startswith("github:"), sid)


class JiraTest(unittest.TestCase):
    def setUp(self):
        self.spec, self.notes = imported("jira", {
            "project": "PAY", "sprint_field": "customfield_10020",
            "from_file": str(FIXTURES / "jira-search.json")})

    def test_draws_without_complaint(self):
        self.assertEqual(mm.validate_spec(self.spec), [])
        self.assertEqual(mm.spec_warnings(self.spec), [])

    def test_an_epic_is_a_line(self):
        self.assertEqual([ln["name"] for ln in self.spec["lines"]],
                         ["Payments", "Reporting"])

    def test_a_lane_of_unstarted_work_is_drawn_as_planned(self):
        reporting = [ln for ln in self.spec["lines"] if ln["name"] == "Reporting"][0]
        self.assertEqual(reporting["status"], "planned")

    def test_a_fix_version_is_a_capsule_at_its_release_date(self):
        names = [c["label"] for c in self.spec["interchanges"]]
        self.assertEqual(sorted(names), ["R1.0", "R2.0"])

    def test_sprints_become_bands(self):
        self.assertEqual([p["name"] for p in self.spec["phases"]],
                         ["Sprint 14", "Sprint 15"])

    def test_no_sprint_field_means_no_bands(self):
        spec, _ = imported("jira", {"project": "PAY",
                                    "from_file": str(FIXTURES / "jira-search.json")})
        self.assertNotIn("phases", spec)


class AwkwardTest(unittest.TestCase):
    """The shapes that turn up in real projects and used to be assumed away."""

    def _github(self, issues, milestones):
        path = FIXTURES / "tmp-awkward.json"
        path.write_text(json.dumps({"repo": "a/b", "issues": issues,
                                    "milestones": milestones}))
        try:
            return imported("github", {"repo": "a/b", "from_file": str(path)})
        finally:
            path.unlink()

    def test_nothing_dated_says_so_rather_than_crashing(self):
        from metro_map_tool.sources import SourceError
        with self.assertRaises(SourceError) as caught:
            self._github([{"number": 1, "title": "x", "state": "open",
                           "labels": [{"name": "area:a"}], "milestone": None}], [])
        self.assertIn("date", str(caught.exception))

    def test_one_epic_one_issue_still_validates(self):
        spec, _ = self._github(
            [{"number": 1, "title": "only", "state": "open",
              "labels": [{"name": "area:a"}], "milestone": {"number": 1}}],
            [{"number": 1, "title": "M", "due_on": "2026-04-01T00:00:00Z"}])
        self.assertEqual(mm.validate_spec(spec), [])

    def test_two_area_labels_pick_one_and_do_not_duplicate_the_issue(self):
        spec, _ = self._github(
            [{"number": 1, "title": "both", "state": "open",
              "labels": [{"name": "area:a"}, {"name": "area:b"}],
              "milestone": {"number": 1}},
             {"number": 2, "title": "other", "state": "open",
              "labels": [{"name": "area:b"}], "milestone": {"number": 1}}],
            [{"number": 1, "title": "M", "due_on": "2026-04-01T00:00:00Z"}])
        on_a_line = [sid for ln in spec["lines"] for sid in ln["stations"]]
        self.assertEqual(len(on_a_line), len(set(on_a_line)))


if __name__ == "__main__":
    unittest.main()
