"""Tests for scripts/lib/pkg.sh -- the shell's one reader of package names.

b085210 (the database must list only each package's newest build) landed here
with no test: the sort rule and the file glob were written twice, in `prune()`
and in `newest()`. They are one function now, and this is what holds it.

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LIB = REPO / "scripts" / "lib" / "pkg.sh"


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

    def test_nothing_on_disk_is_empty_not_an_error(self):
        self.assertEqual(self.current(), [])


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
        result = self.field("packages", "packages=packages/magpie-bin packages/magpie-cli-bin\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "packages/magpie-bin packages/magpie-cli-bin\n")

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


if __name__ == "__main__":
    unittest.main()
