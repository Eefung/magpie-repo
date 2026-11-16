"""The facts the docs state, held to the programs that own them.

README is read by someone who will act on what it says, and it is not
executable: a sentence can be asserted, not generated. Two of its sentences
quote a value a program decides -- when the pipeline runs, and what the desktop
package declares against -- and neither had a reader, so moving the cron or
dropping `conflicts=` left the prose promising something that is no longer
true. (The third such fact, the 保留窗口's number, is held by
`test_inventory.RetentionWindowTest`, next to the window it belongs to.)

AGENTS.md's test map is the same shape and is held here too: a guard that lists
files fails until the map is complete, which is what makes the sentence above
the table a checked claim rather than a hope.

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import re
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "tests"))

import bump  # noqa: E402  (the module lives beside the scripts, not on the path)
import workflow  # noqa: E402  (the one reader of publish.yml)


def readme() -> str:
    return (REPO / "README.md").read_text()


def template(pkg: str) -> str:
    return (REPO / "templates" / f"{pkg}.PKGBUILD.in").read_text()


class ScheduleTest(unittest.TestCase):
    """README says when the run happens; the workflow's cron is the answer."""

    def stated(self, source: str | None = None) -> str:
        """The README's sentence, derived from the cron the workflow declares."""
        minute, hour, *_ = workflow.schedule(source=source).split()
        return f"每天 {int(hour):02d}:{int(minute):02d} UTC"

    def test_the_readme_states_the_time_the_cron_declares(self):
        self.assertIn(self.stated(), readme())

    def test_a_moved_cron_without_the_readme_is_caught(self):
        moved = workflow.text().replace('cron: "17 4 * * *"', 'cron: "17 6 * * *"')
        self.assertIn('cron: "17 6 * * *"', moved, "the cron line moved; reword this test")
        self.assertNotIn(self.stated(moved), readme())

    def test_two_cron_lines_are_a_second_answer_not_a_choice(self):
        doubled = workflow.text().replace(
            '    - cron: "17 4 * * *"', '    - cron: "17 4 * * *"\n    - cron: "0 0 * * *"'
        )
        self.assertEqual(doubled.count("cron:"), 2, "the schedule block moved; reword this test")
        with self.assertRaises(AssertionError):
            workflow.schedule(source=doubled)


class ProvidesClaimTest(unittest.TestCase):
    """README's FAQ says the desktop package both provides and conflicts `magpie`.

    A user reads that to know why the package cannot live beside the unrelated
    `magpie` one. It is a claim about the template, and the template is where
    `provides=` and `conflicts=` live, so the sentence is held to them instead
    of to a memory of what they said.
    """

    DESKTOP = "magpie-bin"  # the 桌面版 the sentence is about

    def claimed(self, document: str) -> str:
        """The line of the document that makes the claim, or a failure."""
        for line in document.splitlines():
            if "provides" in line and "conflicts" in line:
                return line
        raise AssertionError("README no longer says what provides/conflicts are")

    def problems(self, document: str, template_text: str) -> list[str]:
        """Names the template declares that the document's sentence omits."""
        claim = self.claimed(document)
        names = (bump.array_entries(template_text, "provides") or []) + (
            bump.array_entries(template_text, "conflicts") or []
        )
        return [
            f"the claim does not name {name!r}" for name in names if f"`{name}`" not in claim
        ]

    def test_the_readme_names_what_the_template_declares(self):
        self.assertEqual(self.problems(readme(), template(self.DESKTOP)), [])

    def test_a_name_the_template_drops_is_caught(self):
        # The guard's own fixture: a template whose conflict is another name
        # while the sentence still says `magpie`. The previous version of this
        # test asserted on the string it had just edited and passed with the
        # guard removed.
        drifted = template(self.DESKTOP).replace("conflicts=('magpie')", "conflicts=('magpie-x')")
        self.assertIn("conflicts=('magpie-x')", drifted, "the conflict line moved; reword this test")
        self.assertEqual(
            [problem for problem in self.problems(readme(), drifted) if "magpie-x" in problem],
            ["the claim does not name 'magpie-x'"],
        )


