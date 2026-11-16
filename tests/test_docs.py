"""The facts the docs state, held to the programs that own them.

README is read by someone who will act on what it says, and it is not
executable: a sentence can be asserted, not generated. Two of its sentences
quote a value a program decides -- when the pipeline runs, and what the desktop
package declares against -- and neither had a reader, so moving the cron or
dropping `conflicts=` left the prose promising something that is no longer
true. (The third such fact, the 保留窗口's number, is held by
`test_inventory.RetentionWindowTest`, next to the window it belongs to.)

AGENTS.md's test map is the same shape and is *not* guarded here yet: a guard
that lists files must fail until the map is complete, and the map is a file an
agent may not edit without approval.

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

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

    def test_the_readme_names_the_name_the_template_declares(self):
        text = template(self.DESKTOP)
        provides = bump.array_entries(text, "provides")
        self.assertEqual(provides, bump.array_entries(text, "conflicts"))
        self.assertTrue(provides, "the template declares no provides=")
        line = self.claimed(readme())
        for name in provides:
            self.assertIn(f"`{name}`", line)

    def test_a_template_that_stops_conflicting_is_caught(self):
        text = template(self.DESKTOP)
        drifted = text.replace("conflicts=('magpie')", "conflicts=()")
        self.assertNotEqual(drifted, text)
        self.assertNotEqual(
            bump.array_entries(text, "provides"), bump.array_entries(drifted, "conflicts")
        )


if __name__ == "__main__":
    unittest.main()
