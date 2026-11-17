"""The frozen lines have one reader, and the tests go through it.

`frozen_var` / `frozen_list` / `frozen_write` in scripts/lib/pkg.sh are the
gates the pipeline reads and writes a producer's lines with, and the kinds table
beside them says what each key may hold. `test_bump` used to split the lines
itself: its parser took the last of two `bump=` lines, while the workflow's
reader fails on the duplicate -- so a producer that wrote twice kept the test
job green and only bothered a publish run ten minutes later.

This holds the test-side reader to the production rules, so the failure lands in
the test job, in the same words -- and holds the table, its readers and its
writers to each other, three ways. GLOSSARY keeps no second list for them to
drift from: the entry says what a frozen line is and points at the table.

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

# A frozen line's name, where a program reads it: `line_field bump ...` or
# `frozen_var bump ...`. The shell's own definitions take `"$1"` there, which is
# why the pattern wants a literal name.
READ = re.compile(r"(?:line_field(?:_or)?|frozen_(?:var|list))\s+([a-z][a-z0-9_]*)")

# The same name where a program writes it. A producer is not a reader: counting
# `frozen_write notes` here would let the list promise a reader for a key whose
# only call site produces it.
WRITE = re.compile(r"frozen_write\s+([a-z][a-z0-9_]*)")

# A 包清单 row is read by its own door -- `pkglist_field` and the two answers
# over it -- which is not a `line_field` call, so it needs a pattern of its own.
PKGLIST = re.compile(r"pkglist_(?:field|dirs|names)\b")

# The producer column names a script's file stem, or one of bump.py's modes.
MODES = {"entry-check", "render", "packages"}

# A run of backticked `x=` joined by a separator -- a list. One backticked key
# is a sentence about that key (`pkgver=` is the counterexample the entry
# teaches), so the separators matter: `/` and `、`, `，` and `,` are how a list
# of keys gets written in this repository's prose.
LIST_RUN = re.compile(r"`[a-z][a-z0-9_]*=`(?:\s*[/、，,]\s*`[a-z][a-z0-9_]*=`)+")




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


def writes_in(text: str) -> set[str]:
    """The frozen-line names a file writes through the gate, code lines only.

    Only the gate's calls. bump.py prints its own lines as f-strings, and two of
    them (`pinned=`, `carried=`) are logs the table does not own -- a direction
    that counted them would demand a row per log line. Its contract keys are
    held the other way, by the row that names it as their producer and by the
    step that reads them.
    """
    names: set[str] = set()
    for line in text.splitlines():
        names.update(WRITE.findall(line.split("#", 1)[0]))
    return names


def entry_of(glossary: str) -> str:
    """GLOSSARY's 冻结行 entry, up to its next heading."""
    return glossary.split("**冻结行**：", 1)[1].split("\n_Avoid_", 1)[0]


def sources(repo: Path = REPO) -> list[Path]:
    """Every file whose frozen-line calls count: the scripts and the workflow."""
    return [*repo.glob("scripts/**/*.sh"), *repo.glob("scripts/**/*.py"),
            *repo.glob(".github/workflows/*.yml")]


def readers(repo: Path = REPO) -> set[str]:
    """Every name a program reads -- through a gate or through `line_field`."""
    return {name for path in sources(repo) for name in keys_in(path.read_text())}


def writers(repo: Path = REPO) -> set[str]:
    """Every name a program writes through the write gate.

    The list promises a reader; a producer is a different promise, and the two
    directions are asked separately so `release.sh`'s three lines cannot stand
    in for a reader that moved away.
    """
    return {name for path in sources(repo) for name in writes_in(path.read_text())}


def keys() -> set[str]:
    """Every key the kinds table names."""
    return {row["key"] for row in frozen.table()}


def pkglist_readers() -> list[str]:
    """Files outside pkg.sh that read the 包清单 through its own door.

    pkg.sh defines `pkglist_field`, so counting its own body would make the
    question true of every tree; what the row needs is a *caller*.
    """
    return [
        path.name
        for path in sources()
        if path.name != "pkg.sh"
        and PKGLIST.search("\n".join(l.split("#", 1)[0] for l in path.read_text().splitlines()))
    ]


def read_keys() -> set[str]:
    """Every key a program reads: through a gate, or the 包清单's own door."""
    names = readers()
    if pkglist_readers():
        names.add("package")
    return names


def producer_of(token: str) -> Path:
    """The source a producer token names: a script, or bump.py for its modes."""
    return REPO / "scripts" / ("bump.py" if token in MODES else f"{token}.sh")


