"""The frozen lines have one reader, and the tests go through it.

`line_field` / `line_field_or` in scripts/lib/pkg.sh are what the pipeline
reads a producer's lines with. `test_bump` used to split them itself: its parser
took the last of two `bump=` lines, while the workflow's reader fails on the
duplicate -- so a producer that wrote twice kept the test job green and only
bothered a publish run ten minutes later.

This holds the test-side reader to the production rules, so the failure lands
in the test job, in the same words.

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import frozen  # noqa: E402  (the reader the other tests use)


class FrozenFieldTest(unittest.TestCase):
    def test_a_frozen_line_reads_through(self):
        self.assertEqual(frozen.field("bump", "upstream=0.1.9\nbump=1\n"), "1")

    def test_an_absent_line_is_the_callers_call(self):
        # What `bump.py --check` prints when it has nothing to say: None here,
        # the caller's default in upstream.sh.
        self.assertIsNone(frozen.field("bump", "upstream=0.1.9\n"))

    def test_a_duplicated_line_is_the_readers_complaint(self):
        with self.assertRaises(AssertionError) as caught:
            frozen.field("bump", "bump=0\nbump=1\n")

        self.assertIn("2 bump= lines", str(caught.exception))

    def test_an_empty_value_is_not_a_value(self):
        with self.assertRaises(AssertionError) as caught:
            frozen.field("bump", "bump=\n")

        self.assertIn("empty bump=", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
