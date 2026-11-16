"""release-state.sh: does this 版本 already have a Release?

The Release step asked `gh release view` and branched on its exit status, which
is the same for "no such Release" and "the query failed". The rule here is a
value GitHub gives -- the HTTP status -- so it can be stated offline: the stub
below is the second adapter for `GH`.

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "release-state.sh"

sys.path.insert(0, str(REPO / "tests"))

import frozen  # noqa: E402  (the production reader of the frozen lines)

STUB = """\
#!/usr/bin/env bash
# Answers whatever status this test told it to, in the shape `gh api -i` uses.
printf 'HTTP/2.0 %s\\n' "$STATUS"
printf 'content-type: application/json\\r\\n\\r\\n'
[ "$STATUS" = 200 ] && printf '{"tag_name": "v%s"}\\n' "$WANTED"
echo "$*" >> "$GH_LOG"
[ "$STATUS" = 200 ] || [ "$STATUS" = 404 ]
"""


class ReleaseStateTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.stub = self.root / "gh"
        self.stub.write_text(STUB)
        self.stub.chmod(0o755)
        self.log = self.root / "gh.log"

    def ask(self, status: str = "200", *, version: str = "0.1.4318") -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["GH"] = str(self.stub)
        env["STATUS"] = status
        env["WANTED"] = version
        env["GH_LOG"] = str(self.log)
        return subprocess.run(
            ["bash", str(SCRIPT), version, "Eefung/magpie-repo"],
            text=True,
            capture_output=True,
            env=env,
        )

    def handed(self, result: subprocess.CompletedProcess) -> str:
        self.assertEqual(result.returncode, 0, result.stderr)
        value = frozen.field("release", result.stdout + "\n")
        if value is None:
            self.fail(f"the answer carried no release= line: {result.stdout!r}")
        return value

    def test_a_release_that_exists_is_present(self):
        self.assertEqual(self.handed(self.ask("200")), "present")

    def test_a_tag_without_a_release_is_absent(self):
        self.assertEqual(self.handed(self.ask("404")), "absent")

    def test_it_asks_about_the_version_it_was_given(self):
        result = self.ask("200", version="0.1.9")
        self.handed(result)
        self.assertEqual(
            self.log.read_text().strip(),
            "api -i repos/Eefung/magpie-repo/releases/tags/v0.1.9",
        )

    def test_a_failed_lookup_is_not_an_absent_release(self):
        # The whole card: reading a 500 (or a rate limit) as "no Release" is how
        # a transient failure took the create path and reported already_exists.
        for status in ("500", "403", "429"):
            with self.subTest(status=status):
                result = self.ask(status)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
                self.assertIn(status, result.stderr)

    def test_an_answer_with_no_status_line_is_refused(self):
        # A gh that printed nothing -- not authenticated, or not there -- has not
        # answered the question. Silence must not read as "absent".
        self.stub.write_text("#!/usr/bin/env bash\nexit 0\n")
        self.stub.chmod(0o755)
        result = self.ask("200")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("answered nothing", result.stderr)


if __name__ == "__main__":
    unittest.main()
