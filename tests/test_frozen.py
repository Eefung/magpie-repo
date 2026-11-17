"""The frozen lines have one reader, and the tests go through it.

`line_field` / `line_field_or` in scripts/lib/pkg.sh are what the pipeline
reads a producer's lines with. `test_bump` used to split them itself: its parser
took the last of two `bump=` lines, while the workflow's reader fails on the
duplicate -- so a producer that wrote twice kept the test job green and only
bothered a publish run ten minutes later.

This holds the test-side reader to the production rules, so the failure lands
in the test job, in the same words.

It also holds GLOSSARY's list of frozen lines to the code: the paragraph that
names them said a line is frozen "由 grep 决定", and that grep was the
maintainer's. `release=` was read by the workflow and had never been listed.

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import frozen  # noqa: E402  (the reader the other tests use)

REPO = Path(__file__).resolve().parent.parent
GLOSSARY = REPO / "GLOSSARY.md"

# A frozen line's name, where a program reads it: `line_field bump ...`. The
# shell's own two definitions take `"$1"` there, which is why the pattern wants
# a literal name.
READ = re.compile(r"line_field(?:_or)?\s+([a-z][a-z0-9_]*)")

# The same names as the list writes them: a backticked `bump=`, closing right
# after the `=`. The entry's opening phrase writes the shape itself as
# `name=value`, which is a metavariable and not a line anyone reads.
LISTED = re.compile(r"`([a-z][a-z0-9_]*)=`")


def keys_in(text: str) -> set[str]:
    """The frozen-line names a file reads, from its code lines only.

    Comment lines are dropped before the search: `line_field` appears in prose
    about itself in `pkg.sh`, `check.sh` and the workflow, and a list satisfied
    by prose is a list that cannot go stale -- which is the whole point of
    reading it.
    """
    names: set[str] = set()
    for line in text.splitlines():
        names.update(READ.findall(line.split("#", 1)[0]))
    return names


def entry_of(glossary: str) -> str:
    """GLOSSARY's 冻结行 entry, up to its next heading."""
    return glossary.split("**冻结行**：", 1)[1].split("\n_Avoid_", 1)[0]


# The sentence that says what being in the list means. Everything after it is
# prose *about* the list -- including its counterexample.
LIST_END = "一条行算不算冻结行"


def list_part(glossary: str) -> str:
    """The part of the entry that is the list, before the rule that names it.

    The entry goes on to name `pkgver=` as the counterexample -- the line that
    used to be listed and whose only reader is a test -- so "everything listed
    has a reader" is only answerable over the list itself, never over the prose
    around it.
    """
    return entry_of(glossary).split(LIST_END, 1)[0]


def readers(repo: Path = REPO) -> set[str]:
    """Every name a program reads through line_field, outside tests/."""
    sources = [*repo.glob("scripts/**/*.sh"), *repo.glob("scripts/**/*.py"),
               *repo.glob(".github/workflows/*.yml")]
    return {name for path in sources for name in keys_in(path.read_text())}


def listed(glossary: str) -> set[str]:
    """The names the list itself names."""
    return set(LISTED.findall(list_part(glossary)))


def unlisted(glossary: str, names: set[str]) -> list[str]:
    """The names in `names` that the list does not name."""
    return sorted(set(names) - listed(glossary))


def unread(glossary: str, names: set[str]) -> list[str]:
    """The names the list names that nothing reads through line_field."""
    return sorted(listed(glossary) - set(names))


class FrozenFieldTest(unittest.TestCase):
    def test_a_frozen_line_reads_through(self):
        self.assertEqual(frozen.field("bump", "upstream=0.1.9\nbump=1\n"), "1")

    def test_fields_reads_every_line_through_the_reader(self):
        self.assertEqual(
            frozen.fields("upstream=0.1.9\nbump=1\n"),
            {"upstream": "0.1.9", "bump": "1"},
        )

    def test_fields_refuses_a_duplicate_the_way_the_step_does(self):
        with self.assertRaises(AssertionError) as caught:
            frozen.fields("bump=0\nbump=1\n")

        self.assertIn("2 bump= lines", str(caught.exception))

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


class GlossaryListTest(unittest.TestCase):
    """GLOSSARY lists the frozen lines, and until now nothing read that list.

    The 冻结行 entry answers two questions at once -- which lines a caller may
    not reword freely, and which of them a program actually reads -- and both
    answers were maintained by grep by hand. `release=` was read by the workflow
    through `line_field` and had never been added, so the sentence "冻结行就是
    这些" was already false; a seventh line could appear the same way.

    One direction only. The entry also names `pkgver=` on purpose -- the line
    that used to be listed whose only reader is a test -- so "every name in the
    entry has a call site" would fail on the counterexample that teaches the
    rule. What is checked is the direction that goes stale in silence: every
    name a program reads is named in the entry.
    """

    def test_every_key_a_program_reads_is_in_the_glossary_list(self):
        self.assertEqual(unlisted(GLOSSARY.read_text(), readers()), [])

    def test_every_key_the_list_names_has_a_reader(self):
        # The other direction, which the first version of this guard left out:
        # a key kept in the list after its reader went is a promise about a
        # contract nothing keeps.
        self.assertEqual(unread(GLOSSARY.read_text(), readers()), [])

    def test_a_key_missing_from_the_list_is_caught(self):
        drifted = GLOSSARY.read_text().replace("`release=`、", "")
        self.assertNotEqual(drifted, GLOSSARY.read_text(), "the entry moved; reword this test")
        self.assertEqual(unlisted(drifted, readers()), ["release"])

    def test_a_listed_key_without_a_reader_is_caught(self):
        # `tests` is read by check-range.sh; take the reader away and the list
        # is the only place the name survives.
        self.assertEqual(unread(GLOSSARY.read_text(), readers() - {"tests"}), ["tests"])

    def test_the_counterexample_is_read_as_prose_not_as_a_listing(self):
        # The entry names `pkgver=` *after* the rule, as the line that used to
        # be listed and whose only reader is a test. Reading the whole entry
        # would count it as listed, and the reverse direction would then fail
        # on a sentence that is there to teach.
        self.assertIn("`pkgver=`", entry_of(GLOSSARY.read_text()))
        self.assertNotIn("pkgver", listed(GLOSSARY.read_text()))

    def test_the_list_is_read_from_its_own_entry(self):
        # Scoped to the 冻结行 entry: another section naming a `name=` is a term
        # being explained, not a line claiming to be frozen.
        document = (
            "**包清单**：\n一句话里有 `ghost=` 但不在冻结行那一节。\n"
            "**冻结行**：\n只有 `bump=`。\n"
        )
        self.assertEqual(listed(document), {"bump"})

    def test_a_comment_naming_a_reader_is_not_a_reader(self):
        # `pkg.sh` documents both readers, and its own bodies pass "$1"; a list
        # satisfied by that prose would pass forever.
        self.assertEqual(keys_in("# line_field ghost ...\nline_field bump \"$log\"\n"), {"bump"})
        self.assertEqual(keys_in("line_field_or bump \"$check\" 0\n"), {"bump"})


if __name__ == "__main__":
    unittest.main()
