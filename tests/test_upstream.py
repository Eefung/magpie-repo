"""The entry check, run for real: whether this run publishes and which 版本.

The decision used to live in publish.yml's `run:` block and was guarded by two
greps over the YAML text -- so a reformat with no behaviour change failed the
test job, and changing `${bump:-0}` to `${bump:-1}` (a check that never
finished now meaning "publish") did not. `scripts/upstream.sh` holds the
decision instead, with a stub 上游 in place of the network, and this drives it
through the door the workflow uses.

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "upstream.sh"

sys.path.insert(0, str(REPO / "tests"))

import frozen  # noqa: E402  (the one reader of the frozen lines, as in production)


def stub(*, upstream: str = "0.1.10", packaged: str = "0.1.10", bump: str | None = "0",
         code: int = 0, raw: str | None = None, packages: bool = True) -> str:
    """A stand-in for bump.py: the frozen lines it would print, and an exit code.

    `raw` replaces the check's body when the case is about its shape rather
    than its values (a duplicated line, a value nobody expects).
    """
    lines = [f"upstream={upstream}", f"packaged magpie-bin={packaged}"]
    if bump is not None:
        lines.append(f"bump={bump}")
    check = raw if raw is not None else "\n".join(lines)
    listing = (
        'print("package packages/magpie-bin magpie-bin")\n'
        '    print("package packages/magpie-cli-bin magpie-cli-bin")'
        if packages
        else "pass"
    )
    return f"""\
import sys

check = {check!r}
if sys.argv[1] == "--check":
    print(check)
    sys.exit({code})
if sys.argv[1] == "--packages":
    {listing}
    sys.exit(0)
sys.exit("stub: unknown mode " + sys.argv[1])
"""


class UpstreamTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.work = self.root / "up"

    def bump(self, source: str) -> Path:
        path = self.root / "bump-stub.py"
        path.write_text(source)
        return path

    def run_script(self, source: str | None = None, *, wanted: str | None = None):
        env = dict(os.environ)
        env.pop("WANTED", None)
        env["BUMP"] = str(self.bump(source or stub()))
        if wanted is not None:
            env["WANTED"] = wanted
        return subprocess.run(
            ["bash", str(SCRIPT), str(self.work)],
            cwd=str(REPO),
            text=True,
            capture_output=True,
            env=env,
        )

    def handover(self, result: subprocess.CompletedProcess) -> dict[str, str]:
        """The lines the script printed, read the way the workflow reads them."""
        self.assertEqual(result.returncode, 0, result.stderr)
        return frozen.fields(result.stdout)

    def test_nothing_behind_means_do_not_publish(self):
        handed = self.handover(self.run_script())

        self.assertEqual(handed, {
            "upstream": "0.1.10",
            "target": "0.1.10",
            "bump": "0",
            "packages": str(self.work / "packages.txt"),
        })

    def test_a_newer_upstream_means_publish_it(self):
        handed = self.handover(
            self.run_script(stub(upstream="0.1.11", packaged="0.1.10", bump="1"))
        )

        self.assertEqual(handed["upstream"], "0.1.11")
        self.assertEqual(handed["target"], "0.1.11")
        self.assertEqual(handed["bump"], "1")

    def test_a_version_a_human_asked_for_wins_over_the_check(self):
        # The override is a decision, not a comparison: a pinned 版本 publishes
        # even though --check just said the tree is current.
        handed = self.handover(self.run_script(wanted="0.1.5"))

        self.assertEqual(handed["target"], "0.1.5")
        self.assertEqual(handed["bump"], "1")
        # What 上游 looks like is still reported; it is no longer what decides.
        self.assertEqual(handed["upstream"], "0.1.10")

    def test_an_absent_bump_line_does_not_mean_publish(self):
        # A check that exited 0 without its frozen line is a check that never
        # finished. Reading that as "publish" is exactly the rewiring this
        # module exists to make impossible to hide.
        result = self.run_script(stub(bump=None))

        self.assertEqual(self.handover(result)["bump"], "0")

    def test_a_check_that_fails_is_named_and_its_log_survives(self):
        result = self.run_script(stub(upstream="0.1.11", bump="1", code=1))

        self.assertNotEqual(result.returncode, 0)
        # Not swallowed: the stub's own lines, then this module's verdict.
        self.assertIn("upstream=0.1.11", result.stderr)
        self.assertIn("the entry check failed", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_a_duplicated_frozen_line_is_a_protocol_break(self):
        # Two `bump=` lines is the producer having written twice, which
        # line_field reports instead of picking one. The workflow would die on
        # the same line; the test job must see it too -- and see it as the
        # reader's complaint, not as "bump= is not 0 or 1", which is what a
        # reader that swallowed the message would leave behind.
        result = self.run_script(stub(raw="upstream=0.1.10\nbump=0\nbump=1"))

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("2 bump= lines", result.stderr)
        self.assertNotIn("is not 0 or 1", result.stderr)

    def test_anything_but_zero_or_one_is_refused(self):
        # The gate downstream is `if: bump == '1'`; a third value would skip a
        # publish silently, which is the failure this guard is for.
        result = self.run_script(stub(bump="2"))

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("bump=2 is not 0 or 1", result.stderr)

    def test_the_package_list_is_written_and_its_path_handed_over(self):
        handed = self.handover(self.run_script())

        listing = Path(handed["packages"])
        self.assertTrue(listing.is_file(), handed["packages"])
        self.assertEqual(listing.read_text().splitlines(), [
            "package packages/magpie-bin magpie-bin",
            "package packages/magpie-cli-bin magpie-cli-bin",
        ])

    def test_an_empty_package_list_is_refused(self):
        result = self.run_script(stub(packages=False))

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("names no packages", result.stderr)

    def test_the_check_log_is_a_log_not_a_frozen_line(self):
        # It goes to stderr, where a human reads it; stdout carries only what a
        # program reads, so nothing downstream has to filter prose out.
        result = self.run_script()

        self.assertIn("packaged magpie-bin=0.1.10", result.stderr)
        self.assertEqual(
            [line.split("=", 1)[0] for line in result.stdout.splitlines()],
            ["upstream", "target", "bump", "packages"],
        )


if __name__ == "__main__":
    unittest.main()
