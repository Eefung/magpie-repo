"""The package inventory is stated once, and these hold it that way.

Three things used to claim to know which packages this repository publishes:
`bump.PACKAGES`, the argv `publish.yml` handed to `build-repo.sh`, and the
directories under `packages/`. Nothing compared them. A package added to the
dict was rendered and committed but never built -- and the next `--check` saw
its PKGBUILD current and printed `bump=0` for good, so the 软件源 never gained
its 包文件.

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import contextlib
import io
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "tests"))

import bump  # noqa: E402  (the module lives beside the scripts, not on the path)
import fixtures  # noqa: E402  (the one spelling of a 包文件's name in the test tree)
import frozen  # noqa: E402  (the test-side reader of the frozen lines and their table)
import install_block  # noqa: E402  (the one reader of the block a user pastes)
import live  # noqa: E402  (the one reader of where a comment starts)
import workflow  # noqa: E402  (the reader of .github/workflows/publish.yml)

LIB = REPO / "scripts" / "lib" / "pkg.sh"


class InventoryAgreementTest(unittest.TestCase):
    def test_every_package_on_disk_is_in_the_inventory(self):
        # The failing input is a third directory with a PKGBUILD in it: the
        # render would write it, the commit would carry it, nothing would build
        # it. This is the assertion that notices.
        on_disk = {
            path.name
            for path in (REPO / "packages").iterdir()
            if (path / "PKGBUILD").is_file()
        }
        self.assertEqual(on_disk, set(bump.PACKAGES))

    def test_the_workflow_does_not_name_packages(self):
        # The 包清单 is the only place a package name is spelled: the build
        # step takes the directories from it, the release step takes the names.
        # The old assertion only caught the `packages/<name>` spelling, so the
        # install lines in the release notes -- a bare `pacman -S magpie-bin` --
        # went unseen. Reading the inventory instead of one hardcoded string
        # means a third package is held to this too.
        source = workflow.text()
        self.assertEqual([pkg for pkg in bump.PACKAGES if pkg in source], [])


class UpstreamHandoverTest(unittest.TestCase):
    """The entry check is a module, and the workflow only relays its lines.

    The decision -- whether this run publishes, and which 版本 it renders --
    used to be bash in the step's own `run:` block, held only by greps over
    this file. It moved into a script, and then into the check itself, which is
    where the comparison already lived: the step calls it and reads its lines.
    """

    STEP = "What is upstream at, and what gets packaged"

    def workflow(self) -> str:
        return workflow.text()

    def code(self, source: str | None = None) -> str:
        """The entry step's code, comments dropped: a note about it is not it."""
        for step in workflow.steps(source=source):
            if step.name == self.STEP:
                return live.live(step.run)
        raise AssertionError("the entry step moved; reword this test")

    def test_the_step_calls_the_check_itself(self):
        # No script between them any more: a relay with one caller and no
        # decision is the module ADR-0004 would send back to the YAML, and this
        # one was deleted (ADR-0005).
        self.assertEqual(len(live.naming(self.code(), "scripts/bump.py --check")), 1)
        self.assertFalse((REPO / "scripts" / "upstream.sh").exists())

    def test_the_step_reads_the_four_lines_it_was_handed(self):
        # Through the table's gates: `bump=` and `pin=` are `0` or `1` there, so
        # the `if:` below can no longer read a third value as "not 1".
        for key in ("upstream", "target", "bump", "pin"):
            self.assertIn(f'frozen_var {key} "$UP.txt" {key}', self.code())

    def step_keys(self) -> set[str]:
        """The keys the step takes out of the check's log."""
        return set(re.findall(r'frozen_var (\w+) "\$UP\.txt"', self.code()))

    def tabled_keys(self) -> set[str]:
        return {row["key"] for row in frozen.table() if row["producer"] == "entry-check"}

    def test_the_step_reads_exactly_the_keys_the_table_credits_it_with(self):
        # Both directions, each naming its own difference: a key the table
        # promises and the step stopped reading is a fact nothing checks, and a
        # key the step reads without a row fails at run time with "no such
        # frozen key" -- after the comparison, not here.
        read, tabled = self.step_keys(), self.tabled_keys()
        self.assertTrue(tabled, "the table credits the entry check with no keys")
        self.assertEqual(sorted(read - tabled), [], "read by the step, absent from the table")
        self.assertEqual(sorted(tabled - read), [], "in the table, unread by the step")

    def test_the_handover_path_is_declared_once(self):
        # Declared in the job's env, where every step sees it -- a step-level
        # declaration would satisfy a text search while the other two steps
        # read an unset variable at run time. One live line names it, and it is
        # the declaration.
        self.assertEqual(workflow.env().get("UP"), "/tmp/up")
        self.assertEqual(declared_lines("UP"), ["UP: /tmp/up"])

    def test_a_comment_naming_the_handover_path_is_not_a_declaration(self):
        # The count this replaced read the whole text, so a note mentioning the
        # path made it two declarations (reproduced: 1 -> 2, #21).
        moved = self.workflow().replace(
            "      UP: /tmp/up\n",
            "      UP: /tmp/up\n      # the check's log lands in /tmp/up\n",
        )
        self.assertNotEqual(moved, self.workflow(), "the handover path moved; reword this test")
        self.assertEqual(declared_lines("UP", moved), ["UP: /tmp/up"])

    def test_the_package_list_path_is_declared_once(self):
        # It used to ride a frozen line -- `packages=` existed so that a path
        # would not be spelled twice -- whose only job was to name a file. With
        # no script in between, the job env is that one place.
        self.assertEqual(workflow.env().get("PKGS"), "/tmp/packages.txt")
        self.assertEqual(declared_lines("PKGS"), ["PKGS: /tmp/packages.txt"])
        self.assertIn('--packages > "$PKGS"', self.code())

    def test_the_repository_the_fifth_direction_asks_about_is_declared(self):
        # The check asks GitHub whether this run's build has a Release; without
        # this line it would only ever say it could not ask.
        self.assertEqual(workflow.env().get("RELEASE_REPO"), "${{ github.repository }}")
        self.assertEqual(len(live.naming(self.workflow(), "github.repository")), 1)

    def test_a_trailing_comment_does_not_reach_the_value(self):
        # The reverse of the quoted case: this one fails if `env()` goes back to
        # matching the raw line and leaving the comment to `_value`.
        source = workflow.text().replace(
            "      ARCH: x86_64\n", "      ARCH: x86_64  # the served arch\n"
        )
        self.assertNotEqual(source, workflow.text(), "the env block moved; reword this test")
        self.assertEqual(workflow.env(source=source)["ARCH"], "x86_64")

    def test_a_quoted_env_value_keeps_its_hash(self):
        # The comment rule belongs to `live` now. `_value` used to cut at the
        # first " #", which truncates a quoted scalar carrying one -- no value
        # in today's workflow does, so this is the reader's own contract, not a
        # change to what the publish job declares.
        source = workflow.text().replace(
            "      GIT_EMAIL: 41898282+github-actions[bot]@users.noreply.github.com\n",
            '      GIT_EMAIL: "a # b"\n',
        )
        self.assertNotEqual(source, workflow.text(), "the env block moved; reword this test")
        self.assertEqual(workflow.env(source=source)["GIT_EMAIL"], "a # b")

    def test_the_render_step_takes_the_version_the_check_decided(self):
        workflow = self.workflow()
        self.assertIn('--render --version "$target"', workflow)
        self.assertIn("TARGET: ${{ steps.upstream.outputs.target }}", workflow)
        # One render invocation, and it always carries a version: a bare
        # `--render` would ask 上游 again and undo the handover.
        self.assertEqual(len(live.naming(workflow, "scripts/bump.py --render")), 1)

    def test_the_decision_is_not_made_a_second_time_in_this_file(self):
        # These two are what the step used to decide: `bump` from a default,
        # and `target` from WANTED-or-UPSTREAM. A reformat broke the test job
        # while flipping the default -- a broken check now meaning "publish"
        # -- did not. Both are the check's now, so neither spelling returns,
        # and the step has no branch of its own either.
        workflow = self.workflow()
        self.assertNotIn("bump:-", workflow)
        self.assertNotIn("WANTED:-", workflow)
        self.assertNotIn("case ", self.code())


