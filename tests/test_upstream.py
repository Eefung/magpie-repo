"""The entry check, run for real: whether this run publishes and which 版本.

The decision used to live in publish.yml's `run:` block and was guarded by two
greps over the YAML text -- so a reformat with no behaviour change failed the
test job, and changing `${bump:-0}` to `${bump:-1}` (a check that never
finished now meaning "publish") did not. `scripts/upstream.sh` holds the
decision instead, with a stub 上游 in place of the network, and this drives it
through the door the workflow uses.

One reason to publish is not the check's to see -- a Release that failed after
a rebuild's branch push -- so the script asks release-state.sh about it, with a
stub standing in for that script's own `gh`.

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
    than its values (a duplicated line, a value nobody expects). `carried` is
    the 版本 the tree holds; by default it is the one `packaged` carries, which
    is what production prints. `target` is what a scheduled run renders, so it
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


def state_stub(*, verdict: str | None = "present", code: int = 0, raw: str | None = None) -> str:
    """A stand-in for release-state.sh: one answer, and a note that it was asked.

    `ASKED` is where it writes its argv, so a test can prove the question was
    *not* asked (a check that already says publish has nothing to ask about)
    as well as that it was.
    """
    body = raw if raw is not None else f"release={verdict}"
    return f"""\
#! /usr/bin/env python3
import os
import pathlib
import sys

asked = os.environ.get("ASKED")
if asked:
    pathlib.Path(asked).write_text(" ".join(sys.argv[1:]))
