"""The per-commit gate: three answers, each of them a line a reader can act on.

`scripts/check-range.sh` is the loop AGENTS.md asks for when a change lands in
pieces -- one worktree per commit, each commit's own tree through its own gate.
It is the only module in `scripts/` whose verdict nothing drove, and its three
branches are all reachable:

- the tree's gate prints `gates=` and a count -> that count is reported;
- it prints `gates=` and no count -> the gate is broken, not old, so FAIL;
- it prints neither -> a tree older than the contract, so `none`, and the
  commit still gets its verdict from its exit code.

That last branch is why the contract is a line in the log rather than a grep
over the gate's source: a behaviour-identical rewrite of the count's call site
(`line_field "$COUNT_KEY"`) left the number in the log and reported `none`
anyway, with every test green because this file did not exist.

The gate's own table is held here too, in both directions: a row whose function
is gone, and a function no row runs.

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CHECK = REPO / "scripts" / "check.sh"
RANGE = REPO / "scripts" / "check-range.sh"
LIB = REPO / "scripts" / "lib" / "pkg.sh"

sys.path.insert(0, str(REPO / "tests"))

import gate  # noqa: E402  (the reader of check.sh: its table, bodies and note)


class GateTableTest(unittest.TestCase):
    """Every row has a body, and every body is a row.

    A row without a body is a gate that vanished from the list of gates -- the
    summary would still count what it declared, so the count alone cannot see
    it. A body no row runs is a gate that was dropped from the table and left
    behind, which reads to the next person as one that still runs.
    """

    def test_every_row_has_a_body(self):
        source = CHECK.read_text()
        rows = gate.rows(source)
        self.assertEqual(rows, ["tests", "shellcheck", "ruff", "actionlint", "probe"])
        self.assertEqual([slug for slug in rows if slug not in gate.functions(source)], [])

    def test_every_body_is_a_row(self):
        source = CHECK.read_text()
        self.assertEqual([slug for slug in gate.functions(source) if slug not in gate.rows(source)], [])

    def test_a_deleted_gate_is_caught(self):
        # The mutation this file exists for: twenty lines gone, the run green,
        # the summary saying everything had passed.
        source = CHECK.read_text()
        dropped = re.sub(r"^gate_ruff\(\) \{.*?^\}\n", "", source, flags=re.M | re.S)
        self.assertNotEqual(dropped, source, "the gate moved; reword this test")
        self.assertEqual(
            [slug for slug in gate.rows(dropped) if slug not in gate.functions(dropped)],
            ["ruff"],
        )

    def test_a_gate_dropped_from_the_table_is_caught(self):
        source = CHECK.read_text()
        moved = source.replace('    "ruff|ruff"\n', "")
        self.assertNotEqual(moved, source, "the row moved; reword this test")
        self.assertEqual(
            [slug for slug in gate.functions(moved) if slug not in gate.rows(moved)],
            ["ruff"],
        )


class RangeTest(unittest.TestCase):
    """The real check-range.sh against a fixture history, one gate per commit.

    The gates are three-line stubs: this is about the loop's reading of three
    log shapes, not about running the real gate (which would download linters
    and build a package). check-range.sh itself is the production copy, and the
    lib it sources is the production one, so the reader under test is the one
    the repository runs.
    """

    COUNTED = '#!/usr/bin/env bash\necho "== offline tests"\necho "tests=7"\necho "gates=1/1"\nexit 0\n'
    COUNT_MISSING = '#!/usr/bin/env bash\necho "== offline tests"\necho "gates=1/1"\nexit 0\n'
    NO_CONTRACT = '#!/usr/bin/env bash\necho "== offline tests"\necho "all gates passed"\nexit 0\n'
    # A gate that counts in words, and one whose contract line is the wrong
    # shape. Both used to be reported as green: `tests=` was read as a string
    # and `gates=bogus` was simply "not pre-contract".
    COUNT_NOT_A_NUMBER = '#!/usr/bin/env bash\necho "tests=seven"\necho "gates=1/1"\nexit 0\n'
    # No contract line at all, and a count in words: the absence is the table's
    # answer and comes first, so the count is never read.
    NO_CONTRACT_BAD_COUNT = '#!/usr/bin/env bash\necho "tests=seven"\nexit 0\n'
    GATES_NOT_A_COUNT = '#!/usr/bin/env bash\necho "tests=7"\necho "gates=bogus"\nexit 0\n'

    @staticmethod
    def counted_as(count: int) -> str:
        """A gate that reports that many tests, for the count-comparison cases."""
        return f'#!/usr/bin/env bash\necho "tests={count}"\necho "gates=1/1"\nexit 0\n'

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "repo"
        (self.root / "scripts" / "lib").mkdir(parents=True)
        for source, target in ((RANGE, "check-range.sh"), (LIB, "lib/pkg.sh")):
            (self.root / "scripts" / target).write_bytes(source.read_bytes())
        (self.root / "scripts" / "check-range.sh").chmod(0o755)
        self.git("init", "-q", "--initial-branch=main", ".")
        # A base commit the ranges below start from: no contract, no count, and
        # no verdict either way -- it is the history's floor, not a case.
        self.commit("base", '#!/usr/bin/env bash\nexit 0\n')

    def git(self, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-c", "user.email=test@example.invalid", "-c", "user.name=test", *args],
            cwd=self.root if cwd is None else cwd,
            check=True,
            capture_output=True,
            text=True,
        )

    def commit(self, message: str, gate: str) -> str:
        """Stage the tree with that gate, commit it, and return the short sha."""
        path = self.root / "scripts" / "check.sh"
        path.write_text(gate)
        path.chmod(0o755)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "--short", "HEAD").stdout.strip()

    def run_range(self, spec: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["./scripts/check-range.sh", spec],
            cwd=self.root,
            text=True,
            capture_output=True,
        )

    def test_the_three_answers_are_three_lines(self):
        self.commit("a gate that counts", self.COUNTED)
        self.commit("a gate that lost its count", self.COUNT_MISSING)
        self.commit("a gate older than the contract", self.NO_CONTRACT)

        result = self.run_range("main~3..main")

        self.assertEqual(result.returncode, 1, result.stderr)
        counted, missing, old = self.git("log", "--reverse", "--format=%h", "main~3..main").stdout.split()
        self.assertIn(f"{counted} ok   tests=7 gates=1/1", result.stdout)
        # No line at all and a line that is not a count are one answer: the
        # gate printed a contract and no number to report.
        self.assertIn(f"{missing} FAIL a broken tests= line", result.stderr)
        # The old tree is not a failure: its exit code decides, and it has no
        # contract to break. It says so, and still gets a verdict line.
        self.assertIn(f"{old}: its gate prints no gates= line", result.stderr)
        self.assertIn(f"{old} ok   tests=none gates=none", result.stdout)
        self.assertIn("1 of 3 commit(s) failed their own tree", result.stderr)

    def test_a_count_that_is_not_a_number_is_a_broken_gate(self):
        # The table says `tests=` is an integer. A gate that prints a word is
        # one this loop cannot report a number for, and reporting the word as
        # the count is how a broken gate reads as a green commit.
        self.commit("a gate that counts in words", self.COUNT_NOT_A_NUMBER)

        result = self.run_range("main~1..main")

        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("FAIL a broken tests= line", result.stderr)

    def test_a_gates_line_that_is_not_a_count_is_a_broken_gate(self):
        self.commit("a gate with a malformed contract", self.GATES_NOT_A_COUNT)

        result = self.run_range("main~1..main")

        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("FAIL a broken gates= line", result.stderr)

    def test_an_old_tree_is_not_read_for_a_count(self):
        # Which of the two failures wins is the table's answer, not a choice
        # this loop makes quietly: `gates` may be absent, and an absent one
        # means the tree predates the contract -- so a count it printed anyway
        # is not read at all.
        commit = self.commit("a tree older than the contract, counting in words", self.NO_CONTRACT_BAD_COUNT)

        result = self.run_range("main~1..main")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"{commit} ok   tests=none gates=none", result.stdout)
        self.assertIn("its gate prints no gates= line", result.stderr)

    def test_a_commit_that_loses_tests_is_caught(self):
        # A deleted test does not fail: the suite is green without it, and the
        # count still goes up in a slice that also adds cases. The count moving
        # down is the one signal, so it is compared against the commit before.
        self.commit("a gate that counts nine", self.counted_as(9))
        lost = self.commit("a gate that lost two of them", self.counted_as(7))

        result = self.run_range("main~2..main")

        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn(f"{lost} FAIL tests=7, down from 9", result.stderr)
        self.assertIn("1 of 2 commit(s) failed their own tree", result.stderr)

    def test_a_commit_that_says_it_dropped_tests_passes(self):
        # The escape hatch is a line in the message, not a lower count nobody
        # explains: a feature's tests go with the feature, and that is a fact
        # the commit can state.
        self.commit("a gate that counts nine", self.counted_as(9))
        dropped = self.commit(
            "removed the feature\n\ntest-drop: its two cases went with it",
            self.counted_as(7),
        )

        result = self.run_range("main~2..main")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"{dropped} ok   tests=7 gates=1/1", result.stdout)

    def test_a_green_range_says_so(self):
        self.commit("a gate that counts", self.COUNTED)
        result = self.run_range("main~1..main")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("tests=7 gates=1/1", result.stdout)
        self.assertIn("all 1 commit(s) passed their own tree", result.stdout)


if __name__ == "__main__":
    unittest.main()
