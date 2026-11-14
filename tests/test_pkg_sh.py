"""Tests for scripts/lib/pkg.sh -- the shell's one reader of package names.

b085210 (the database must list only each package's newest build) landed here
with no test: the sort rule and the file glob were written twice, in `prune()`
and in `newest()`. They are one function now, and this is what holds it. The
window (`pkgfiles_prune`), the db entry (`pkgfiles_current`) and the release
selection (`pkgfiles_at`) are thin answers over that one order, so they are
tested here too instead of only inside a publish run.

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import io
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

import bump  # noqa: E402  (the module lives beside the scripts, not on the path)


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

    def touch(self, *names: str) -> None:
        for name in names:
            (self.out / name).touch()

    def answer(self, script: str) -> list[str]:
        result = call(script)
        self.assertEqual(result.returncode, 0, result.stderr)
        return [Path(line).name for line in result.stdout.splitlines()]

    def out_arg(self) -> str:
        return shlex.quote(str(self.out))


def build(version: str, rel: str = "1", arch: str = "x86_64") -> str:
    return f"magpie-bin-{version}-{rel}-{arch}.pkg.tar.zst"


class PkgfilesPruneTest(RepoDirectory):
    """The retention window: which files fall out, given KEEP."""

    def prune(self, keep: int) -> list[str]:
        return self.answer(f"pkgfiles_prune {self.out_arg()} magpie-bin {keep}")

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
        self.assertEqual(self.prune(2), [])


class PkgfilesCurrentTest(RepoDirectory):
    """The one file the database entry names."""

    def current(self) -> list[str]:
        return self.answer(f"pkgfiles_current {self.out_arg()} magpie-bin")

    def test_the_newest_build_is_the_current_one(self):
        self.touch(build("0.1.9"), build("0.1.10"), build("0.1.1000"))
        self.assertEqual(self.current(), [build("0.1.1000")])

    def test_the_window_and_the_database_read_one_order(self):
        self.touch(build("0.1.9"), build("0.1.10"), build("0.1.1000"))
        pruned = self.answer(f"pkgfiles_prune {self.out_arg()} magpie-bin 2")
        self.assertNotIn(self.current()[0], pruned)

    def test_every_arch_of_the_newest_version_is_current(self):
        # One database entry per arch. With one arch built that is one file,
        # which is what build-repo.sh hands repo-add today; the rule behind it
        # is "the newest 版本", not "the last file in the sort order".
        self.touch(build("0.1.9"), build("0.1.10"), build("0.1.10", arch="aarch64"))
        self.assertEqual(
            sorted(self.current()),
            sorted([build("0.1.10"), build("0.1.10", arch="aarch64")]),
        )

    def test_nothing_on_disk_is_empty_not_an_error(self):
        self.assertEqual(self.current(), [])


class PkgfilesAtTest(RepoDirectory):
    """The release step's selection: one version's files, nothing else."""

    def at(self, version: str) -> list[str]:
        return self.answer(f"pkgfiles_at {self.out_arg()} magpie-bin {version}")

    def test_only_that_version(self):
        self.touch(build("0.1.9"), build("0.1.10"), build("0.1.10", rel="2"))
        self.assertEqual(self.at("0.1.10"), [build("0.1.10"), build("0.1.10", rel="2")])

    def test_a_pkgrel_bump_does_not_hide_the_files(self):
        # The glob this replaced spelled `-1-` out, so a pkgrel of 2 published
        # nothing and failed after gh-pages had already been pushed.
        self.touch(build("0.1.10", rel="2"))
        self.assertEqual(self.at("0.1.10"), [build("0.1.10", rel="2")])

    def test_a_version_that_was_never_built_is_empty(self):
        self.touch(build("0.1.9"))
        self.assertEqual(self.at("0.1.10"), [])

    def test_a_longer_version_does_not_match_a_shorter_one(self):
        self.touch(build("0.1.100"), build("0.1.10"))
        self.assertEqual(self.at("0.1.10"), [build("0.1.10")])


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

    def test_a_list_valued_line_survives_whole(self):
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
    """One shape for pkgver, read from both runtimes.

    `pkg_field` is the shell's reader and `bump.packaged_versions` is python's:
    they answer the same question about the same file in two languages, because
    the build container has no python3. Their agreement used to live in a
    comment under a shell-only test, and their leniency had already drifted --
    so one table is fed to both and both answers are compared.
    """

    SHAPES = [
        "pkgver=0.1.4147\n",
        "pkgver='0.1.4147'\n",
        'pkgver="0.1.4147"\n',
        # The first line wins on both sides.
        "pkgver=0.1.1\npkgver=0.1.2\n",
        # A hand edit that leaves a trailing space used to split them.
        "pkgver=0.1.4147   \n",
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

                from_python = bump.packaged_versions(bump.layout_for(self.root))[pkg]
                from_shell = call(f"pkg_field {shlex.quote(str(pkgbuild))} pkgver")

                self.assertEqual(from_shell.returncode, 0, from_shell.stderr)
                # Exact, not stripped: a trailing space is one of the shapes,
                # and stripping stdout here would hide the split it describes.
                self.assertEqual(from_shell.stdout, from_python + "\n")


if __name__ == "__main__":
    unittest.main()
