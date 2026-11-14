"""build-check.sh, run for real with a stand-in makepkg.

`scripts/check.sh` used to print that this script could only be proved by a
publish run. Only its root branch (useradd / su / chown, and the depends
install behind it) needs the container: the rest -- the directory it owns, the
`artifact=` line it hands back, the 载荷核对 it calls -- is filename arithmetic
and one subprocess, and makepkg is reachable through PATH.

The fixture is an uncompressed tar under a `.pkg.tar.zst` name: bsdtar and GNU
tar both read by content, and compression is not what this test is about.
`PAYLOAD_TAR=tar` keeps the test off bsdtar, which the test runner does not
ship; the container's bsdtar path is proved by a publish run.

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "build-check.sh"
PKG = REPO / "packages" / "magpie-bin"

STUB_MAKEPKG = """\
#!/usr/bin/env bash
# Stands in for makepkg: answers the two questions build-check asks, then makes
# the package file under the name makepkg would give it. Its scratch tree lives
# in a mktemp directory, so nothing it creates can be mistaken for the script's
# own leftovers.
set -euo pipefail
case "${1:-}" in
    --printsrcinfo) printf 'pkgbase = magpie-bin\\n\\tdepends = glibc\\n'; exit 0 ;;
    --packagelist)
        . ./PKGBUILD
        printf '%s/%s-%s-%s-x86_64.pkg.tar.zst\\n' "$PWD" "$pkgname" "$pkgver" "$pkgrel"
        exit 0
        ;;
esac
. ./PKGBUILD
[ "${STUB_SKIP_BUILD:-}" = "1" ] && exit 0
build="$(mktemp -d)"
trap 'rm -rf "$build"' EXIT
mkdir -p "$build/usr/bin"
printf '#!/bin/sh\\necho "magpie %s"\\n' "${STUB_REPORTS:-$pkgver}" >"$build/usr/bin/magpie"
chmod +x "$build/usr/bin/magpie"
tar -cf "$pkgname-$pkgver-$pkgrel-x86_64.pkg.tar.zst" -C "$build" usr
"""


class BuildCheckTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

        self.bin = self.root / "bin"
        self.bin.mkdir()
        stub = self.bin / "makepkg"
        stub.write_text(STUB_MAKEPKG)
        stub.chmod(0o755)

        self.pkgdir = self.root / "packages" / "magpie-bin"
        shutil.copytree(PKG, self.pkgdir, ignore=shutil.ignore_patterns("*.pkg.tar.*"))

    def pkgver(self) -> str:
        # Read, not pinned: the committed PKGBUILD bumps every released 版本.
        match = re.search(r"^pkgver=(\S+)", (self.pkgdir / "PKGBUILD").read_text(), re.M)
        if match is None:
            self.fail("the copied PKGBUILD has no pkgver=")
        return match.group(1)

    def run_script(
        self, *, reports: str | None = None, skip_build: bool = False
    ) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["PATH"] = f"{self.bin}{os.pathsep}{env['PATH']}"
        env["PAYLOAD_TAR"] = "tar"
        if reports is not None:
            env["STUB_REPORTS"] = reports
        if skip_build:
            env["STUB_SKIP_BUILD"] = "1"
        return subprocess.run(
            ["bash", str(SCRIPT), str(self.pkgdir)],
            cwd=self.root,
            text=True,
            capture_output=True,
            env=env,
        )

    def artifact_of(self, result: subprocess.CompletedProcess) -> Path:
        lines = result.stdout.splitlines()
        self.assertTrue(lines, result.stderr)
        self.assertRegex(lines[-1], r"^artifact=/")
        return Path(lines[-1].removeprefix("artifact="))

    def test_the_artifact_line_names_the_file_and_nothing_else_survives(self):
        result = self.run_script()

        self.assertEqual(result.returncode, 0, result.stderr)
        artifact = self.artifact_of(result)
        self.assertTrue(artifact.is_file(), result.stdout)
        # The one thing the caller is promised, and the only thing left: no
        # src/, pkg/, .srcdest/, .SRCINFO and no second package file.
        self.assertEqual(
            [path.name for path in self.pkgdir.glob("*.pkg.tar.*")], [artifact.name]
        )
        for leftover in ("src", "pkg", ".srcdest", ".SRCINFO"):
            self.assertFalse((self.pkgdir / leftover).exists(), leftover)

    def test_a_package_file_from_an_earlier_run_is_not_published(self):
        stale = self.pkgdir / "magpie-bin-0.1.1-1-x86_64.pkg.tar.zst"
        stale.touch()

        result = self.run_script()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(stale.exists())
        self.assertEqual(
            [path.name for path in self.pkgdir.glob("*.pkg.tar.*")],
            [self.artifact_of(result).name],
        )

    def test_a_stale_file_cannot_stand_in_for_a_build_that_wrote_nothing(self):
        # The shape 8994a3a was about: makepkg writes nothing, a same-named file
        # from an earlier run is still there, and `[ -f "$artifact" ]` would
        # then pass on bytes nobody built. The directory is emptied before the
        # build for exactly this.
        stale = self.pkgdir / f"magpie-bin-{self.pkgver()}-1-x86_64.pkg.tar.zst"
        stale.touch()

        result = self.run_script(skip_build=True)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("makepkg did not write", result.stderr)
        self.assertNotIn("artifact=", result.stdout)

    def test_a_payload_that_reports_another_version_fails_before_the_line(self):
        result = self.run_script(reports="0.1.412")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(f"reports '0.1.412', expected {self.pkgver()}", result.stderr)
        # The caller reads the last line, so a failed run must not write one.
        self.assertNotIn("artifact=", result.stdout)
        # And it must not leave the file it was about to publish behind.
        self.assertEqual(list(self.pkgdir.glob("*.pkg.tar.*")), [])


if __name__ == "__main__":
    unittest.main()