def step_source(name: str) -> str:
    """A step's shell, ready to run: the workflow's own text for it."""
    for step in workflow.steps():
        if step.name == name:
            return "set -euo pipefail\n" + step.run
    raise AssertionError(f"the step moved; reword this test: {name}")


def run_step(name: str, root: Path, **env: str) -> subprocess.CompletedProcess:
    """Run one step the way the runner does: its shell, its env, a scratch root.

    `WANTED` is dropped from the inherited environment unless the caller names
    it: a developer's shell must not stand in for a workflow input.
    """
    full = dict(os.environ)
    if "WANTED" not in env:
        full.pop("WANTED", None)
    full.update(env)
    return subprocess.run(
        ["bash", "-c", step_source(name)],
        cwd=str(root),
        env=full,
        text=True,
        capture_output=True,
    )


def declared_lines(key: str, source: str | None = None) -> list[str]:
    """The live lines that name that job env value: a note about it is not one.

    The three questions that ask this -- the served arch, the pages branch, a
    path -- differ only in the key, so they ask it once. `naming` answers in
    lines, not occurrences: a line that spells the value twice is one
    declaration, and a comment naming it is none.
    """
    text = workflow.text() if source is None else source
    return live.naming(text, workflow.env(source=source)[key])


class EntryStepTest(unittest.TestCase):
    """The entry step, run for real against a stub check.

    The step is shell in YAML, and what it does with a broken check is the
    behaviour no text guard can hold: the check's log is written to a file by
    redirection, so under `set -e` the reason a run stopped is exactly what
    would be swallowed. The relay used to `cat` it before exiting; the step
    says both itself now, and this drives it.
    """

    STEP = "What is upstream at, and what gets packaged"
    LOG = ["upstream=0.1.10", "packaged magpie-bin=0.1.10-1", "carried=0.1.10",
           "target=0.1.10", "pin=0", "bump=0"]

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "scripts" / "lib").mkdir(parents=True)
        (self.root / "scripts" / "lib" / "pkg.sh").write_text(LIB.read_text())

    def check(self, *, code: int = 0, lines: list[str] | None = None) -> None:
        """A stub entry check: the lines it prints, and its exit code."""
        body = self.LOG if lines is None else lines
        (self.root / "scripts" / "bump.py").write_text(
            "import sys\n"
            f"lines = {body!r}\n"
            "if sys.argv[1] == '--check':\n"
            "    print('\\n'.join(lines))\n"
            f"    sys.exit({code})\n"
            "if sys.argv[1] == '--packages':\n"
            "    print('package packages/magpie-bin magpie-bin')\n"
            "    sys.exit(0)\n"
            "sys.exit('stub: unknown mode ' + sys.argv[1])\n"
        )


    def run_step(self) -> subprocess.CompletedProcess:
        return run_step(
            self.STEP,
            self.root,
            UP=str(self.root / "up"),
            PKGS=str(self.root / "packages.txt"),
            GITHUB_OUTPUT=str(self.root / "outputs.txt"),
            RELEASE_REPO="owner/repo",
        )

    def test_the_check_log_reaches_the_run_when_it_fails(self):
        self.check(code=1, lines=["upstream=0.1.10", "behind magpie-bin=upstream"])
        result = self.run_step()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("behind magpie-bin=upstream", result.stderr)
        self.assertIn("the entry check failed", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_a_bump_outside_its_domain_fails_the_step(self):
        # The gate is the only thing between a producer's typo and a `if:` that
        # reads it as "not 1" and skips the publish in silence.
        self.check(lines=[
            "upstream=0.1.10", "target=0.1.10", "pin=0", "bump=2",
        ])

        result = self.run_step()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("bump=2 is not 0 or 1", result.stderr)
        self.assertFalse((self.root / "outputs.txt").exists())

    def test_a_pin_outside_its_domain_fails_the_step(self):
        self.check(lines=[
            "upstream=0.1.10", "target=0.1.10", "pin=yes", "bump=1",
        ])

        result = self.run_step()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("pin=yes is not 0 or 1", result.stderr)
        self.assertFalse((self.root / "outputs.txt").exists())

    def test_a_missing_key_fails_the_step(self):
        # Required in the table, so an absent line is the reader's complaint --
        # `line_field`'s wording, which the gate hands on rather than replaces.
        self.check(lines=["upstream=0.1.10", "pin=0", "bump=0"])

        result = self.run_step()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no target= in", result.stderr)

    def test_the_four_lines_and_the_package_list_are_what_it_hands_on(self):
        self.check()
        result = self.run_step()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            (self.root / "outputs.txt").read_text().splitlines(),
            ["upstream=0.1.10", "target=0.1.10", "bump=0", "pin=0"],
        )
        self.assertEqual(
            (self.root / "packages.txt").read_text().splitlines(),
            ["package packages/magpie-bin magpie-bin"],
        )


class ReleaseStateStepTest(unittest.TestCase):
    """发布状态那一步：读的是表里的域，不是猜字符串。

    The step hands `release=` and `tag=` to the publish step as outputs, and
    the publish step used to re-check the state with its own `case` -- a domain
    written twice, and the second copy's only job was to catch the first one's
    mistake. The gate reads it once; this drives the step that reads it.
    """

    STEP = "Does this version already have a Release?"

    SETUP = (
        'mkdir -p scripts/lib && cp {repo}/scripts/lib/pkg.sh scripts/lib/ && '
        "cp {repo}/scripts/release-state.sh scripts/"
    )

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        subprocess.run(
            ["bash", "-c", self.SETUP.format(repo=REPO)], cwd=str(self.root), check=True
        )

    def corrupt(self, body: str) -> None:
        """A release-state that answers the wrong thing, without gh at all."""
        path = self.root / "scripts" / "release-state.sh"
        path.write_text("#!/usr/bin/env bash\n" + body)
        path.chmod(0o755)

    def run_it(self) -> subprocess.CompletedProcess:
        return run_step(
            self.STEP,
            self.root,
            VER="0.1.10",
            GITHUB_REPOSITORY="owner/repo",
            GITHUB_OUTPUT=str(self.root / "outputs.txt"),
        )

    def test_the_two_lines_are_what_it_hands_on(self):
        self.corrupt('printf \'release=present\\ntag=v0.1.10\\n\'\n')

        result = self.run_it()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            (self.root / "outputs.txt").read_text().splitlines(),
            ["release=present", "tag=v0.1.10"],
        )

    def test_a_state_outside_its_domain_fails_the_step(self):
        self.corrupt('printf \'release=maybe\\ntag=v0.1.10\\n\'\n')

        result = self.run_it()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("release=maybe is not present or absent", result.stderr)
        self.assertFalse((self.root / "outputs.txt").exists())

    def test_a_tag_that_is_not_a_version_fails_the_step(self):
        self.corrupt('printf \'release=present\\ntag=0.1.10\\n\'\n')

        result = self.run_it()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("tag=0.1.10 is not v<版本>", result.stderr)
        self.assertFalse((self.root / "outputs.txt").exists())


