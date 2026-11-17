"""The repository assembly script, run for real against a throwaway tree.

`scripts/check.sh` used to print that the package build was not covered here:
`build-repo.sh` only ran inside the publish container. The retention window and
the database entry are filename arithmetic over a directory, so the real script
runs offline with a stub build-check and a stub repo-add (`BUILD_CHECK`, and
PATH). What stays uncovered is the tools `BUILD_AS=root` calls (useradd / su /
chown / pacman); the adapter around them runs in tests/test_build_check.py.

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import fixtures  # noqa: E402  (the one spelling of a 包文件's name in the test tree)

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "build-repo.sh"

STUB_BUILD_CHECK = """\
#!/usr/bin/env bash
# Stands in for build-check.sh: names the file makepkg would have made, makes
# it, and hands back the same two frozen lines the real one prints.
#
# Nothing here parses a PKGBUILD. The real script asks makepkg for the name and
# the 版本 (srcinfo_field); a stub reading them out of the file would be a second
# reader of the shape that just left the tree. The package name is the
# directory's -- `packages/<pkgname>` is the layout build-repo walks -- and the
# 版本 is what the test staged.
#
# The name's other three fields arrive from tests/fixtures.py (PKG_REL,
# PKG_ARCH, PKG_SUFFIX) instead of being written into this printf: a stub that
# said `-1-x86_64` itself would keep every retention assertion green through a
# pkgrel bump or a second arch, which is the one change this file is about.
set -euo pipefail
dir="$1"
pkgname="$(basename "$dir")"
pkgver="${STUB_PKGVER:?}"
artifact="$dir/$pkgname-$pkgver-${PKG_REL}-${PKG_ARCH}$PKG_SUFFIX"
: > "$artifact"
echo "ok: $(basename "$artifact")"
echo "pkgname=$pkgname"
echo "artifact=$artifact"
"""

STUB_REPO_ADD = """\
#!/usr/bin/env bash
# Records what the database would have been given (args after `-q <db>`), then
# makes the db file the script expects to find afterwards.
set -euo pipefail
: > "$2"
printf '%s\\n' "${@:3}" >> "$REPO_ADD_LOG"
"""


class BuildRepoTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.record = self.root / "repo-add.log"
        repo_add = self.bin / "repo-add"
        repo_add.write_text(STUB_REPO_ADD)
        repo_add.chmod(0o755)

        self.stub_build = self.root / "build-check-stub.sh"
        self.stub_build.write_text(STUB_BUILD_CHECK)
        self.stub_build.chmod(0o755)

        self.repo = self.root / "repo"
        self.out = self.repo / "x86_64"
        self.out.mkdir(parents=True)
        self.pkgdir = self.root / "packages" / "magpie-bin"
        self.pkgdir.mkdir(parents=True)
        self.staged: str | None = None

    def stage(self, version: str) -> None:
        # The PKGBUILD is the real input a build would read; the stub is told
        # the 版本 separately, because makepkg is what would answer with it.
        self.staged = version
        (self.pkgdir / "PKGBUILD").write_text(
            f"pkgname=magpie-bin\npkgver={version}\npkgrel=1\n"
        )

    def seed(self, version: str) -> None:
        (self.out / fixtures.pkgfile_name("magpie-bin", version)).touch()

    def run_script(
        self, build_check: Path | None = None, *, repo_name: str | None = "testrepo"
    ) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["BUILD_CHECK"] = str(build_check or self.stub_build)
        env["REPO_ADD_LOG"] = str(self.record)
        if self.staged is not None:
            env["STUB_PKGVER"] = self.staged
        env["PKG_REL"] = fixtures.REL
        env["PKG_ARCH"] = fixtures.ARCH
        env["PKG_SUFFIX"] = fixtures.SUFFIX
        env["PATH"] = f"{self.bin}{os.pathsep}{env['PATH']}"
        # A name of its own, not the repository's: the database's file name is
        # what proves the variable crossed into the script and reached repo-add.
        # With the real name a run could not tell a passed value from a default.
        env.pop("REPO_NAME", None)
        if repo_name is not None:
            env["REPO_NAME"] = repo_name
        return subprocess.run(
            ["bash", str(SCRIPT), str(self.out), str(self.pkgdir)],
            cwd=self.root,
            text=True,
            capture_output=True,
            env=env,
        )

    def files_on_disk(self) -> list[str]:
        return sorted(path.name for path in self.out.glob("*.pkg.tar.*"))

    def database_entries(self) -> list[str]:
        if not self.record.exists():
            return []
        return [Path(line).name for line in self.record.read_text().splitlines()]

    def test_the_window_prunes_and_the_database_takes_the_newest(self):
        self.seed("0.1.8")
        self.seed("0.1.9")
        self.stage("0.1.10")

        result = self.run_script()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.files_on_disk(),
            [
                fixtures.pkgfile_name("magpie-bin", "0.1.10"),
                fixtures.pkgfile_name("magpie-bin", "0.1.9"),
            ],
        )
        # The pruned build stays out of the database even though it arrived in
        # the same directory the database is rebuilt from.
        self.assertEqual(
            self.database_entries(), [fixtures.pkgfile_name("magpie-bin", "0.1.10")]
        )
        self.assertIn(
            f"pruning {fixtures.pkgfile_name('magpie-bin', '0.1.8')}", result.stdout
        )
        # The database is filed under the name it was given, not under the
        # script's own idea of one.
        self.assertTrue((self.out / "testrepo.db.tar.gz").is_file())
        # Nothing outside the directory it was given: the checksum file the
        # release publishes is built by the release step out of that 版本's
        # 包文件, not here out of whatever happens to be on disk.
        self.assertEqual(sorted(path.name for path in self.repo.iterdir()), ["x86_64"])
        # The artifact left the package directory: the source tree stays clean.
        self.assertEqual(list(self.pkgdir.glob("*.pkg.tar.*")), [])

    def test_a_package_that_left_the_inventory_is_retired(self):
        # The window counts the packages this run built, so a package removed
        # from the 包清单 was built by nobody, pruned by nobody, and carried
        # forward by the seed on every publish. The directory is asked what it
        # carries instead, and what this run did not build goes.
        (self.out / fixtures.pkgfile_name("magpie-retired", "0.0.1")).touch()
        (self.out / fixtures.pkgfile_name("magpie-retired", "0.0.2")).touch()
        self.stage("0.1.10")

        result = self.run_script()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.files_on_disk(), [fixtures.pkgfile_name("magpie-bin", "0.1.10")]
        )
        for version in ("0.0.1", "0.0.2"):
            self.assertIn(
                f"retiring {fixtures.pkgfile_name('magpie-retired', version)}",
                result.stdout,
            )
        self.assertEqual(
            self.database_entries(), [fixtures.pkgfile_name("magpie-bin", "0.1.10")]
        )

    def test_a_pinned_older_version_is_the_one_that_ships(self):
        # `WANTED` pins a 版本 and upstream.sh publishes it without a
        # comparison, so this is the rollback path. The window used to count
        # back from the directory's maximum and deleted the build the run had
        # just made: the database named 0.1.10 and release.sh -- which asks for
        # the pinned 版本's files -- found nothing, after the push had landed.
        self.seed("0.1.9")
        self.seed("0.1.10")
        self.stage("0.1.8")

        result = self.run_script()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.database_entries(), [fixtures.pkgfile_name("magpie-bin", "0.1.8")]
        )
        self.assertEqual(
            self.files_on_disk(), [fixtures.pkgfile_name("magpie-bin", "0.1.8")]
        )
        for version in ("0.1.9", "0.1.10"):
            self.assertIn(
                f"pruning {fixtures.pkgfile_name('magpie-bin', version)}", result.stdout
            )

    def test_a_window_that_cannot_compute_stops_the_run(self):
        # The anchor is the 包文件 this run made, and the window refuses when
        # the directory does not carry it under the package's name. That refusal
        # used to die inside a process substitution -- the run printed the
        # complaint, pruned nothing, and handed repo-add the stale tree.
        self.seed("0.1.9")
        self.stage("0.1.10")
        liar = self.root / "build-check-lying.sh"
        liar.write_text(
            "#!/usr/bin/env bash\n"
            'dir="$1"\n'
            'artifact="$dir/other-bin-0.1.10-${PKG_REL}-${PKG_ARCH}$PKG_SUFFIX"\n'
            ': > "$artifact"\n'
            'echo "pkgname=magpie-bin"\n'
            'echo "artifact=$artifact"\n'
        )
        liar.chmod(0o755)

        result = self.run_script(build_check=liar)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not in", result.stderr)
        self.assertFalse((self.out / "testrepo.db.tar.gz").exists())

    def test_a_failed_build_writes_no_database(self):
        self.stage("0.1.10")
        failing = self.root / "build-check-fails.sh"
        failing.write_text("#!/usr/bin/env bash\necho boom >&2\nexit 1\n")
        failing.chmod(0o755)

        result = self.run_script(build_check=failing)

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.database_entries(), [])
        self.assertFalse((self.out / "testrepo.db.tar.gz").exists())

    def test_a_run_without_the_section_name_is_refused(self):
        # The workflow passes it with `-e REPO_NAME`. While the script carried a
        # default equal to the declared value, a step that forgot the flag
        # produced the same tree as one that passed it, so a publish could not
        # tell the two apart; now it fails before the build.
        self.stage("0.1.10")

        result = self.run_script(repo_name=None)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("REPO_NAME", result.stderr)
        self.assertEqual(self.files_on_disk(), [])
        self.assertEqual(self.database_entries(), [])


if __name__ == "__main__":
    unittest.main()
