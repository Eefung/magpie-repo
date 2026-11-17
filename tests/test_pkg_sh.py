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


class PkgFieldTest(unittest.TestCase):
    """`pkg_field` is the one place the shell parses a rendered PKGBUILD."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.pkgbuild = Path(self._tmp.name) / "PKGBUILD"

    def field(self, name: str) -> subprocess.CompletedProcess:
        return call(f"pkg_field {shlex.quote(str(self.pkgbuild))} {name}")

    def test_reads_a_field_and_drops_the_quotes(self):
        self.pkgbuild.write_text("pkgname=magpie-bin\npkgver='0.1.4131'\npkgrel=1\n")
        self.assertEqual(self.field("pkgver").stdout, "0.1.4131\n")
        self.assertEqual(self.field("pkgname").stdout, "magpie-bin\n")

    def test_the_first_occurrence_wins(self):
        # bump.py's own reader takes the first match too; two readers that
        # disagreed about a duplicated field would be a silent split.
        self.pkgbuild.write_text("pkgver=0.1.1\npkgver=0.1.2\n")
        self.assertEqual(self.field("pkgver").stdout, "0.1.1\n")

    def test_a_missing_field_fails_loudly(self):
        self.pkgbuild.write_text("pkgname=magpie-bin\n")
        result = self.field("pkgver")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("pkgver", result.stderr)

    def test_a_missing_file_fails_loudly(self):
        result = self.field("pkgver")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no such PKGBUILD", result.stderr)


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
        lines = [
            line.strip()
            for line in (REPO / ".gitignore").read_text().splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        for prefix in sorted(set(bump.PACKAGES.values())):
            with self.subTest(prefix=prefix):
                self.assertTrue(
                    any(fnmatch.fnmatch(f"packages/x/{prefix}-0.1.9-amd64", line) for line in lines),
                    f"no .gitignore line covers packages/*/{prefix}-*",
                )


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

    def field(self, body: str, default: str = "0") -> subprocess.CompletedProcess:
        self.log.write_text(body)
        return call(
            f"line_field_or bump {shlex.quote(str(self.log))} {shlex.quote(default)}"
        )

    def test_an_absent_line_is_the_callers_default(self):
        # A check with nothing to say: not a failure, and not a publish.
        result = self.field("upstream=0.1.2\n")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "0\n")

    def test_a_present_line_wins_over_the_default(self):
        result = self.field("bump=1\n", default="0")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "1\n")

    def test_two_lines_are_still_a_protocol_break(self):
        # `line_field ... || true` collapsed this into the default, so a check
        # that wrote twice read as "nothing to do" and the publish was skipped
        # without a word.
        result = self.field("bump=0\nbump=1\n")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("2 bump= lines", result.stderr)

    def test_an_empty_value_is_a_break_not_the_default(self):
        result = self.field("bump=\n")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("empty bump=", result.stderr)


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


class PkgverReadersTest(unittest.TestCase):
    """One shape for the 版本, read from both runtimes.

    `pkg_field` is the shell's reader and `bump.packaged_evr` is python's: they
    answer the same question about the same file in two languages, because the
    build container has no python3. Their agreement used to live in a comment
    under a shell-only test, and their leniency had already drifted -- so one
    table is fed to both and both answers are compared. The answer is the pair
    `<pkgver>-<pkgrel>`, which is what the entry check compares against the
    软件源, so both halves are in the table.
    """

    SHAPES = [
        "pkgver=0.1.4147\npkgrel=1\n",
        "pkgver='0.1.4147'\npkgrel=1\n",
        'pkgver="0.1.4147"\npkgrel=1\n',
        # The first line wins on both sides.
        "pkgver=0.1.1\npkgver=0.1.2\npkgrel=1\n",
        # A hand edit that leaves a trailing space used to split them.
        "pkgver=0.1.4147   \npkgrel=1\n",
        # The other half is read by the same rule, and a rebuild changes it.
        "pkgver=0.1.4147\npkgrel='2'\n",
    ]

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_both_readers_give_the_same_answer(self):
        for shape in self.SHAPES:
            with self.subTest(shape=shape):
                pkg = "magpie-bin"
                pkgbuild = self.root / "packages" / pkg / "PKGBUILD"
                pkgbuild.parent.mkdir(parents=True, exist_ok=True)
                pkgbuild.write_text(shape)

                from_python = bump.packaged_evr(bump.layout_for(self.root))[pkg]
                pkgver = call(f"pkg_field {shlex.quote(str(pkgbuild))} pkgver")
                pkgrel = call(f"pkg_field {shlex.quote(str(pkgbuild))} pkgrel")

                self.assertEqual(pkgver.returncode, 0, pkgver.stderr)
                self.assertEqual(pkgrel.returncode, 0, pkgrel.stderr)
                # Exact, not stripped beyond the line's own newline: a trailing
                # space is one of the shapes, and stripping it here would hide
                # the split the table describes.
                self.assertEqual(
                    from_python, pkgver.stdout.rstrip("\n") + "-" + pkgrel.stdout.rstrip("\n")
                )


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


if __name__ == "__main__":
    unittest.main()