def writes_key(text: str, key: str) -> bool:
    """Does that source write this key -- through the gate, or as bump.py does.

    Code lines only: a producer whose last call is commented out no longer
    writes anything, and a guard satisfied by the prose above it is the shape
    this repository has removed twice.
    """
    code = "\n".join(line.split("#", 1)[0] for line in text.splitlines())
    if key == "package":
        return 'f"package ' in code
    return f"frozen_write {key} " in code or f'f"{key}=' in code


def leftovers(repo: Path = REPO) -> list[str]:
    """Files outside pkg.sh that still read a frozen line through the bare reader.

    `line_field` and `line_field_or` are the gates' inner reads now: every
    caller outside the module goes through `frozen_var` / `frozen_list`, so a
    new direct call is a second reader with its own rules -- the shape this
    repository has removed twice.
    """
    found = []
    for path in sources(repo):
        if path.name == "pkg.sh":
            continue
        code = "\n".join(line.split("#", 1)[0] for line in path.read_text().splitlines())
        if re.search(r"\bline_field(?:_or)?\s", code):
            found.append(path.name)
    return found




class LeftoverReaderTest(unittest.TestCase):
    """The gates are the only door: nothing outside pkg.sh reads a line bare.

    The last direct reads moved with #13's keys, and a guard is what makes the
    absence stay true -- a new `line_field` call in a step or a script would be
    a reader with none of the table's answers, and nothing else would notice.
    """

    def test_nothing_outside_pkg_sh_calls_the_bare_reader(self):
        self.assertEqual(leftovers(), [])

    def test_a_bare_call_would_be_caught(self):
        # The detector itself: a call spelled the way the gate's caller used to
        # spell it, so this guard cannot pass by matching no pattern at all.
        self.assertTrue(re.search(r"\bline_field(?:_or)?\s", 'bump="$(line_field bump "$log")"'))


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

    def test_an_absent_required_key_is_the_readers_complaint(self):
        # The table says whether a key may be absent, and `bump` may not: the
        # reader that answers None for it would be answering a question the
        # pipeline already answered, with the wrong answer.
        with self.assertRaises(AssertionError) as caught:
            frozen.field("bump", "upstream=0.1.9\n")

        self.assertIn("no bump=", str(caught.exception))

    def test_an_absent_optional_key_is_the_callers_call(self):
        # `gates` is the one key the table calls optional, so its absence is a
        # value this reader can hand back as None.
        self.assertIsNone(frozen.field("gates", "tests=7\n"))

    def test_a_domain_failure_reads_as_the_gates_sentence(self):
        with self.assertRaises(AssertionError) as caught:
            frozen.field("bump", "bump=2\n")

        self.assertIn("bump=2 is not 0 or 1", str(caught.exception))

    def test_a_path_that_is_not_there_reads_as_the_gates_sentence(self):
        with self.assertRaises(AssertionError) as caught:
            frozen.field("sums", "sums=/tmp/nothing-sums\n")

        self.assertIn("sums=/tmp/nothing-sums is not a file", str(caught.exception))

    def test_a_gate_call_is_a_reader(self):
        # The gates are the readers now: without this, a key read through
        # `frozen_var` would count as unread and the list's reverse direction
        # would demand the key be dropped instead of moved.
        self.assertEqual(keys_in('frozen_var bump "$f" bump\n'), {"bump"})
        self.assertEqual(keys_in('frozen_list assets "$f" files\n'), {"assets"})

    def test_a_producer_is_not_a_reader(self):
        # `release.sh` writes `notes=`; counting that as a read would let the
        # list keep a key whose reader moved away, which is the direction this
        # guard exists for.
        self.assertEqual(keys_in('frozen_write notes "$path"\n'), set())
        self.assertEqual(writes_in('frozen_write notes "$path"\n'), {"notes"})

    def test_a_duplicated_line_is_the_readers_complaint(self):
        with self.assertRaises(AssertionError) as caught:
            frozen.field("bump", "bump=0\nbump=1\n")

        self.assertIn("2 bump= lines", str(caught.exception))

    def test_an_empty_value_is_not_a_value(self):
        with self.assertRaises(AssertionError) as caught:
            frozen.field("bump", "bump=\n")

        self.assertIn("empty bump=", str(caught.exception))


