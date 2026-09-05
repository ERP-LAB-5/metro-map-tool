# SPDX-License-Identifier: GPL-3.0-or-later
"""The registry, and what it does with a plugin somebody else wrote badly.

A stale importer pip-installed last year is no reason `metro-map map.json`
should stop working, so the cost of a broken plugin has to be that one plugin.
"""

import unittest

from metro_map_tool import sources as S


class CatalogueTest(unittest.TestCase):
    def test_the_three_that_ship_are_there(self):
        self.assertLessEqual({"git", "github", "jira"}, set(S.catalogue()))

    def test_an_unknown_name_says_what_is_known(self):
        with self.assertRaises(S.SourceError) as caught:
            S.get("gitlab")
        self.assertIn("github", str(caught.exception))

    def test_describing_a_source_never_carries_a_credential(self):
        import os
        os.environ["GITHUB_TOKEN"] = "ghp_pretend_secret_value"
        try:
            said = repr(S.describe(S.get("github")))
            self.assertNotIn("ghp_pretend_secret_value", said)
            self.assertTrue(any(e["present"] for e in S.describe(S.get("github"))["env"]))
        finally:
            del os.environ["GITHUB_TOKEN"]

    def test_the_stamp_never_carries_a_local_path(self):
        src = S.get("git")
        opts, _ = S.coerce(src, {"repo": ".", "model": "/home/someone/secret.json",
                                 "from_file": "/home/someone/payload.json"})
        said = S.stamp(src, opts)
        self.assertNotIn("model", said["options"])
        self.assertNotIn("from_file", said["options"])
        self.assertEqual(said["options"]["repo"], ".")


class RejectionTest(unittest.TestCase):
    """What _rejected refuses, so a bad plugin fails at load and not mid-render."""

    def _source(self, **over):
        base = dict(name="ok", title="t", summary="s", options=(),
                    fetch=lambda o, m: {}, build=lambda d, o, m: ({}, []))
        base.update(over)
        return S.Source(**base)

    def test_a_name_already_taken_loses(self):
        self.assertIn("already taken",
                      S._rejected(self._source(name="git"), {"git": None}))

    def test_something_that_is_not_a_source_is_refused(self):
        self.assertIn("not a Source", S._rejected(object(), {}))

    def test_an_option_of_an_unknown_kind_is_refused(self):
        bad = self._source(options=(S.Option("x", "h", kind="colour"),))
        self.assertIn("unknown kind", S._rejected(bad, {}))

    def test_a_duplicated_option_name_is_refused(self):
        bad = self._source(options=(S.Option("x", "h"), S.Option("x", "h")))
        self.assertIn("twice", S._rejected(bad, {}))

    def test_shadowing_a_universal_option_is_refused(self):
        bad = self._source(options=(S.Option("limit", "mine"),))
        self.assertIn("twice", S._rejected(bad, {}))

    def test_a_good_one_passes(self):
        self.assertEqual(S._rejected(self._source(), {}), "")


class CoerceTest(unittest.TestCase):
    def setUp(self):
        self.src = S.get("github")

    def test_a_missing_required_option_says_which(self):
        _, errors = S.coerce(self.src, {})
        self.assertTrue(any("repo" in e for e in errors))

    def test_a_typo_lists_the_real_names(self):
        _, errors = S.coerce(self.src, {"repo": "a/b", "lable_prefix": "x"})
        self.assertTrue(any("label_prefix" in e for e in errors))

    def test_kinds_are_coerced_not_passed_through_as_strings(self):
        opts, errors = S.coerce(self.src, {"repo": "a/b", "limit": "50",
                                           "prune": "yes", "refresh": "label, gx"})
        self.assertEqual(errors, [])
        self.assertEqual(opts["limit"], 50)
        self.assertIs(opts["prune"], True)
        self.assertEqual(opts["refresh"], ["label", "gx"])

    def test_a_bad_number_is_a_sentence_not_a_crash(self):
        _, errors = S.coerce(self.src, {"repo": "a/b", "limit": "lots"})
        self.assertTrue(any("whole number" in e for e in errors))

    def test_a_choice_outside_its_choices_is_refused(self):
        _, errors = S.coerce(self.src, {"repo": "a/b", "interval": "fortnight"})
        self.assertTrue(any("fortnight" in e for e in errors))


if __name__ == "__main__":
    unittest.main()
