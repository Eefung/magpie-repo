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
        source = (REPO / ".github" / "workflows" / "publish.yml").read_text()
        self.assertEqual([pkg for pkg in bump.PACKAGES if pkg in source], [])


class UpstreamHandoverTest(unittest.TestCase):
    """The entry check is a module, and the workflow only relays its lines.

    The decision -- whether this run publishes, and which 版本 it renders --
    used to be bash in the step's own `run:` block, held only by greps over
    this file. tests/test_upstream.py drives the module against a stub 上游;
    this holds the step to calling it and reading its lines, and holds the
    decision words out of the YAML.
    """

    def workflow(self) -> str:
        return (REPO / ".github" / "workflows" / "publish.yml").read_text()

    def test_the_step_calls_the_entry_check_once(self):
        self.assertEqual(self.workflow().count("./scripts/upstream.sh"), 1)

    def test_the_step_reads_the_lines_it_was_handed(self):
        for key in ("upstream", "target", "bump", "packages"):
            self.assertIn(f"line_field {key} /tmp/up.txt", self.workflow())

    def test_the_render_step_takes_the_version_the_check_decided(self):
        workflow = self.workflow()
        self.assertIn('--render --version "$target"', workflow)
        self.assertIn("TARGET: ${{ steps.upstream.outputs.target }}", workflow)
        # One render invocation, and it always carries a version: a bare
        # `--render` would ask 上游 again and undo the handover.
        self.assertEqual(workflow.count("scripts/bump.py --render"), 1)

    def test_the_decision_is_not_made_a_second_time_in_this_file(self):
        # These two are what the step used to decide: `bump` from a default,
        # and `target` from WANTED-or-UPSTREAM. A reformat broke the test job
        # while flipping the default -- a broken check now meaning "publish"
        # -- did not. Both are upstream.sh's now, so neither spelling returns.
        workflow = self.workflow()
        self.assertNotIn("bump:-", workflow)
        self.assertNotIn("WANTED:-", workflow)
        self.assertNotIn("bump.py --check", workflow)


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
            "printf 'upstream=%s\\ntarget=%s\\nbump=%s\\n' \\",
            "printf 'upstream=%s\\ntarget=%s\\nbump=%s\\nunused=%s\\n' \\",
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
        workflow = (REPO / ".github" / "workflows" / "publish.yml").read_text()
        self.assertIn('--volume "$PWD:/src" --workdir /src', workflow)

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

    def hits(self, source: str | None = None) -> list[str]:
        """Live lines naming the served arch; comments are not declarations."""
        arch = workflow.env(source=source)["ARCH"]
        return [
            line.strip()
            for line in (workflow.text() if source is None else source).splitlines()
            if arch in line and not line.lstrip().startswith("#")
        ]

    def test_the_workflow_declares_the_served_arch_once(self):
        found = self.hits()
        self.assertEqual(len(found), 1, found)
        self.assertTrue(found[0].startswith("ARCH:"), found)

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
        self.assertEqual(len(self.hits(moved)), 1, self.hits(moved))
        self.assertTrue(self.hits(moved)[0].startswith("ARCH:"), self.hits(moved))

    def test_a_second_live_line_naming_the_arch_is_caught(self):
        text = workflow.text()
        moved = text.replace(
            "      ARCH: x86_64\n", "      ARCH: x86_64\n      EXTRA_ARCH: x86_64\n"
        )
        self.assertNotEqual(moved, text)
        self.assertEqual(len(self.hits(moved)), 2, self.hits(moved))


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
    """

    WORKFLOW = REPO / ".github" / "workflows" / "publish.yml"

    def workflow(self) -> str:
        return self.WORKFLOW.read_text()

    def readme(self) -> str:
        return (REPO / "README.md").read_text()

    def declared(self, source: str | None = None) -> dict[str, str]:
        """The identity the workflow declares, or the text standing in for it."""
        found = workflow.env(source=source)
        missing = [key for key in ("ARCH", "PAGES_URL", "REPO_NAME") if key not in found]
        self.assertEqual(missing, [], f"the workflow declares no {missing}")
        return {
            "arch": found["ARCH"],
            "url": found["PAGES_URL"],
            "name": found["REPO_NAME"],
        }

    def install_block(self, readme: str) -> str:
        """The fenced block a user copies into pacman.conf.

        The block, not the whole README: every fact it carries is also
        explained in the prose around it, so a search of the file as a whole is
        satisfied by the sentence that describes the line instead of by the
        line the user copies.
        """
        try:
            section = readme.split("## 安装", 1)[1]
            start = section.index("```bash") + len("```bash")
            end = section.index("```", start)
        except (IndexError, ValueError):
            return ""
        return section[start:end]

    def problems(self, readme: str, source: str | None = None) -> list[str]:
        """Every install-block fact the README does not restate exactly."""
        want = self.declared(source)
        server = f"{want['url']}/{want['arch']}"
        block = self.install_block(readme)
        missing = [
            f"README's install block carries no {literal!r}"
            for literal in (
                f"[{want['name']}]",
                # Unsigned is a choice (ADR-0002), and this line is what makes
                # it work: without it the user inherits [options]' Required and
                # the install dies on a signature that will never exist. No
                # producer hands this line over -- the workflow does not know
                # it -- so the guard is where it lives, and it has to read the
                # block: the prose beside it says the same words.
                "SigLevel = Optional",
                f"Server = {server}",
            )
            if literal not in block
        ]
        if f"{server}/{want['name']}.db" not in readme:
            missing.append(f"README carries no {server}/{want['name']}.db")
        missing += [
            f"README never installs {pkg}"
            for pkg in bump.PACKAGES
            if f"pacman -S {pkg}" not in readme
        ]
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
            ["README's install block carries no 'SigLevel = Optional'"],
        )


class ArchTableTest(unittest.TestCase):
    """What the arch 表 declares is what CI builds and serves.

    aarch64 lived in the table and in both templates while CI built x86_64
    alone. The render then demanded an upstream arm64 asset nobody packaged, so
    a release that stopped publishing one stopped the x86_64 release too
    (ADR-0003). An arch may not be declared here without a build behind it.
    """

    def workflow(self) -> str:
        return (REPO / ".github" / "workflows" / "publish.yml").read_text()

    def served(self) -> str:
        return workflow.env()["ARCH"]

    def test_every_arch_the_table_names_is_the_one_ci_serves(self):
        # The mutation that mattered: the table gains an arch while no container
        # builds it, and the next render asks upstream for its asset.
        self.assertEqual(set(bump.ARCHES), {self.served()})


class PackageFilePatternTest(unittest.TestCase):
    """What counts as a 包文件 is one rule, and it lives in pkg.sh.

    The workflow's seed whitelist spelled the glob itself, in a file that also
    decides which tree gets carried forward; build-repo's window spelled it
    again. Two spellings meant a retired package could be seeded forward by one
    and pruned by neither, and a `.sig` beside a package would match both.
    """

    def workflow(self) -> str:
        return (REPO / ".github" / "workflows" / "publish.yml").read_text()

    def spelling(self, text: str) -> list[str]:
        """Live lines that spell the extension themselves.

        A comment may name it -- that is how the history is explained -- but a
        line that runs must ask pkg.sh.
        """
        return [
            line.strip()
            for line in text.splitlines()
            if ".pkg.tar" in line and not line.lstrip().startswith("#")
        ]

    def sourcing(self) -> list[Path]:
        """The scripts that source pkg.sh, and can therefore ask it."""
        return [
            path
            for path in sorted((REPO / "scripts").glob("*.sh"))
            if any(
                "lib/pkg.sh" in line and "source" in line
                for line in path.read_text().splitlines()
                if not line.lstrip().startswith("#")
            )
        ]

    def test_the_seed_asks_pkg_sh_for_the_pattern(self):
        self.assertIn('"$ARCH/$(pkgfile_pattern)"', self.workflow())

    def test_the_workflow_does_not_spell_the_extension(self):
        self.assertNotIn(".pkg.tar", self.workflow())

    def test_no_script_that_sources_pkg_sh_spells_the_extension(self):
        # The guard used to stop at the workflow, so build-check's own globs
        # (their own width, `*.pkg.tar.*`) and the probe's sat outside it. A
        # script that already sources the rule has no excuse for spelling it.
        # scripts/probe-repo-build.sh is deliberately not on this list: it
        # counts files with a glob of its own, which is what makes it a
        # measurement of the build rather than a second copy of the rule.
        sources = self.sourcing()
        self.assertIn("build-check.sh", [path.name for path in sources])
        self.assertIn("build-repo.sh", [path.name for path in sources])
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


class SeedTest(unittest.TestCase):
    """The seed line, run for real against a branch that has the decoys in it.

    What it carries decides what survives a publish: a seed that drops a 包文件
    silently empties the 保留窗口, and one that carries a `.sig` feeds it to
    repo-add the day signing lands. The workflow line is the only place this
    runs, so the test takes that line from the workflow and runs it, rather than
    restating what it does -- a test that spelled its own `tar` call would pin
    the test author's copy.
    """

    WORKFLOW = REPO / ".github" / "workflows" / "publish.yml"
    SEED_RE = re.compile(
        r'git archive FETCH_HEAD \| tar -x -C \S+ --wildcards "[^"]+"'
    )

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.origin = self.root / "origin"
        self.work = self.root / "work"

    def seed_line(self) -> str:
        found = self.SEED_RE.findall(self.WORKFLOW.read_text())
        self.assertEqual(len(found), 1, found)
        return found[0]

    def git(self, *args: str, cwd: Path) -> None:
        subprocess.run(
            ["git", "-c", "user.email=test@example.invalid", "-c", "user.name=test", *args],
            cwd=cwd,
            check=True,
            capture_output=True,
        )

    def published_tree(self) -> list[str]:
        """A previous gh-pages branch: two 包文件, a signature, a stray file."""
        (self.origin / "x86_64").mkdir(parents=True)
        for name in (
            "magpie-bin-0.1.9-1-x86_64.pkg.tar.zst",
            "magpie-bin-0.1.10-1-x86_64.pkg.tar.zst",
            "magpie-bin-0.1.10-1-x86_64.pkg.tar.zst.sig",
        ):
            (self.origin / "x86_64" / name).touch()
        (self.origin / "SHA256SUMS").touch()
        self.git("init", "-q", "--initial-branch=main", ".", cwd=self.origin)
        self.git("add", "-A", cwd=self.origin)
        self.git("commit", "-q", "-m", "previous publish", cwd=self.origin)
        return [
            "magpie-bin-0.1.10-1-x86_64.pkg.tar.zst",
            "magpie-bin-0.1.9-1-x86_64.pkg.tar.zst",
        ]

    def test_it_carries_exactly_the_pkgfiles_pkg_sh_can_see(self):
        expected = self.published_tree()
        self.work.mkdir()
        self.git("init", "-q", "--initial-branch=gh-pages", ".", cwd=self.work)
        self.git("remote", "add", "origin", str(self.origin), cwd=self.work)
        self.git("fetch", "--depth", "1", "origin", "main", cwd=self.work)

        result = subprocess.run(
            [
                "bash",
                "-c",
                f'set -euo pipefail; source {LIB}; ARCH=x86_64; TREE=repo; '
                f'mkdir -p "$TREE"; {self.seed_line()}',
            ],
            cwd=self.work,
            text=True,
            capture_output=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        tree = self.work / workflow.env()["TREE"]
        seeded = sorted(
            str(path.relative_to(tree))
            for path in tree.rglob("*")
            if path.is_file()
        )
        self.assertEqual(seeded, [f"x86_64/{name}" for name in expected])
        # The decoys: a signature is not a 包文件, and a stray file is not seeded.
        self.assertNotIn("x86_64/magpie-bin-0.1.10-1-x86_64.pkg.tar.zst.sig", seeded)
        self.assertNotIn("SHA256SUMS", seeded)


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
        # The value crosses into the container through `-e`, beside REPO_NAME:
        # a script default equal to the declared value makes a forgotten flag
        # invisible, which is why this is asserted as text rather than by a run.
        self.assertIn("-e REPO_NAME -e KEEP", workflow.text())

    def test_the_readme_promises_the_versions_the_window_keeps(self):
        self.assertIn(f"保留 {self.keep()} 个版本", (REPO / "README.md").read_text())

    def test_the_glossary_states_the_same_number(self):
        self.assertIn(f"KEEP={self.keep()}", (REPO / "GLOSSARY.md").read_text())


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

    WORKFLOW = REPO / ".github" / "workflows" / "publish.yml"

    def text(self) -> str:
        return self.WORKFLOW.read_text()

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
            line.strip()
            for line in text.splitlines()
            if "/src" in line
            and not line.strip().startswith("#")
            and "docker run" not in line
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
