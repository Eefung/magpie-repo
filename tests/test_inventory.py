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

import bump  # noqa: E402  (the module lives beside the scripts, not on the path)

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
        workflow = (REPO / ".github" / "workflows" / "publish.yml").read_text()
        self.assertEqual([pkg for pkg in bump.PACKAGES if pkg in workflow], [])


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

    def test_the_workflow_spells_the_served_arch_on_one_line(self):
        workflow = (REPO / ".github" / "workflows" / "publish.yml").read_text()
        hits = [line.strip() for line in workflow.splitlines() if "x86_64" in line]
        self.assertEqual(hits, ["ARCH: x86_64"], hits)


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

    def declared(self, workflow: str | None = None) -> dict[str, str]:
        """The identity the workflow declares, or the text standing in for it."""
        text = self.workflow() if workflow is None else workflow
        found: dict[str, str] = {}
        for key, pattern in (
            ("arch", r"^\s+ARCH: (\S+)$"),
            ("url", r"^\s+PAGES_URL: (\S+)$"),
            ("name", r"^\s+REPO_NAME: (\S+)$"),
        ):
            match = re.search(pattern, text, re.M)
            assert match is not None, f"the workflow declares no {key}"
            found[key] = match.group(1)
        return found

    def problems(self, readme: str, workflow: str | None = None) -> list[str]:
        """Every install-block fact the README does not restate exactly."""
        want = self.declared(workflow)
        server = f"{want['url']}/{want['arch']}"
        missing = [
            f"README carries no {literal!r}"
            for literal in (
                f"[{want['name']}]",
                f"Server = {server}",
                f"{server}/{want['name']}.db",
            )
            if literal not in readme
        ]
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
        match = re.search(r"^\s+ARCH: (\S+)$", self.workflow(), re.M)
        assert match is not None, "the workflow declares no ARCH"
        return match.group(1)

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

    def test_the_seed_asks_pkg_sh_for_the_pattern(self):
        self.assertIn('"$ARCH/$(pkgfile_pattern)"', self.workflow())

    def test_the_workflow_does_not_spell_the_extension(self):
        self.assertNotIn(".pkg.tar", self.workflow())


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
    SEED_RE = re.compile(r"git archive FETCH_HEAD \| tar -x -C repo --wildcards \"[^\"]+\"")

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
                f'set -euo pipefail; source {LIB}; ARCH=x86_64; '
                f'mkdir -p repo; {self.seed_line()}',
            ],
            cwd=self.work,
            text=True,
            capture_output=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        seeded = sorted(
            str(path.relative_to(self.work / "repo"))
            for path in (self.work / "repo").rglob("*")
            if path.is_file()
        )
        self.assertEqual(seeded, [f"x86_64/{name}" for name in expected])
        # The decoys: a signature is not a 包文件, and a stray file is not seeded.
        self.assertNotIn("x86_64/magpie-bin-0.1.10-1-x86_64.pkg.tar.zst.sig", seeded)
        self.assertNotIn("SHA256SUMS", seeded)


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
