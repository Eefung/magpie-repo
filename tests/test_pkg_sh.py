"""Tests for scripts/lib/pkg.sh -- the shell's one reader of package names.

b085210 (the database must list only each package's newest build) landed here
with no test: the sort rule and the file glob were written twice, in `prune()`
and in `newest()`. They are one function now, and this is what holds it. The
window (`pkgfiles_prune`), the db entry (`pkgfiles_current`) and the release
selection (`pkgfiles_current` again -- a Release describes one build, not every
build of one 版本) are thin answers over that one order, so they are tested here
too instead of only inside a publish run.

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import contextlib
import fnmatch
import io
import re
import shlex
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LIB = REPO / "scripts" / "lib" / "pkg.sh"

sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "tests"))

import bump  # noqa: E402  (the module lives beside the scripts, not on the path)
import fixtures  # noqa: E402  (the one spelling of a 包文件's name in the test tree)
import live  # noqa: E402  (the one reader of where a comment starts)


def ignore_lines(text: str) -> list[str]:
    """The patterns a .gitignore declares: a `#` is a comment only in column one.

    This is the one reader that does not go through `live`, and the reason is
    the file's format, not a second shell width: `gitignore(5)` has no trailing
    comments, so `packages/*/magpie-*  # note` is a pattern that matches nothing
    (checked: `git check-ignore` refuses it). Reading it with the bash reader
    would cut the line there and let the guard pass on an ignore file that
    ignores nothing -- the false green this guard exists to catch.
    """
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def call(script: str) -> subprocess.CompletedProcess:
    """Run `script` with the library sourced, the way the build scripts do."""
    return subprocess.run(
        ["bash", "-c", f'set -euo pipefail; source {shlex.quote(str(LIB))}; {script}'],
        capture_output=True,
        text=True,
    )


class PkgfilesTest(unittest.TestCase):
    """`pkgfiles` answers "which builds of this package are here, in order"."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.out = Path(self._tmp.name)

    def touch(self, *names: str) -> None:
        for name in names:
            (self.out / name).touch()

    def files(self, pkgname: str) -> list[str]:
        result = call(f"pkgfiles {shlex.quote(str(self.out))} {shlex.quote(pkgname)}")
        self.assertEqual(result.returncode, 0, result.stderr)
        return [Path(line).name for line in result.stdout.splitlines()]

    def test_orders_by_version_number_not_by_text(self):
        self.touch(
            "magpie-bin-0.1.9-1-x86_64.pkg.tar.zst",
            "magpie-bin-0.1.1000-1-x86_64.pkg.tar.zst",
            "magpie-bin-0.1.10-1-x86_64.pkg.tar.zst",
        )
        self.assertEqual(
            self.files("magpie-bin"),
            [
                "magpie-bin-0.1.9-1-x86_64.pkg.tar.zst",
                "magpie-bin-0.1.10-1-x86_64.pkg.tar.zst",
                "magpie-bin-0.1.1000-1-x86_64.pkg.tar.zst",
            ],
        )

    def test_the_tail_is_the_build_that_belongs_in_the_database(self):
        self.touch(
            "magpie-bin-0.1.9-1-x86_64.pkg.tar.zst",
            "magpie-bin-0.1.1000-1-x86_64.pkg.tar.zst",
            "magpie-bin-0.1.10-1-x86_64.pkg.tar.zst",
        )
        self.assertEqual(self.files("magpie-bin")[-1], "magpie-bin-0.1.1000-1-x86_64.pkg.tar.zst")

    def test_leaves_other_packages_alone(self):
        self.touch(
            "magpie-bin-0.1.9-1-x86_64.pkg.tar.zst",
            "magpie-cli-bin-0.1.9-1-x86_64.pkg.tar.zst",
        )
        self.assertEqual(self.files("magpie-bin"), ["magpie-bin-0.1.9-1-x86_64.pkg.tar.zst"])

    def test_a_dash_in_the_package_name_does_not_leak_into_the_glob(self):
        # magpie-bin must not pick up magpie-bin-extra's files.
        self.touch(
            "magpie-bin-0.1.9-1-x86_64.pkg.tar.zst",
            "magpie-bin-extra-0.1.9-1-x86_64.pkg.tar.zst",
        )
        self.assertEqual(self.files("magpie-bin"), ["magpie-bin-0.1.9-1-x86_64.pkg.tar.zst"])

    def test_a_package_with_no_builds_is_empty_not_an_error(self):
        self.assertEqual(self.files("magpie-bin"), [])


