"""The relay: the check's four lines out, and the 包清单's path beside them.

`scripts/upstream.sh` used to decide which 版本 a run publishes and to ask the
fifth direction itself. Both are the entry check's now, so this drives the
script against a stub check and asserts what it does with what it is handed:
relay, refuse a broken handover, and write the 包清单.

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
         code: int = 0, raw: str | None = None, packages: bool = True,
         carried: str | None = None, target: str | None = None, pin: str = "0") -> str:
    """A stand-in for bump.py: the lines it would print, and an exit code.

    `raw` replaces the check's body when the case is about its shape rather
    than its values (a duplicated line, a line nobody wrote). `carried` is the
    版本 the tree holds; by default it is the one `packaged` carries, which is
    what production prints. `target` is what a scheduled run renders, so it
    follows `upstream` unless a case is about a pinned run.
    """
    carried = packaged.split("-", 1)[0] if carried is None else carried
    target = upstream if target is None else target
    lines = [
        f"upstream={upstream}",
        f"packaged magpie-bin={packaged}",
        f"carried={carried}",
        f"target={target}",
        f"pin={pin}",
    ]
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

    def run_script(self, source: str | None = None, *, repo: str | None = None):
        env = dict(os.environ)
        # The run's own context must not leak in from this machine: `WANTED`
        # and `RELEASE_REPO` are read by the check, and a stray one would
        # decide a test that never said anything about them.
        env.pop("WANTED", None)
        env.pop("RELEASE_REPO", None)
        env["BUMP"] = str(self.bump(source or stub()))
        argv = ["bash", str(SCRIPT), str(self.work)]
        if repo is not None:
            argv.append(repo)
        return subprocess.run(
            argv,
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
            "pin": "0",
            "packages": str(self.work / "packages.txt"),
        })

    def test_a_newer_upstream_means_publish_it(self):
        handed = self.handover(
            self.run_script(stub(upstream="0.1.11", packaged="0.1.10", bump="1"))
        )

        self.assertEqual(handed["upstream"], "0.1.11")
        self.assertEqual(handed["target"], "0.1.11")
        self.assertEqual(handed["bump"], "1")

    def test_a_pinned_run_is_relayed_as_the_check_said_it(self):
        # A rollback: `target` is the 版本 a human asked for, and `pin=1` is
        # what the commit step writes to PINNED. The relay carries both; it no
        # longer works either out.
        handed = self.handover(self.run_script(stub(bump="1", target="0.1.5", pin="1")))

        self.assertEqual(handed["target"], "0.1.5")
        self.assertEqual(handed["pin"], "1")
        self.assertEqual(handed["bump"], "1")

    def test_an_absent_bump_line_is_a_broken_check(self):
        # A check that exited 0 without its conclusion is a check that never
        # finished. It has no default here: `line_field` names the line, and
        # the run stops instead of reading a missing conclusion as "nothing to
        # do".
        result = self.run_script(stub(bump=None))

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no bump= in", result.stderr)

    def test_a_missing_target_line_is_a_broken_check(self):
        # The relay reads four lines and defaults none of them. `target=` is
        # what the render takes, so a check that dropped it must stop the run
        # rather than hand the render an empty 版本.
        result = self.run_script(stub(raw="upstream=0.1.10\npin=0\nbump=0"))

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no target= in", result.stderr)

    def test_the_repository_is_handed_to_the_check(self):
        # The check asks the fifth direction about a repository, and argv is
        # the only place this script can learn one. The handover is one line,
        # and a break in it would leave every run asking about nobody while a
        # stub-checking test stayed green.
        marker = self.root / "release_repo.txt"
        source = (
            "import os, pathlib, sys\n"
            "if sys.argv[1] == '--check':\n"
            f"    pathlib.Path({str(marker)!r}).write_text(os.environ.get('RELEASE_REPO', ''))\n"
        ) + stub()
        self.run_script(source, repo="owner/repo")

        self.assertEqual(marker.read_text(), "owner/repo")

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
        result = self.run_script(
            stub(raw="upstream=0.1.10\ntarget=0.1.10\npin=0\nbump=0\nbump=1")
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("2 bump= lines", result.stderr)
        self.assertNotIn("is not 0 or 1", result.stderr)

    def test_anything_but_zero_or_one_is_refused(self):
        # The gate downstream is `if: bump == '1'`; a third value would skip a
        # publish silently, which is the failure this guard is for.
        result = self.run_script(stub(bump="2"))

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("bump=2 is not 0 or 1", result.stderr)

    def test_a_pin_that_is_neither_zero_nor_one_is_refused(self):
        # The commit step writes PINNED when `[ "$pin" = 1 ]`, so a third value
        # would silently pin nothing.
        result = self.run_script(stub(pin="maybe"))

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("pin=maybe is not 0 or 1", result.stderr)

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
            ["upstream", "target", "bump", "pin", "packages"],
        )


if __name__ == "__main__":
    unittest.main()