class PublishTransactionTest(unittest.TestCase):
    """The publish job is one transaction, and its order is the invariant.

    Every step after the entry check is gated on `bump`, the Release comes
    before the commit that tells `main` the tree is current, and that commit is
    last. The order exists because a failed release has to leave `main` at the
    old pkgver: commit first, and a failed `gh release create` leaves `main`
    looking current, so no later run ever retries and that 版本 never gets a
    release. All three rules were prose in the file -- ADR-0001's 后果 says it
    out loud ("插一个 step 没人拦") -- and both probes left the suite green:
    deleting the gh-pages step's `if:`, and moving the Release after the
    commit.

    The file is read through tests/workflow.py now, so a reformat that changes
    nothing (a comment, a quote) is not what this class notices.
    """

    CHECK = "What is upstream at, and what gets packaged"
    RELEASE = "Publish the GitHub release"
    COMMIT = "Commit the rendered PKGBUILDs"

    # The two gates this job uses, verbatim: publish when there is something to
    # publish, and the one line a skipped run prints. Anything else -- a
    # half-gate, `== '1' || false`, a `!=` on a step that publishes -- is a
    # step whose behaviour no rule here can predict.
    PUBLISH_GATE = "steps.upstream.outputs.bump == '1'"
    SKIP_GATE = "steps.upstream.outputs.bump != '1'"
    GATES = (PUBLISH_GATE, SKIP_GATE)

    def steps(self, source: str | None = None) -> list[workflow.Step]:
        found = workflow.steps(source)
        self.assertIn(self.CHECK, [step.name for step in found], found)
        return found

    def after_check(self, source: str | None = None) -> list[workflow.Step]:
        found = self.steps(source)
        return found[[step.name for step in found].index(self.CHECK) + 1 :]

    def ungated(self, source: str | None = None) -> list[str]:
        """Steps after the check whose gate is neither of the two above."""
        return [
            step.name
            for step in self.after_check(source)
            if step.if_ not in self.GATES
        ]

    def publishing(self, source: str | None = None) -> list[str]:
        """Steps that run when there is something to publish."""
        return [
            step.name
            for step in self.steps(source)
            if self.PUBLISH_GATE in (step.if_ or "")
        ]

    def misordered(self, source: str | None = None) -> list[str]:
        """The order violations: release after commit, or a step after the commit."""
        names = [step.name for step in self.steps(source)]
        publishing = self.publishing(source)
        problems = []
        if names.index(self.RELEASE) > names.index(self.COMMIT):
            problems.append(f"{self.RELEASE} runs after {self.COMMIT}")
        if publishing[-1] != self.COMMIT:
            problems.append(f"{publishing[-1]} publishes after {self.COMMIT}")
        return problems

    def dead_outputs(self, source: str | None = None) -> list[str]:
        """Keys written to $GITHUB_OUTPUT that no step reads back."""
        written = workflow.output_keys(source)
        self.assertTrue(
            written,
            "no `printf ... >> $GITHUB_OUTPUT` left; teach this reader the new carrier",
        )
        return sorted(written - workflow.output_reads(source))

    def test_every_step_after_the_check_is_gated(self):
        self.assertEqual(self.ungated(), [])

    def test_the_commit_comes_after_the_release_and_last(self):
        self.assertEqual(self.misordered(), [])

    def test_every_declared_output_has_a_reader(self):
        # `upstream` was written and read by nobody: a handover line nothing
        # consumes is a promise the next person has to keep guessing about. It
        # has its reader now (the skipped run's line names the 版本); this is
        # what stops the next dead one -- and the carrier guard below stops one
        # arriving through a shape this reader cannot parse.
        self.assertEqual(self.dead_outputs(), [])
        self.assertEqual(workflow.unparsed_output_writes(), [])

    def test_every_step_to_step_fact_rides_one_carrier(self):
        """One carrier, so one set of rules -- and `ver` is on it.

        `$GITHUB_ENV` had no rule at all: no "every key has a reader", no
        "only one write shape". `ver` travelled on it while the two invariants
        this class asserts governed `$GITHUB_OUTPUT` only, so a dead handover
        could come back through the unguarded carrier and the suite would stay
        green. If a step ever needs `$GITHUB_ENV` again, that is the moment to
        give the second carrier the same two rules -- not a line to write the
        fact on and hope.
        """
        writes = workflow.env_writes()
        self.assertEqual(writes, [])
        self.assertIn("ver", workflow.output_keys())
        self.assertIn("ver", workflow.output_reads())

    def test_a_comment_after_a_step_name_is_not_part_of_the_name(self):
        # `steps()` reads header lines as code now. Before that, the name
        # captured the comment, so every exact-name lookup in this file missed
        # the step and read it as "moved".
        text = workflow.text()
        moved = text.replace(
            f"      - name: {self.COMMIT}\n",
            f"      - name: {self.COMMIT}   # last\n",
        )
        self.assertNotEqual(moved, text, "the commit step moved; reword this test")
        # The lookup every consumer here performs, not membership in a list.
        commit = [step for step in workflow.steps(source=moved) if step.name == self.COMMIT]
        self.assertEqual([step.name for step in commit], [self.COMMIT])

    def test_a_comment_on_the_gate_is_not_a_change(self):
        # The failure the reader exists to end: a behaviour-identical edit that
        # used to redden the job. `if_` is a parsed value now, not the rest of
        # the line -- a comment on the Commit step's gate left `misordered()`
        # reporting a violation nothing had done.
        text = workflow.text()
        moved = text.replace(
            f"      - name: {self.COMMIT}\n"
            f"        if: {self.PUBLISH_GATE}\n",
            f"      - name: {self.COMMIT}\n"
            f"        if: {self.PUBLISH_GATE}   # last, so a failure is retried\n",
        )
        self.assertNotEqual(moved, text)
        self.assertEqual(self.ungated(moved), [])
        self.assertEqual(self.misordered(moved), [])

    def test_a_gate_that_only_looks_like_one_is_caught(self):
        # `== '1' || false` never runs, but it is not the gate the job uses,
        # and the order rule cannot reason about a step whose condition it has
        # to interpret.
        text = workflow.text()
        moved = text.replace(
            f"      - name: {self.COMMIT}\n"
            f"        if: {self.PUBLISH_GATE}\n",
            f"      - name: {self.COMMIT}\n"
            f"        if: {self.PUBLISH_GATE} || false\n",
        )
        self.assertNotEqual(moved, text)
        self.assertEqual(self.ungated(moved), [self.COMMIT])

    def test_a_step_after_the_commit_that_publishes_is_caught(self):
        text = workflow.text()
        appended = text.rstrip("\n") + (
            "\n\n      - name: Upload the checksums somewhere\n"
            f"        if: {self.PUBLISH_GATE}\n"
            "        run: echo hi\n"
        )
        self.assertNotEqual(appended, text)
        self.assertEqual(
            self.misordered(appended),
            [f"Upload the checksums somewhere publishes after {self.COMMIT}"],
        )

    def test_an_output_written_by_another_carrier_is_caught(self):
        text = workflow.text()
        marker = "      - name: Say what was skipped\n"
        moved = text.replace(
            marker,
            "      - name: Write an output nobody can see\n"
            "        run: echo \"dead=1\" >> \"$GITHUB_OUTPUT\"\n\n" + marker,
            1,
        )
        self.assertNotEqual(moved, text)
        self.assertEqual(len(workflow.unparsed_output_writes(moved)), 1)

    def test_a_step_added_without_a_gate_is_caught(self):
        text = workflow.text()
        moved = text.replace(
            f"      - name: {self.RELEASE}\n"
            "        if: steps.upstream.outputs.bump == '1'\n",
            f"      - name: {self.RELEASE}\n",
        )
        self.assertNotEqual(moved, text)
        self.assertEqual(self.ungated(moved), [self.RELEASE])

    def test_the_release_moved_after_the_commit_is_caught(self):
        text = workflow.text()
        start = text.index("      # Before the commit, deliberately")
        commit = text.index(f"      - name: {self.COMMIT}")
        release = text[start:commit]
        moved = text[:start] + text[commit:]
        moved = moved.rstrip("\n") + "\n\n" + release
        self.assertNotEqual(moved, text)
        self.assertEqual(len(self.misordered(moved)), 2, self.misordered(moved))

    def test_an_output_nobody_reads_is_caught(self):
        text = workflow.text()
        moved = text.replace(
            "printf 'upstream=%s\\ntarget=%s\\nbump=%s\\npin=%s\\n' \\",
            "printf 'upstream=%s\\ntarget=%s\\nbump=%s\\npin=%s\\nunused=%s\\n' \\",
        )
        self.assertNotEqual(moved, text)
        self.assertEqual(self.dead_outputs(moved), ["unused"])


