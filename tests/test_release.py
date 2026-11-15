"""release.sh, run for real against a synthesized repository directory.

The Release step used to be 45 lines of inline YAML: which files belong to this
版本 (the 保留窗口 keeps others on disk), the checksum file, the notes with the
install lines from the 包清单. `scripts/check.sh` said all of it could only be
proved by a publish run, and the only offline assertion about the step was that
the workflow text contains no package name.

None of it needs gh, a container or the network: the inputs are file names and
the 包清单, so the rules are driven here. `gh release create` itself stays in
the step, where a real publish is what proves it.

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "release.sh"

sys.path.insert(0, str(REPO / "tests"))

import frozen  # noqa: E402  (the one reader of the frozen lines, as in production)
import install_block  # noqa: E402  (the one reader of the block a user pastes)

PKGS = ["magpie-bin", "magpie-cli-bin"]
CURRENT = "0.1.10"
PREVIOUS = "0.1.9"
# What the workflow's env declares this 软件源 is served as. The test spells it
# so the notes are held to a value the script was *given*, not to today's.
SERVED = {"ARCH": "x86_64", "REPO_NAME": "magpie", "PAGES_URL": "https://example.invalid/repo"}


def pkgfile(pkg: str, version: str, rel: str = "1") -> str:
    return f"{pkg}-{version}-{rel}-x86_64.pkg.tar.zst"


class ReleaseTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.repo = self.root / "repo" / "x86_64"
        self.repo.mkdir(parents=True)
        self.out = self.root / "rel"
        self.listing = self.root / "packages.txt"
        self.listing.write_text("".join(f"package packages/{p} {p}\n" for p in PKGS))

        # Two versions of each package, as the 保留窗口 leaves them on disk.
        for pkg in PKGS:
            for version in (PREVIOUS, CURRENT):
                (self.repo / pkgfile(pkg, version)).write_bytes(
                    f"{pkg} {version}".encode()
                )

    def run_script(self, *argv: str, env: dict[str, str] | None = None):
        full = dict(os.environ)
        for key in SERVED:
            full.pop(key, None)
        full.update(SERVED)
        full.update(env or {})
        return subprocess.run(
            ["bash", str(SCRIPT), *argv],
            cwd=self.root,
            text=True,
            capture_output=True,
            env=full,
        )

    def release(self, version: str = CURRENT):
        result = self.run_script(
            str(self.repo), version, str(self.listing), str(self.out)
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def assets(self) -> list[str]:
        return (self.out / "assets.txt").read_text().splitlines()

    def sums(self) -> dict[str, str]:
        found = {}
        for line in (self.out / "SHA256SUMS").read_text().splitlines():
            digest, name = line.split()
            found[name] = digest
        return found

    def test_only_this_version_is_handed_over(self):
        self.release()

        self.assertEqual(
            [Path(path).name for path in self.assets()],
            [pkgfile(pkg, CURRENT) for pkg in PKGS],
        )
        # The previous build stays on disk for the 保留窗口 and out of the
        # release; a file it carried would advertise two versions at once.
        self.assertEqual(
            sorted(self.sums()), sorted(pkgfile(pkg, CURRENT) for pkg in PKGS)
        )

    def test_the_checksums_are_of_the_bytes_that_will_be_served(self):
        self.release()

        for name, digest in self.sums().items():
            self.assertEqual(
                digest, hashlib.sha256((self.repo / name).read_bytes()).hexdigest()
            )

    def test_the_notes_carry_the_block_a_user_pastes(self):
        """The copy nobody guarded: the page a user lands on.

        README's block is held to the workflow's env by tests/test_inventory.py.
        The notes were prose -- `[magpie]` written into the heredoc, no
        `Server =` line, no `SigLevel = Optional` -- and no test read them, so a
        renamed section or a moved URL shipped instructions that 404 or cannot
        install.
        """
        self.release()

        notes = (self.out / "notes.md").read_text()
        self.assertIn(f"sudo pacman -S {' '.join(PKGS)}", notes)
        self.assertIn(f"v{CURRENT}", notes)
        self.assertEqual(
            install_block.problems(
                config=notes,
                installs=notes,
                label="the Release notes",
                section=SERVED["REPO_NAME"],
                url=SERVED["PAGES_URL"],
                arch=SERVED["ARCH"],
                packages=PKGS,
            ),
            [],
        )

    def test_the_block_is_emitted_from_the_identity_it_was_given(self):
        # Held to a value this run did not use before: a guard against the
        # current wording would pass with the literal still in the heredoc.
        served = {
            **SERVED,
            "REPO_NAME": "nightly",
            "PAGES_URL": "https://example.invalid/nightly",
        }
        result = self.run_script(
            str(self.repo), CURRENT, str(self.listing), str(self.out), env=served
        )
        self.assertEqual(result.returncode, 0, result.stderr)

        notes = (self.out / "notes.md").read_text()
        self.assertIn("[nightly]", notes)
        self.assertNotIn("[magpie]", notes)
        self.assertIn("Server = https://example.invalid/nightly/x86_64", notes)
        self.assertEqual(
            install_block.problems(
                config=notes,
                installs=notes,
                label="the Release notes",
                section="nightly",
                url="https://example.invalid/nightly",
                arch="x86_64",
                packages=PKGS,
            ),
            [],
        )

    def test_the_three_frozen_lines_are_the_handover(self):
        result = self.release()

        # By name, through the reader the pipeline uses: every consumer asks
        # for a key, so a line in another order is not a break -- a line
        # written twice is, and a hand-rolled split cannot tell.
        handed = frozen.fields(result.stdout)
        self.assertEqual(sorted(handed), ["assets", "notes", "sums"])
        for line, path in handed.items():
            self.assertTrue(
                (self.root / path).is_file() or Path(path).is_file(), line
            )

    def test_a_version_that_was_never_built_is_named(self):
        result = self.run_script(
            str(self.repo), "0.1.11", str(self.listing), str(self.out)
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no package files for v0.1.11", result.stderr)
        self.assertFalse((self.out / "assets.txt").exists())

    def test_an_empty_package_list_is_refused(self):
        self.listing.write_text("")
        result = self.run_script(
            str(self.repo), CURRENT, str(self.listing), str(self.out)
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("names no packages", result.stderr)

    def test_a_malformed_package_list_is_reported_as_itself(self):
        # `mapfile < <(reader)` exits 0 when the reader fails, so a broken
        # 包清单 would otherwise read as "no packages" -- the wrong problem,
        # reported by the wrong module.
        self.listing.write_text(f"{PKGS[0]}\n")
        result = self.run_script(
            str(self.repo), CURRENT, str(self.listing), str(self.out)
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not a 包清单 line", result.stderr)
        self.assertNotIn("names no packages", result.stderr)

    def test_the_served_identity_is_required(self):
        # The notes quote these to a user about to copy them into pacman.conf;
        # asking for them here is what stops this module from inventing a
        # section name or a URL.
        for key in ("ARCH", "REPO_NAME", "PAGES_URL"):
            with self.subTest(key=key):
                result = self.run_script(
                    str(self.repo), CURRENT, str(self.listing), str(self.out),
                    env={key: ""},
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(key, result.stderr)


if __name__ == "__main__":
    unittest.main()