class TestMapTest(unittest.TestCase):
    """AGENTS.md's test map lists every module in tests/, or it is not a map.

    The map is what a reader -- human or agent -- uses instead of grepping, and
    the sentence above it promises completeness. `tests/frozen.py` was missing
    while every test imported it, and the line count in the same sentence had
    drifted by six hundred lines; both are the shape this repository keeps
    finding, a document stating a fact a directory decides. The count is gone,
    and the listing is held to the directory here.

    The names are read from the **table's rows**, not from the file: the
    sentence above the table names a file to say who checks the map, and a
    document-wide search would count that sentence as a row.
    """

    SECTION = "## 测试地图"

    def listed(self, map_text: str) -> set[str]:
        """The file names the map's own table rows list."""
        section = map_text.split(self.SECTION, 1)[1].split("\n## ", 1)[0]
        return {
            line.split("|")[1].strip().strip("`")
            for line in section.splitlines()
            if line.startswith("| `")
        }

    def unlisted(self, map_text: str) -> list[str]:
        """Modules under tests/ the map's table does not list."""
        listed = self.listed(map_text)
        return [
            path.name for path in sorted((REPO / "tests").glob("*.py")) if path.name not in listed
        ]

    def agents(self) -> str:
        return (REPO / "AGENTS.md").read_text()

    def rows(self) -> set[str]:
        return {path.name for path in (REPO / "tests").glob("*.py")}

    def test_every_module_is_in_the_map(self):
        self.assertEqual(self.unlisted(self.agents()), [])
        # And the table lists nothing that is not a module in tests/.
        self.assertEqual(self.listed(self.agents()) - self.rows(), set())

    def test_a_module_the_map_omits_is_caught(self):
        drifted = "\n".join(
            line
            for line in self.agents().splitlines()
            if not line.startswith("| `test_docs.py`")
        )
        self.assertNotIn("test_docs.py", self.listed(drifted), "the row moved; reword this test")
        self.assertEqual(self.unlisted(drifted), ["test_docs.py"])


class ChineseSpacingTest(unittest.TestCase):
    """Two Chinese words in a row take no space between them.

    English prose embeds a glossary term with spaces around it -- `the 包清单
    publishes` -- and that is right: those are English word boundaries, and
    dropping them would run two English words together. The same habit carried
    into Chinese prose leaves a space between two Chinese words, where the
    language has no word separator and the space does no work: it reads as a
    typo, and this repository has made it three times.

    A grep is what stops a fourth. The scope is what git tracks, so an untracked
    scratch file is not the gate's business; the self-test below is what keeps
    the rule from passing vacuously.
    """

    SUFFIXES = ("*.md", "*.py", "*.sh", "*.yml", "*.in")
    SPACED = re.compile(r"[\u4e00-\u9fff] +[\u4e00-\u9fff]")

    def tracked(self) -> list[Path]:
        """The files this rule covers: what git would carry, not a walk.

        `--others --exclude-standard` is what makes a brand-new file part of the
        answer: it is not in the index until someone stages it, and the gate runs
        before the commit.
        """
        result = subprocess.run(
            ["git", "-C", str(REPO), "ls-files", "--cached", "--others",
             "--exclude-standard", *self.SUFFIXES],
            capture_output=True,
            text=True,
            check=True,
        )
        return [REPO / line for line in result.stdout.splitlines() if line]

    def spaced(self, text: str) -> list[str]:
        """The lines of this text that set two Chinese words off from each other."""
        return [line.strip() for line in text.splitlines() if self.SPACED.search(line)]

    def test_no_chinese_word_is_set_off_from_the_next(self):
        offenders = [
            f"{path.relative_to(REPO)}: {line[:70]}"
            for path in self.tracked()
            for line in self.spaced(path.read_text())
        ]
        self.assertEqual(offenders, [])

    def test_the_rule_bites(self):
        # Built at run time: a literal of the offending shape in this file would
        # be found by the test above, and a self-test that reddens itself is not
        # one. Both accepted shapes are here too, so the rule cannot be widened
        # by accident -- an English sentence embedding a Chinese term keeps its
        # spaces, and so does a term with a Latin half.
        bad = "落后" + " " + "的第四向"
        self.assertEqual(self.spaced(bad), [bad])
        self.assertEqual(self.spaced("the 包清单 publishes"), [])
        self.assertEqual(self.spaced("arch 表管的是另一半"), [])


if __name__ == "__main__":
    unittest.main()