class ContainerWorkdirTest(unittest.TestCase):
    """The container works from the root the 包清单 names things against.

    The 包清单 carries directories relative to the repository root, and the
    build step mounts that root at /src and works from there. It used to be one
    assumption held by a printed note -- `check.sh`'s "not covered here" -- so
    moving the mount or the workdir would have left every build step resolving
    a directory that is not there, with nothing offline to say so.
    """

    def test_the_build_mounts_the_root_and_works_from_it(self):
        source = workflow.text()
        self.assertIn('--volume "$PWD:/src" --workdir /src', source)

    def test_the_package_list_is_named_relative_to_that_root(self):
        for directory, _ in bump.package_entries(bump.layout_for(REPO)):
            self.assertFalse(directory.startswith("/"), directory)
            self.assertTrue((REPO / directory).is_dir(), directory)


class ServedArchTest(unittest.TestCase):
    """The 软件源's arch directory is declared once.

    GLOSSARY puts the arch set on the arch 表 and says adding one is a change to
    that table, not to a step. The served directory was spelled nine times over
    four steps, so a second arch meant a grep hunt rather than one env.
    """

    def test_the_workflow_declares_the_served_arch_once(self):
        self.assertEqual(declared_lines("ARCH"), ["ARCH: x86_64"])

    def test_a_comment_naming_the_arch_is_not_a_second_declaration(self):
        # The value is `workflow.env()`'s answer, so a note after the
        # declaration -- or a comment line mentioning the arch, like the one
        # ADR-0003 leaves in this file -- is not a change to what runs. The old
        # scan compared whole lines and reddened the job for both.
        text = workflow.text()
        moved = text.replace(
            "      ARCH: x86_64\n",
            "      # only x86_64 is built and payload-checked (ADR-0003)\n"
            "      ARCH: x86_64   # the served directory\n",
        )
        self.assertNotEqual(moved, text)
        # The note after the declaration is not part of the line it names.
        self.assertEqual(declared_lines("ARCH", moved), ["ARCH: x86_64"])

    def test_a_second_live_line_naming_the_arch_is_caught(self):
        text = workflow.text()
        moved = text.replace(
            "      ARCH: x86_64\n", "      ARCH: x86_64\n      EXTRA_ARCH: x86_64\n"
        )
        self.assertNotEqual(moved, text)
        self.assertEqual(len(declared_lines("ARCH", moved)), 2, declared_lines("ARCH", moved))


class PackageListTest(unittest.TestCase):
    """`--packages` is the 包清单's only exit, and it carries the whole entry."""

    def entries(self) -> list[str]:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            self.assertEqual(bump.main(["--packages"]), 0)
        return buffer.getvalue().splitlines()

    def test_one_line_per_package_with_its_name(self):
        # The pkgname travel with the directory: the release step used to read
        # it back out of the PKGBUILD, a second source for a fact the inventory
        # already holds as its keys.
        self.assertEqual(
            self.entries(),
            [f"package packages/{pkg} {pkg}" for pkg in bump.PACKAGES],
        )

    def test_the_answer_does_not_depend_on_the_cwd(self):
        # The value is named by the layout, not by whoever ran it. This test
        # used to chdir to REPO first just to make the strings line up, which
        # was the working directory leaking into the interface.
        expected = self.entries()
        with tempfile.TemporaryDirectory() as tmp:
            cwd = os.getcwd()
            try:
                os.chdir(tmp)
                self.assertEqual(self.entries(), expected)
            finally:
                os.chdir(cwd)


class ServedIdentityTest(unittest.TestCase):
    """The install block in README is the interface a user actually touches.

    A 软件源 is four facts at once: the section name (which is the database's
    file name), the URL, the arch directory, and the 包清单. They are decided in
    three files and restated a fourth time in README -- the copy a user types
    from -- and nothing compared them. `ARCH` was pinned to one line of the
    workflow; the other three had no guard at all, so changing the served arch
    or renaming the section would have shipped instructions that 404.

    Regenerating the block would be nicer than asserting it, but README is not
    executable: a guard can say "these lines are here", not that the prose
    around them still reads correctly. That is what it is for.

    The Release notes are the same block again, on the page a user lands on from
    the Releases tab. `release.sh` emits it from `ARCH` / `REPO_NAME` /
    `PAGES_URL` (it used to write `[magpie]` as a literal), and
    tests/test_release.py holds it to the same literals through
    tests/install_block.py -- one reader, two documents.
    """

    def workflow(self) -> str:
        return workflow.text()

    def readme(self) -> str:
        return (REPO / "README.md").read_text()

    def declared(self, source: str | None = None) -> install_block.Served:
        """The identity the workflow declares, or the text standing in for it."""
        found = workflow.env(source=source)
        missing = [key for key in ("ARCH", "PAGES_URL", "REPO_NAME") if key not in found]
        self.assertEqual(missing, [], f"the workflow declares no {missing}")
        return install_block.Served(
            section=found["REPO_NAME"], url=found["PAGES_URL"], arch=found["ARCH"]
        )

    def install_block(self, readme: str) -> str:
        """The fenced block a user copies into pacman.conf.

        The block, not the whole README: every fact it carries is also
        explained in the prose around it, so a search of the file as a whole is
        satisfied by the sentence that describes the line instead of by the
        line the user copies. The fence itself is read by tests/install_block.py
        now, so README and the Release notes go through the same rule.
        """
        try:
            section = readme.split("## 安装", 1)[1]
        except IndexError:
            return ""
        return install_block.block(section)

    def problems(self, readme: str, source: str | None = None) -> list[str]:
        """Every install-block fact the README does not restate exactly."""
        want = self.declared(source)
        missing = install_block.problems(
            want,
            config=self.install_block(readme),
            installs=readme,
            label="README",
            packages=bump.PACKAGES,
        )
        if f"{want.server}/{want.section}.db" not in readme:
            missing.append(f"README carries no {want.server}/{want.section}.db")
        return missing

    def test_the_install_block_matches_what_the_repository_serves(self):
        self.assertEqual(self.problems(self.readme()), [])

    def test_the_database_is_named_after_the_section_the_readme_shows(self):
        # pacman keys the section off the database's file name, so `[magpie]`
        # and magpie.db.tar.gz are one fact. The db is created inside the
        # container, so the name has to cross that boundary: build-repo.sh
        # refuses to run without it, which is what makes a missing `-e` a
        # failure instead of a run that agrees by luck.
        self.assertIn("-e REPO_NAME", self.workflow())

    def test_serving_another_arch_without_a_readme_edit_is_caught(self):
        moved = self.workflow().replace("ARCH: x86_64", "ARCH: aarch64")
        self.assertNotEqual(moved, self.workflow())
        self.assertTrue(self.problems(self.readme(), moved))

    def test_a_package_missing_from_the_readme_is_caught(self):
        readme = self.readme()
        for pkg in bump.PACKAGES:
            with self.subTest(pkg=pkg):
                dropped = readme.replace(f"sudo pacman -S {pkg}", "")
                self.assertEqual(
                    [problem for problem in self.problems(dropped) if pkg in problem],
                    [f"README never installs {pkg}"],
                )

    def test_a_block_line_that_only_the_prose_still_says_is_caught(self):
        # The failure this guard did not have: every fact in the block is also
        # explained in the sentence beside it, so reading the whole README let
        # the prose stand in for the line a user copies. Deleting the block's
        # `SigLevel = Optional` left problems() empty -- while the prose quote
        # of it stayed, which is where a whole-file search found it.
        readme = self.readme()
        block = self.install_block(readme)
        dropped = readme.replace(block, block.replace("SigLevel = Optional\n", ""))
        self.assertNotEqual(dropped, readme)
        self.assertIn("SigLevel = Optional", dropped, "the prose no longer quotes it; reword this test")
        self.assertEqual(
            [problem for problem in self.problems(dropped) if "SigLevel" in problem],
            ["README carries no 'SigLevel = Optional'"],
        )


