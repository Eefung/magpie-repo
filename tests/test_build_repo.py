"""The repository assembly script, run for real against a throwaway tree.

`scripts/check.sh` used to print that the package build was not covered here:
`build-repo.sh` only ran inside the publish container. The retention window, the
database entry and the checksum file are filename arithmetic over a directory,
so the real script runs offline with a stub build-check and a stub repo-add
(`BUILD_CHECK`, and PATH). What stays uncovered is `build-check.sh`'s root
branch (useradd / su / chown), which needs the container.

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "build-repo.sh"

STUB_BUILD_CHECK = """\
#!/usr/bin/env bash
# Stands in for build-check.sh: makes the file it would have built and hands
# back its path, the way the real one does.
set -euo pipefail
dir="$1"
pkgname="$(sed -n 's/^pkgname=//p' "$dir/PKGBUILD" | head -1 | tr -d "'\\"")"
pkgver="$(sed -n 's/^pkgver=//p' "$dir/PKGBUILD" | head -1 | tr -d "'\\"")"
artifact="$dir/$pkgname-$pkgver-1-x86_64.pkg.tar.zst"
: > "$artifact"
echo "ok: $(basename "$artifact")"
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

    def stage(self, version: str) -> None:
        (self.pkgdir / "PKGBUILD").write_text(
            f"pkgname=magpie-bin\npkgver={version}\npkgrel=1\n"
        )

    def seed(self, version: str) -> None:
        (self.out / f"magpie-bin-{version}-1-x86_64.pkg.tar.zst").touch()

    def run_script(self, build_check: Path | None = None) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["BUILD_CHECK"] = str(build_check or self.stub_build)
        env["REPO_ADD_LOG"] = str(self.record)
        env["PATH"] = f"{self.bin}{os.pathsep}{env['PATH']}"
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
                "magpie-bin-0.1.10-1-x86_64.pkg.tar.zst",
                "magpie-bin-0.1.9-1-x86_64.pkg.tar.zst",
            ],
        )
        # The pruned build stays out of the database even though it arrived in
        # the same directory the database is rebuilt from.
        self.assertEqual(self.database_entries(), ["magpie-bin-0.1.10-1-x86_64.pkg.tar.zst"])
        self.assertIn("pruning magpie-bin-0.1.8-1-x86_64.pkg.tar.zst", result.stdout)
        # One checksum per surviving file, written where the release expects it.
        checksums = (self.repo / "SHA256SUMS").read_text().splitlines()
        self.assertEqual(len(checksums), 2)
        self.assertTrue(all(line.endswith("-x86_64.pkg.tar.zst") for line in checksums))
        # The artifact left the package directory: the source tree stays clean.
        self.assertEqual(list(self.pkgdir.glob("*.pkg.tar.*")), [])

    def test_a_failed_build_writes_no_database(self):
        self.stage("0.1.10")
        failing = self.root / "build-check-fails.sh"
        failing.write_text("#!/usr/bin/env bash\necho boom >&2\nexit 1\n")
        failing.chmod(0o755)

        result = self.run_script(build_check=failing)

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.database_entries(), [])
        self.assertFalse((self.out / "magpie.db.tar.gz").exists())
        self.assertFalse((self.repo / "SHA256SUMS").exists())


if __name__ == "__main__":
    unittest.main()
