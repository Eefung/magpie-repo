"""publish-pages.sh, driven against a bare repository on this machine.

It was the step nothing could test: the one that force-pushes to the branch the
软件源 is served from, with its three rules -- `.nojekyll`, one squashed commit
per release, the message shape -- written as prose in the same YAML block.
`check.sh` listed the whole block as uncovered, and the only part of it with a
test was the seed line, by pulling that line out of the YAML with a regex.

A local bare repository is the second adapter the seam was missing: CI passes
the token URL, this test passes a path, and the script cannot tell the
difference. What a publish run still proves is the token URL and Pages itself.

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "publish-pages.sh"

sys.path.insert(0, str(REPO / "tests"))

import fixtures  # noqa: E402  (the one spelling of a 包文件's name in the test tree)
import workflow  # noqa: E402  (the one reader of publish.yml, where the bot is declared)


@dataclass(frozen=True)
class GitIdentity:
    """The bot the workflow's env declares, so the test can feed the script it.

    Mirrors `install_block.Served` in test_release.py: the script is held to
    a value it was *given*, not to today's literal. The value is read from the
    workflow rather than spelled here -- a literal in this file was a third
    copy, and `GitIdentityTest` counts only the workflow's, so renaming the bot
    left this fixture feeding the old identity while every test stayed green.
    """

    name: str
    email: str

    @classmethod
    def declared(cls) -> "GitIdentity":
        """The identity the job env declares, which is the only place it lives."""
        env = workflow.env()
        return cls(name=env["GIT_NAME"], email=env["GIT_EMAIL"])

    def env(self) -> dict[str, str]:
        return {"GIT_NAME": self.name, "GIT_EMAIL": self.email}


GIT = GitIdentity.declared()

# The tree this publishes is served as uploaded, so the name is only a name --
# but it is still a 包文件's name, and the test tree spells those one way.
CURRENT = fixtures.pkgfile_name("magpie-bin", "0.1.9")
LATER = fixtures.pkgfile_name("magpie-bin", "0.1.10")


def git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-c", "init.defaultBranch=main", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
    )


class PublishPagesTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.remote = self.root / "remote.git"
        made = git("init", "--quiet", "--bare", str(self.remote), cwd=self.root)
        self.assertEqual(made.returncode, 0, made.stderr)

    def tree(self, name: str, *files: str) -> Path:
        """An assembled tree, the way build-repo.sh leaves one."""
        path = self.root / name
        (path / "x86_64").mkdir(parents=True)
        for file in files:
            (path / "x86_64" / file).touch()
        return path

    def publish(self, tree: Path, version: str,
                env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
        full = dict(os.environ)
        for key in GIT.env():
            full.pop(key, None)
        full.update(GIT.env())
        full.update(env or {})
        return subprocess.run(
            ["bash", str(SCRIPT), str(tree), version, str(self.remote)],
            cwd=self.root,
            text=True,
            capture_output=True,
            env=full,
        )

    def branch_files(self) -> list[str]:
        listing = git("ls-tree", "-r", "--name-only", "gh-pages", cwd=self.remote)
        self.assertEqual(listing.returncode, 0, listing.stderr)
        return listing.stdout.splitlines()

    def commits(self) -> int:
        counted = git("rev-list", "--count", "gh-pages", cwd=self.remote)
        self.assertEqual(counted.returncode, 0, counted.stderr)
        return int(counted.stdout.strip())

    def test_the_tree_is_served_as_uploaded(self):
        result = self.publish(self.tree("one", CURRENT), "0.1.9")

        self.assertEqual(result.returncode, 0, result.stderr)
        files = self.branch_files()
        self.assertIn(f"x86_64/{CURRENT}", files)
        # Without it Pages runs the tree through Jekyll instead of serving it.
        self.assertIn(".nojekyll", files)

    def test_each_publish_replaces_the_branch_with_one_commit(self):
        first = self.publish(self.tree("one", CURRENT), "0.1.9")
        self.assertEqual(first.returncode, 0, first.stderr)
        second = self.publish(self.tree("two", LATER), "0.1.10")
        self.assertEqual(second.returncode, 0, second.stderr)

        # A force push of a fresh root, not a second commit on top: the branch
        # is a deployment, and every package it ever served would otherwise stay
        # in its history.
        self.assertEqual(self.commits(), 1)
        files = self.branch_files()
        self.assertIn(f"x86_64/{LATER}", files)
        self.assertNotIn(f"x86_64/{CURRENT}", files)

    def test_the_commit_names_the_version_it_publishes(self):
        self.publish(self.tree("one", CURRENT), "0.1.9")

        message = git("log", "-1", "--format=%s", "gh-pages", cwd=self.remote)
        self.assertEqual(message.stdout.strip(), "build(gh-pages): magpie 0.1.9")

    def test_the_commit_is_signed_by_the_given_identity(self):
        self.publish(self.tree("one", CURRENT), "0.1.9")

        author = git(
            "log", "-1", "--format=%an <%ae>", "gh-pages", cwd=self.remote
        )
        self.assertEqual(
            author.stdout.strip(), f"{GIT.name} <{GIT.email}>"
        )

    def test_the_identity_is_required(self):
        # A hand-run that forgets the env must fail rather than commit as
        # whatever git's local defaults happen to be.
        for key in ("GIT_NAME", "GIT_EMAIL"):
            with self.subTest(key=key):
                result = self.publish(
                    self.tree(f"req_{key}", CURRENT), "0.1.9", env={key: ""}
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(key, result.stderr)

    def test_a_tree_that_is_not_there_is_refused(self):
        result = self.publish(self.root / "absent", "0.1.9")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no such tree", result.stderr)
        self.assertNotEqual(git("rev-parse", "--verify", "gh-pages", cwd=self.remote).returncode, 0)

    def test_a_version_that_is_not_dotted_digits_is_refused(self):
        # `v0.1.9` was the shape both doors refused, so on its own it proved
        # nothing about which one this script uses. `1`, `1.` and `1..2` are
        # the ones its own character class used to accept while the render
        # refused them. One rule now, from pkg.sh: `version_shape_ok`.
        for index, version in enumerate(("v0.1.9", "1", "1.", "1..2")):
            with self.subTest(version=version):
                result = self.publish(self.tree(f"bad{index}"), version)

                self.assertNotEqual(result.returncode, 0)
                self.assertIn("not a 版本", result.stderr)
                self.assertNotEqual(
                    git("rev-parse", "--verify", "gh-pages", cwd=self.remote).returncode, 0
                )


if __name__ == "__main__":
    unittest.main()