class ArchTableTest(unittest.TestCase):
    """What the arch 表 declares is what CI builds and serves.

    aarch64 lived in the table and in both templates while CI built x86_64
    alone. The render then demanded an upstream arm64 asset nobody packaged, so
    a release that stopped publishing one stopped the x86_64 release too
    (ADR-0003). An arch may not be declared here without a build behind it.
    """

    def workflow(self) -> str:
        return workflow.text()

    def served(self) -> str:
        return workflow.env()["ARCH"]

    def test_every_arch_the_table_names_is_the_one_ci_serves(self):
        # The mutation that mattered: the table gains an arch while no container
        # builds it, and the next render asks upstream for its asset.
        self.assertEqual(set(bump.ARCHES), {self.served()})


class GitIdentityTest(unittest.TestCase):
    """The bot both commits sign as is declared once, and both consume it.

    The two commits this workflow makes -- the gh-pages squash in
    scripts/publish-pages.sh and the main-branch PKGBUILD commit -- used to
    spell the committer verbatim in their own files, and nothing held the two
    copies together: renaming the bot in one place shipped two different
    committers. The env declares it once; this holds the step and the script
    to the same source, the way ServedIdentityTest does for the served facts.
    """

    def name(self) -> str:
        return workflow.env()["GIT_NAME"]

    def email(self) -> str:
        return workflow.env()["GIT_EMAIL"]

    def test_both_commits_sign_as_the_declared_identity(self):
        text = workflow.text()

        # The main-branch commit step reads the env, not its own literal: a
        # second spelling no test could compare against would be a second
        # source for the fact the env holds.
        self.assertIn('git config user.name "$GIT_NAME"', text)
        self.assertIn('git config user.email "$GIT_EMAIL"', text)
        # Each identity literal appears only in its env declaration. The name
        # is also a substring of the email, so the email's are counted from
        # their full `GIT_NAME:` / `GIT_EMAIL:` lines, not bare substrings.
        self.assertEqual(len(live.naming(text, f"GIT_NAME: {self.name()}")), 1)
        self.assertEqual(len(live.naming(text, f"GIT_EMAIL: {self.email()}")), 1)

    def test_publish_pages_consumes_the_same_identity(self):
        # The script signs the gh-pages squash as the same env. Holding it to
        # the declared values is what stops it printing a second literal the
        # step's guard would not see.
        script = (REPO / "scripts" / "publish-pages.sh").read_text()
        for literal in (self.name(), self.email()):
            self.assertNotIn(f'"{literal}"', script)
        self.assertIn("$GIT_NAME", script)
        self.assertIn("$GIT_EMAIL", script)


class PackageFilePatternTest(unittest.TestCase):
    """What counts as a 包文件 is one rule, and it lives in pkg.sh.

    The workflow's seed whitelist spelled the glob itself, in a file that also
    decides which tree gets carried forward; build-repo's window spelled it
    again. Two spellings meant a retired package could be seeded forward by one
    and pruned by neither, and a `.sig` beside a package would match both.
    """

    def workflow(self) -> str:
        return workflow.text()

    def spelling(self, text: str) -> list[str]:
        """Live lines that spell the extension themselves.

        A comment may name it -- that is how the history is explained -- but a
        line that runs must ask pkg.sh.
        """
        return [line for line in live.live_lines(text) if ".pkg.tar" in line]

    def sourcing(self, directory: Path = REPO / "scripts") -> list[Path]:
        """The scripts that source pkg.sh, and can therefore ask it."""
        return [
            path
            for path in sorted(directory.glob("*.sh"))
            if any(
                "lib/pkg.sh" in line and "source" in line
                for line in live.live_lines(path.read_text())
            )
        ]

    def test_a_trailing_comment_does_not_make_a_script_a_sourcer(self):
        # The mention has to be live code: a note saying "this used to source
        # pkg.sh" is exactly the prose the guard must not take for a call.
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "x.sh").write_text("x  # source lib/pkg.sh\n")
            self.assertEqual(self.sourcing(Path(tmp)), [])

    def test_the_seed_asks_pkg_sh_for_the_pattern(self):
        # The seed owns the decision and the tar call now, so it is the script
        # that has to ask the rule rather than spell the extension.
        self.assertIn('"$arch/$(pkgfile_pattern)"', (REPO / "scripts" / "seed-tree.sh").read_text())

    def test_the_workflow_does_not_spell_the_extension(self):
        self.assertNotIn(".pkg.tar", self.workflow())

    def test_no_script_that_sources_pkg_sh_spells_the_extension(self):
        # The guard used to stop at the workflow, so build-check's own globs
        # (their own width, `*.pkg.tar.*`) and the probe's sat outside it. A
        # script that already sources the rule has no excuse for spelling it --
        # the probe included: it sources pkg.sh for its own `assertions=`
        # handover, which costs it the independent glob it used to count with.
        # What it still measures is repo-add's answer against the window's, and
        # both are compared to the numbers each case expects, not to each other,
        # so a rule that stopped matching the files would show as 0 ≠ 1.
        sources = self.sourcing()
        self.assertIn("build-check.sh", [path.name for path in sources])
        self.assertIn("build-repo.sh", [path.name for path in sources])
        self.assertIn("probe-repo-build.sh", [path.name for path in sources])
        self.assertEqual(
            [path.name for path in sources if self.spelling(path.read_text())], []
        )

    def test_a_script_spelling_the_extension_is_caught(self):
        self.assertEqual(
            self.spelling('find . -name "*.pkg.tar.zst"\n'),
            ['find . -name "*.pkg.tar.zst"'],
        )
        # A comment that names it is how the history stays readable.
        self.assertEqual(self.spelling("# used to spell *.pkg.tar.* here\n"), [])
        # And so is a note at the end of a line: the reader is `live` now, so a
        # trailing comment stops counting as a line that spells it.
        self.assertEqual(self.spelling("run  # *.pkg.tar.zst is the suffix\n"), [])