class SrcinfoFieldTest(unittest.TestCase):
    """The fields a srcinfo block answers, and the shapes it must refuse.

    build-check.sh reads `pkgver`/`pkgname` out of the block makepkg printed
    rather than parsing the PKGBUILD beside it. `pkg_field`, the shell reader
    this replaced, existed for those two lines only, and it was half of a
    two-runtime agreement -- bump.py reads the same fields out of a tree no
    makepkg has touched -- that needed a table of shapes to hold together. With
    the shell side gone there is one reader for the file and one question for
    the block, and what has to hold is the refusal: no answer is a block that
    decided nothing, and two answers is a block the caller cannot choose from.
    """

    def field(self, name: str, text: str) -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "srcinfo"
            path.write_text(text)
            return call(f'srcinfo_field {name} "$(cat {shlex.quote(str(path))})"')

    def test_reads_a_field_and_drops_the_indent(self):
        text = "pkgbase = magpie-bin\n\tpkgver = 0.1.4131\npkgname = magpie-cli-bin\n"
        pkgver = self.field("pkgver", text)
        self.assertEqual(pkgver.returncode, 0, pkgver.stderr)
        self.assertEqual(pkgver.stdout, "0.1.4131\n")
        self.assertEqual(self.field("pkgname", text).stdout, "magpie-cli-bin\n")

    def test_a_block_that_does_not_answer_is_refused(self):
        result = self.field("pkgver", "pkgname = magpie-bin\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("pkgver", result.stderr)

    def offenders(self, needle: str) -> list[str]:
        """Scripts whose live lines mention `needle`: a note about it is not it."""
        return [
            path.name
            for path in sorted((REPO / "scripts").rglob("*.sh"))
            if live.naming(path.read_text(), needle)
        ]

    def test_the_shell_parses_no_pkgbuild(self):
        # The reader that pulled `pkgname=`/`pkgver=` out of a PKGBUILD is
        # gone -- makepkg answers both, from the file it just parsed. A
        # reintroduction would be a second parse of the same file, in the
        # runtime that has no python3 to read it the way the renderer writes it.
        self.assertEqual(self.offenders("pkg_field"), [])
        # And not by another route: a text tool reaching into the file is the
        # same parse under another name, which the name guard above cannot see.
        self.assertEqual(self.parsers(), [])

    def test_a_trailing_comment_is_not_a_mention(self):
        # A note that names the old reader, or shows a text tool at work, is
        # prose: before this the two readers dropped whole-line comments only,
        # so the note counted against the script that carried it.
        self.assertEqual(live.naming("x  # pkg_field is gone\n", "pkg_field"), [])
        self.assertEqual(self.parsing("x  # grep PKGBUILD\n"), [])

    def parsing(self, text: str) -> list[str]:
        """The live lines of one text that read a PKGBUILD with a text tool."""
        return [
            line
            for line in live.naming(text, "PKGBUILD")
            if re.search(r"\b(sed|grep|awk|cut)\b", line)
        ]

    def parsers(self) -> list[str]:
        """Every script's live lines that reach into a PKGBUILD, named."""
        return [
            f"{path.name}: {line.strip()}"
            for path in sorted((REPO / "scripts").rglob("*.sh"))
            for line in self.parsing(path.read_text())
        ]

    def test_two_answers_are_refused_not_chosen(self):
        result = self.field("pkgver", "pkgver = 0.1.1\npkgver = 0.1.2\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not one", result.stderr)


class RepoDirectory(unittest.TestCase):
    """A temporary directory holding 包文件 name-shaped files."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.out = Path(self._tmp.name)
        self.touched: list[str] = []

    def touch(self, *names: str) -> None:
        for name in names:
            (self.out / name).touch()
            self.touched.append(name)

    def answer(self, script: str) -> list[str]:
        result = call(script)
        self.assertEqual(result.returncode, 0, result.stderr)
        return [Path(line).name for line in result.stdout.splitlines()]

    def pruned(self, keep: int, anchor: str | None = None) -> list[str]:
        """The files the 保留窗口 drops, with the window ending at `anchor`.

        Every test below lays its 包文件 down oldest first, so the default --
        the last one put down -- is the build a normal run would have just made.
        """
        anchor = anchor or self.touched[-1]
        path = shlex.quote(str(self.out / anchor))
        return self.answer(f"pkgfiles_prune {self.out_arg()} magpie-bin {keep} {path}")

    def out_arg(self) -> str:
        return shlex.quote(str(self.out))


def build(version: str, rel: str = fixtures.REL, arch: str = fixtures.ARCH) -> str:
    return fixtures.pkgfile_name("magpie-bin", version, rel, arch)


class PkgfilesPruneTest(RepoDirectory):
    """The retention window: which files fall out, given KEEP and a build."""

    def prune(self, keep: int, anchor: str | None = None) -> list[str]:
        return self.pruned(keep, anchor)

    def test_drops_the_oldest_once_the_window_is_full(self):
        self.touch(build("0.1.9"), build("0.1.10"), build("0.1.1000"))
        self.assertEqual(self.prune(2), [build("0.1.9")])

    def test_drops_nothing_while_the_window_has_room(self):
        self.touch(build("0.1.9"), build("0.1.10"))
        self.assertEqual(self.prune(2), [])

    def test_drops_every_surplus_when_the_window_shrank(self):
        self.touch(build("0.1.7"), build("0.1.8"), build("0.1.9"), build("0.1.10"))
        self.assertEqual(self.prune(1), [build("0.1.7"), build("0.1.8"), build("0.1.9")])

    def test_the_window_counts_versions_not_files(self):
        # GLOSSARY: 保留窗口 is 每个包同时保留的版本数. Counting files instead left
        # one version in a two-arch repository and dropped both files of the
        # older one -- and the database then named a single arch.
        self.touch(
            build("0.1.9", arch="x86_64"), build("0.1.9", arch="aarch64"),
            build("0.1.10", arch="x86_64"), build("0.1.10", arch="aarch64"),
            build("0.1.11", arch="x86_64"), build("0.1.11", arch="aarch64"),
        )
        self.assertEqual(
            sorted(self.prune(2)),
            sorted([build("0.1.9", arch="x86_64"), build("0.1.9", arch="aarch64")]),
        )

    def test_every_arch_of_a_surviving_version_is_kept(self):
        self.touch(
            build("0.1.9"),
            build("0.1.10"), build("0.1.10", arch="aarch64"),
            build("0.1.11"), build("0.1.11", arch="aarch64"),
        )
        self.assertEqual(set(self.prune(2)), {build("0.1.9")})

    def test_the_oldest_is_found_by_version_not_by_text(self):
        # Read as text, 0.1.1000 sorts before 0.1.999; the window must not care.
        self.touch(build("0.1.999"), build("0.1.1000"), build("0.1.1001"))
        self.assertEqual(self.prune(2), [build("0.1.999")])

    def test_a_package_with_no_builds_prunes_nothing(self):
        # The first publish of a package: nothing in the directory, so there is
        # nothing to anchor on either and the answer is empty rather than an
        # error about a build that never landed.
        self.assertEqual(self.prune(2, anchor=build("0.1.9")), [])

    def test_the_window_ends_at_the_build_this_run_made(self):
        # A pinned 版本 (`WANTED`) is the oldest file in the directory. The
        # window used to count back from the maximum, so it deleted the build
        # the run had just made and the release step found nothing to publish,
        # after gh-pages had already been force-pushed.
        self.touch(build("0.1.8"), build("0.1.9"), build("0.1.10"))
        self.assertEqual(
            self.prune(2, anchor=build("0.1.8")), [build("0.1.9"), build("0.1.10")]
        )

    def test_anything_above_the_build_stops_being_served(self):
        # The database names the build this run published; a version above it is
        # no longer what a client is offered, so it does not get to hold a slot
        # in the window either.
        self.touch(build("0.1.9"), build("0.1.10"))
        self.assertEqual(self.prune(2, anchor=build("0.1.9")), [build("0.1.10")])

    def test_a_build_the_directory_does_not_carry_is_refused(self):
        # The anchor is the caller's claim about what it just built. If the file
        # is not there, the claim is wrong -- not a window to compute.
        self.touch(build("0.1.9"))
        missing = shlex.quote(str(self.out / build("0.1.8")))
        result = call(f"pkgfiles_prune {self.out_arg()} magpie-bin 2 {missing}")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not in", result.stderr)


class PkgfilesCurrentTest(RepoDirectory):
    """The one file the database entry names."""

    def current(self) -> list[str]:
        return self.answer(f"pkgfiles_current {self.out_arg()} magpie-bin")

    def test_the_newest_build_is_the_current_one(self):
        self.touch(build("0.1.9"), build("0.1.10"), build("0.1.1000"))
        self.assertEqual(self.current(), [build("0.1.1000")])

    def test_the_window_and_the_database_read_one_order(self):
        self.touch(build("0.1.9"), build("0.1.10"), build("0.1.1000"))
        pruned = self.pruned(2)
        self.assertNotIn(self.current()[0], pruned)

    def test_every_arch_of_the_newest_version_is_current(self):
        # One database entry per arch. With one arch built that is one file,
        # which is what build-repo.sh hands repo-add today; the rule behind it
        # is "the newest build", not "the last file in the sort order".
        self.touch(build("0.1.9"), build("0.1.10"), build("0.1.10", arch="aarch64"))
        self.assertEqual(
            sorted(self.current()),
            sorted([build("0.1.10"), build("0.1.10", arch="aarch64")]),
        )

    def test_a_rebuild_is_the_database_entry_not_the_build_it_replaces(self):
        # pkgrel 2 of the same 版本. The window keeps both files -- they are one
        # 版本 -- and handing repo-add both would enter magpie-bin twice, so the
        # entry takes the newest <pkgver>-<pkgrel>. This is the path the entry
        # check now opens: a rebuild is a publish, and it must land as one build.
        self.touch(build("0.1.9"), build("0.1.9", rel="2"))
        self.assertEqual(self.current(), [build("0.1.9", rel="2")])

    def test_nothing_on_disk_is_empty_not_an_error(self):
        self.assertEqual(self.current(), [])


class PkgfileNamingTest(RepoDirectory):
    """The naming rule, and its inverse: what package does this file belong to."""

    def pattern(self) -> str:
        result = call("pkgfile_pattern")
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def suffix(self) -> str:
        result = call("pkgfile_suffix")
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def pkgname(self, name: str) -> subprocess.CompletedProcess:
        return call(f"pkgfile_pkgname {shlex.quote(str(self.out / name))}")

    def evr(self, name: str) -> subprocess.CompletedProcess:
        return call(f"pkgfile_evr magpie-bin {shlex.quote(str(self.out / name))}")

    def test_the_pattern_is_the_suffix_with_a_star(self):
        self.assertEqual(self.suffix(), ".pkg.tar.zst")
        self.assertEqual(self.pattern(), "*.pkg.tar.zst")

    def test_the_evr_is_the_pkgver_and_the_pkgrel(self):
        # What the database entry and the release describe, as opposed to the
        # 版本 the window counts: two builds of one 版本 differ here.
        self.assertEqual(
            self.evr("magpie-bin-0.1.1000-2-aarch64.pkg.tar.zst").stdout, "0.1.1000-2\n"
        )

    def test_a_dash_in_the_pkgname_survives_the_peel(self):
        # The three fields makepkg appends come off the right; magpie-cli-bin is
        # one package, not `magpie`.
        self.assertEqual(
            self.pkgname("magpie-cli-bin-0.1.9-1-x86_64.pkg.tar.zst").stdout,
            "magpie-cli-bin\n",
        )

    def test_pkgrel_and_arch_are_not_read_out_of_the_name(self):
        self.assertEqual(
            self.pkgname("magpie-bin-0.1.1000-2-aarch64.pkg.tar.zst").stdout,
            "magpie-bin\n",
        )

    def test_a_file_that_is_not_a_pkgfile_is_refused(self):
        for name in ("notes.pkg.tar.zst", "magpie-bin-0.1.9-1-x86_64.pkg.tar.gz",
                     "magpie-bin.pkg.tar.zst"):
            with self.subTest(name=name):
                self.assertNotEqual(self.pkgname(name).returncode, 0, name)


class FixtureNamingTest(unittest.TestCase):
    """The test tree's one spelling of a 包文件 name, read back by pkg.sh.

    `pkgfile_pattern`, `pkgfile_pkgname` and `pkgfile_version` are the rule; a
    fixture that spelled the name itself was the second copy of it, and the
    stubs wrote `-1-x86_64` in by hand, so a pkgrel bump or a second arch would
    leave every retention test green while the real chain produced other names.
    """

    def test_the_fixture_name_is_the_one_pkg_sh_reads(self):
        with tempfile.TemporaryDirectory() as tmp:
            for pkg, version, rel, arch in (
                ("magpie-bin", "0.1.10", "1", "x86_64"),
                ("magpie-cli-bin", "0.1.1000", "2", "aarch64"),
            ):
                with self.subTest(pkg=pkg, arch=arch):
                    path = Path(tmp) / fixtures.pkgfile_name(pkg, version, rel, arch)
                    path.touch()
                    read_back = call(f"pkgfile_pkgname {shlex.quote(str(path))}")
                    self.assertEqual(read_back.stdout, f"{pkg}\n", read_back.stderr)
                    read_version = call(
                        f"pkgfile_version {shlex.quote(pkg)} {shlex.quote(str(path))}"
                    )
                    self.assertEqual(read_version.stdout, f"{version}\n", read_version.stderr)

    def test_the_fixture_suffix_is_the_served_one(self):
        served = call("pkgfile_suffix")
        self.assertEqual(served.returncode, 0, served.stderr)
        self.assertEqual(fixtures.SUFFIX, served.stdout.strip())

    def test_the_fixture_arch_is_the_one_this_repository_builds(self):
        # The arch has one owner -- the arch 表, itself held to the workflow's
        # env by test_inventory's ArchTableTest. This says the fixture did not
        # grow a second one: the aarch64 case above proves the *reader* accepts
        # any arch, so nothing else would notice.
        self.assertEqual({fixtures.ARCH}, set(bump.ARCHES))

    def test_the_fixture_pkgrel_is_the_one_the_templates_declare(self):
        # pkgrel is the PKGBUILD's, and every template says the same thing --
        # so a bump there has one place to land and one test to redden.
        declared = set()
        for path in sorted((REPO / "templates").glob("*.PKGBUILD.in")):
            match = re.search(r"^pkgrel=(\S+)$", path.read_text(), re.M)
            self.assertIsNotNone(match, f"{path.name} declares no pkgrel=")
            declared.add(match.group(1))
        self.assertEqual(declared, {fixtures.REL})


class PackageResidueTest(unittest.TestCase):
    """What a build leaves is wider than what the repository serves."""

    def pattern(self, function: str) -> str:
        result = call(function)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def test_the_residue_set_is_wider_than_the_served_set(self):
        served = self.pattern("pkgfile_pattern")
        residue = self.pattern("pkgfile_residue_pattern")
        self.assertEqual(served, "*.pkg.tar.zst")
        self.assertEqual(residue, "*.pkg.tar.*")
        # A detached signature and an older default's archive are residue: the
        # sweep must clear them, the database and the seed must never see them.
        for name in (
            "magpie-bin-0.1.9-1-x86_64.pkg.tar.zst.sig",
            "magpie-bin-0.1.9-1-x86_64.pkg.tar.xz",
        ):
            self.assertFalse(Path(name).match(served), name)
            self.assertTrue(Path(name).match(residue), name)

    def test_the_gitignore_covers_the_residue_set(self):
        # The trap clears the directory; .gitignore is what protects a run that
        # was killed before its trap could fire. One set, so a shape added to
        # the sweep cannot be left out of the ignore.
        residue = self.pattern("pkgfile_residue_pattern")
        self.assertIn(f"packages/*/{residue}", (REPO / ".gitignore").read_text())

    def leftovers(self) -> list[str]:
        result = call("pkgfile_leftovers")
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.split()

    def test_the_leftovers_are_the_working_trees_and_a_stale_srcinfo(self):
        self.assertEqual(self.leftovers(), ["src", "pkg", ".srcdest", ".SRCINFO"])

    def test_the_gitignore_covers_the_leftovers(self):
        # GLOSSARY says `.gitignore` covers the same set as the sweep. Before
        # this, only the residue glob had a reader: the four names were spelled
        # by hand in the ignore file, in build-check's LEFTOVERS and again in
        # the build-check test, and a fifth name would have reached main
        # through a hand `git add packages`.
        ignored = (REPO / ".gitignore").read_text()
        for name in self.leftovers():
            with self.subTest(name=name):
                self.assertIn(f"packages/*/{name}", ignored)

    def test_the_gitignore_covers_the_asset_prefixes(self):
        # A hand-run makepkg drops the upstream assets beside the PKGBUILD,
        # named `<prefix>-<version>-<suffix>`; the line that ignores them was
        # the inventory's prefixes spelled a second time, with nothing holding
        # the two together. A package whose prefix no line covers would leave
        # a 30 MB binary for `git add packages` to pick up.
        lines = ignore_lines((REPO / ".gitignore").read_text())
        for prefix in sorted(set(bump.PACKAGES.values())):
            with self.subTest(prefix=prefix):
                self.assertTrue(
                    any(fnmatch.fnmatch(f"packages/x/{prefix}-0.1.9-amd64", line) for line in lines),
                    f"no .gitignore line covers packages/*/{prefix}-*",
                )

    def test_a_line_end_comment_is_not_a_gitignore_comment(self):
        # `gitignore(5)`: only a line starting with `#` is a comment. A note
        # after a pattern is part of the pattern, which then matches nothing --
        # so the reader keeps it, and the prefix guard reddens the line. This
        # case is what keeps someone from "fixing" it with the bash reader.
        self.assertEqual(
            ignore_lines("packages/*/magpie-*  # the desktop assets\n"),
            ["packages/*/magpie-*  # the desktop assets"],
        )
        self.assertEqual(ignore_lines("# packages/*/magpie-*\n"), [])


class PkgfilesAllTest(RepoDirectory):
    """One glob decides what a 包文件 is, for every caller that gropes a dir."""

    def all(self) -> list[str]:
        return self.answer(f'pkgfiles_all {self.out_arg()}')

    def test_a_signature_beside_a_package_is_not_a_package(self):
        # `*.pkg.tar.*` matched the .sig too, which repo-add would have been
        # handed the day signing lands.
        self.touch(build("0.1.9"), build("0.1.9") + ".sig")
        self.assertEqual(self.all(), [build("0.1.9")])

    def test_an_empty_directory_is_empty_not_an_error(self):
        self.assertEqual(self.all(), [])

    def test_the_order_is_the_window_and_database_order(self):
        self.touch(build("0.1.9"), build("0.1.1000"), build("0.1.10"))
        self.assertEqual(self.all(), [build("0.1.9"), build("0.1.10"), build("0.1.1000")])

    def test_another_packages_files_are_listed_here(self):
        # This one question is "the tree", not "my package"; pkgfiles narrows.
        self.touch(build("0.1.9"), "magpie-cli-bin-0.1.9-1-x86_64.pkg.tar.zst")
        self.assertEqual(len(self.all()), 2)


class PkgfilesPackagesTest(RepoDirectory):
    """Which packages does this directory carry -- the question nobody asked."""

    def packages(self) -> subprocess.CompletedProcess:
        return call(f"pkgfiles_packages {self.out_arg()}")

    def names(self) -> list[str]:
        result = self.packages()
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.splitlines()

    def test_each_package_once_however_many_versions(self):
        self.touch(build("0.1.9"), build("0.1.10"),
                   "magpie-cli-bin-0.1.9-1-x86_64.pkg.tar.zst",
                   "magpie-cli-bin-0.1.10-1-x86_64.pkg.tar.zst")
        self.assertEqual(self.names(), ["magpie-bin", "magpie-cli-bin"])

    def test_files_that_are_not_pkgfiles_are_ignored(self):
        self.touch("SHA256SUMS", "magpie.db.tar.gz", build("0.1.9"))
        self.assertEqual(self.names(), ["magpie-bin"])

    def test_an_empty_directory_carries_nothing(self):
        self.assertEqual(self.names(), [])

    def test_a_pkgfile_whose_name_does_not_parse_is_a_failure(self):
        # Membership is the extension; a name that then does not parse means
        # the naming rule changed under the reader.
        self.touch("notes.pkg.tar.zst")
        result = self.packages()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not a 包文件 name", result.stderr)


class LineFieldTest(unittest.TestCase):
    """The one reader of the frozen `name=value` lines."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.log = Path(self._tmp.name) / "out.txt"

    def field(self, name: str, body: str | None = None) -> subprocess.CompletedProcess:
        if body is not None:
            self.log.write_text(body)
        return call(f"line_field {shlex.quote(name)} {shlex.quote(str(self.log))}")

    def test_reads_the_line_the_producer_wrote(self):
        result = self.field("ver", "rendered packages/magpie-bin at 0.1.2\nver=0.1.2\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "0.1.2\n")

    def test_a_value_with_spaces_survives_whole(self):
        # line_field hands back the rest of the line, whatever it holds. No
        # frozen line carries a list today (the 包清单's rows go through
        # pkglist_field, not here), so this pins the reader's shape rather than
        # a producer: a future line-valued list line is read whole.
        result = self.field("behind", "behind=magpie-bin magpie-cli-bin\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "magpie-bin magpie-cli-bin\n")

    def test_two_matches_are_a_protocol_break_not_a_choice(self):
        # Every producer writes its line once, so a second one is a bug worth
        # stopping on rather than picking a winner from.
        result = self.field("ver", "ver=1\nver=2\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("2 ver= lines", result.stderr)

    def test_a_missing_line_fails_and_names_it(self):
        result = self.field("ver", "upstream=0.1.2\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no ver=", result.stderr)

    def test_an_empty_value_is_not_a_value(self):
        result = self.field("ver", "ver=\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("empty ver=", result.stderr)

    def test_a_missing_file_fails_loudly(self):
        result = self.field("ver")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no such file", result.stderr)


class LineFieldOrTest(unittest.TestCase):
    """The reader that tells "absent" (a default) from "broken" (a failure)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.log = Path(self._tmp.name) / "out.txt"

    def field(self, body: str, default: str = "pre-contract") -> subprocess.CompletedProcess:
        self.log.write_text(body)
        return call(
            f"line_field_or carried {shlex.quote(str(self.log))} {shlex.quote(default)}"
        )

    def test_an_absent_line_is_the_callers_default(self):
        # A key a producer may leave out -- `carried=` is absent when the tree
        # carries no 版本 -- is the caller's default, not a failure.
        result = self.field("upstream=0.1.2\n")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "pre-contract\n")

    def test_a_present_line_wins_over_the_default(self):
        result = self.field("carried=0.1.10\n", default="pre-contract")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "0.1.10\n")

    def test_two_lines_are_still_a_protocol_break(self):
        # `line_field ... || true` collapsed this into the default, so a
        # producer that wrote twice read as "absent" and the caller took its
        # default without a word.
        result = self.field("carried=0.1.9\ncarried=0.1.10\n")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("2 carried= lines", result.stderr)

    def test_an_empty_value_is_a_break_not_the_default(self):
        result = self.field("carried=\n")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("empty carried=", result.stderr)


class PkglistTest(unittest.TestCase):
    """The one reader of the 包清单 lines bump.py writes."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.list = Path(self._tmp.name) / "packages.txt"

    def write(self, *lines: str) -> None:
        self.list.write_text("".join(line + "\n" for line in lines))

    def read(self, reader: str) -> subprocess.CompletedProcess:
        return call(f"{reader} {shlex.quote(str(self.list))}")

    def test_the_dirs_and_the_names_are_two_columns_of_one_order(self):
        # The build step takes the first column, the release step the second;
        # both are the inventory's order, so neither can disagree about which
        # package is which.
        self.write(
            "package packages/magpie-bin magpie-bin",
            "package packages/magpie-cli-bin magpie-cli-bin",
        )
        self.assertEqual(
            self.read("pkglist_dirs").stdout.splitlines(),
            ["packages/magpie-bin", "packages/magpie-cli-bin"],
        )
        self.assertEqual(
            self.read("pkglist_names").stdout.splitlines(),
            ["magpie-bin", "magpie-cli-bin"],
        )

    def test_a_line_that_is_not_the_frozen_shape_fails(self):
        # The shape the producer writes is the contract; anything else is a
        # protocol break, not a value to salvage.
        self.write("magpie-bin")
        result = self.read("pkglist_names")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not a 包清单 line", result.stderr)

    def test_an_empty_list_is_no_packages(self):
        self.write()
        result = self.read("pkglist_dirs")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")

    def test_a_missing_file_fails_loudly(self):
        result = self.read("pkglist_dirs")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no such file", result.stderr)


class FrozenKindTest(unittest.TestCase):
    """冻结行是有种类的东西，一张表说清每一行的规矩。

    What a key may say, whether a missing line is a failure, and whether the
    value names a file were each decided by whoever happened to read the key:
    the workflow carried its own `case` for `release=`, and an `assets=` naming
    a file that lists nothing handed `gh release` the checksums alone -- a green
    Release with no packages in it.

    The table is data, and `frozen_var` / `frozen_list` / `frozen_write` are the
    only readers and writers that consult it.
    """

    COLUMNS = 5

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.log = self.root / "out.txt"

    def table(self) -> list[list[str]]:
        result = call("frozen_table")
        self.assertEqual(result.returncode, 0, result.stderr)
        return [line.split("|") for line in result.stdout.strip().splitlines()]

    def column(self, key: str, index: int) -> subprocess.CompletedProcess:
        return call(f"frozen_field {shlex.quote(key)} {index}")

    def test_every_row_answers_all_five_columns(self):
        rows = self.table()

        self.assertTrue(rows, "the table is empty")
        for row in rows:
            self.assertEqual(len(row), self.COLUMNS, row)
            self.assertTrue(all(cell.strip() for cell in row), row)
        keys = [row[0] for row in rows]
        self.assertEqual(len(keys), len(set(keys)), keys)

    def test_the_release_keys_say_what_shape_they_are(self):
        # The three this slice moves: one list of paths, two paths of their own.
        for key, shape in (("assets", "list"), ("sums", "path"), ("notes", "path")):
            with self.subTest(key=key):
                result = self.column(key, 3)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, shape + "\n")

    def test_a_key_the_table_does_not_name_is_refused(self):
        result = call("frozen_var ghost /tmp/nothing.txt ghost_value")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no such frozen key: ghost", result.stderr)

    def test_a_value_key_is_assigned_into_the_callers_variable(self):
        self.log.write_text("upstream=0.1.9\n")
        result = call(
            f'upstream_value=""; frozen_var upstream {shlex.quote(str(self.log))} upstream_value; '
            '[ "$upstream_value" = 0.1.9 ]'
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_path_key_that_names_no_file_is_refused(self):
        # The read the workflow used to do with `mapfile` on the way to `gh`:
        # what the key names has to be a file, and the refusal has to say so.
        self.log.write_text("sums=/tmp/nothing-sums\n")
        result = call(f'frozen_var sums {shlex.quote(str(self.log))} sums_value')

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("sums=/tmp/nothing-sums", result.stderr)
        self.assertIn("not a file", result.stderr)

    def test_a_missing_line_is_still_the_readers_complaint(self):
        self.log.write_text("assets=/tmp/nothing\n")
        result = call(f'frozen_var sums {shlex.quote(str(self.log))} sums_value')

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no sums=", result.stderr)

    def test_a_value_outside_its_domain_is_refused_by_the_read(self):
        # `release=` had a `case` in the workflow and another in the script that
        # wrote it; the domain is the table's now, and the read enforces it.
        self.log.write_text("release=maybe\n")
        result = call(f'frozen_var release {shlex.quote(str(self.log))} state')

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("release=maybe is not present or absent", result.stderr)

    def test_each_domain_the_table_uses_accepts_and_refuses(self):
        cases = [
            ("bump", "1", True),
            ("bump", "2", False),
            ("upstream", "0.1.9", True),
            ("upstream", "nope", False),
            ("release", "absent", True),
            ("release", "maybe", False),
            ("tag", "v0.1.9", True),
            ("tag", "0.1.9", False),
            ("pkgname", "magpie-bin", True),
            ("pkgname", "a/b", False),
            ("tests", "399", True),
            ("tests", "399.5", False),
            ("gates", "5/5", True),
            ("gates", "5", False),
        ]
        for key, value, accepted in cases:
            with self.subTest(key=key, value=value):
                result = call(f"frozen_write {shlex.quote(key)} {shlex.quote(value)}")
                if accepted:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout, f"{key}={value}\n")
                else:
                    self.assertNotEqual(result.returncode, 0)
                    # The key, the value, and the shape it failed to be.
                    self.assertIn(f"{key}={value} is not ", result.stderr)

    def test_the_table_domain_vocabulary_is_the_one_the_gate_interprets(self):
        tokens = {row[1] for row in self.table()}
        self.assertEqual(
            tokens,
            {"version", "flag", "present-or-absent", "v<version>", "package-name",
             "integer", "gates", "-"},
        )

    def test_the_write_gate_refuses_a_key_the_table_does_not_name(self):
        result = call("frozen_write ghost /tmp/whatever")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no such frozen key: ghost", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_a_duplicated_line_is_still_the_readers_complaint(self):
        # The gates wrap `line_field`; they must not grow their own rules for
        # the two breaks it already names.
        self.log.write_text("ver=0.1.9\nver=0.1.10\n")
        result = call(f'ver_value=""; frozen_var ver {shlex.quote(str(self.log))} ver_value')

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("2 ver= lines", result.stderr)

    def test_an_empty_line_is_still_the_readers_complaint(self):
        self.log.write_text("ver=\n")
        result = call(f'ver_value=""; frozen_var ver {shlex.quote(str(self.log))} ver_value')

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("empty ver=", result.stderr)

    def test_a_required_key_cannot_be_given_a_default(self):
        # The default is for the keys the table calls optional; on a required
        # key it would hide exactly the absence the table exists to report.
        self.log.write_text("upstream=0.1.9\n")
        result = call(
            f'v=""; frozen_var upstream {shlex.quote(str(self.log))} v 0.1.0'
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("upstream= is required", result.stderr)

    def test_an_optional_key_without_a_default_is_refused(self):
        # `gates=` may be absent, and then only the caller knows what that
        # means; reading it without saying so leaves a caller with no answer.
        self.log.write_text("tests=399\n")
        result = call(f'g=""; frozen_var gates {shlex.quote(str(self.log))} g')

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("gates= may be absent", result.stderr)

    def test_an_absent_optional_key_is_the_callers_default(self):
        self.log.write_text("tests=399\n")
        result = call(
            f'g=""; frozen_var gates {shlex.quote(str(self.log))} g pre-contract; '
            '[ "$g" = pre-contract ]'
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_present_optional_key_is_still_checked_by_its_domain(self):
        self.log.write_text("gates=5\n")
        result = call(
            f'g=""; frozen_var gates {shlex.quote(str(self.log))} g pre-contract'
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("gates=5 is not <整数>/<整数>", result.stderr)

    def test_the_write_gate_refuses_a_list_naming_a_path_that_is_not_there(self):
        # The write half of the same rule the read enforces: this file is about
        # to become a Release's asset list, and one name in it is not a file.
        (self.root / "assets.txt").write_text(f"{self.root / 'gone.pkg.tar.zst'}\n")
        result = call(f"frozen_write assets {shlex.quote(str(self.root / 'assets.txt'))}")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("assets=", result.stderr)
        self.assertIn("gone.pkg.tar.zst", result.stderr)

    def test_the_table_shape_vocabulary_is_the_one_the_gates_interpret(self):
        # The twin of the 域 guard above: without it a row could name a shape no
        # gate implements, and the refusal would only show up at run time.
        shapes = {row[2] for row in self.table()}
        self.assertEqual(shapes, {"value", "path", "list", "pkglist"})

    def test_the_package_list_row_is_read_through_its_own_door(self):
        # The 包清单 is a row with a shape of its own. Each gate says so by name
        # instead of reporting a missing `package=` line -- a diagnostic about a
        # key nothing writes, for the one row whose reader is elsewhere.
        self.log.write_text("package packages/magpie-bin magpie-bin\n")
        quoted = shlex.quote(str(self.log))
        cases = (
            (f'v=""; frozen_var package {quoted} v', "package= is a pkglist, not a value"),
            (f'files=(); frozen_list package {quoted} files', "package= is a pkglist, not a list"),
            (f"frozen_write package {quoted}", "the table gives package the shape pkglist"),
        )
        for invocation, message in cases:
            with self.subTest(call=invocation):
                result = call(invocation)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
                self.assertNotIn("no package=", result.stderr)

    def test_frozen_list_reads_the_paths_the_file_names(self):
        first, second = self.root / "a.pkg.tar.zst", self.root / "b.pkg.tar.zst"
        first.write_text("a")
        second.write_text("b")
        (self.root / "assets.txt").write_text(f"{first}\n{second}\n")
        self.log.write_text(f"assets={self.root / 'assets.txt'}\n")
        result = call(
            f'files=(); frozen_list assets {shlex.quote(str(self.log))} files; '
            '[ "${#files[@]}" -eq 2 ] && [ "${files[1]}" = ' + shlex.quote(str(second)) + " ]"
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_list_naming_a_path_that_is_not_there_is_refused(self):
        (self.root / "assets.txt").write_text(f"{self.root / 'missing.pkg.tar.zst'}\n")
        self.log.write_text(f"assets={self.root / 'assets.txt'}\n")
        result = call(f'frozen_list assets {shlex.quote(str(self.log))} files')

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("assets=", result.stderr)
        self.assertIn("missing.pkg.tar.zst", result.stderr)

    def test_a_list_that_names_nothing_is_refused(self):
        # The defect this slice exists for: an empty assets file handed `gh
        # release` the checksums alone, and the run went green.
        (self.root / "assets.txt").write_text("")
        self.log.write_text(f"assets={self.root / 'assets.txt'}\n")
        result = call(f'frozen_list assets {shlex.quote(str(self.log))} files')

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("assets=", result.stderr)
        self.assertIn("assets.txt", result.stderr)
        self.assertIn("lists nothing", result.stderr)

    def test_a_path_key_is_written_only_when_the_file_is_there(self):
        present = self.root / "notes.md"
        present.write_text("body")
        written = call(f"frozen_write notes {shlex.quote(str(present))}")
        self.assertEqual(written.returncode, 0, written.stderr)
        self.assertEqual(written.stdout, f"notes={present}\n")

        refused = call(f"frozen_write notes {shlex.quote(str(self.root / 'gone.md'))}")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("notes=", refused.stderr)
        self.assertIn("gone.md", refused.stderr)


class FrozenLineJoinTest(unittest.TestCase):
    """One test per frozen line, with the producer's real output in the reader.

    Every test on either side pins its own literal: the producer's test asserts
    the string it prints, the reader's test writes its own fixture. A producer
    change that keeps its own test happy therefore leaves the pipeline's real
    reader rejecting lines while the suite stays green -- adding a fourth column
    to the 包清单 did exactly that, and the build step would have died on it.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def run_lib(self, script: str) -> list[str]:
        result = call(script)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.splitlines()

    def test_the_package_list_reaches_its_reader(self):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            self.assertEqual(bump.main(["--packages"]), 0)
        listing = self.dir / "packages.txt"
        listing.write_text(buffer.getvalue())

        self.assertEqual(
            self.run_lib(f"pkglist_dirs {shlex.quote(str(listing))}"),
            [f"packages/{pkg}" for pkg in bump.PACKAGES],
        )
        self.assertEqual(
            self.run_lib(f"pkglist_names {shlex.quote(str(listing))}"),
            list(bump.PACKAGES),
        )

    def test_the_rendered_version_reaches_its_reader(self):
        version = "0.1.2"
        root = self.dir / "tree"
        layout = bump.layout_for(
            root,
            templates=REPO / "templates",
            packages_dir=root / "packages",
            maintainer="Test Maintainer <test@example.invalid>",
        )
        sums = {
            "magpie-linux-amd64": "1" * 64,
            "magpie-linux-arm64": "2" * 64,
            "magpie-cli-linux-amd64": "3" * 64,
            "magpie-cli-linux-arm64": "4" * 64,
        }
        table = {
            f"{bump.RELEASES}/download/v{version}/SHA256SUMS": "".join(
                f"{digest}  {name}\n" for name, digest in sums.items()
            ).encode(),
            f"https://raw.githubusercontent.com/yetone/magpie/v{version}/LICENSE": b"license",
            "https://usemagpie.ai/img/icon-256.png": b"icon",
        }

        def fetch(url: str, *, missing_ok: bool = False) -> bytes:
            return table[url]

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            bump.render(version, layout=layout, fetch=fetch)
        render_txt = self.dir / "render.txt"
        render_txt.write_text(buffer.getvalue())

        self.assertEqual(
            self.run_lib(f"line_field ver {shlex.quote(str(render_txt))}"), [version]
        )


class SrcinfoDependsTest(unittest.TestCase):
    """The depends a PKGBUILD declares, read from what makepkg printed.

    build-check.sh used to write `.SRCINFO` to disk only to sed it back into
    memory, inside the root branch no offline test reaches. The read lives in
    pkg.sh now, so the fixture is a string and the temp file is gone.
    """

    def depends(self, text: str) -> list[str]:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "srcinfo"
            path.write_text(text)
            result = call(f"srcinfo_depends \"$(cat {shlex.quote(str(path))})\"")
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.splitlines()

    def test_reads_every_depends_line_in_order(self):
        text = "pkgbase = magpie-bin\n\tpkgdesc = d\n\tdepends = glib2\n\tdepends = gtk3\n"
        self.assertEqual(self.depends(text), ["glib2", "gtk3"])

    def test_does_not_pick_up_the_other_depend_arrays(self):
        # makedepends/checkdepends/optdepends are not runtime depends, and
        # installing them would be a different (slower, wrong) build.
        text = (
            "\tmakedepends = go\n"
            "\tcheckdepends = pytest\n"
            "\toptdepends = x: y\n"
            "\tdepends = glib2\n"
        )
        self.assertEqual(self.depends(text), ["glib2"])

    def test_no_depends_is_empty_not_an_error(self):
        self.assertEqual(self.depends("pkgbase = magpie-bin\n"), [])


class PayloadTest(unittest.TestCase):
    """The judgement that can publish a wrong 版本, off the container's floor.

    A fixture is a real `.pkg.tar` written by python's own tarfile and read by
    GNU tar, which the test job has; the container's bsdtar is the other
    adapter behind the same PAYLOAD_TAR seam.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def artifact(self, *members: tuple[str, bytes]) -> Path:
        path = self.dir / "magpie-bin-0.1.4147-1-x86_64.pkg.tar"
        with tarfile.open(path, "w") as tar:
            for name, data in members:
                info = tarfile.TarInfo(name)
                info.size = len(data)
                info.mode = 0o755
                tar.addfile(info, io.BytesIO(data))
        return path

    def program(self, reported: str, name: str = "usr/bin/magpie") -> tuple[str, bytes]:
        return (name, f"#!/bin/sh\necho 'magpie {reported}'\n".encode())

    def reports(self, artifact: Path, expected: str) -> subprocess.CompletedProcess:
        return call(
            f"export PAYLOAD_TAR=tar; payload_reports {shlex.quote(str(artifact))} {expected}"
        )

    def member(self, artifact: Path) -> subprocess.CompletedProcess:
        return call(f"export PAYLOAD_TAR=tar; payload_member {shlex.quote(str(artifact))}")

    def test_the_member_is_the_package_itself(self):
        artifact = self.artifact(self.program("0.1.4147"), ("usr/share/x", b"x"))
        result = self.member(artifact)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "usr/bin/magpie\n")

    def test_a_file_with_no_program_is_refused(self):
        result = self.member(self.artifact(("usr/share/licenses/x", b"x")))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("installs nothing under usr/bin/", result.stderr)

    def test_two_programs_are_refused_rather_than_picked_from(self):
        result = self.member(
            self.artifact(self.program("0.1.4147"), self.program("0.1.4147", "usr/bin/helper"))
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("installs 2 programs", result.stderr)

    def test_the_reported_version_has_to_match(self):
        result = self.reports(self.artifact(self.program("0.1.4147")), "0.1.4147")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "0.1.4147\n")

    def test_a_longer_version_does_not_pass_for_a_shorter_one(self):
        # The substring test this replaced would accept 0.1.412 for 0.1.41.
        result = self.reports(self.artifact(self.program("magpie 0.1.412")), "0.1.41")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("reports '0.1.412', expected 0.1.41", result.stderr)

    def test_a_program_that_will_not_run_is_refused(self):
        artifact = self.artifact(("usr/bin/magpie", b"#!/bin/sh\nexit 3\n"))
        result = self.reports(artifact, "0.1.4147")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("failed to run --version", result.stderr)


class PkgrelShapeTest(unittest.TestCase):
    """One table of pkgrel shapes, and the two readers that must agree on it.

    `bump.check_pkgrel` is where pkgrel *enters* this repository (a template's
    `pkgrel=`), and `pkgfile_pkgname` is where it comes back out of a 包文件's
    name after makepkg decided it. Nothing compared their accept sets, and they
    had drifted: `str.isdigit` answered yes to `²` and to the fullwidth `１`
    while this reader refuses both, so the entry check blessed a value the
    container then died on -- `not a 包文件 name` -- after the render, inside a
    module nobody had asked about.

    The table is literals a template may write. What the door accepts must be
    what a name can carry, because the name is written from the same value.
    """

    SHAPES = (
        "1",
        "2",
        "'3'",      # quoted: both sides read the first token and drop the quotes
        "1 ",       # a trailing space is the token rule again
        "1 2",      # ... and so is this
        "1.1",      # Arch's dependency-bump form: this repository refuses it
        "２",       # fullwidth
        "²",        # superscript
        "",         # no value at all
    )

    def door_accepts(self, literal: str) -> bool:
        """Does the template door take this value?"""
        try:
            bump.check_pkgrel("magpie-bin", f"pkgname=magpie-bin\npkgrel={literal}\n")
        except bump.BumpError:
            return False
        return True

    def name_accepts(self, literal: str) -> bool:
        """Does a 包文件 name carrying the value this literal reads as parse?"""
        value = bump.pkg_field(f"pkgrel={literal}\n", "pkgrel")
        with tempfile.TemporaryDirectory() as tmp:
            name = fixtures.pkgfile_name("magpie-bin", "0.1.10", rel=value)
            result = call(f"pkgfile_pkgname {shlex.quote(str(Path(tmp) / name))}")
        return result.returncode == 0 and result.stdout == "magpie-bin\n"

    def test_the_two_readers_accept_the_same_shapes(self):
        for literal in self.SHAPES:
            with self.subTest(pkgrel=literal):
                self.assertEqual(
                    self.door_accepts(literal),
                    self.name_accepts(literal),
                    f"pkgrel={literal!r}: the template door and the name reader disagree",
                )

    def test_the_table_holds_both_verdicts(self):
        # A table everything accepted would pass with either reader deleted.
        verdicts = {self.door_accepts(literal) for literal in self.SHAPES}
        self.assertEqual(verdicts, {True, False})
        # The premise the table rests on: python's own `str.isdigit` calls these
        # two digits, which is why the ASCII rule has to be what decides.
        self.assertTrue("１".isdigit() and "²".isdigit())
        self.assertFalse(self.door_accepts("１"))
        self.assertFalse(self.name_accepts("１"))


class VersionShapeTest(unittest.TestCase):
    """One table of 版本 shapes, and the two runtimes that must agree on it.

    Three places used to answer "what is a 版本": the render's `VERSION_RE`, the
    包文件 name reader's character class, and publish-pages.sh's own copy of
    that class. The two shell copies accepted `1`, `1.` and `1..2` -- shapes no
    render can produce -- while GLOSSARY described the loose rule and the render
    enforced the strict one. pkgrel had the same split and its repair was one
    table fed to both readers; this is that table for the other half.

    The shell side is one function now (`version_shape_ok`: the name reader and
    the hand-run gate both call it), so the pair here is python's regex and
    that function.
    """

    SHAPES = (
        "0.1.3995",
        "0.1.2.3",   # more parts than upstream uses: the rule has no ceiling
        "1",         # one part: no version this repository renders
        "1.",        # a trailing empty part
        "1..2",      # an empty part in the middle
        ".1",        # a leading one
        "v0.1.9",    # the tag shape, before the `v` comes off
        "",
        "0.1.２",    # a fullwidth digit: neither side calls that a number
    )

    def python_accepts(self, version: str) -> bool:
        return bool(bump.VERSION_RE.fullmatch(version))

    def shell_accepts(self, version: str) -> bool:
        return call(f"version_shape_ok {shlex.quote(version)}").returncode == 0

    def test_the_two_readers_accept_the_same_shapes(self):
        for version in self.SHAPES:
            with self.subTest(version=version):
                self.assertEqual(
                    self.python_accepts(version),
                    self.shell_accepts(version),
                    f"版本={version!r}: the render and pkg.sh disagree",
                )

    def test_the_table_holds_both_verdicts(self):
        verdicts = {self.python_accepts(version) for version in self.SHAPES}
        self.assertEqual(verdicts, {True, False})

    def test_a_package_name_the_render_would_refuse_does_not_parse(self):
        # The reader asks the same function, so the width reaches all the way
        # out to a name the directory carries.
        with tempfile.TemporaryDirectory() as tmp:
            for version in ("1", "1..2", "1."):
                name = fixtures.pkgfile_name("magpie-bin", version)
                result = call(f"pkgfile_pkgname {shlex.quote(str(Path(tmp) / name))}")
                self.assertNotEqual(result.returncode, 0, name)


if __name__ == "__main__":
    unittest.main()
