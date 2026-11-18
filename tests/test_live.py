"""The live-line reader (``tests/live.py``): where a comment starts, and nothing else.

A guard that reads a shell-ish text has to tell code from prose before it asks
anything. Every guard in this suite did that itself, in one of four spellings
and two widths: before ``live.py`` existed, eight split at the first ``#``
(which eats a trailing comment -- and would eat a quoted one), fifteen dropped
only whole-line comments (which lets ``true  # ./scripts/seed-tree.sh …`` read
as the call).

These cases pin the reader's interface: ``live`` keeps the text's line count,
``live_lines`` hands back the code lines, ``naming`` hands back the code lines
that name a value. The rules are bash's: a ``#`` begins a comment only at the
start of a word, and not inside single quotes, double quotes, or after a
backslash. A quote that does not close on its own line is not handled -- the
reader is a line reader, and its docstring says so.

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tests"))

import live  # noqa: E402  (the reader under test)
import workflow  # noqa: E402  (a real `run:` block to hold it to)


class LiveTest(unittest.TestCase):
    """Where the comment starts, on one line at a time."""

    def test_a_comment_only_line_leaves_an_empty_line(self):
        text = "a\n  # a note\nb\n"

        self.assertEqual(live.live(text), "a\n\nb\n")
        self.assertEqual(live.live_lines(text), ["a", "b"])

    def test_a_line_end_comment_is_not_a_live_line(self):
        text = "true  # ./scripts/seed-tree.sh \"$TREE\"\n"

        self.assertEqual(live.live(text), "true\n")
        self.assertEqual(live.live_lines(text), ["true"])

    def test_a_hash_inside_single_quotes_is_content(self):
        self.assertEqual(live.live("echo 'a # b'"), "echo 'a # b'")

    def test_a_hash_inside_double_quotes_is_content(self):
        self.assertEqual(live.live('echo "a # b"'), 'echo "a # b"')

    def test_an_escaped_hash_is_content(self):
        self.assertEqual(live.live(r"echo a\#b"), r"echo a\#b")

    def test_an_escaped_quote_does_not_close_the_quote_it_is_inside(self):
        # The escape rule only shows when the escaped character would otherwise
        # change the state: here `\"` keeps the string open, so the `#` is still
        # inside it. Without the escape branch this line would be cut at `#`.
        self.assertEqual(live.live(r'echo "a\" # b"'), r'echo "a\" # b"')

    def test_a_hash_inside_a_word_is_content_and_at_a_word_start_is_not(self):
        # bash: "a word beginning with #" begins the comment.
        self.assertEqual(live.live("echo a#b"), "echo a#b")
        self.assertEqual(live.live("echo a #b"), "echo a")

    def test_a_hash_after_a_separator_or_a_redirect_is_a_comment(self):
        # bash: `;`, `<` and `>` end the previous word too, so the `#` starts a
        # new one. `bash -c 'echo hi >#f'` is a syntax error at the newline --
        # the rest of the line was a comment.
        self.assertEqual(live.live("echo hi;# tail"), "echo hi;")
        self.assertEqual(live.live("echo hi >#f"), "echo hi >")
        self.assertEqual(live.live("cat <#f"), "cat <")

    def test_trailing_whitespace_survives_on_a_line_without_a_comment(self):
        # Only the space a removed comment leaves behind is trimmed; the rest
        # of the line is what the source says.
        self.assertEqual(live.live("echo hi   "), "echo hi   ")
        self.assertEqual(live.live("echo hi   # note"), "echo hi")

    def test_the_line_count_survives_a_real_run_block(self):
        for step in workflow.steps():
            self.assertEqual(
                len(live.live(step.run).splitlines()),
                len(step.run.splitlines()),
                step.name,
            )


class NamingTest(unittest.TestCase):
    """Which live lines name a value: lines, not occurrences."""

    def test_two_lines_naming_the_value_give_two(self):
        text = "A: x\n# A: x in a comment\nB: x\n"

        self.assertEqual(live.naming(text, "A: x"), ["A: x"])
        self.assertEqual(live.naming(text, "x"), ["A: x", "B: x"])

    def test_one_line_naming_the_value_twice_gives_one(self):
        self.assertEqual(live.naming("a x x\n", "x"), ["a x x"])


if __name__ == "__main__":
    unittest.main()