class TableGuardTest(unittest.TestCase):
    """表、读点与写点三向一致，而 GLOSSARY 不再留第二份清单。

    The 冻结行 entry used to enumerate the keys, and the guard held that list to
    the call sites; a key the workflow read had never been listed, so the
    sentence "冻结行就是这些" was false the day it was written. The entry points
    at the table now, and the three directions are asked of it: a row with no
    producer or no reader is a promise nothing keeps, and a key read or written
    without a row is a call the gates refuse at run time -- after the comparison,
    in the case of the entry check.
    """

    def test_every_row_has_a_reader(self):
        self.assertEqual(sorted(keys() - read_keys()), [])

    def test_every_key_a_program_reads_is_a_row(self):
        self.assertEqual(sorted(read_keys() - keys()), [])

    def test_every_key_a_program_writes_is_a_row(self):
        self.assertEqual(sorted(writers() - keys()), [])

    def test_every_row_names_a_producer_that_writes_it(self):
        for row in frozen.table():
            with self.subTest(key=row["key"]):
                source = producer_of(row["producer"])
                self.assertTrue(
                    source.is_file(), f"the table names {row['producer']}, which is not a file"
                )
                self.assertTrue(writes_key(source.read_text(), row["key"]), row)

    def test_a_row_without_a_reader_is_caught(self):
        # The mutation is in the tree's text, not in a set: take the release
        # step's read of `assets` away and the row is the only place the key
        # survives. A key with two readers would answer for itself through the
        # other one, so this picks one that has exactly one.
        source = (REPO / ".github" / "workflows" / "publish.yml").read_text()
        stripped = source.replace("frozen_list assets /tmp/release.txt assets", "")
        self.assertNotEqual(stripped, source, "the read moved; reword this test")
        names = {
            name
            for path in sources()
            if path.name != "publish.yml"
            for name in keys_in(path.read_text())
        } | keys_in(stripped)
        if pkglist_readers():
            names.add("package")

        self.assertEqual(sorted(keys() - names), ["assets"])

    def test_a_key_read_without_a_row_is_caught(self):
        # A call site naming a key no row carries, read the way the guard reads
        # one: the scanner must see it, and the difference must name it.
        self.assertEqual(sorted(keys_in('frozen_var ghost "$f" ghost\n') - keys()), ["ghost"])

    def test_the_package_list_row_has_a_caller(self):
        # The row's reader is `pkglist_field`, and pkg.sh defining it is not a
        # caller: a row whose last caller went is a contract nothing keeps.
        self.assertTrue(pkglist_readers(), "nothing outside pkg.sh reads the 包清单")

    def test_a_producer_that_stops_writing_its_key_is_caught(self):
        # Deleted, and commented out: a guard satisfied by the prose above the
        # call is the shape this repository has removed twice.
        row = next(row for row in frozen.table() if row["key"] == "assets")
        source = producer_of(row["producer"]).read_text()
        commented = source.replace(
            'frozen_write assets "$out/assets.txt"',
            '# frozen_write assets "$out/assets.txt"',
        )

        self.assertTrue(writes_key(source, "assets"))
        self.assertFalse(writes_key(source.replace("frozen_write assets", "echo"), "assets"))
        self.assertFalse(writes_key(commented, "assets"))

    def test_the_glossary_entry_keeps_no_list(self):
        # A second list is a second fact, and the one that goes stale: the entry
        # says what a frozen line is, what its three failures are, and where its
        # rules live -- and names no run of keys.
        entry = entry_of(GLOSSARY.read_text())

        self.assertEqual(LIST_RUN.findall(entry), [])
        self.assertIn("种类表", entry)
        for failure in ("缺行", "写了两行", "空值"):
            self.assertIn(failure, entry)

    def test_a_list_in_the_entry_is_caught(self):
        # Every way this repository's prose joins a list of keys: the guard that
        # only knew `/` let a re-enumeration with `、` through.
        for entry in (
            "生产者打印一次的 `name=value` 行：`upstream=` / `target=` / `bump=`。",
            "交出 `upstream=`、`target=`、`bump=`。",
            "交出 `upstream=`, `target=`。",
            "交出 `upstream=`，`target=`。",
        ):
            with self.subTest(entry=entry):
                self.assertEqual(len(LIST_RUN.findall(entry)), 1)
        # One backticked key is a sentence about it, not a list: the entry names
        # `pkgver=` as the line that taught the rule.
        self.assertEqual(LIST_RUN.findall("它唯一的读者是一条测试：`pkgver=`。"), [])

    def test_a_comment_naming_a_reader_is_not_a_reader(self):
        # `pkg.sh` documents both readers, and its own bodies pass "$1"; a list
        # satisfied by that prose would pass forever.
        self.assertEqual(keys_in("# line_field ghost ...\nline_field bump \"$log\"\n"), {"bump"})
        self.assertEqual(keys_in("line_field_or bump \"$check\" 0\n"), {"bump"})


if __name__ == "__main__":
    unittest.main()