print({body!r})
sys.exit({code})
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

    def state(self, source: str) -> Path:
        # Executable: upstream.sh runs it the way it runs the real script, so a
        # stub without the bit fails as "权限不够" -- which is what this test
        # tree hit first.
        path = self.root / "release-state-stub"
        path.write_text(source)
        path.chmod(0o755)
        return path

    def asked(self) -> str | None:
        """What the Release lookup was asked, or None when it was not asked."""
        marker = self.root / "asked.txt"
        return marker.read_text() if marker.exists() else None

    def run_script(self, source: str | None = None, *, state: str | None = None,
                   repo: str | None = "owner/repo"):
        env = dict(os.environ)
        # Both name the run's context, and this machine's must not leak into a
        # test: a real GITHUB_REPOSITORY would reach the real `gh`. The script
        # reads the repository from argv, so the variable is popped even on the
        # paths that pass one -- a version that read the env again would be a
        # second door, and this keeps it shut.
        env.pop("WANTED", None)
        env.pop("GITHUB_REPOSITORY", None)
        env["BUMP"] = str(self.bump(source or stub()))
        env["RELEASE_STATE"] = str(self.state(state or state_stub()))
        env["ASKED"] = str(self.root / "asked.txt")
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

    def test_an_absent_bump_line_is_a_broken_check(self):
        # A check that exited 0 without its conclusion is a check that never
        # finished. It has no default here: `line_field` names the line, and
        # the run stops instead of reading a missing conclusion as "nothing to
        # do". The fifth direction is not asked about a run nobody concluded.
        result = self.run_script(stub(bump=None), state=state_stub(verdict="absent"))

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no bump= in", result.stderr)
        self.assertIsNone(self.asked())

    def test_a_check_that_fails_is_named_and_its_log_survives(self):
        result = self.run_script(stub(upstream="0.1.11", bump="1", code=1))

        self.assertNotEqual(result.returncode, 0)
        # Not swallowed: the stub's own lines, then this module's verdict.
        self.assertIn("upstream=0.1.11", result.stderr)
        self.assertIn("the entry check failed", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_a_release_that_does_not_exist_is_a_reason_to_publish(self):
        # The fifth direction. A rebuild's 版本 is already committed, so after
        # the branch push every direction the check can see is quiet -- and a
        # Release that failed there would never be retried while the 软件源
        # served a build without one.
        result = self.run_script(stub(), state=state_stub(verdict="absent"))
        handed = self.handover(result)

        self.assertEqual(handed["bump"], "1")
        self.assertEqual(handed["target"], "0.1.10")
        # Asked about the 版本 this tree carries, in this repository.
        self.assertEqual(self.asked(), "0.1.10 owner/repo")
        # This branch is new, and stdout is the interface: the other branches
        # are read through frozen.fields, which drops prose, so a leak onto
        # stdout here would have gone unnoticed.
        self.assertEqual(
            [line.split("=", 1)[0] for line in result.stdout.splitlines()],
            ["upstream", "target", "bump", "pin", "packages"],
        )

    def test_the_release_question_is_about_the_version_the_tree_carries(self):
        # A pinned tree: every direction the check can see is quiet because the
        # pin holds, so `target` is 上游's 版本 while the tree still carries the
        # older one. Asking about 上游's would answer "no Release yet" for a
        # 版本 this run must not publish.
        result = self.run_script(
            stub(upstream="0.1.11", packaged="0.1.9-1", bump="0"),
            state=state_stub(verdict="present"),
        )

        self.assertEqual(self.asked(), "0.1.9 owner/repo")
        self.assertEqual(self.handover(result)["bump"], "0")

    def test_a_missing_release_does_not_publish_over_a_pin(self):
        # absent is a reason to publish, and this run would render $target --
        # 上游's 版本, not the one it is missing a Release for. Publishing that
        # would undo the pin on the next upstream move, which is the day-long
        # rollback the pin exists to end; it says so and leaves the tree alone.
        result = self.run_script(
            stub(upstream="0.1.11", packaged="0.1.9-1", bump="0"),
            state=state_stub(verdict="absent"),
        )
        handed = self.handover(result)

        self.assertEqual(handed["bump"], "0")
        self.assertEqual(handed["target"], "0.1.11")
        self.assertIn("v0.1.9 has no Release", result.stderr)
        self.assertIn("dispatch 0.1.9", result.stderr)

    def test_a_release_that_exists_really_is_nothing_to_do(self):
        result = self.run_script(stub(), state=state_stub(verdict="present"))

        self.assertEqual(self.handover(result)["bump"], "0")
        self.assertEqual(self.asked(), "0.1.10 owner/repo")

    def test_a_lookup_that_failed_is_not_a_missing_release(self):
        # A 500 or a rate limit is not "no Release": it says so and publishes
        # only what the check found, and the next run asks again.
        result = self.run_script(
            stub(), state=state_stub(code=1, raw="the release lookup for v0.1.10 answered HTTP 500")
        )

        self.assertEqual(self.handover(result)["bump"], "0")
        self.assertIn("HTTP 500", result.stderr)
        self.assertIn("cannot ask whether v0.1.10 has a Release", result.stderr)

    def test_without_a_repository_the_question_is_skipped_out_loud(self):
        # A hand run names no repository, and this script does not read one out
        # of the environment: guessing would ask about somebody else's releases,
        # and reading Actions' context would make a hand run unable to work.
        result = self.run_script(stub(), repo=None)

        self.assertEqual(self.handover(result)["bump"], "0")
        self.assertIn("no repository named", result.stderr)
        self.assertIsNone(self.asked())

    def test_an_answer_that_is_neither_present_nor_absent_is_refused(self):
        result = self.run_script(stub(), state=state_stub(raw="release=maybe"))

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("'maybe', not present or absent", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_a_pinned_run_is_relayed_as_the_check_said_it(self):
        # A rollback: `target` is the 版本 a human asked for, and `pin=1` is
        # what the commit step writes to PINNED. The relay carries both; it no
        # longer works either out.
        handed = self.handover(
            self.run_script(stub(bump="1", target="0.1.5", pin="1"), state=state_stub(verdict="absent"))
        )

        self.assertEqual(handed["target"], "0.1.5")
        self.assertEqual(handed["pin"], "1")
        self.assertEqual(handed["bump"], "1")
        self.assertIsNone(self.asked())

    def test_a_missing_target_line_is_a_broken_check(self):
        # The relay reads four lines and defaults none of them. `target=` is
        # what the render takes, so a check that dropped it must stop the run
        # rather than hand the render an empty 版本.
        result = self.run_script(stub(raw="upstream=0.1.10\npin=0\nbump=0"))

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no target= in", result.stderr)

    def test_a_check_that_already_says_publish_does_not_ask(self):
        # The fifth direction is for the quiet runs; an absent Release is about
        # to be created by this one anyway.
        handed = self.handover(
            self.run_script(stub(upstream="0.1.11", bump="1"), state=state_stub(verdict="absent"))
        )

        self.assertEqual(handed["bump"], "1")
        self.assertIsNone(self.asked())

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