class SeedTest(unittest.TestCase):
    """播种：三种答案，各跑一遍真的。

    What it carries decides what survives a publish: a seed that drops a 包文件
    silently empties the 保留窗口, and one that carries a `.sig` feeds it to
    repo-add the day signing lands. That much the step's own line already
    proved -- but only that line.

    What nothing could reach was the decision above it: `if git ls-remote
    --exit-code --heads origin "$PAGES_BRANCH"` read an exit status as a value,
    and git answers 2 for "no such ref" and 128 for "cannot ask" -- an
    unreachable host, a credential that stopped working, a remote that is not a
    repository. Both took the "no branch yet" path, so a transient failure
    seeded an empty tree and the publish that followed force-pushed a
    repository holding this run's 版本 alone over the branch: window emptied,
    earlier 包文件 404, run green. The decision is scripts/seed-tree.sh's now,
    against the bare repository this fixture already builds, so its three
    answers are three cases here.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.origin = self.root / "origin"
        self.work = self.root / "work"

    def git(self, *args: str, cwd: Path) -> None:
        subprocess.run(
            ["git", "-c", "user.email=test@example.invalid", "-c", "user.name=test", *args],
            cwd=cwd,
            check=True,
            capture_output=True,
        )

    def published_tree(self) -> list[str]:
        """A previous branch of the 软件源: two 包文件, a signature, a stray file."""
        (self.origin / workflow.env()["ARCH"]).mkdir(parents=True)
        # The names come from tests/fixtures.py -- the one spelling of a 包文件
        # in the test tree -- plus a signature, which is the decoy: it matches
        # the residue set and must never be seeded.
        for name in (
            fixtures.pkgfile_name("magpie-bin", "0.1.9"),
            fixtures.pkgfile_name("magpie-bin", "0.1.10"),
            fixtures.pkgfile_name("magpie-bin", "0.1.10") + ".sig",
        ):
            (self.origin / workflow.env()["ARCH"] / name).touch()
        (self.origin / "SHA256SUMS").touch()
        self.git(
            "init", "-q", "--initial-branch=" + workflow.env()["PAGES_BRANCH"], ".", cwd=self.origin
        )
        self.git("add", "-A", cwd=self.origin)
        self.git("commit", "-q", "-m", "previous publish", cwd=self.origin)
        return [
            fixtures.pkgfile_name("magpie-bin", "0.1.10"),
            fixtures.pkgfile_name("magpie-bin", "0.1.9"),
        ]

    def work_tree(self) -> None:
        """The step's cwd: a checkout whose `origin` is the fixture repository."""
        self.work.mkdir()
        self.git("init", "-q", "--initial-branch=main", ".", cwd=self.work)
        self.git("remote", "add", "origin", str(self.origin), cwd=self.work)

    def seed(self, *extra: str) -> subprocess.CompletedProcess:
        """Run the script the way the step does: tree, arch and branch as argv."""
        return subprocess.run(
            [
                "bash",
                str(REPO / "scripts" / "seed-tree.sh"),
                workflow.env()["TREE"],
                workflow.env()["ARCH"],
                workflow.env()["PAGES_BRANCH"],
                *extra,
            ],
            cwd=self.work,
            text=True,
            capture_output=True,
        )

    def seeded(self) -> list[str]:
        tree = self.work / workflow.env()["TREE"]
        if not tree.exists():
            return []
        return sorted(str(path.relative_to(tree)) for path in tree.rglob("*") if path.is_file())

    def seed_source(self) -> str:
        """The script as code: the usage example names a branch, and prose is not code."""
        return live.live((REPO / "scripts" / "seed-tree.sh").read_text())

    def missing_flags(self, source: str) -> list[str]:
        """The probe flags `source` is missing: branch-not-tag, and one commit."""
        return [flag for flag in ("--heads", "--depth 1") if flag not in source]

    def test_the_probe_asks_for_the_branch_and_one_commit(self):
        # `--heads` is what tells a branch from a tag of the same name -- git
        # answers 0 for either, and seeding from a tag would carry the wrong
        # tree. `--depth 1` is what keeps a seed from fetching the whole
        # gh-pages history: AGENTS.md records it as a 150 MB tree, per run.
        self.assertEqual(self.missing_flags(self.seed_source()), [])

    def test_a_probe_flag_that_went_missing_is_reported(self):
        dropped = self.seed_source().replace("--heads", "")
        self.assertNotEqual(dropped, self.seed_source(), "the probe moved; reword this test")
        self.assertEqual(self.missing_flags(dropped), ["--heads"])

    def test_it_carries_exactly_the_pkgfiles_pkg_sh_can_see(self):
        expected = self.published_tree()
        self.work_tree()

        result = self.seed()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.seeded(), [f"x86_64/{name}" for name in expected])
        # The decoys: a signature is not a 包文件, and a stray file is not seeded.
        self.assertNotIn(
            f"x86_64/{fixtures.pkgfile_name('magpie-bin', '0.1.10')}.sig", self.seeded()
        )
        self.assertNotIn("SHA256SUMS", self.seeded())

    def test_a_branch_that_is_not_there_starts_a_fresh_repository(self):
        # The first publish, and the only other reason to continue with an
        # empty tree.
        (self.origin / "x86_64").mkdir(parents=True)
        self.git("init", "-q", "--initial-branch=main", ".", cwd=self.origin)
        self.git("commit", "-q", "--allow-empty", "-m", "main only", cwd=self.origin)
        self.work_tree()

        result = self.seed()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.seeded(), [])
        self.assertIn(f"no {workflow.env()['PAGES_BRANCH']} branch yet", result.stdout)

    def test_a_remote_it_cannot_ask_is_refused(self):
        # Not a repository, so `git ls-remote` exits 128 rather than 2. Reading
        # that as "no branch yet" is the defect: the run would seed nothing and
        # publish over the window this repository keeps.
        self.work_tree()
        unreachable = self.root / "not-a-repository"
        unreachable.mkdir()

        result = self.seed(str(unreachable))

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cannot ask", result.stderr)
        self.assertNotIn("starting a fresh repository", result.stdout + result.stderr)
        self.assertEqual(self.seeded(), [])


class PublishTreeTest(unittest.TestCase):
    """The tree the 软件源 is assembled in is declared once, like its arch.

    `repo` was the one name of the thing this repository serves that nothing
    owned: three steps spelled it eight times, so moving the tree -- which is
    what a second arch with a different layout asks for (ADR-0003) -- meant
    editing every one of them, and a step could disagree with the seed about
    where the tree was. The env declares it; the steps reference it.
    """

    def test_the_tree_is_declared_in_the_env(self):
        self.assertEqual(workflow.env()["TREE"], "repo")

    def test_the_ignore_covers_the_tree_the_env_names(self):
        # The tree is named in the env and in .gitignore, and nowhere else. A
        # rename that skipped the ignore would put every assembled file -- the
        # 30 MB package files included -- into the next `git add`.
        self.assertIn(
            f"{workflow.env()['TREE']}/", (REPO / ".gitignore").read_text()
        )

    def test_no_step_spells_the_tree(self):
        # The shapes that were there before, not a general "no repo in this
        # file": PAGES_URL, the push URL and `origin` all legitimately carry
        # the word.
        text = workflow.text()
        for shape in ("mkdir -p repo", "-C repo", "cd repo", '"repo/'):
            self.assertNotIn(shape, text)


class PushPathsTest(unittest.TestCase):
    """Which pushes start a run: one list, and a sentence held to it.

    `on.push.paths` decides whether a push runs the pipeline at all, and it was
    the one declaration in the file with no reader: `tests/workflow.py` parsed
    the env, the step order, the cron and the output carrier. Dropping
    `templates/**` from the list kept all 355 tests green while AGENTS.md still
    told a maintainer that a template edit triggers a publish -- and the advice
    beside that sentence ("这类提交与别的工作分开推") is advice about this
    list, because one push is one run.

    Two memberships, and they are different claims: an input whose change
    changes what gets published has to be watched, and nothing the publish job
    commits may be -- a rendered PKGBUILD pushed by a human would otherwise
    start a second run for the tree it just published.
    """

    # The trees whose change changes what gets published, and the workflow
    # itself, which the entry check and every step's shape live in.
    INPUTS = ("templates/**", "scripts/**", "tests/**", ".github/workflows/publish.yml")

    def test_the_list_is_the_publishing_inputs(self):
        self.assertEqual(sorted(workflow.paths()), sorted(self.INPUTS))

    def test_the_job_does_not_watch_what_it_commits(self):
        self.assertEqual(
            [
                entry
                for entry in workflow.paths()
                if entry.startswith(("packages", bump.PIN_FILE))
            ],
            [],
        )

    def test_a_dropped_input_is_reported(self):
        dropped = workflow.text().replace('"templates/**", ', "")
        self.assertNotIn("templates/**", dropped, "the list moved; reword this test")
        self.assertEqual(sorted(set(self.INPUTS) - set(workflow.paths(source=dropped))), ["templates/**"])

    def test_the_agents_sentence_names_every_entry(self):
        # The sentence is the only place a maintainer reads this list, and it
        # said "含", so an entry could go missing from either side unnoticed.
        sentence = self.agents_sentence()
        self.assertEqual([entry for entry in workflow.paths() if entry not in sentence], [])

    def agents_sentence(self) -> str:
        for line in (REPO / "AGENTS.md").read_text().splitlines():
            if "`paths`" in line:
                return line
        raise AssertionError("AGENTS.md no longer says what `paths` watches; reword this test")

    def test_the_sentence_names_nothing_the_list_does_not_watch(self):
        # The other direction: the sentence is prose a maintainer acts on, so an
        # entry added to it that the workflow does not watch is the same defect
        # as one dropped from it -- and `packages/**` in particular would make
        # every publish trigger a second run.
        named = set(re.findall(r"`((?:[\w.-]+/\*\*)|(?:\.github/[\w./-]+))`", self.agents_sentence()))
        self.assertEqual(sorted(named - set(workflow.paths())), [])

    def test_the_sentence_dropping_an_entry_is_reported(self):
        drifted = self.agents_sentence().replace("、`tests/**`", "")
        self.assertNotEqual(drifted, self.agents_sentence(), "the sentence moved; reword this test")
        self.assertEqual([entry for entry in workflow.paths() if entry not in drifted], ["tests/**"])


class RetentionWindowTest(unittest.TestCase):
    """The number of 版本 users are promised is the number CI keeps.

    `KEEP=2` was build-repo.sh's default and the README's prose with nothing
    between them: the workflow never passed it, so the copy a user reads was the
    one copy no run could compare, and changing it reddened nothing. Probe:
    README's "保留 2 个版本" -> 3 left the suite green. (Changing the script's
    default does redden tests/test_build_repo.py, which pins the behaviour; the
    promise in the docs was the unguarded half.)
    """

    def keep(self) -> str:
        return workflow.env()["KEEP"]

    def test_the_env_declares_it_and_the_container_gets_it(self):
        self.assertRegex(self.keep(), r"^[1-9][0-9]*$")
        # The value crosses into the container through `-e`, beside REPO_NAME.
        # Both flags, each on its own: a single-string assertion would fail on
        # a reordering that changes nothing. A script default equal to the
        # declared value makes a forgotten flag invisible, which is why this is
        # asserted as text rather than by a run.
        self.assertIn("-e REPO_NAME", workflow.text())
        self.assertIn("-e KEEP", workflow.text())

    def test_the_readme_promises_the_versions_the_window_keeps(self):
        self.assertIn(f"保留 {self.keep()} 个版本", (REPO / "README.md").read_text())

    def test_the_glossary_states_the_same_number(self):
        self.assertIn(f"KEEP={self.keep()}", (REPO / "GLOSSARY.md").read_text())

    def test_the_scripts_default_is_the_declared_number(self):
        # build-repo.sh keeps a default so the hand run README documents works
        # without the flag. `REPO_NAME` lost its default for exactly this
        # reason: a default equal to the declared value makes "the env arrived"
        # and "the default was used" produce identical runs, so a run cannot
        # prove either. What is left is the value itself -- a window raised in
        # the env and not here prunes the documented hand run to the old one,
        # and nothing else would notice.
        script = (REPO / "scripts" / "build-repo.sh").read_text()
        match = re.search(r'^KEEP="\$\{KEEP:-(\d+)\}"', script, re.M)
        self.assertIsNotNone(match, "build-repo.sh no longer defaults KEEP")
        self.assertEqual(match.group(1), self.keep())


class PinnedRollbackTest(unittest.TestCase):
    """The pin the entry check reads is the one the commit step writes.

    One name crossing the workflow-to-script seam: bump.py reads PINNED out of
    the tree, and the step that commits the PKGBUILDs is what puts it there. A
    rename on one side is a pin that never lands -- the run publishes an older
    版本, the next scheduled run reads it as 落后 and moves forward again, which
    is the day-long rollback the pin exists to prevent -- and every test stays
    green, because nothing else reads the name.
    """

    def run_of(self, source: str | None = None) -> str:
        """The commit step's code, comments dropped: a note about it is not it."""
        for step in workflow.steps(source=source):
            if step.name == "Commit the rendered PKGBUILDs":
                return live.live(step.run)
        raise AssertionError("the commit step moved; reword this test")

    def test_the_step_writes_the_file_the_check_reads(self):
        self.assertIn(bump.PIN_FILE, self.run_of())

    def test_the_pin_is_the_version_this_run_rendered(self):
        # What lands in the file is the 版本 the renderer produced, which is the
        # one this run's build and Release describe -- not a string relayed
        # straight from the dispatch input.
        self.assertIn('"$VER" > PINNED', self.run_of())

    def test_which_runs_pin_is_the_checks_answer(self):
        # `pin=` comes from the module that compares target with 上游. Reading
        # it off "the dispatch input was non-empty" made a retry of the current
        # 版本 a pin nobody asked for, and a pin suppresses the upstream
        # direction on every later run. It rides the step output now that the
        # relay is gone -- one carrier, one reader.
        self.assertIn("PIN: ${{ steps.upstream.outputs.pin }}", workflow.text())
        self.assertIn('[ "$PIN" = 1 ]', self.run_of())
        self.assertNotIn("inputs.version", self.run_of())

    def test_a_renamed_file_is_caught(self):
        code = self.run_of()
        self.assertIn("> PINNED", code)
        # Every spelling in the step, not the one the assertion happens to name:
        # the write and the `git rm` are two statements about the same file.
        self.assertNotIn(bump.PIN_FILE, self.run_of(workflow.text().replace("PINNED", "PIN")), "the guard did not notice the rename")


class PublishNamesTest(unittest.TestCase):
    """The branch and the tag: two names that cross the workflow-script seam.

    Both were spelled once in the workflow and once in the script it calls, and
    nothing held the two together. The branch was the worse half: the seed's
    copy had no test at all (SeedTest fetches its own `main`), and its
    `no branch yet` path turns a rename into "first publish" with an empty
    保留窗口 while the run stays green. They are declarations now -- the branch
    in the job env, the tag handed over by release-state.sh -- and this holds
    the consumers to them.
    """

    def run_of(self, prefix: str) -> str:
        """A step's run block, comments dropped: a note about it is not it."""
        for step in workflow.steps():
            if step.name.startswith(prefix):
                return live.live(step.run)
        raise AssertionError(f"no step named {prefix}*")

    def test_the_branch_is_declared_once(self):
        # The name is pinned here, not read back: it is the branch `seed_tree`
        # reads, and renaming it is a change to the 软件源's address.
        self.assertEqual(declared_lines("PAGES_BRANCH"), ["PAGES_BRANCH: gh-pages"])

    def test_a_trailing_comment_is_not_part_of_the_declaration(self):
        # The line that declares it may carry a note; the note is not part of
        # what it declares, and not a second declaration either (#21).
        moved = workflow.text().replace(
            "      PAGES_BRANCH: gh-pages\n",
            "      PAGES_BRANCH: gh-pages  # served branch\n",
        )
        self.assertNotEqual(moved, workflow.text(), "the branch line moved; reword this test")
        self.assertEqual(declared_lines("PAGES_BRANCH", moved), ["PAGES_BRANCH: gh-pages"])

    def seed_call(self, source: str | None = None) -> str:
        """The one live line that hands the branch to scripts/seed-tree.sh.

        Live: the comment reader is `live`, so a call that moved behind a `#`
        is prose, this finds no line to hand back, and it raises "the seed call
        moved" itself rather than returning something the caller has to check
        (#15, #18).
        """
        for step in workflow.steps(source=source):
            if step.name.startswith("Build the packages"):
                for line in live.live_lines(step.run):
                    if "seed-tree.sh" in line:
                        return line
        raise AssertionError("the seed call moved; reword this test")

    def test_the_seed_is_handed_the_branch(self):
        # The script takes the branch as argv and spells no literal: its
        # `no branch yet` path is also the first-publish path, so the copy of
        # the name that goes stale is the one that stays green.
        self.assertIn('"$PAGES_BRANCH"', self.seed_call())
        seed = (REPO / "scripts" / "seed-tree.sh").read_text()
        self.assertIn('branch="${3:', seed)
        # Code, not prose: the usage example in the header names a branch.
        seed_live = live.live(seed)
        self.assertNotIn(workflow.env()["PAGES_BRANCH"], seed_live)

    def test_the_publish_step_hands_it_over(self):
        self.assertIn('"$PAGES_BRANCH"', self.run_of("Publish the pacman repository"))

    def test_the_release_step_reads_its_three_keys_through_the_gates(self):
        # The step used to consume `assets` through a redirect on a command
        # substitution: the key's value was a file name, and what that file held
        # was never checked -- an empty one handed `gh release` the checksums
        # alone and the run went green.
        run = self.run_of("Publish the GitHub release")
        self.assertIn("frozen_list assets /tmp/release.txt assets", run)
        self.assertIn("frozen_var sums /tmp/release.txt sums", run)
        self.assertIn("frozen_var notes /tmp/release.txt notes", run)
        self.assertNotIn("mapfile", run)

    def test_the_tag_is_the_one_the_lookup_asked_about(self):
        self.assertIn("TAG: ${{ steps.release_state.outputs.tag }}", workflow.text())
        run = self.run_of("Publish the GitHub release")
        self.assertNotIn("v$VER", run)
        self.assertIn('"$TAG"', run)

    def test_a_literal_branch_in_the_seed_is_caught(self):
        # The mutation: the step naming `gh-pages` directly, which is the copy
        # that had no reader.
        branch = workflow.env()["PAGES_BRANCH"]
        moved = workflow.text().replace(
            './scripts/seed-tree.sh "$TREE" "$ARCH" "$PAGES_BRANCH"',
            f'./scripts/seed-tree.sh "$TREE" "$ARCH" {branch}',
        )
        self.assertNotEqual(moved, workflow.text(), "the seed moved; reword this test")
        self.assertNotIn('"$PAGES_BRANCH"', self.seed_call(source=moved))

    def test_a_commented_out_seed_call_is_not_the_live_line(self):
        # Behind a `#` the call is prose. The guard has to stop finding a line
        # to hand the branch to -- otherwise a disabled seed keeps it green,
        # and the branch it never passes is exactly what goes stale (#15, #18).
        call = './scripts/seed-tree.sh "$TREE" "$ARCH" "$PAGES_BRANCH"'
        moved = workflow.text().replace(call, "true  # " + call)
        self.assertNotEqual(moved, workflow.text(), "the seed moved; reword this test")
        # The guard names the failure it hands back, so a step renamed under it
        # fails the same way and not by some other AssertionError.
        with self.assertRaisesRegex(AssertionError, "the seed call moved"):
            self.seed_call(source=moved)


class WorkflowShellTest(unittest.TestCase):
    """The workflow's `run:` blocks are shell, and two shapes in them bite.

    One file, two contexts: most steps run on the runner, and a few cross into
    `docker run`, where the checkout is mounted at /src. A path written as if
    the step ran inside the container -- `/src/scripts/release.sh` in a step
    that does not -- fails only when that step runs, which for the release is
    after the branch has been pushed. That is one typo away from what this
    session wrote before checking the diff.

    The other: `mapfile -t x < <(reader)` exits 0 when the reader rejects a
    line, so the reader's message is printed and then discarded and the next
    module reports the failure in its own words, from inside the container.

    The scope is the workflow on purpose. In `scripts/`, `set -e` plus the
    empty-list guards already turn those reads into named failures, and four
    deliberate `mapfile < <(...)` sites there would have to be rewritten to
    satisfy a check that is guarding a different file.
    """

    def text(self) -> str:
        return workflow.text()

    def missing_scripts(self, text: str) -> list[str]:
        """Every `./scripts/...` the workflow names that cannot be run."""
        return [
            path
            for path in sorted(set(re.findall(r"\./scripts/[\w./-]+", text)))
            if not (REPO / path).is_file() or not os.access(REPO / path, os.X_OK)
        ]

    def mismounted(self, text: str) -> list[str]:
        """Lines that reach for the container's mount outside a `docker run`."""
        return [
            line
            for line in live.live_lines(text)
            if "/src" in line and "docker run" not in line
        ]

    def swallowed(self, text: str) -> list[str]:
        """`mapfile` reads whose reader's failure never reaches the step."""
        return [
            line.strip()
            for line in text.splitlines()
            if re.search(r"mapfile\s+-\S+\s+\w+\s*<\s*<\(", line)
        ]

    def test_every_script_it_names_exists_and_can_run(self):
        named = re.findall(r"\./scripts/[\w./-]+", self.text())
        self.assertTrue(named, "the workflow names no script at all")
        self.assertEqual(self.missing_scripts(self.text()), [])

    def test_the_container_mount_is_used_only_by_docker(self):
        self.assertIn("/src", self.text(), "the mount is gone; drop this check")
        self.assertEqual(self.mismounted(self.text()), [])

    def test_no_reader_is_consumed_through_a_process_substitution(self):
        self.assertEqual(self.swallowed(self.text()), [])

    def test_a_path_in_the_wrong_context_is_caught(self):
        moved = self.text().replace("./scripts/release.sh", "/src/scripts/release.sh")
        self.assertTrue(
            moved != self.text(), "the workflow no longer names ./scripts/release.sh"
        )
        self.assertTrue(self.mismounted(moved))

    def test_a_line_end_comment_does_not_reach_the_mount(self):
        # The mount named in prose is not a line that reaches for it; before
        # this, a trailing comment made `mismounted` report a step that uses
        # the mount correctly.
        self.assertEqual(self.mismounted("run  # /src is mounted here\n"), [])

    def test_a_script_that_cannot_run_is_caught(self):
        broken = self.text().replace("./scripts/release.sh", "./scripts/released.sh")
        self.assertTrue(
            broken != self.text(), "the workflow no longer names ./scripts/release.sh"
        )
        self.assertEqual(self.missing_scripts(broken), ["./scripts/released.sh"])

    def test_a_swallowed_reader_is_caught(self):
        self.assertTrue(self.swallowed("mapfile -t x < <(reader)"))
        self.assertEqual(self.swallowed('mapfile -t x <<<"$value"'), [])


if __name__ == "__main__":
    unittest.main()
